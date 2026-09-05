from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import Event, SessionLocal, Source, User, UserEvent, UserEventStatus, UserSource
from app.sources import parser_for_url


def matches(user: User, event: Event) -> bool:
    """Return whether an event is in the user's city.

    Interests are no longer a hard filter. They are kept as preferences for
    future ranking/AI summaries, but the bot should not hide potentially useful
    events just because a keyword did not match.
    """
    if user.city and event.city:
        user_city = user.city.casefold().strip()
        event_city = event.city.casefold().strip()
        if user_city not in event_city and event_city not in user_city:
            # Denmark-wide sources may still contain events relevant to Copenhagen;
            # keep them visible rather than dropping them at ingestion time.
            if event_city != "denmark":
                return False
    return True


async def refresh_curated_sources(bot=None) -> int:
    created = 0
    with SessionLocal() as db:
        sources = db.scalars(select(Source).where(Source.active.is_(True))).all()
        for source in sources:
            try:
                parser = parser_for_url(source.url, source.source_type)
                candidates = await parser.fetch(source.url)
            except Exception:
                continue

            if source.source_type == "curated":
                recipients = db.scalars(select(User)).all()
            else:
                recipients = db.scalars(
                    select(User)
                    .join(UserSource, UserSource.user_id == User.id)
                    .where(UserSource.source_id == source.id, UserSource.enabled.is_(True))
                ).all()

            for c in candidates:
                event = db.scalar(select(Event).where(Event.dedup_key == c.dedup_key))
                is_new = event is None
                if event:
                    event.last_seen_at = datetime.now(timezone.utc)
                else:
                    event = Event(
                        source_id=source.id,
                        external_id=c.external_id,
                        title=c.title,
                        description=c.description,
                        start_datetime=c.start_datetime,
                        end_datetime=c.end_datetime,
                        venue_name=c.venue_name,
                        address=c.address,
                        city=c.city,
                        registration_url=c.registration_url,
                        source_url=c.source_url,
                        image_url=c.image_url,
                        price_text=c.price_text,
                        is_free=c.is_free,
                        dedup_key=c.dedup_key,
                    )
                    db.add(event)
                    db.flush()
                    created += 1

                for user in recipients:
                    if not matches(user, event):
                        continue
                    existing = db.scalar(
                        select(UserEvent).where(
                            UserEvent.user_id == user.id,
                            UserEvent.event_id == event.id,
                        )
                    )
                    if existing:
                        continue

                    db.add(
                        UserEvent(
                            user_id=user.id,
                            event_id=event.id,
                            status=UserEventStatus.discovered.value,
                        )
                    )

                    # Only push a Telegram notification immediately when the event
                    # was first discovered. Previously stored events still become
                    # visible in Discover without creating a notification flood.
                    if bot and is_new:
                        await bot.send_message(
                            user.telegram_user_id,
                            f"🏛 {event.title}\n📅 {event.start_datetime:%d %b %Y, %H:%M}\n📍 {event.venue_name or event.city}\n\n🔗 {event.registration_url}",
                        )
        db.commit()
    return created


async def send_due_reminders(bot) -> int:
    sent = 0
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        items = db.scalars(
            select(UserEvent).where(
                UserEvent.status == UserEventStatus.registered.value,
                UserEvent.reminder_sent_at.is_(None),
            )
        ).all()
        for item in items:
            user = db.get(User, item.user_id)
            event = item.event
            if not user or event.start_datetime <= now:
                continue
            due = event.start_datetime - timedelta(days=user.reminder_days)
            if due <= now:
                await bot.send_message(
                    user.telegram_user_id,
                    f"🔔 Reminder: {event.title}\n📅 {event.start_datetime:%d %b %Y, %H:%M}\n📍 {event.venue_name or event.city}",
                )
                item.reminder_sent_at = now
                sent += 1
        db.commit()
    return sent
