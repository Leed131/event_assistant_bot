import logging
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser

from app.brave_discovery import discover_event_payload
from app.sources import EventCandidate


logger = logging.getLogger(__name__)
COPENHAGEN_TZ = ZoneInfo("Europe/Copenhagen")
THORAVEJ_EVENTS_URL = "https://www.thoravej29.dk/da/events"


def _events_from_payload(
    payload: dict,
    *,
    now: datetime | None = None,
) -> list[EventCandidate]:
    now = now or datetime.now(COPENHAGEN_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=COPENHAGEN_TZ)

    found: list[EventCandidate] = []
    seen: set[str] = set()

    for item in payload.get("events", []):
        if not isinstance(item, dict):
            continue

        title = str(item.get("title") or "").strip()
        source_url = str(item.get("source_url") or item.get("registration_url") or "").strip()
        registration_url = str(item.get("registration_url") or source_url).strip()
        if not title or not source_url:
            continue
        if urlparse(source_url).scheme not in {"http", "https"}:
            continue
        if urlparse(registration_url).scheme not in {"http", "https"}:
            registration_url = source_url

        try:
            start = date_parser.isoparse(str(item.get("start_datetime") or ""))
        except (TypeError, ValueError):
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=COPENHAGEN_TZ)

        end = None
        if item.get("end_datetime"):
            try:
                end = date_parser.isoparse(str(item["end_datetime"]))
                if end.tzinfo is None:
                    end = end.replace(tzinfo=COPENHAGEN_TZ)
            except (TypeError, ValueError):
                end = None

        if end is not None:
            if end < now:
                continue
        elif start < now:
            continue

        dedup = f"{title.casefold()}|{start.date().isoformat()}"
        if dedup in seen:
            continue
        seen.add(dedup)

        found.append(
            EventCandidate(
                title=title,
                description=str(item.get("description") or "").strip() or None,
                start_datetime=start,
                end_datetime=end,
                city="Copenhagen",
                venue_name="Thoravej 29",
                address="Thoravej 29, 2400 Copenhagen NV",
                registration_url=registration_url,
                source_url=source_url,
                source_name="Thoravej 29 · Brave discovery",
                is_free=item.get("is_free") if isinstance(item.get("is_free"), bool) else None,
                external_id=source_url,
            )
        )

    found.sort(key=lambda event: event.start_datetime)
    return found


async def search_thoravej29_events() -> list[EventCandidate]:
    """Discover Thoravej 29 events with Brave Search and local page fetching."""
    from app.config import settings

    if not settings.brave_search_api_key:
        logger.warning("BRAVE_SEARCH_API_KEY is missing; Thoravej 29 discovery is disabled")
        return []

    now = datetime.now(COPENHAGEN_TZ)
    year = now.year
    prompt = f"""
Today is {now.date().isoformat()}. From the supplied Brave Search evidence, identify
all UPCOMING or CURRENTLY RUNNING public events at Thoravej 29 in Copenhagen.

The official calendar is {THORAVEJ_EVENTS_URL}. Include multi-day programmes and
recurring series that are still active. Evidence may come from official Thoravej 29
pages, organizer pages or registration pages that explicitly confirm the event is
at Thoravej 29.

Do not invent title, date, time, URL, organizer, price or description. Include an
event only when the supplied evidence explicitly confirms a concrete date. Prefer
the direct Thoravej 29 event URL when available.

Return ONLY valid JSON in this exact shape:
{{
  "events": [
    {{
      "title": "string",
      "start_datetime": "ISO-8601 datetime with Europe/Copenhagen offset",
      "end_datetime": null,
      "registration_url": "https://...",
      "source_url": "https://...",
      "description": "short factual summary",
      "is_free": true,
      "time_confirmed": true
    }}
  ]
}}
"""

    payload = await discover_event_payload(
        queries=[
            f'site:thoravej29.dk ({year} OR {year + 1}) (event OR arrangement OR talk OR workshop OR exhibition)',
            f'"Thoravej 29" Copenhagen ({year} OR {year + 1}) (event OR talk OR workshop OR exhibition)',
        ],
        extraction_prompt=prompt,
        max_results=12,
        max_pages=8,
        max_output_tokens=3500,
    )
    return _events_from_payload(payload, now=now)
