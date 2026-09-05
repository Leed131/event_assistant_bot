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


class DagensByggeriAccurateSource(BaseEventSource):
    """Parse event dates from Dagens Byggeri articles without using publish dates.

    Pages under /arrangementer/ are editorial articles about events. The page
    contains both the article publication date and the actual event date. The
    old parser could accidentally treat an undated event mention as next year.
    Here an undated event date is anchored to the article's publication year.
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

        # Dagens Byggeri visibly prints publication dates such as
        # "4. aug. 2026 - kl. 09:40". Prefer an explicit-year date.
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

    def _actual_event_date(self, text: str, published: datetime) -> datetime | None:
        """Return the first plausible event date after article publication.

        If the article says only "28. august", the year is the publication
        year, not automatically the next future year. This prevents 2026 events
        from being fabricated as 2027 events after they have passed.
        """
        candidates: list[datetime] = []
        publication_day = published.date()

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

            # Skip the article's own publication date and unrelated historical
            # dates. An event promoted by the article should be after publishing.
            if candidate.date() <= publication_day:
                continue
            if (candidate - published).days > 370:
                continue
            candidates.append(candidate)

        return min(candidates) if candidates else None

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
                start = self._actual_event_date(text, published)
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
