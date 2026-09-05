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
        lines = text.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


async def search_royal_danish_academy_events() -> list[EventCandidate]:
    """Use OpenAI web search as a discovery layer for Academy public events.

    The Academy often announces public lectures and exhibitions on LinkedIn,
    partner sites or registration platforms instead of a single machine-readable
    calendar. This search layer complements the deterministic parsers.
    """
    if not settings.openai_api_key:
        return []

    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    prompt = f"""
Today is {today}. Find UPCOMING public events in Copenhagen connected to
The Royal Danish Academy — Architecture, Design, Conservation / Det Kongelige Akademi
(kglakademi.dk, Royal Danish Academy), taking place after today.

Search broadly on the public web, including:
- official Royal Danish Academy pages and research portal
- official Royal Danish Academy LinkedIn pages and institute/showcase pages
- public registration pages
- partner/organizer pages that explicitly say the event takes place at the Royal Danish Academy
- public exhibition announcements

Include these types when open to the public:
- International Lecture Series, public lectures, talks, debates
- exhibitions, exhibition openings, graduation exhibitions
- public symposia, conferences, seminars and architecture/design events

Exclude:
- ordinary courses/classes restricted to enrolled students or PhD students
- internal meetings and exams
- events that already happened
- events outside Copenhagen unless the source explicitly says they take place at the Royal Danish Academy in Copenhagen

Critical accuracy rules:
- Do not invent an event, date, time, price, address or URL.
- Include an event only if a public web source explicitly confirms a concrete date.
- Prefer the direct event/registration/announcement URL, not a generic home page.
- If a time is not published, use 12:00 but set time_confirmed=false.
- Return no more than 20 events.

Return ONLY valid JSON in this exact shape:
{{
  "events": [
    {{
      "title": "string",
      "start_datetime": "ISO-8601 datetime with timezone, preferably Europe/Copenhagen offset",
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

    try:
        response = await client.responses.create(
            model="gpt-5.6-luna",
            tools=[{"type": "web_search"}],
            input=prompt,
            max_output_tokens=5000,
        )
        payload = json.loads(_strip_code_fence(response.output_text))
    except Exception:
        logger.exception("Royal Danish Academy web search failed")
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
                source_name="The Royal Danish Academy · web discovery",
                is_free=item.get("is_free") if isinstance(item.get("is_free"), bool) else None,
                external_id=source_url,
            )
        )

    found.sort(key=lambda event: event.start_datetime)
    return found
