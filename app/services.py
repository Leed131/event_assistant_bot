from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import Event, SessionLocal, Source, User, UserEvent, UserEventStatus, UserSource
from app.sources import DACSource, GenericJsonLdSource


def matches(user: User, event: Event) -> bool:
    if user.city and event.city.casefold() != user.city.casefold():
        return False
    interests = [x.strip().casefold() for x in user.interests.split(",") if x.strip()]
    haystack = f"{event.title} {event.description or ''}".casefold()
    return not interests or any(word in haystack for word in interests)


async def refresh_curated_sources(bot=None) -> int:
    created = 0
    with SessionLocal() as db:
        sources = db.scalars(select(Source).where(Source.active.is_(True))).all()
        for source in sources:
            try:
                parser = DACSource() if source.source_type == "curated" else GenericJsonLdSource()
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

                if bot and is_new:
                    for user in recipients:
                        if matches(user, event):
                            existing = db.scalar(
                                select(UserEvent).where(
                                    UserEvent.user_id == user.id,
                                    UserEvent.event_id == event.id,
                                )
                            )
                            if not existing:
                                db.add(
                                    UserEvent(
                                        user_id=user.id,
                                        event_id=event.id,
                                        status=UserEventStatus.discovered.value,
                                    )
                                )
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
