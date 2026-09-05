from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.db import Event, SessionLocal, Source, User, UserEvent, UserEventStatus, UserSource
from app.sources import GenericJsonLdSource


def menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 Discover events", callback_data="discover"), InlineKeyboardButton("📅 This week", callback_data="week")],
        [InlineKeyboardButton("⭐ My registrations", callback_data="registered")],
        [InlineKeyboardButton("🌍 My city", callback_data="city"), InlineKeyboardButton("❤️ My interests", callback_data="interests")],
        [InlineKeyboardButton("🌐 My sources", callback_data="sources")],
    ])


def sources_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add source", callback_data="source:add")],
        [InlineKeyboardButton("📋 My sources", callback_data="source:list")],
        [InlineKeyboardButton("⬅️ Main menu", callback_data="main")],
    ])


def get_or_create_user(tg_user) -> User:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.telegram_user_id == tg_user.id))
        if not user:
            user = User(telegram_user_id=tg_user.id, username=tg_user.username, first_name=tg_user.first_name)
            db.add(user)
            db.commit()
            db.refresh(user)
        return user


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_or_create_user(update.effective_user)
    await update.message.reply_text(
        f"Hi {user.first_name or ''}! I find events before they sell out and keep your registrations, tickets and reminders in one place.",
        reply_markup=menu(),
    )


async def show_sources(message, user: User) -> None:
    with SessionLocal() as db:
        curated = db.scalars(select(Source).where(Source.source_type == "curated", Source.active.is_(True))).all()
        custom_rows = db.execute(
            select(UserSource, Source)
            .join(Source, UserSource.source_id == Source.id)
            .where(UserSource.user_id == user.id)
            .order_by(Source.name)
        ).all()

        if curated:
            curated_text = "\n".join(f"✅ {source.name}\n{source.url}" for source in curated)
        else:
            curated_text = "No curated sources yet."

        await message.reply_text(f"🏛 Curated sources\n\n{curated_text}")

        if not custom_rows:
            await message.reply_text("You have no custom sources yet.", reply_markup=sources_menu())
            return

        for user_source, source in custom_rows:
            state = "✅ Enabled" if user_source.enabled else "⏸ Disabled"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("⏸ Disable" if user_source.enabled else "▶️ Enable", callback_data=f"source:toggle:{source.id}")],
                [InlineKeyboardButton("🗑 Remove", callback_data=f"source:remove:{source.id}")],
            ])
            await message.reply_text(f"{state} · {source.name}\n{source.url}", reply_markup=keyboard)


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = get_or_create_user(q.from_user)

    if q.data == "main":
        await q.message.reply_text("Main menu", reply_markup=menu())
        return
    if q.data == "sources":
        await q.message.reply_text(
            "Manage the websites I monitor for you. You can add an events/calendar page directly from Telegram.",
            reply_markup=sources_menu(),
        )
        return
    if q.data == "source:add":
        context.user_data["awaiting_source_url"] = True
        await q.message.reply_text("Send me the full URL of an events or calendar page, for example:\nhttps://example.org/events")
        return
    if q.data == "source:list":
        await show_sources(q.message, user)
        return
    if q.data.startswith("source:toggle:"):
        source_id = int(q.data.rsplit(":", 1)[1])
        with SessionLocal() as db:
            link = db.scalar(select(UserSource).where(UserSource.user_id == user.id, UserSource.source_id == source_id))
            if not link:
                await q.message.reply_text("Source not found.")
                return
            link.enabled = not link.enabled
            db.commit()
            state = "enabled ✅" if link.enabled else "disabled ⏸"
        await q.message.reply_text(f"Source {state}.", reply_markup=sources_menu())
        return
    if q.data.startswith("source:remove:"):
        source_id = int(q.data.rsplit(":", 1)[1])
        with SessionLocal() as db:
            link = db.scalar(select(UserSource).where(UserSource.user_id == user.id, UserSource.source_id == source_id))
            if link:
                db.delete(link)
                db.commit()
        await q.message.reply_text("🗑 Source removed from your list.", reply_markup=sources_menu())
        return

    with SessionLocal() as db:
        if q.data in {"week", "registered", "discover"}:
            stmt = select(UserEvent).where(UserEvent.user_id == user.id)
            if q.data == "registered":
                stmt = stmt.where(UserEvent.status == UserEventStatus.registered.value)
            if q.data == "week":
                now = datetime.now(timezone.utc)
                stmt = stmt.join(Event).where(
                    UserEvent.status == UserEventStatus.registered.value,
                    Event.start_datetime.between(now, now + timedelta(days=7)),
                )
            items = db.scalars(stmt).all()
            if not items:
                await q.message.reply_text("No events here yet.")
                return
            for item in items[:20]:
                event = db.get(Event, item.event_id)
                buttons = [[InlineKeyboardButton("🔗 Register", url=event.registration_url)]]
                if item.status != UserEventStatus.registered.value:
                    buttons.append([
                        InlineKeyboardButton("✅ I registered", callback_data=f"reg:{item.id}"),
                        InlineKeyboardButton("🙈 Not interested", callback_data=f"ignore:{item.id}"),
                    ])
                else:
                    buttons.append([
                        InlineKeyboardButton("🎟 Show ticket", callback_data=f"ticket:{item.id}"),
                        InlineKeyboardButton("📝 Add note", callback_data=f"note:{item.id}"),
                    ])
                await q.message.reply_text(
                    f"🏛 {event.title}\n📅 {event.start_datetime:%d %b %Y, %H:%M}\n📍 {event.venue_name or event.city}\n{item.note or ''}",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
        elif q.data.startswith("reg:"):
            item = db.get(UserEvent, int(q.data.split(":")[1]))
            item.status = UserEventStatus.registered.value
            item.registered_at = datetime.now(timezone.utc)
            db.commit()
            context.user_data["ticket_for"] = item.id
            await q.message.reply_text("✅ Saved. Send me the QR image/PDF now, or skip it and add it later.")
        elif q.data.startswith("ignore:"):
            item = db.get(UserEvent, int(q.data.split(":")[1]))
            item.status = UserEventStatus.ignored.value
            db.commit()
            await q.message.reply_text("Hidden.")
        elif q.data.startswith("ticket:"):
            item = db.get(UserEvent, int(q.data.split(":")[1]))
            if not item.ticket_file_id:
                context.user_data["ticket_for"] = item.id
                await q.message.reply_text("Send the ticket image or PDF.")
            elif item.ticket_type == "photo":
                await q.message.reply_photo(item.ticket_file_id)
            else:
                await q.message.reply_document(item.ticket_file_id)
        elif q.data.startswith("note:"):
            context.user_data["note_for"] = int(q.data.split(":")[1])
            await q.message.reply_text("Send your note as a message.")
        elif q.data == "city":
            context.user_data["setting"] = "city"
            await q.message.reply_text(f"Current city: {user.city}. Send a new city name.")
        elif q.data == "interests":
            context.user_data["setting"] = "interests"
            await q.message.reply_text(f"Current interests: {user.interests}. Send comma-separated interests.")


async def incoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_or_create_user(update.effective_user)

    if context.user_data.pop("awaiting_source_url", False):
        raw_url = (update.message.text or "").strip()
        parsed = urlparse(raw_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            context.user_data["awaiting_source_url"] = True
            await update.message.reply_text("That does not look like a valid URL. Please send a full http:// or https:// address.")
            return

        await update.message.reply_text("🔎 Checking the source…")
        try:
            detected_events = await GenericJsonLdSource().fetch(raw_url)
        except Exception as exc:
            await update.message.reply_text(
                f"I could not open this page right now ({type(exc).__name__}). Nothing was saved. Check the URL and try again."
            )
            return

        hostname = parsed.netloc.removeprefix("www.")
        with SessionLocal() as db:
            source = db.scalar(select(Source).where(Source.url == raw_url))
            if not source:
                source = Source(name=hostname, url=raw_url, source_type="user", city=user.city, active=True)
                db.add(source)
                db.flush()
            link = db.scalar(select(UserSource).where(UserSource.user_id == user.id, UserSource.source_id == source.id))
            if not link:
                db.add(UserSource(user_id=user.id, source_id=source.id, enabled=True))
            else:
                link.enabled = True
            db.commit()

        if detected_events:
            await update.message.reply_text(
                f"✅ Source added. I detected {len(detected_events)} structured event(s) on the page and will monitor it.",
                reply_markup=sources_menu(),
            )
        else:
            await update.message.reply_text(
                "⚠️ Source saved, but I did not detect structured Event data on this page yet. I will keep it in your list; this site may need a dedicated parser later.",
                reply_markup=sources_menu(),
            )
        return

    with SessionLocal() as db:
        if context.user_data.get("ticket_for"):
            item = db.get(UserEvent, context.user_data.pop("ticket_for"))
            if update.message.photo:
                item.ticket_file_id = update.message.photo[-1].file_id
                item.ticket_type = "photo"
            elif update.message.document:
                item.ticket_file_id = update.message.document.file_id
                item.ticket_type = "document"
            else:
                await update.message.reply_text("Please send an image or PDF.")
                return
            db.commit()
            await update.message.reply_text("🎟 Ticket saved.")
            return
        if context.user_data.get("note_for"):
            item = db.get(UserEvent, context.user_data.pop("note_for"))
            item.note = update.message.text
            db.commit()
            await update.message.reply_text("📝 Note saved.")
            return
        setting = context.user_data.pop("setting", None)
        if setting:
            db_user = db.get(User, user.id)
            setattr(db_user, setting, update.message.text.strip())
            db.commit()
            await update.message.reply_text("Updated.", reply_markup=menu())
            return
        await update.message.reply_text("Use the menu below.", reply_markup=menu())


def register_handlers(app):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.Document.ALL, incoming))
