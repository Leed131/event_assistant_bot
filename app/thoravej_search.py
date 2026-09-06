import json
import logging
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser
from openai import AsyncOpenAI

from app.sources import EventCandidate


logger = logging.getLogger(__name__)
COPENHAGEN_TZ = ZoneInfo("Europe/Copenhagen")
THORAVEJ_EVENTS_URL = "https://www.thoravej29.dk/da/events"


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

        # Keep currently-running multi-day programmes, but drop completed items.
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
                source_name="Thoravej 29 · web discovery",
                is_free=item.get("is_free") if isinstance(item.get("is_free"), bool) else None,
                external_id=source_url,
            )
        )

    found.sort(key=lambda event: event.start_datetime)
    return found


async def search_thoravej29_events() -> list[EventCandidate]:
    """Discover Thoravej 29 events when the calendar is rendered client-side.

    The /events page initially returns only a loading placeholder, so ordinary
    HTML/JSON-LD parsers cannot see the event cards. Use web search as a
    discovery layer and require concrete public-source evidence for each item.
    """
    # Import lazily so source parser unit tests do not require DATABASE_URL.
    from app.config import settings

    if not settings.openai_api_key:
        return []

    now = datetime.now(COPENHAGEN_TZ)
    today = now.date().isoformat()
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    prompt = f"""
Today is {today}. Find all UPCOMING or CURRENTLY RUNNING public events shown by
Thoravej 29 in Copenhagen. The official calendar is:
{THORAVEJ_EVENTS_URL}

The calendar is client-side rendered, so search broadly and verify events using:
- official thoravej29.dk event/detail pages
- official Thoravej 29 social posts
- official member/organizer pages when they explicitly say the event is at Thoravej 29
- registration pages linked to the event

Important: include multi-day programmes and recurring series that are still active.
The current calendar can contain items such as Forandringskrafts Spireforløb,
Åben AkademiMorgen, and Bogklubben for Mulige Fremtider: Teknologi; use these
names only as search hints and include them only when a public source confirms
their date(s).

Critical accuracy rules:
- Do not invent a title, date, time, URL, organizer, price, or description.
- Include an event only when a public source explicitly confirms a concrete date.
- Prefer the direct thoravej29.dk event URL when one exists; otherwise use the
  most direct organizer/registration page that proves the event is at Thoravej 29.
- Preserve published start/end dates for multi-day programmes.
- If a start time is not published, use 12:00 and set time_confirmed=false.
- Return no more than 30 events.

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

    try:
        response = await client.responses.create(
            model="gpt-5.6-luna",
            tools=[{"type": "web_search"}],
            input=prompt,
            max_output_tokens=6500,
        )
        payload = json.loads(_strip_code_fence(response.output_text))
    except Exception:
        logger.exception("Thoravej 29 web search failed")
        return []

    return _events_from_payload(payload, now=now)
