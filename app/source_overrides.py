import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.sources import (
    DATE_RE,
    MONTHS_DA,
    USER_AGENT,
    BaseEventSource,
    EventCandidate,
    parser_for_url,
)


DATE_RANGE_RE = re.compile(
    r"\b(?P<start_day>\d{1,2})\.?\s*[-–]\s*(?P<end_day>\d{1,2})\.?\s+"
    r"(?P<month>jan(?:uar)?|feb(?:ruar)?|mar(?:ts)?|apr(?:il)?|maj|jun(?:i)?|jul(?:i)?|aug(?:ust)?|sep(?:tember)?|okt(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"(?:\s+(?P<year>20\d{2}))?\b",
    re.IGNORECASE,
)


class DagensByggeriAccurateSource(BaseEventSource):
    """Parse event dates from Dagens Byggeri editorial event articles.

    These pages contain both an article publication date and a separate event
    date, sometimes written as a date range such as "16.-17. september".
    The event year is anchored to the article publication year when omitted.
    """

    name = "Dagens Byggeri"
    listing_url = "https://dagensbyggeri.dk/arrangementer/"

    @staticmethod
    def _month_number(name: str) -> int | None:
        return MONTHS_DA.get(name.lower().rstrip("."))

    def _publication_date(self, page: BeautifulSoup, text: str) -> datetime | None:
        meta = page.find("meta", attrs={"property": "article:published_time"})
        if meta and meta.get("content"):
            try:
                value = datetime.fromisoformat(meta["content"].replace("Z", "+00:00"))
                return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        for time_tag in page.find_all("time", attrs={"datetime": True}):
            try:
                value = datetime.fromisoformat(time_tag["datetime"].replace("Z", "+00:00"))
                return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            except ValueError:
                continue

        for match in DATE_RE.finditer(text):
            if not match.group("year"):
                continue
            month = self._month_number(match.group("month"))
            if not month:
                continue
            try:
                return datetime(
                    int(match.group("year")),
                    month,
                    int(match.group("day")),
                    0,
                    0,
                    tzinfo=timezone.utc,
                )
            except ValueError:
                continue
        return None

    def _actual_event_dates(
        self,
        text: str,
        published: datetime,
    ) -> tuple[datetime | None, datetime | None]:
        """Return the most plausible event start/end after publication.

        Date ranges are parsed before single dates, so "16.-17. september"
        becomes a start on 16 September and an end on 17 September rather than
        being misread as only 17 September.
        """
        publication_day = published.date()
        range_candidates: list[tuple[datetime, datetime]] = []

        for match in DATE_RANGE_RE.finditer(text):
            month = self._month_number(match.group("month"))
            if not month:
                continue
            year = int(match.group("year")) if match.group("year") else published.year
            try:
                start = datetime(
                    year,
                    month,
                    int(match.group("start_day")),
                    12,
                    0,
                    tzinfo=timezone.utc,
                )
                end = datetime(
                    year,
                    month,
                    int(match.group("end_day")),
                    12,
                    0,
                    tzinfo=timezone.utc,
                )
            except ValueError:
                continue
            if start.date() <= publication_day:
                continue
            if end < start or (start - published).days > 370:
                continue
            range_candidates.append((start, end))

        if range_candidates:
            return min(range_candidates, key=lambda item: item[0])

        candidates: list[datetime] = []
        for match in DATE_RE.finditer(text):
            month = self._month_number(match.group("month"))
            if not month:
                continue
            year = int(match.group("year")) if match.group("year") else published.year
            try:
                candidate = datetime(
                    year,
                    month,
                    int(match.group("day")),
                    12,
                    0,
                    tzinfo=timezone.utc,
                )
            except ValueError:
                continue

            if candidate.date() <= publication_day:
                continue
            if (candidate - published).days > 370:
                continue
            candidates.append(candidate)

        return (min(candidates), None) if candidates else (None, None)

    async def fetch(self, url: str) -> list[EventCandidate]:
        now = datetime.now(timezone.utc)
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=25,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = await client.get(self.listing_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            links: list[str] = []
            for anchor in soup.find_all("a", href=True):
                href = urljoin(self.listing_url, anchor["href"]).split("#", 1)[0]
                parsed = urlparse(href)
                if not parsed.netloc.endswith("dagensbyggeri.dk"):
                    continue
                if "/arrangementer/" not in parsed.path or parsed.path.rstrip("/") == "/arrangementer":
                    continue
                if href not in links:
                    links.append(href)

            found: list[EventCandidate] = []
            for detail_url in links[:40]:
                try:
                    detail = await client.get(detail_url)
                    detail.raise_for_status()
                except httpx.HTTPError:
                    continue

                page = BeautifulSoup(detail.text, "html.parser")
                h1 = page.find("h1")
                if not h1:
                    continue
                title = h1.get_text(" ", strip=True)
                main = page.find("main") or page.find("article") or page
                text = main.get_text(" ", strip=True)

                published = self._publication_date(page, text)
                if not published:
                    continue
                start, end = self._actual_event_dates(text, published)
                if not start or start < now:
                    continue

                description_tag = page.find("meta", attrs={"name": "description"})
                image_tag = page.find("meta", attrs={"property": "og:image"})
                description = description_tag.get("content") if description_tag else None
                image_url = image_tag.get("content") if image_tag else None

                lower = text.casefold()
                city = "Copenhagen" if ("københavn" in lower or "copenhagen" in lower) else "Denmark"

                found.append(
                    EventCandidate(
                        title=title,
                        description=description,
                        start_datetime=start,
                        end_datetime=end,
                        city=city,
                        registration_url=detail_url,
                        source_url=detail_url,
                        source_name=self.name,
                        image_url=image_url,
                        external_id=detail_url,
                    )
                )

        found.sort(key=lambda item: item.start_datetime)
        return found


def parser_for_source(url: str, source_type: str | None = None) -> BaseEventSource:
    host = urlparse(url).netloc.casefold().removeprefix("www.")
    if host.endswith("dagensbyggeri.dk"):
        return DagensByggeriAccurateSource()
    return parser_for_url(url, source_type)
