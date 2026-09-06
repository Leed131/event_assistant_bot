import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

from dateutil import parser as date_parser

from app.brave_discovery import discover_event_payload
from app.config import settings
from app.sources import EventCandidate


logger = logging.getLogger(__name__)


async def search_dac_talks() -> list[EventCandidate]:
    """Find upcoming DAC talks using Brave Search plus local page parsing."""
    if not settings.brave_search_api_key:
        logger.warning("BRAVE_SEARCH_API_KEY is missing; DAC web discovery is disabled")
        return []

    now = datetime.now(timezone.utc)
    year = now.year
    prompt = f"""
Today is {now.date().isoformat()}. From the supplied Brave Search evidence, identify
UPCOMING events at the Danish Architecture Center (DAC) in Copenhagen.

Prioritize:
- MorgenDAC and AfterDAC
- International Talk / International Talks
- architecture and urbanism talks, lectures, debates and conversations
- Film & Talk
- professional seminars, symposiums or conferences at DAC

Exclude guided/gallery tours, kids and family activities, LEGO nights, generic
cafe/dinner offers, Free Fridays and past events.

Use only official dac.dk evidence. Do not invent dates, times, speakers or URLs.
Return ONLY valid JSON:
{{
  "events": [
    {{
      "title": "string",
      "start_datetime": "ISO-8601 datetime with timezone",
      "end_datetime": null,
      "venue_name": "Danish Architecture Center (DAC)",
      "address": "Bryghuspladsen 10, 1473 Copenhagen K",
      "registration_url": "https://dac.dk/...",
      "source_url": "https://dac.dk/...",
      "description": "short factual summary",
      "is_free": false
    }}
  ]
}}
"""

    payload = await discover_event_payload(
        queries=[
            f'site:dac.dk ({year} OR {year + 1}) (MorgenDAC OR AfterDAC OR "International Talk")',
            f'site:dac.dk ({year} OR {year + 1}) (architecture OR urbanism) (talk OR lecture OR symposium OR seminar)',
        ],
        extraction_prompt=prompt,
        allowed_hosts=("dac.dk",),
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

        parsed = urlparse(source_url)
        host = parsed.netloc.casefold().removeprefix("www.")
        if parsed.scheme not in {"http", "https"} or not host.endswith("dac.dk"):
            continue

        lower = f"{title} {item.get('description') or ''}".casefold()
        if any(
            bad in lower
            for bad in (
                "guided tour",
                "gallery tour",
                "kids and family",
                "lego® night",
                "lego night",
                "dinner experience",
                "free fridays",
            )
        ):
            continue

        try:
            start = date_parser.isoparse(str(item.get("start_datetime") or ""))
        except (TypeError, ValueError):
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if start <= now:
            continue

        direct_url = registration_url.split("#", 1)[0]
        dedup = direct_url.split("?", 1)[0].rstrip("/") + f"|{start.date().isoformat()}"
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
                venue_name="Danish Architecture Center (DAC)",
                address="Bryghuspladsen 10, 1473 Copenhagen K",
                registration_url=registration_url,
                source_url=source_url,
                source_name="Danish Architecture Center · Brave discovery",
                is_free=item.get("is_free") if isinstance(item.get("is_free"), bool) else None,
                external_id=f"{source_url}|{start.date().isoformat()}",
            )
        )

    found.sort(key=lambda event: event.start_datetime)
    return found
