from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import select

from app.academy_search import search_royal_danish_academy_events
from app.db import Event, SessionLocal, Source, User, UserEvent, UserEventStatus, UserSource
from app.source_overrides import parser_for_source


def matches(user: User, event: Event) -> bool:
    """Return whether an event is in the user's city.

    Interests are preferences, not a hard filter. The bot should not hide useful
    events merely because a keyword did not match.
    """
    if user.city and event.city:
        user_city = user.city.casefold().strip()
        event_city = event.city.casefold().strip()
        if user_city not in event_city and event_city not in user_city:
            if event_city != "denmark":
                return False
    return True


def _refresh_existing_event(event: Event, candidate) -> None:
    event.external_id = candidate.external_id
    event.title = candidate.title
    event.description = candidate.description
    event.start_datetime = candidate.start_datetime
    event.end_datetime = candidate.end_datetime
    event.venue_name = candidate.venue_name
    event.address = candidate.address
    event.city = candidate.city
    event.registration_url = candidate.registration_url
    event.source_url = candidate.source_url
    event.image_url = candidate.image_url
    event.price_text = candidate.price_text
    event.is_free = candidate.is_free
    event.last_seen_at = datetime.now(timezone.utc)


def _hide_stale_discovered_items(db, source: Source, candidate_keys: set[str]) -> None:
    host = urlparse(source.url).netloc.casefold().removeprefix("www.")
    if not (
        host.endswith("dagensbyggeri.dk")
        or host.endswith("dac.dk")
        or host.endswith("kglakademi.dk")
        or host.endswith("royaldanishacademy.com")
    ):
        return

    source_events = db.scalars(select(Event).where(Event.source_id == source.id)).all()
    for event in source_events:
        if event.dedup_key in candidate_keys:
            continue
        discovered_links = db.scalars(
            select(UserEvent).where(
                UserEvent.event_id == event.id,
                UserEvent.status == UserEventStatus.discovered.value,
            )
        ).all()
        for link in discovered_links:
            db.delete(link)


async def _candidates_for_source(source: Source):
    parser = parser_for_source(source.url, source.source_type)
    candidates = await parser.fetch(source.url)

    host = urlparse(source.url).netloc.casefold().removeprefix("www.")
    if (
        host == "kglakademi.dk"
        or host.endswith(".kglakademi.dk")
        or host == "royaldanishacademy.com"
        or host.endswith(".royaldanishacademy.com")
    ):
        web_candidates = await search_royal_danish_academy_events()
        by_key = {candidate.dedup_key: candidate for candidate in candidates}
        for candidate in web_candidates:
            by_key.setdefault(candidate.dedup_key, candidate)
        candidates = list(by_key.values())

    return candidates


async def refresh_curated_sources(bot=None) -> int:
    created = 0
    with SessionLocal() as db:
        sources = db.scalars(select(Source).where(Source.active.is_(True))).all()
        for source in sources:
            try:
                candidates = await _candidates_for_source(source)
            except Exception:
                continue

            candidate_keys = {candidate.dedup_key for candidate in candidates}
            _hide_stale_discovered_items(db, source, candidate_keys)

            if source.source_type == "curated":
                recipients = db.scalars(select(User)).all()
            else:
                recipients = db.scalars(
                    select(User)
                    .join(UserSource, UserSource.user_id == User.id)
                    .where(UserSource.source_id == source.id, UserSource.enabled.is_(True))
                ).all()

            for candidate in candidates:
                event = db.scalar(select(Event).where(Event.dedup_key == candidate.dedup_key))
                is_new = event is None
                if event:
                    _refresh_existing_event(event, candidate)
                else:
                    event = Event(
                        source_id=source.id,
                        external_id=candidate.external_id,
                        title=candidate.title,
                        description=candidate.description,
                        start_datetime=candidate.start_datetime,
                        end_datetime=candidate.end_datetime,
                        venue_name=candidate.venue_name,
                        address=candidate.address,
                        city=candidate.city,
                        registration_url=candidate.registration_url,
                        source_url=candidate.source_url,
                        image_url=candidate.image_url,
                        price_text=candidate.price_text,
                        is_free=candidate.is_free,
                        dedup_key=candidate.dedup_key,
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
