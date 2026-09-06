import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

from dateutil import parser as date_parser

from app.brave_discovery import discover_event_payload
from app.config import settings
from app.sources import EventCandidate


logger = logging.getLogger(__name__)


async def search_eventbrite_events(city: str = "Copenhagen") -> list[EventCandidate]:
    """Discover useful Eventbrite events with Brave Search.

    Brave finds candidate Eventbrite pages. We parse Schema.org Event data locally
    where possible and use one compact OpenAI extraction only when structured data
    is absent. OpenAI never receives the web_search tool here.
    """
    if not settings.brave_search_api_key:
        logger.warning("BRAVE_SEARCH_API_KEY is missing; Eventbrite discovery is disabled")
        return []

    now = datetime.now(timezone.utc)
    year = now.year
    prompt = f"""
Today is {now.date().isoformat()}. From the supplied Brave Search evidence, identify
UPCOMING Eventbrite events relevant to an adult professional or creative person
based in {city}, Denmark.

Include:
1. In-person or hybrid events in {city} / Greater Copenhagen.
2. Online events that can reasonably be attended from Denmark/Europe.

Prioritize architecture, urbanism, built environment, construction, design, AI,
technology, innovation, startups, sustainability, climate, circular economy,
materials, talks, panels, conferences, seminars, workshops, exhibitions, creative
industries, leadership, research and useful professional networking.

Exclude nightlife, raves, pub crawls, speed dating, generic parties/concerts,
generic fitness/wellness classes and children's entertainment.

Include only future events with a concrete published date. Do not invent title,
date, time, venue, price, format or URL. Return ONLY valid JSON:
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
      "format": "in_person|online|hybrid",
      "time_confirmed": true
    }}
  ]
}}
"""

    payload = await discover_event_payload(
        queries=[
            f'site:eventbrite.com {city} ({year} OR {year + 1}) (architecture OR design OR urbanism OR sustainability)',
            f'site:eventbrite.com {city} ({year} OR {year + 1}) (AI OR technology OR innovation OR startup OR conference)',
            f'site:eventbrite.com online ({year} OR {year + 1}) (architecture OR design OR AI OR sustainability) Europe',
        ],
        extraction_prompt=prompt,
        max_results=15,
        max_pages=10,
        max_output_tokens=4000,
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

        host = urlparse(source_url).netloc.casefold()
        if "eventbrite." not in host:
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

        event_format = str(item.get("format") or "in_person").strip().casefold()
        city_value = "Online" if event_format == "online" else city
        venue = str(item.get("venue_name") or "").strip() or None
        if event_format == "online" and not venue:
            venue = "Online · Eventbrite"
        elif not venue:
            venue = "Eventbrite"

        dedup = registration_url.split("?", 1)[0].rstrip("/")
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
                city=city_value,
                venue_name=venue,
                address=str(item.get("address") or "").strip() or None,
                registration_url=registration_url,
                source_url=source_url,
                source_name="Eventbrite · Brave discovery",
                is_free=item.get("is_free") if isinstance(item.get("is_free"), bool) else None,
                external_id=dedup,
            )
        )

    found.sort(key=lambda event: event.start_datetime)
    return found
