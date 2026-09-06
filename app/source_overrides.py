import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup, Tag

from app.sources import (
    DATE_RE,
    MONTHS_DA,
    USER_AGENT,
    BaseEventSource,
    EventCandidate,
    parser_for_url,
)
from app.thoravej_search import search_thoravej29_events


DATE_RANGE_RE = re.compile(
    r"\b(?P<start_day>\d{1,2})\.?\s*[-–]\s*(?P<end_day>\d{1,2})\.?\s+"
    r"(?P<month>jan(?:uar)?|feb(?:ruar)?|mar(?:ts)?|apr(?:il)?|maj|jun(?:i)?|jul(?:i)?|aug(?:ust)?|sep(?:tember)?|okt(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"(?:\s+(?P<year>20\d{2}))?\b",
    re.IGNORECASE,
)
ACADEMY_DATE_RE = re.compile(r"\b(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>20\d{2})\b")
ACADEMY_TIME_RANGE_RE = re.compile(
    r"\b(?P<start_h>(?:[01]?\d|2[0-3]))[.:](?P<start_m>[0-5]\d)\s*[-–]\s*"
    r"(?P<end_h>(?:[01]?\d|2[0-3]))[.:](?P<end_m>[0-5]\d)\b"
)
ACADEMY_TIME_RE = re.compile(
    r"\b(?P<h>(?:[01]?\d|2[0-3]))[.:](?P<m>[0-5]\d)\b"
)
ACADEMY_TYPE_WORDS = {
    "event",
    "arrangement",
    "udstilling",
    "exhibition",
    "webinar",
    "ph.d.-forsvar",
    "ph.d.-defense",
    "phd defense",
    "forsvar",
    "lecture",
    "talk",
}
ACADEMY_HOSTS = ("kglakademi.dk", "royaldanishacademy.com")
COPENHAGEN_TZ = ZoneInfo("Europe/Copenhagen")


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


class RoyalDanishAcademyCalendarSource(BaseEventSource):
    """Parse the Academy's official public calendar.

    The official calendar contains exhibitions, events, webinars, PhD defenses,
    book launches and other public activities. We intentionally keep all future
    or currently-running calendar cards rather than filtering only for lectures.
    """

    name = "The Royal Danish Academy"
    calendar_urls = (
        "https://kglakademi.dk/da/kalender",
        "https://royaldanishacademy.com/en/calendar",
    )

    @staticmethod
    def _academy_host(host: str) -> bool:
        host = host.casefold().removeprefix("www.")
        return any(host == domain or host.endswith(f".{domain}") for domain in ACADEMY_HOSTS)

    @staticmethod
    def _card_for_anchor(anchor: Tag) -> Tag:
        """Find the smallest nearby element that contains the event date."""
        if ACADEMY_DATE_RE.search(anchor.get_text(" ", strip=True)):
            return anchor
        parent = anchor.parent
        for _ in range(5):
            if not isinstance(parent, Tag):
                break
            text = parent.get_text(" ", strip=True)
            if ACADEMY_DATE_RE.search(text) and len(text) <= 1400:
                return parent
            parent = parent.parent
        return anchor

    @staticmethod
    def _extract_title(card: Tag, anchor: Tag) -> str:
        candidates: list[str] = []
        for value in list(card.stripped_strings):
            clean = " ".join(value.split()).strip()
            lower = clean.casefold()
            if len(clean) < 4:
                continue
            if lower in ACADEMY_TYPE_WORDS:
                continue
            if ACADEMY_DATE_RE.fullmatch(clean):
                continue
            if ACADEMY_TIME_RANGE_RE.fullmatch(clean) or ACADEMY_TIME_RE.fullmatch(clean):
                continue
            if re.fullmatch(r"\d{1,2}[.:]\d{2}\s*[-–]\s*\d{1,2}[.:]\d{2}", clean):
                continue
            candidates.append(clean)

        anchor_text = " ".join(anchor.stripped_strings).strip()
        if anchor_text and not ACADEMY_DATE_RE.search(anchor_text):
            candidates.append(anchor_text)

        if not candidates:
            return "Royal Danish Academy event"
        # Calendar titles tend to be the longest meaningful line in the card.
        return max(candidates, key=len)[:400]

    @staticmethod
    def _category(text: str) -> str | None:
        lower = text.casefold()
        checks = (
            ("udstilling", "Exhibition"),
            ("exhibition", "Exhibition"),
            ("webinar", "Webinar"),
            ("ph.d.-forsvar", "PhD defense"),
            ("ph.d.-defense", "PhD defense"),
            ("phd defense", "PhD defense"),
            ("book launch", "Book launch"),
            ("lecture", "Lecture"),
            ("forelæsning", "Lecture"),
            ("arrangement", "Event"),
            ("event", "Event"),
        )
        for needle, label in checks:
            if needle in lower:
                return label
        return None

    @staticmethod
    def _event_datetimes(text: str) -> tuple[datetime | None, datetime | None]:
        date_matches = list(ACADEMY_DATE_RE.finditer(text))
        if not date_matches:
            return None, None

        def date_from_match(match) -> datetime:
            return datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
                0,
                0,
                tzinfo=COPENHAGEN_TZ,
            )

        try:
            start = date_from_match(date_matches[0])
            end = date_from_match(date_matches[1]) if len(date_matches) > 1 else None
        except ValueError:
            return None, None

        # Remove dates before looking for clock values so 08.09.2026 cannot be
        # misread as 08:09 (and 26.06.2026 cannot become the invalid time 26:06).
        time_text = ACADEMY_DATE_RE.sub(" ", text)
        time_range = ACADEMY_TIME_RANGE_RE.search(time_text)
        if time_range:
            start = start.replace(
                hour=int(time_range.group("start_h")),
                minute=int(time_range.group("start_m")),
            )
            if end is None:
                end = start.replace(
                    hour=int(time_range.group("end_h")),
                    minute=int(time_range.group("end_m")),
                )
            else:
                end = end.replace(
                    hour=int(time_range.group("end_h")),
                    minute=int(time_range.group("end_m")),
                )
        else:
            single_time = ACADEMY_TIME_RE.search(time_text)
            if single_time:
                start = start.replace(
                    hour=int(single_time.group("h")),
                    minute=int(single_time.group("m")),
                )
            else:
                # Noon is only an internal fallback when the site has no time.
                start = start.replace(hour=12, minute=0)

        return start, end

    async def fetch(self, url: str) -> list[EventCandidate]:
        now = datetime.now(COPENHAGEN_TZ)
        found: list[EventCandidate] = []
        seen: set[str] = set()

        requested_host = urlparse(url).netloc.casefold().removeprefix("www.")
        ordered_urls = list(self.calendar_urls)
        if requested_host.endswith("royaldanishacademy.com"):
            ordered_urls.reverse()

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            for calendar_url in ordered_urls:
                try:
                    response = await client.get(calendar_url)
                    response.raise_for_status()
                except httpx.HTTPError:
                    continue

                soup = BeautifulSoup(response.text, "html.parser")
                for anchor in soup.find_all("a", href=True):
                    detail_url = urljoin(str(response.url), anchor["href"]).split("#", 1)[0]
                    parsed = urlparse(detail_url)
                    if not self._academy_host(parsed.netloc):
                        continue
                    if detail_url in seen:
                        continue

                    card = self._card_for_anchor(anchor)
                    text = " ".join(card.stripped_strings).strip()
                    if not ACADEMY_DATE_RE.search(text):
                        continue

                    start, end = self._event_datetimes(text)
                    if start is None:
                        continue
                    # Keep ongoing exhibitions whose start date has passed but end
                    # date is still in the future. Drop everything fully finished.
                    if end is not None:
                        if end < now:
                            continue
                    elif start < now:
                        continue

                    title = self._extract_title(card, anchor)
                    category = self._category(text)
                    description = f"{category}: {title}" if category else title

                    image_url = None
                    image = card.find("img") if isinstance(card, Tag) else None
                    if isinstance(image, Tag):
                        raw_image = image.get("src") or image.get("data-src")
                        if raw_image:
                            image_url = urljoin(str(response.url), raw_image)

                    seen.add(detail_url)
                    found.append(
                        EventCandidate(
                            title=title,
                            description=description,
                            start_datetime=start,
                            end_datetime=end,
                            city="Copenhagen",
                            venue_name="The Royal Danish Academy",
                            address="Philip de Langes Allé 10, 1435 Copenhagen K",
                            registration_url=detail_url,
                            source_url=detail_url,
                            source_name=self.name,
                            image_url=image_url,
                            external_id=detail_url,
                        )
                    )

                # If one official calendar was successfully parsed, avoid duplicate
                # work on the mirrored language/domain version.
                if found:
                    break

        found.sort(key=lambda item: item.start_datetime)
        return found


class Thoravej29Source(BaseEventSource):
    """Discover Thoravej 29 events that are rendered by client-side JavaScript."""

    name = "Thoravej 29"

    async def fetch(self, url: str) -> list[EventCandidate]:
        return await search_thoravej29_events()


def parser_for_source(url: str, source_type: str | None = None) -> BaseEventSource:
    host = urlparse(url).netloc.casefold().removeprefix("www.")
    if host.endswith("dagensbyggeri.dk"):
        return DagensByggeriAccurateSource()
    if (
        host == "kglakademi.dk"
        or host.endswith(".kglakademi.dk")
        or host == "royaldanishacademy.com"
        or host.endswith(".royaldanishacademy.com")
    ):
        return RoyalDanishAcademyCalendarSource()
    if host == "thoravej29.dk" or host.endswith(".thoravej29.dk"):
        return Thoravej29Source()
    return parser_for_url(url, source_type)