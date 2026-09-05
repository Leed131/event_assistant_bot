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


async def search_dac_talks() -> list[EventCandidate]:
    """Find upcoming DAC talks that can be missed by the static calendar parser.

    DAC's focus pages (MorgenDAC and AfterDAC) can be rendered differently from
    the general calendar. Web discovery complements the deterministic parser and
    focuses on the architecture talks the user actually wants.
    """
    if not settings.openai_api_key:
        return []

    now = datetime.now(timezone.utc)
    client = AsyncOpenAI(api_key=settings.openai_api_key)
    prompt = f"""
Today is {now.date().isoformat()}.
Find UPCOMING events at the Danish Architecture Center (DAC) in Copenhagen,
using ONLY official dac.dk pages as evidence.

Prioritize and include:
- MorgenDAC
- AfterDAC talks
- International Talk / International Talks
- architecture and urbanism talks, lectures, debates and conversations
- Film & Talk
- professional seminars, symposiums or conferences at DAC

Exclude:
- guided tours / gallery tours
- Kids and family activities
- LEGO nights or build/play workshops
- generic café/dinner admission offers
- Free Fridays / youth social evenings
- events that have already happened

Important:
- Find the direct dac.dk event page whenever possible.
- Include a result only when an official DAC page confirms a concrete future date.
- Do not invent dates, times, prices, speakers or URLs.
- Return at most 30 events.

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

    try:
        response = await client.responses.create(
            model="gpt-5.6-luna",
            tools=[{"type": "web_search"}],
            input=prompt,
            max_output_tokens=6000,
        )
        payload = json.loads(_strip_code_fence(response.output_text))
    except Exception:
        logger.exception("DAC web discovery failed")
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
                source_name="Danish Architecture Center · talks",
                is_free=item.get("is_free") if isinstance(item.get("is_free"), bool) else None,
                external_id=f"{source_url}|{start.date().isoformat()}",
            )
        )

    found.sort(key=lambda event: event.start_datetime)
    return found
