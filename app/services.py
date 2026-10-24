import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.academy_search import search_royal_danish_academy_events
from app.bloxhub_source import BloxhubSource
from app.config import settings
from app.dac_search import search_dac_talks
from app.db import (
    Event,
    ServiceRunState,
    SessionLocal,
    Source,
    User,
    UserEvent,
    UserEventStatus,
    UserSource,
)
from app.event_display import event_card
from app.eventbrite_search import search_eventbrite_events
from app.source_overrides import parser_for_source
from app.thoravej_search import search_thoravej29_events

logger = logging.getLogger(__name__)
# Keep the legacy key so the existing weekly clock survives this migration from
# OpenAI web_search to Brave Search.
AI_DISCOVERY_STATE_KEY = "openai_web_discovery"


def matches(user: User, event: Event) -> bool:
    """Return whether an event is relevant to the user's location.

    Interests are preferences, not a hard filter. Online events are allowed for
    every user regardless of city. In-person events stay scoped to the user's city.
    """
    event_city = (event.city or "").casefold().strip()
    venue = (event.venue_name or "").casefold()
    description = (event.description or "").casefold()
    if event_city == "online" or "online" in venue or "online" in description:
        return True

    if user.city and event.city:
        user_city = user.city.casefold().strip()
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


def _new_event(source: Source, candidate) -> Event:
    return Event(
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


def persist_detected_events_for_user(user_id: int, source_id: int, candidates) -> int:
    """Persist events already detected while a user adds a source.

    This avoids waiting for the periodic source refresh after the bot has already
    fetched and parsed the page successfully.
    """
    created = 0
    with SessionLocal() as db:
        user = db.get(User, user_id)
        source = db.get(Source, source_id)
        if not user or not source:
            return 0

        for candidate in candidates:
            event = db.scalar(select(Event).where(Event.dedup_key == candidate.dedup_key))
            if event:
                _refresh_existing_event(event, candidate)
            else:
                event = _new_event(source, candidate)
                db.add(event)
                db.flush()
                created += 1

            if not matches(user, event):
                continue

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

        db.commit()
    return created


def _hide_stale_discovered_items(db, source: Source, candidate_keys: set[str]) -> None:
    host = urlparse(source.url).netloc.casefold().removeprefix("www.")
    if not (
        host.endswith("dagensbyggeri.dk")
        or host.endswith("dac.dk")
        or host.endswith("kglakademi.dk")
        or host.endswith("royaldanishacademy.com")
        or host.endswith("bloxhub.org")
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


def _host(source: Source) -> str:
    return urlparse(source.url).netloc.casefold().removeprefix("www.")


def _ai_discovery_family(source: Source) -> str | None:
    host = _host(source)
    if "eventbrite." in host:
        return "eventbrite"
    if host == "dac.dk" or host.endswith(".dac.dk"):
        return "dac"
    if (
        host == "kglakademi.dk"
        or host.endswith(".kglakademi.dk")
        or host == "royaldanishacademy.com"
        or host.endswith(".royaldanishacademy.com")
    ):
        return "academy"
    if host == "thoravej29.dk" or host.endswith(".thoravej29.dk"):
        return "thoravej29"
    return None


async def _routine_candidates_for_source(source: Source):
    """Cheap/deterministic discovery used by the frequent scrape job.

    Eventbrite and Thoravej 29 depend on the separate weekly Brave discovery.
    DAC and the Academy still use their deterministic parsers frequently; Brave
    only complements them during the weekly discovery pass.
    """
    host = _host(source)

    if "eventbrite." in host:
        return []
    if host == "thoravej29.dk" or host.endswith(".thoravej29.dk"):
        return []
    if host == "bloxhub.org" or host.endswith(".bloxhub.org"):
        return await BloxhubSource().fetch(source.url)

    parser = parser_for_source(source.url, source.source_type)
    return await parser.fetch(source.url)


async def _ai_candidates_for_source(source: Source):
    family = _ai_discovery_family(source)
    if family == "eventbrite":
        return await search_eventbrite_events(source.city or "Copenhagen")
    if family == "dac":
        return await search_dac_talks()
    if family == "academy":
        return await search_royal_danish_academy_events()
    if family == "thoravej29":
        return await search_thoravej29_events()
    return []


def _recipients_for_source(db, source: Source):
    if source.source_type == "curated":
        return db.scalars(select(User)).all()
    return db.scalars(
        select(User)
        .join(UserSource, UserSource.user_id == User.id)
        .where(UserSource.source_id == source.id, UserSource.enabled.is_(True))
    ).all()


async def _persist_candidates(db, source: Source, candidates, bot=None) -> int:
    created = 0
    recipients = _recipients_for_source(db, source)

    for candidate in candidates:
        event = db.scalar(select(Event).where(Event.dedup_key == candidate.dedup_key))
        is_new = event is None
        if event:
            _refresh_existing_event(event, candidate)
        else:
            event = _new_event(source, candidate)
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

            user_event = UserEvent(
                user_id=user.id,
                event_id=event.id,
                status=UserEventStatus.discovered.value,
            )
            db.add(user_event)
            db.flush()

            if bot and is_new:
                await bot.send_message(
                    user.telegram_user_id,
                    event_card(event, kicker="New for you"),
                    parse_mode="HTML",
                    reply_markup=_event_actions(user_event.id, event.registration_url),
                )

    return created


def _event_actions(user_event_id: int, registration_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Register", url=registration_url)],
        [
            InlineKeyboardButton("I registered", callback_data=f"reg:{user_event_id}"),
            InlineKeyboardButton("Not for me", callback_data=f"ignore:{user_event_id}"),
        ],
    ])


def _normalized_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def ai_discovery_due(last_run_at: datetime | None, *, now: datetime | None = None) -> bool:
    if last_run_at is None:
        return True
    current = _normalized_utc(now or datetime.now(timezone.utc))
    last = _normalized_utc(last_run_at)
    return current - last >= timedelta(days=max(1, settings.ai_discovery_interval_days))


async def refresh_curated_sources(bot=None) -> int:
    """Frequent low-cost refresh using only deterministic parsers."""
    created = 0
    with SessionLocal() as db:
        sources = db.scalars(select(Source).where(Source.active.is_(True))).all()
        for source in sources:
            try:
                candidates = await _routine_candidates_for_source(source)
            except Exception:
                logger.exception("Failed to refresh source %s (%s)", source.name, source.url)
                continue

            if not candidates:
                continue

            candidate_keys = {candidate.dedup_key for candidate in candidates}
            _hide_stale_discovered_items(db, source, candidate_keys)
            created += await _persist_candidates(db, source, candidates, bot=bot)

        db.commit()
    return created


async def refresh_ai_discovery(bot=None, *, force: bool = False) -> int:
    """Run Brave-first discovery no more than once per configured interval.

    Brave performs the web search. Result pages are fetched directly and parsed
    for Schema.org Event data locally. OpenAI is only a fallback to structure a
    small evidence set when pages do not expose structured event data; it never
    receives the web_search tool.

    The timestamp is persisted in PostgreSQL so Render restarts do not reset the
    weekly clock. Multiple source rows in one discovery family are collapsed to
    one discovery run.
    """
    if not settings.brave_search_api_key:
        logger.info("BRAVE_SEARCH_API_KEY is missing; weekly Brave discovery is disabled")
        return 0

    now = datetime.now(timezone.utc)
    created = 0
    with SessionLocal() as db:
        state = db.get(ServiceRunState, AI_DISCOVERY_STATE_KEY)
        if not force and state and not ai_discovery_due(state.last_run_at, now=now):
            logger.info("Weekly web discovery is not due yet; last run: %s", state.last_run_at)
            return 0

        sources = db.scalars(select(Source).where(Source.active.is_(True))).all()
        seen_families: set[str] = set()
        attempted = 0

        for source in sources:
            family = _ai_discovery_family(source)
            if not family or family in seen_families:
                continue
            seen_families.add(family)
            attempted += 1

            try:
                candidates = await _ai_candidates_for_source(source)
            except Exception:
                logger.exception("Brave discovery failed for %s (%s)", source.name, source.url)
                continue

            if candidates:
                created += await _persist_candidates(db, source, candidates, bot=bot)

        if state is None:
            state = ServiceRunState(key=AI_DISCOVERY_STATE_KEY, last_run_at=now)
            db.add(state)
        else:
            state.last_run_at = now
        db.commit()

    logger.info(
        "Weekly Brave discovery finished: %s source families checked, %s new events",
        attempted,
        created,
    )
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
                    event_card(event, note=item.note, kicker="Reminder"),
                    parse_mode="HTML",
                )
                item.reminder_sent_at = now
                sent += 1
        db.commit()
    return sent

