from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import urlparse

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from app.config import settings
from app.db import Event, SessionLocal, Source, User, UserEvent, UserEventStatus, UserSource
from app.event_display import event_card
from app.services import persist_detected_events_for_user
from app.source_overrides import parser_for_source

MENU_WORDS = {"start", "menu", "main menu", "cancel", "stop", "back", "меню", "старт", "отмена"}
PENDING_KEYS = (
    "awaiting_interest_add",
    "awaiting_interest_remove",
    "awaiting_source_url",
    "setting",
    "ticket_for",
    "note_for",
)


def is_menu_word(text: str | None) -> bool:
    """Plain words like "Start" or "Menu" should always bring the user back to the menu."""
    return (text or "").strip().lstrip("/").casefold() in MENU_WORDS


def reset_pending(user_data: dict) -> None:
    """Forget any half-finished flow (adding an interest, a note, a ticket, ...)."""
    for key in PENDING_KEYS:
        user_data.pop(key, None)


def menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Discover", callback_data="discover"), InlineKeyboardButton("This week", callback_data="week")],
        [InlineKeyboardButton("My events", callback_data="registered")],
        [InlineKeyboardButton("City", callback_data="city"), InlineKeyboardButton("Interests", callback_data="interests")],
        [InlineKeyboardButton("Sources", callback_data="sources")],
    ])


def sources_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Add a source", callback_data="source:add")],
        [InlineKeyboardButton("View sources", callback_data="source:list")],
        [InlineKeyboardButton("← Menu", callback_data="main")],
    ])


def interests_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Add", callback_data="interest:add")],
        [InlineKeyboardButton("Remove", callback_data="interest:remove")],
        [InlineKeyboardButton("View interests", callback_data="interest:list")],
        [InlineKeyboardButton("← Menu", callback_data="main")],
    ])


def _interest_list(raw: str | None) -> list[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def _calendar_url(event_id: int) -> str:
    return f"{settings.public_base_url.rstrip('/')}/calendar/{event_id}"


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
    reset_pending(context.user_data)
    user = get_or_create_user(update.effective_user)
    await update.message.reply_text(
        f"Hej {user.first_name or ''}.\n\nI watch the places you care about and bring you the events that match your interests. Your registrations, tickets and reminders stay in one place.",
        reply_markup=menu(),
    )


async def show_interests(message, user: User) -> None:
    interests = _interest_list(user.interests)
    if not interests:
        await message.reply_text("No interests yet. Add a few topics to start.", reply_markup=interests_menu())
        return
    formatted = "\n".join(f"· {escape(interest)}" for interest in interests)
    await message.reply_text(f"<b>Your interests</b>\n\n{formatted}", parse_mode="HTML", reply_markup=interests_menu())


async def show_sources(message, user: User) -> None:
    with SessionLocal() as db:
        curated = db.scalars(select(Source).where(Source.source_type == "curated", Source.active.is_(True))).all()
        custom_rows = db.execute(
            select(UserSource, Source)
            .join(Source, UserSource.source_id == Source.id)
            .where(UserSource.user_id == user.id)
            .order_by(Source.name)
        ).all()

        curated_text = "\n\n".join(f"{escape(source.name)}\n{escape(source.url)}" for source in curated) if curated else "No curated sources yet."
        await message.reply_text(f"<b>Curated sources</b>\n\n{curated_text}", parse_mode="HTML", disable_web_page_preview=True)

        if not custom_rows:
            await message.reply_text("No personal sources yet.", reply_markup=sources_menu())
            return

        for user_source, source in custom_rows:
            state = "On" if user_source.enabled else "Paused"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("Pause" if user_source.enabled else "Resume", callback_data=f"source:toggle:{source.id}")],
                [InlineKeyboardButton("Remove", callback_data=f"source:remove:{source.id}")],
            ])
            await message.reply_text(f"{source.name} · {state}\n{source.url}", reply_markup=keyboard, disable_web_page_preview=True)


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = get_or_create_user(q.from_user)

    if q.data == "main":
        reset_pending(context.user_data)
        await q.message.reply_text("Menu", reply_markup=menu())
        return

    if q.data == "interests":
        await q.message.reply_text("Manage the topics I use to filter events for you.", reply_markup=interests_menu())
        return
    if q.data == "interest:add":
        context.user_data["awaiting_interest_add"] = True
        await q.message.reply_text("Send the interest you want to add. You can add several at once separated by commas.\n\nExample: AI, sustainability, women in AI, leadership")
        return
    if q.data == "interest:remove":
        context.user_data["awaiting_interest_remove"] = True
        current = ", ".join(_interest_list(user.interests)) or "none"
        await q.message.reply_text(f"Current interests: {current}\n\nSend the interest you want to remove. You can remove several at once separated by commas.")
        return
    if q.data == "interest:list":
        await show_interests(q.message, user)
        return

    if q.data == "sources":
        await q.message.reply_text("Manage the websites I monitor for you. You can add an events/calendar page directly from Telegram.", reply_markup=sources_menu())
        return
    if q.data == "source:add":
        context.user_data["awaiting_source_url"] = True
        await q.message.reply_text("Paste the link to an events page and send it.\n\nExample: https://example.org/events\n\nYou can also send any http/https link at any time — I will treat it as a new source.")
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
            state = "resumed" if link.enabled else "paused"
        await q.message.reply_text(f"Source {state}.", reply_markup=sources_menu())
        return
    if q.data.startswith("source:remove:"):
        source_id = int(q.data.rsplit(":", 1)[1])
        with SessionLocal() as db:
            link = db.scalar(select(UserSource).where(UserSource.user_id == user.id, UserSource.source_id == source_id))
            if link:
                db.delete(link)
                db.commit()
        await q.message.reply_text("Source removed.", reply_markup=sources_menu())
        return

    with SessionLocal() as db:
        if q.data in {"week", "registered", "discover"}:
            now = datetime.now(timezone.utc)
            stmt = (
                select(UserEvent)
                .join(Event)
                .where(UserEvent.user_id == user.id)
            )
            if q.data == "registered":
                stmt = stmt.where(UserEvent.status == UserEventStatus.registered.value)
            elif q.data == "week":
                stmt = stmt.where(
                    UserEvent.status == UserEventStatus.registered.value,
                    Event.start_datetime.between(now, now + timedelta(days=7)),
                )
            else:
                stmt = stmt.where(
                    UserEvent.status == UserEventStatus.discovered.value,
                    Event.start_datetime >= now,
                )

            stmt = stmt.order_by(Event.start_datetime.asc()).limit(20)
            items = db.scalars(stmt).all()
            if not items:
                await q.message.reply_text("No events here yet.")
                return
            for item in items:
                event = db.get(Event, item.event_id)
                buttons = [
                    [InlineKeyboardButton("Register", url=event.registration_url)],
                    [InlineKeyboardButton("Add to calendar", url=_calendar_url(event.id))],
                ]
                if item.status != UserEventStatus.registered.value:
                    buttons.append([
                        InlineKeyboardButton("I registered", callback_data=f"reg:{item.id}"),
                        InlineKeyboardButton("Not for me", callback_data=f"ignore:{item.id}"),
                    ])
                else:
                    buttons.append([
                        InlineKeyboardButton("Ticket", callback_data=f"ticket:{item.id}"),
                        InlineKeyboardButton("Note", callback_data=f"note:{item.id}"),
                    ])
                await q.message.reply_text(
                    event_card(event, note=item.note),
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
        elif q.data.startswith("reg:"):
            item = db.get(UserEvent, int(q.data.split(":")[1]))
            item.status = UserEventStatus.registered.value
            item.registered_at = datetime.now(timezone.utc)
            db.commit()
            context.user_data["ticket_for"] = item.id
            await q.message.reply_text("Saved to your events.\n\nSend the ticket or QR code (image or PDF) now, or add it later.")
        elif q.data.startswith("ignore:"):
            item = db.get(UserEvent, int(q.data.split(":")[1]))
            item.status = UserEventStatus.ignored.value
            db.commit()
            await q.message.reply_text("Hidden. You will not see this event again.")
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


async def incoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_or_create_user(update.effective_user)
    raw_text = (update.message.text or "").strip()

    if is_menu_word(raw_text):
        reset_pending(context.user_data)
        await update.message.reply_text("Menu", reply_markup=menu())
        return

    if context.user_data.pop("awaiting_interest_add", False):
        new_items = _interest_list(raw_text)
        if not new_items:
            context.user_data["awaiting_interest_add"] = True
            await update.message.reply_text("Please send at least one interest.")
            return
        with SessionLocal() as db:
            db_user = db.get(User, user.id)
            current = _interest_list(db_user.interests)
            existing_keys = {item.casefold() for item in current}
            added: list[str] = []
            for item in new_items:
                if item.casefold() not in existing_keys:
                    current.append(item)
                    existing_keys.add(item.casefold())
                    added.append(item)
            db_user.interests = ",".join(current)
            db.commit()
        await update.message.reply_text("Added: " + ", ".join(added) if added else "Those interests are already in your list.", reply_markup=interests_menu())
        return

    if context.user_data.pop("awaiting_interest_remove", False):
        remove_items = _interest_list(raw_text)
        if not remove_items:
            context.user_data["awaiting_interest_remove"] = True
            await update.message.reply_text("Please send at least one interest to remove.")
            return
        remove_keys = {item.casefold() for item in remove_items}
        with SessionLocal() as db:
            db_user = db.get(User, user.id)
            current = _interest_list(db_user.interests)
            kept = [item for item in current if item.casefold() not in remove_keys]
            removed = [item for item in current if item.casefold() in remove_keys]
            db_user.interests = ",".join(kept)
            db.commit()
        await update.message.reply_text("Removed: " + ", ".join(removed) if removed else "I could not find those interests in your list.", reply_markup=interests_menu())
        return

    parsed_text = urlparse(raw_text) if raw_text else None
    looks_like_url = bool(parsed_text and parsed_text.scheme in {"http", "https"} and parsed_text.netloc)
    awaiting_source = context.user_data.pop("awaiting_source_url", False)

    if awaiting_source or looks_like_url:
        raw_url = raw_text
        parsed = urlparse(raw_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            context.user_data["awaiting_source_url"] = True
            await update.message.reply_text("That does not look like a full link. Send an address that starts with http:// or https://")
            return

        await update.message.reply_text("Checking the page…")
        try:
            detected_events = await parser_for_source(raw_url, "user").fetch(raw_url)
        except Exception as exc:
            await update.message.reply_text(f"I could not open this page ({type(exc).__name__}). Check the link and try again.")
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
            source_id = source.id
            db.commit()

        if detected_events:
            persist_detected_events_for_user(user.id, source_id, detected_events)
            await update.message.reply_text(f"Source added. I found {len(detected_events)} upcoming events and saved them.", reply_markup=sources_menu())
        else:
            await update.message.reply_text("Source saved. I found no upcoming events on this page yet, but I will keep checking it.", reply_markup=sources_menu())
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
            await update.message.reply_text("Ticket saved.")
            return
        if context.user_data.get("note_for"):
            item = db.get(UserEvent, context.user_data.pop("note_for"))
            item.note = update.message.text
            db.commit()
            await update.message.reply_text("Note saved.")
            return
        setting = context.user_data.pop("setting", None)
        if setting:
            db_user = db.get(User, user.id)
            setattr(db_user, setting, update.message.text.strip())
            db.commit()
            await update.message.reply_text("Updated.", reply_markup=menu())
            return
        await update.message.reply_text("Choose from the menu.", reply_markup=menu())


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_pending(context.user_data)
    await update.message.reply_text("Menu", reply_markup=menu())


def register_handlers(app):
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler(["menu", "cancel"], show_menu))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT | filters.PHOTO | filters.Document.ALL, incoming))

