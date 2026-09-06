import re
from datetime import datetime
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup, Tag

from app.sources import BaseEventSource, EventCandidate, USER_AGENT


COPENHAGEN_TZ = ZoneInfo("Europe/Copenhagen")
LISTING_URL = "https://cfu.kp.dk/find-kurser/"

MONTHS_DA = {
    "januar": 1,
    "februar": 2,
    "marts": 3,
    "april": 4,
    "maj": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "december": 12,
}

NUMERIC_DATE_RE = re.compile(r"\b(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>20\d{2})\b")
WORD_DATE_RE = re.compile(
    r"\b(?P<day>\d{1,2})\.?\s+(?P<month>januar|februar|marts|april|maj|juni|juli|august|september|oktober|november|december)\s+(?P<year>20\d{2})\b",
    re.IGNORECASE,
)
TIME_RANGE_RE = re.compile(
    r"\b(?:kl\.?\s*)?(?P<start_h>(?:[01]?\d|2[0-3]))[.:](?P<start_m>[0-5]\d)\s*[-–]\s*"
    r"(?P<end_h>(?:[01]?\d|2[0-3]))[.:](?P<end_m>[0-5]\d)\b",
    re.IGNORECASE,
)


def _date_matches(text: str) -> list[datetime]:
    matches: list[tuple[int, datetime]] = []
    for match in NUMERIC_DATE_RE.finditer(text):
        try:
            value = datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
                tzinfo=COPENHAGEN_TZ,
            )
        except ValueError:
            continue
        matches.append((match.start(), value))

    for match in WORD_DATE_RE.finditer(text):
        month = MONTHS_DA.get(match.group("month").casefold())
        if not month:
            continue
        try:
            value = datetime(
                int(match.group("year")),
                month,
                int(match.group("day")),
                tzinfo=COPENHAGEN_TZ,
            )
        except ValueError:
            continue
        matches.append((match.start(), value))

    matches.sort(key=lambda item: item[0])
    return [value for _, value in matches]


def parse_cfu_datetimes(text: str) -> tuple[datetime | None, datetime | None]:
    dates = _date_matches(text)
    if not dates:
        return None, None

    start = dates[0]
    end = dates[1] if len(dates) > 1 else None
    time_match = TIME_RANGE_RE.search(text)
    if time_match:
        start = start.replace(
            hour=int(time_match.group("start_h")),
            minute=int(time_match.group("start_m")),
        )
        if end is None:
            end = start.replace(
                hour=int(time_match.group("end_h")),
                minute=int(time_match.group("end_m")),
            )
        else:
            end = end.replace(
                hour=int(time_match.group("end_h")),
                minute=int(time_match.group("end_m")),
            )
    else:
        start = start.replace(hour=12, minute=0)
        if end is not None:
            end = end.replace(hour=12, minute=0)

    return start, end


class CFUSource(BaseEventSource):
    name = "CFU · Center for Undervisningsmidler"
    listing_url = LISTING_URL

    @staticmethod
    def _card_for_heading(heading: Tag) -> Tag:
        parent = heading.parent
        for _ in range(6):
            if not isinstance(parent, Tag):
                break
            text = " ".join(parent.stripped_strings)
            if "Dato" in text and (_date_matches(text)) and len(text) <= 5000:
                return parent
            parent = parent.parent
        return heading

    async def fetch(self, url: str) -> list[EventCandidate]:
        now = datetime.now(COPENHAGEN_TZ)
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = await client.get(self.listing_url)
            response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        found: list[EventCandidate] = []
        seen: set[str] = set()

        for heading in soup.find_all(["h2", "h3", "h4"]):
            title = " ".join(heading.stripped_strings).strip()
            if not title or title.casefold() in {"kurser og konferencer", "dato", "sted"}:
                continue

            card = self._card_for_heading(heading)
            text = " ".join(card.stripped_strings).strip()
            start, end = parse_cfu_datetimes(text)
            if start is None:
                continue
            if end is not None:
                if end < now:
                    continue
            elif start < now:
                continue

            anchor = heading.find("a", href=True)
            if anchor is None:
                anchor = card.find("a", href=True)
            detail_url = urljoin(str(response.url), anchor["href"]) if anchor else self.listing_url
            dedup = f"{title.casefold()}|{start.isoformat()}"
            if dedup in seen:
                continue
            seen.add(dedup)

            lower = text.casefold()
            if "bornholm" in lower:
                city = "Bornholm"
                venue = "Campus Bornholm"
                address = "Minervavej 1, Rønne"
            elif "københavn" in lower or "campus carlsberg" in lower:
                city = "Copenhagen"
                venue = "Campus Carlsberg"
                address = "Humletorvet 3, Copenhagen"
            else:
                city = "Denmark"
                venue = None
                address = None

            found.append(
                EventCandidate(
                    title=title[:400],
                    description=text[:900],
                    start_datetime=start,
                    end_datetime=end,
                    city=city,
                    venue_name=venue,
                    address=address,
                    registration_url=detail_url,
                    source_url=detail_url,
                    source_name=self.name,
                    external_id=f"cfu:{dedup}",
                )
            )

        found.sort(key=lambda event: event.start_datetime)
        return found
