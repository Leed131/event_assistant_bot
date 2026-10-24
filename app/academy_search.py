import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

from dateutil import parser as date_parser

from app.brave_discovery import discover_event_payload
from app.config import settings
from app.sources import EventCandidate

logger = logging.getLogger(__name__)


async def search_royal_danish_academy_events() -> list[EventCandidate]:
    """Use Brave Search as the discovery layer for Academy public events.

    Brave finds candidate pages. Structured event data is parsed locally when
    available; OpenAI is used only as a compact fallback to structure the small
    evidence set and never receives the web_search tool.
    """
    if not settings.brave_search_api_key:
        logger.warning("BRAVE_SEARCH_API_KEY is missing; Academy web discovery is disabled")
        return []

    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    year = now.year

    prompt = f"""
Today is {today}. From the supplied Brave Search evidence, identify UPCOMING public
events in Copenhagen connected to The Royal Danish Academy — Architecture, Design,
Conservation / Det Kongelige Akademi.

Include when publicly accessible:
- International Lecture Series, public lectures, talks and debates
- exhibitions and exhibition openings
- public symposia, conferences, seminars and architecture/design events

Exclude ordinary student-only courses, internal meetings, exams, past events and
items outside Copenhagen unless the evidence explicitly says the event is at the
Royal Danish Academy in Copenhagen.

Return ONLY valid JSON in this exact shape:
{{
  "events": [
    {{
      "title": "string",
      "start_datetime": "ISO-8601 datetime with timezone",
      "end_datetime": null,
      "venue_name": "string or null",
      "address": "string or null",
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
            f'site:kglakademi.dk ({year} OR {year + 1}) (lecture OR event OR exhibition OR symposium)',
            f'site:royaldanishacademy.com ({year} OR {year + 1}) (lecture OR event OR exhibition OR symposium)',
            f'"Royal Danish Academy" Copenhagen ({year} OR {year + 1}) (lecture OR exhibition OR symposium)',
        ],
        extraction_prompt=prompt,
        max_results=12,
        max_pages=8,
        max_output_tokens=3500,
    )

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
        try:
            start = date_parser.isoparse(str(item.get("start_datetime") or ""))
        except (TypeError, ValueError):
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if start <= now:
            continue

        dedup = f"{title.casefold()}|{start.date().isoformat()}"
        if dedup in seen:
            continue
        seen.add(dedup)

        end = None
        if item.get("end_datetime"):
            try:
                end = date_parser.isoparse(str(item["end_datetime"]))
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                end = None

        found.append(
            EventCandidate(
                title=title,
                description=str(item.get("description") or "").strip() or None,
                start_datetime=start,
                end_datetime=end,
                city="Copenhagen",
                venue_name=str(item.get("venue_name") or "The Royal Danish Academy").strip(),
                address=str(item.get("address") or "").strip() or None,
                registration_url=registration_url,
                source_url=source_url,
                source_name="The Royal Danish Academy · Brave discovery",
                is_free=item.get("is_free") if isinstance(item.get("is_free"), bool) else None,
                external_id=source_url,
            )
        )

    found.sort(key=lambda event: event.start_datetime)
    return found
