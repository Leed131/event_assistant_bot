import json
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

from dateutil import parser as date_parser
from openai import AsyncOpenAI

from app.config import settings
from app.sources import EventCandidate


logger = logging.getLogger(__name__)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


async def search_eventbrite_events(city: str = "Copenhagen") -> list[EventCandidate]:
    """Discover useful Eventbrite events without scraping Eventbrite directly.

    Eventbrite search pages are broad and noisy. We use web search as the
    discovery layer, restrict results to direct Eventbrite event pages, and ask
    for professional/cultural/educational events in Copenhagen plus worthwhile
    online lectures that are open to participants in Denmark/Europe.
    """
    if not settings.openai_api_key:
        logger.warning("OPENAI_API_KEY is missing; Eventbrite discovery is disabled")
        return []

    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    prompt = f"""
Today is {today}. Find UPCOMING Eventbrite events relevant to an adult professional
or creative person based in {city}, Denmark.

Search only public Eventbrite event pages on eventbrite.com and Eventbrite country domains.
Include two groups:
1. In-person or hybrid events in {city} / Greater Copenhagen.
2. Online events that can reasonably be attended from Denmark/Europe.

Prioritize useful events such as:
- architecture, urbanism, built environment, construction, design
- AI, technology, innovation, startups, entrepreneurship
- sustainability, climate, circular economy, materials
- lectures, talks, panels, conferences, seminars, workshops
- exhibitions, openings, art/design/creative-industry events
- leadership, professional networking, research, women in tech/AI/design

Avoid obvious low-value noise unless it is clearly educational/professional:
- nightlife, clubbing, raves, pub crawls
- speed dating / singles events
- generic parties and concerts
- generic fitness/yoga/wellness classes
- children's entertainment

Accuracy rules:
- Include ONLY future events with a concrete published date.
- Do not invent title, date, time, venue, price, format or URL.
- Prefer the direct Eventbrite event URL, not a category/search/home page.
- If exact time is missing, use 12:00 and set time_confirmed=false.
- Return at most 40 events.

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
      "format": "in_person|online|hybrid",
      "time_confirmed": true
    }}
  ]
}}
"""

    try:
        response = await client.responses.create(
            model="gpt-5.6-luna",
            tools=[{"type": "web_search"}],
            input=prompt,
            max_output_tokens=7000,
        )
        payload = json.loads(_strip_code_fence(response.output_text))
    except Exception:
        logger.exception("Eventbrite web discovery failed")
        return []

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
                source_name="Eventbrite Discovery",
                is_free=item.get("is_free") if isinstance(item.get("is_free"), bool) else None,
                external_id=dedup,
            )
        )

    found.sort(key=lambda event: event.start_datetime)
    return found
