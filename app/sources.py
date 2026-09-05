import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from dateutil import parser as date_parser


USER_AGENT = "EventAssistantBot/0.4"

MONTHS_DA = {
    "jan": 1, "januar": 1,
    "feb": 2, "februar": 2,
    "mar": 3, "marts": 3,
    "apr": 4, "april": 4,
    "maj": 5,
    "jun": 6, "juni": 6,
    "jul": 7, "juli": 7,
    "aug": 8, "august": 8,
    "sep": 9, "september": 9,
    "okt": 10, "oktober": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

DATE_RE = re.compile(
    r"\b(?:den\s+|d\.?\s*)?(?P<day>\d{1,2})\.?(?:\s*og\s*\d{1,2}\.)?\s+"
    r"(?P<month>jan(?:uar)?|feb(?:ruar)?|mar(?:ts)?|apr(?:il)?|maj|jun(?:i)?|jul(?:i)?|aug(?:ust)?|sep(?:tember)?|okt(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"(?:\s+(?P<year>20\d{2}))?\b",
    re.IGNORECASE,
)
TIME_RE = re.compile(r"\b(?:At|Kl\.?|at)\s*(?P<hour>\d{1,2})[:.]?(?P<minute>\d{2})?\b", re.IGNORECASE)


@dataclass
class EventCandidate:
    title: str
    start_datetime: datetime
    city: str
    registration_url: str
    source_url: str
    source_name: str
    description: str | None = None
    venue_name: str | None = None
    address: str | None = None
    end_datetime: datetime | None = None
    image_url: str | None = None
    price_text: str | None = None
    is_free: bool | None = None
    external_id: str | None = None

    @property
    def dedup_key(self) -> str:
        raw = self.external_id or f"{self.title}|{self.start_datetime.isoformat()}|{self.venue_name or ''}"
        return hashlib.sha256(raw.casefold().encode()).hexdigest()


class BaseEventSource:
    name: str

    async def fetch(self, url: str) -> list[EventCandidate]:
        raise NotImplementedError


def _parse_danish_date(text: str, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(timezone.utc)
    matches = list(DATE_RE.finditer(text))
    if not matches:
        return None

    candidates: list[datetime] = []
    for match in matches:
        day = int(match.group("day"))
        month_name = match.group("month").lower().rstrip(".")
        month = MONTHS_DA.get(month_name)
        if not month:
            continue
        year_text = match.group("year")
        years = [int(year_text)] if year_text else [now.year, now.year + 1]
        for year in years:
            try:
                candidate = datetime(year, month, day, 12, 0, tzinfo=timezone.utc)
            except ValueError:
                continue
            if year_text or candidate >= now:
                candidates.append(candidate)
                break

    future = [value for value in candidates if value >= now]
    return min(future) if future else (max(candidates) if candidates else None)


def _time_from_text(text: str) -> tuple[int, int]:
    match = TIME_RE.search(text)
    if not match:
        return 12, 0
    return int(match.group("hour")), int(match.group("minute") or 0)


class GenericJsonLdSource(BaseEventSource):
    name = "generic-jsonld"

    async def fetch(self, url: str) -> list[EventCandidate]:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        found: list[EventCandidate] = []
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                payload = json.loads(script.string or "{}")
            except json.JSONDecodeError:
                continue
            nodes: Iterable[dict] = payload if isinstance(payload, list) else [payload]
            for node in nodes:
                if isinstance(node, dict) and "@graph" in node:
                    nodes = [*nodes, *[x for x in node.get("@graph", []) if isinstance(x, dict)]]
                if not isinstance(node, dict):
                    continue
                kinds = node.get("@type", [])
                kinds = [kinds] if isinstance(kinds, str) else kinds
                if "Event" not in kinds or not node.get("name") or not node.get("startDate"):
                    continue
                location = node.get("location") or {}
                address = location.get("address") if isinstance(location, dict) else None
                if isinstance(address, dict):
                    city = address.get("addressLocality") or "Copenhagen"
                    address_text = ", ".join(
                        str(v)
                        for v in [address.get("streetAddress"), address.get("postalCode"), city]
                        if v
                    )
                else:
                    city, address_text = "Copenhagen", str(address) if address else None
                offers = node.get("offers") or {}
                offer = offers[0] if isinstance(offers, list) and offers else offers if isinstance(offers, dict) else {}
                event_url = node.get("url") or offer.get("url") or url
                found.append(
                    EventCandidate(
                        title=node["name"],
                        description=node.get("description"),
                        start_datetime=date_parser.isoparse(node["startDate"]),
                        end_datetime=date_parser.isoparse(node["endDate"]) if node.get("endDate") else None,
                        venue_name=location.get("name") if isinstance(location, dict) else None,
                        address=address_text,
                        city=city,
                        registration_url=urljoin(url, event_url),
                        source_url=urljoin(url, event_url),
                        source_name=self.name,
                        image_url=node.get("image") if isinstance(node.get("image"), str) else None,
                        price_text=str(offer.get("price")) if offer.get("price") is not None else None,
                        is_free=str(offer.get("price", "")).strip() in {"0", "0.0"},
                        external_id=str(node.get("@id")) if node.get("@id") else None,
                    )
                )
        return found


class DACSource(BaseEventSource):
    name = "Danish Architecture Center"
    calendar_url = "https://dac.dk/en/calendar"
    focus_pages = (
        "https://dac.dk/en/morgendac",
        "https://dac.dk/en/afterdac",
    )

    @staticmethod
    def _is_guided_tour(text: str, href: str) -> bool:
        combined = f"{text} {href}".casefold()
        return (
            "guided-tour" in combined
            or "guided tour" in combined
            or re.search(r"\btours?\b", combined) is not None
        )

    async def _event_from_detail(
        self,
        client: httpx.AsyncClient,
        detail_url: str,
        now: datetime,
    ) -> EventCandidate | None:
        if self._is_guided_tour("", detail_url):
            return None
        try:
            response = await client.get(detail_url)
            response.raise_for_status()
        except httpx.HTTPError:
            return None

        page = BeautifulSoup(response.text, "html.parser")
        h1 = page.find("h1")
        title = h1.get_text(" ", strip=True) if h1 else ""
        if not title or self._is_guided_tour(title, detail_url):
            return None

        # Prefer Schema.org Event data on individual event pages when available.
        for script in page.select('script[type="application/ld+json"]'):
            try:
                payload = json.loads(script.string or "{}")
            except json.JSONDecodeError:
                continue
            nodes = payload if isinstance(payload, list) else [payload]
            expanded: list[dict] = []
            for node in nodes:
                if isinstance(node, dict):
                    expanded.append(node)
                    expanded.extend(x for x in node.get("@graph", []) if isinstance(x, dict))
            for node in expanded:
                kinds = node.get("@type", [])
                kinds = [kinds] if isinstance(kinds, str) else kinds
                if "Event" not in kinds or not node.get("startDate"):
                    continue
                try:
                    start = date_parser.isoparse(node["startDate"])
                except (TypeError, ValueError):
                    continue
                if start.tzinfo is None:
                    start = start.replace(tzinfo=timezone.utc)
                if start < now:
                    continue
                location = node.get("location") if isinstance(node.get("location"), dict) else {}
                address_obj = location.get("address") if isinstance(location, dict) else None
                address_text = None
                if isinstance(address_obj, dict):
                    address_text = ", ".join(
                        str(v)
                        for v in [address_obj.get("streetAddress"), address_obj.get("postalCode"), address_obj.get("addressLocality")]
                        if v
                    )
                return EventCandidate(
                    title=node.get("name") or title,
                    description=node.get("description"),
                    start_datetime=start,
                    end_datetime=date_parser.isoparse(node["endDate"]) if node.get("endDate") else None,
                    city="Copenhagen",
                    venue_name=location.get("name") if isinstance(location, dict) else "Danish Architecture Center (DAC)",
                    address=address_text or "Bryghuspladsen 10, 1473 Copenhagen K",
                    registration_url=detail_url,
                    source_url=detail_url,
                    source_name=self.name,
                    external_id=detail_url,
                )

        text = (page.find("main") or page).get_text(" ", strip=True)
        start = _parse_danish_date(text, now=now)
        if not start:
            return None
        hour, minute = _time_from_text(text)
        start = start.replace(hour=hour, minute=minute)
        if start < now:
            return None
        return EventCandidate(
            title=title,
            description=text[:700],
            start_datetime=start,
            city="Copenhagen",
            venue_name="Danish Architecture Center (DAC)",
            address="Bryghuspladsen 10, 1473 Copenhagen K",
            registration_url=detail_url,
            source_url=detail_url,
            source_name=self.name,
            external_id=detail_url,
        )

    async def fetch(self, url: str) -> list[EventCandidate]:
        now = datetime.now(timezone.utc)
        found: list[EventCandidate] = []
        seen: set[str] = set()

        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = await client.get(self.calendar_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            for anchor in soup.find_all("a", href=True):
                href = urljoin(self.calendar_url, anchor["href"])
                parsed = urlparse(href)
                if not parsed.netloc.endswith("dac.dk") or "/events/" not in parsed.path:
                    continue

                text = anchor.get_text(" ", strip=True)
                if not text or self._is_guided_tour(text, href):
                    continue

                query = parse_qs(parsed.query)
                date_value = (query.get("date") or [None])[0]
                start_date: datetime | None = None
                if date_value:
                    try:
                        start_date = datetime.strptime(date_value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    except ValueError:
                        start_date = None
                if start_date is None:
                    start_date = _parse_danish_date(text, now=now)
                if start_date is None:
                    continue

                hour, minute = _time_from_text(text)
                start = start_date.replace(hour=hour, minute=minute)
                if start < now:
                    continue

                clean_url = href.split("#", 1)[0]
                if clean_url in seen:
                    continue
                seen.add(clean_url)

                title = re.sub(
                    r"^(AfterDAC|MorgenDAC|Knowledge Sharing and Networking|Event|Exhibitions?)\s+",
                    "",
                    text,
                    flags=re.IGNORECASE,
                )
                title = re.sub(r"^(?:At|Kl\.?)\s*\d{1,2}[:.]?\d{0,2}\s+", "", title, flags=re.IGNORECASE)
                title = title[:300].strip()

                found.append(
                    EventCandidate(
                        title=title or "DAC event",
                        description=text[:700],
                        start_datetime=start,
                        city="Copenhagen",
                        venue_name="Danish Architecture Center (DAC)",
                        address="Bryghuspladsen 10, 1473 Copenhagen K",
                        registration_url=clean_url,
                        source_url=clean_url,
                        source_name=self.name,
                        external_id=clean_url,
                    )
                )

            # MorgenDAC and AfterDAC have dedicated pages. Read them too so talks
            # are not missed when they are not yet prominent in the general calendar.
            focus_links: list[str] = []
            for focus_url in self.focus_pages:
                try:
                    focus_response = await client.get(focus_url)
                    focus_response.raise_for_status()
                except httpx.HTTPError:
                    continue
                focus_soup = BeautifulSoup(focus_response.text, "html.parser")
                for anchor in focus_soup.find_all("a", href=True):
                    detail_url = urljoin(focus_url, anchor["href"]).split("#", 1)[0]
                    parsed = urlparse(detail_url)
                    if not parsed.netloc.endswith("dac.dk") or "/events/" not in parsed.path:
                        continue
                    if self._is_guided_tour(anchor.get_text(" ", strip=True), detail_url):
                        continue
                    if detail_url not in focus_links and detail_url not in seen:
                        focus_links.append(detail_url)

            details = await asyncio.gather(
                *(self._event_from_detail(client, link, now) for link in focus_links[:40])
            )
            for event in details:
                if event is None or event.source_url in seen:
                    continue
                seen.add(event.source_url)
                found.append(event)

        if not found:
            fallback = await GenericJsonLdSource().fetch(self.calendar_url)
            found = [
                event for event in fallback
                if not self._is_guided_tour(event.title, event.source_url)
            ]

        # Put the knowledge-sharing formats first, then sort chronologically.
        def priority(event: EventCandidate) -> tuple[int, datetime]:
            text = f"{event.title} {event.description or ''}".casefold()
            rank = 0 if ("morgendac" in text or "afterdac" in text or "international talk" in text) else 1
            return rank, event.start_datetime

        found.sort(key=priority)
        return found


class DagensByggeriSource(BaseEventSource):
    name = "Dagens Byggeri"
    listing_url = "https://dagensbyggeri.dk/arrangementer/"

    async def fetch(self, url: str) -> list[EventCandidate]:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=20,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = await client.get(self.listing_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            links: list[str] = []
            for anchor in soup.find_all("a", href=True):
                href = urljoin(self.listing_url, anchor["href"])
                parsed = urlparse(href)
                if parsed.netloc.endswith("dagensbyggeri.dk") and "/arrangementer/" in parsed.path and parsed.path.rstrip("/") != "/arrangementer":
                    clean = href.split("#", 1)[0]
                    if clean not in links:
                        links.append(clean)
            links = links[:30]

            async def parse_detail(detail_url: str) -> EventCandidate | None:
                try:
                    detail = await client.get(detail_url)
                    detail.raise_for_status()
                except httpx.HTTPError:
                    return None
                page = BeautifulSoup(detail.text, "html.parser")
                h1 = page.find("h1")
                if not h1:
                    return None
                title = h1.get_text(" ", strip=True)
                main = page.find("main") or page.find("article") or page
                text = main.get_text(" ", strip=True)
                start = _parse_danish_date(text)
                if not start or start < datetime.now(timezone.utc):
                    return None
                description_tag = page.find("meta", attrs={"name": "description"})
                description = description_tag.get("content") if description_tag else None
                image_tag = page.find("meta", attrs={"property": "og:image"})
                image_url = image_tag.get("content") if image_tag else None
                city = "Copenhagen" if "københavn" in text.casefold() or "copenhagen" in text.casefold() else "Denmark"
                return EventCandidate(
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

            results = await asyncio.gather(*(parse_detail(link) for link in links))
        return [item for item in results if item is not None]


class RoyalDanishAcademySource(BaseEventSource):
    name = "The Royal Danish Academy"
    public_lectures_url = (
        "https://adk.elsevierpure.com/da/organisations/"
        "det-kongelige-akademi-arkitektur-design-konservering/activities/"
        "?type=%2Fdk%2Fatira%2Fpure%2Factivity%2Factivitytypes%2Ftalk%2Fpubliclecture"
    )

    async def fetch(self, url: str) -> list[EventCandidate]:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=25,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            response = await client.get(self.public_lectures_url)
            response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        now = datetime.now(timezone.utc)
        found: list[EventCandidate] = []
        seen: set[str] = set()

        for container in soup.find_all(["li", "div", "article"]):
            text = container.get_text(" ", strip=True)
            start = _parse_danish_date(text, now=now)
            if not start or start < now:
                continue
            anchor = container.find("a", href=True)
            if not anchor:
                continue
            title = anchor.get_text(" ", strip=True)
            if not title or len(title) < 4:
                continue
            detail_url = urljoin(self.public_lectures_url, anchor["href"])
            if detail_url in seen:
                continue
            seen.add(detail_url)
            found.append(
                EventCandidate(
                    title=title,
                    description=text[:600],
                    start_datetime=start,
                    city="Copenhagen",
                    venue_name="The Royal Danish Academy",
                    address="Philip de Langes Allé 10, 1435 København K",
                    registration_url=detail_url,
                    source_url=detail_url,
                    source_name=self.name,
                    external_id=detail_url,
                )
            )

        found.sort(key=lambda item: item.start_datetime)
        return found[:50]


def parser_for_url(url: str, source_type: str | None = None) -> BaseEventSource:
    host = urlparse(url).netloc.casefold().removeprefix("www.")
    if host.endswith("dagensbyggeri.dk"):
        return DagensByggeriSource()
    if host == "kglakademi.dk" or host.endswith(".kglakademi.dk"):
        return RoyalDanishAcademySource()
    if host.endswith("dac.dk"):
        return DACSource()
    return GenericJsonLdSource()
