import asyncio
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from app.sources import USER_AGENT, BaseEventSource, EventCandidate, GenericJsonLdSource

COPENHAGEN_TZ = ZoneInfo("Europe/Copenhagen")
ISO_DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
TIME_RANGE_RE = re.compile(
    r"\b(?P<start_h>\d{1,2})[:.](?P<start_m>\d{2})\s*[-–]\s*"
    r"(?P<end_h>\d{1,2})[:.](?P<end_m>\d{2})\b"
)
MONTH_DATE_RE = re.compile(
    r"\b(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)\s+"
    r"(?P<day>\d{1,2})(?:,\s*(?P<year>20\d{2}))?",
    re.IGNORECASE,
)


class BloxhubSource(BaseEventSource):
    """Parse BLOXHUB's public upcoming-events calendar.

    BLOXHUB uses a calendar listing where event cards link to /event/... detail
    pages. The listing itself is not reliably represented as Schema.org Event
    JSON-LD, which is why the generic parser can return zero results. We collect
    the detail links and then prefer structured Event data from each detail page,
    with an HTML fallback for resilience.
    """

    name = "BLOXHUB"
    listing_url = "https://bloxhub.org/upcoming-events/"

    @staticmethod
    def _city_from_text(text: str) -> str:
        lower = text.casefold()
        if "malmö" in lower or "malmo" in lower or "sweden" in lower:
            return "Malmö"
        if "online" in lower or "webinar" in lower:
            return "Online"
        if "københavn" in lower or "copenhagen" in lower or "bryghuspladsen" in lower:
            return "Copenhagen"
        # Most events on the official upcoming page are at BLOXHUB Copenhagen.
        return "Copenhagen"

    @staticmethod
    def _fallback_datetime(text: str) -> tuple[datetime | None, datetime | None]:
        date_match = ISO_DATE_RE.search(text)
        if date_match:
            try:
                base = datetime.strptime(date_match.group(1), "%Y-%m-%d").replace(tzinfo=COPENHAGEN_TZ)
            except ValueError:
                base = None
        else:
            base = None

        if base is None:
            match = MONTH_DATE_RE.search(text)
            if not match:
                return None, None
            year = int(match.group("year")) if match.group("year") else datetime.now(COPENHAGEN_TZ).year
            try:
                parsed = date_parser.parse(f"{match.group('month')} {match.group('day')} {year}")
                base = parsed.replace(tzinfo=COPENHAGEN_TZ)
            except (TypeError, ValueError):
                return None, None

        time_match = TIME_RANGE_RE.search(text)
        if time_match:
            start = base.replace(
                hour=int(time_match.group("start_h")),
                minute=int(time_match.group("start_m")),
            )
            end = base.replace(
                hour=int(time_match.group("end_h")),
                minute=int(time_match.group("end_m")),
            )
            if end < start:
                end = None
            return start, end

        return base.replace(hour=12, minute=0), None

    async def _parse_detail(
        self,
        client: httpx.AsyncClient,
        detail_url: str,
    ) -> EventCandidate | None:
        # First try the generic structured-data parser on the event detail page.
        try:
            structured = await GenericJsonLdSource().fetch(detail_url)
            if structured:
                event = structured[0]
                event.source_name = self.name
                event.external_id = detail_url
                event.source_url = detail_url
                event.city = self._city_from_text(
                    f"{event.city} {event.venue_name or ''} {event.address or ''} {event.description or ''}"
                )
                if not event.venue_name and event.city == "Copenhagen":
                    event.venue_name = "BLOXHUB"
                return event
        except Exception:
            pass

        try:
            response = await client.get(detail_url)
            response.raise_for_status()
        except httpx.HTTPError:
            return None

        soup = BeautifulSoup(response.text, "html.parser")
        h1 = soup.find("h1")
        if not h1:
            return None
        title = h1.get_text(" ", strip=True)
        main = soup.find("main") or soup
        text = main.get_text(" ", strip=True)
        start, end = self._fallback_datetime(text)
        if start is None:
            return None

        now = datetime.now(COPENHAGEN_TZ)
        if end is not None:
            if end < now:
                return None
        elif start < now:
            return None

        registration_url = detail_url
        for anchor in main.find_all("a", href=True):
            label = anchor.get_text(" ", strip=True).casefold()
            if any(word in label for word in ("sign up", "register", "registration", "ticket", "book", "join")):
                registration_url = urljoin(str(response.url), anchor["href"])
                break

        description_tag = soup.find("meta", attrs={"name": "description"})
        image_tag = soup.find("meta", attrs={"property": "og:image"})
        description = description_tag.get("content") if description_tag else text[:800]
        image_url = image_tag.get("content") if image_tag else None
        city = self._city_from_text(text)

        return EventCandidate(
            title=title,
            description=description,
            start_datetime=start,
            end_datetime=end,
            city=city,
            venue_name="BLOXHUB" if city == "Copenhagen" else None,
            address="Bryghuspladsen 8, 1473 Copenhagen K" if city == "Copenhagen" else None,
            registration_url=registration_url,
            source_url=detail_url,
            source_name=self.name,
            image_url=image_url,
            external_id=detail_url,
        )

    async def fetch(self, url: str) -> list[EventCandidate]:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = await client.get(self.listing_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            links: list[str] = []
            for anchor in soup.find_all("a", href=True):
                detail_url = urljoin(str(response.url), anchor["href"]).split("#", 1)[0]
                parsed = urlparse(detail_url)
                if not parsed.netloc.casefold().endswith("bloxhub.org"):
                    continue
                if "/event/" not in parsed.path:
                    continue
                if detail_url not in links:
                    links.append(detail_url)

            results = await asyncio.gather(
                *(self._parse_detail(client, detail_url) for detail_url in links[:60])
            )

        found = [event for event in results if event is not None]
        found.sort(key=lambda event: event.start_datetime)
        return found
