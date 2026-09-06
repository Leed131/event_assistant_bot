import re
from datetime import datetime
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup, Tag

from app.sources import USER_AGENT, BaseEventSource, EventCandidate


COPENHAGEN_TZ = ZoneInfo("Europe/Copenhagen")

DTU_MONTHS = {
    "jan": 1,
    "january": 1,
    "januar": 1,
    "feb": 2,
    "february": 2,
    "februar": 2,
    "mar": 3,
    "march": 3,
    "marts": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "maj": 5,
    "jun": 6,
    "june": 6,
    "juni": 6,
    "jul": 7,
    "july": 7,
    "juli": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "okt": 10,
    "oktober": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

MONTH_TOKEN = r"[A-Za-zÆØÅæøå]+"
DTU_EVENT_TIME_RE = re.compile(
    rf"(?P<start_day>\d{{1,2}})\.?\s*(?P<start_month>{MONTH_TOKEN})"
    rf"(?:\s+(?P<start_year>20\d{{2}}))?\s+"
    rf"(?P<start_h>[01]?\d|2[0-3])[:.](?P<start_m>[0-5]\d)"
    rf"(?:\s*[-–]\s*"
    rf"(?:(?P<end_day>\d{{1,2}})\.?\s*(?P<end_month>{MONTH_TOKEN})"
    rf"(?:\s+(?P<end_year>20\d{{2}}))?\s+)?"
    rf"(?P<end_h>[01]?\d|2[0-3])[:.](?P<end_m>[0-5]\d))?",
    re.IGNORECASE,
)
DTU_BADGE_RE = re.compile(
    rf"^\s*\d{{1,2}}\.?\s+{MONTH_TOKEN}(?:\s+20\d{{2}})?\s+",
    re.IGNORECASE,
)
TITLE_YEAR_RE = re.compile(r"\b(20\d{2})\b")


class DTUSource(BaseEventSource):
    """Discover events across DTU's official public and internal event feeds.

    DTU exposes upcoming events in several places. The top-level calendar can be
    client-rendered, while the English front page/news page and DTU Inside expose
    server-rendered event cards. We read all of these official feeds so adding a
    plain dtu.dk URL still discovers substantially more than the homepage subset.
    """

    name = "Technical University of Denmark (DTU)"
    public_pages = (
        "https://www.dtu.dk/english/calendar",
        "https://www.dtu.dk/english/news",
        "https://www.dtu.dk/english",
    )
    inside_pages = (
        "https://www.inside.dtu.dk/en/services/messages/alle-interne-arrangementer",
        "https://www.inside.dtu.dk/en/",
    )
    fallback_pages = public_pages + inside_pages

    @staticmethod
    def _is_dtu_host(host: str) -> bool:
        host = host.casefold().removeprefix("www.")
        return host == "dtu.dk" or host.endswith(".dtu.dk")

    @staticmethod
    def _month_number(value: str | None) -> int | None:
        if not value:
            return None
        return DTU_MONTHS.get(value.casefold().rstrip("."))

    @classmethod
    def _is_event_detail_path(cls, host: str, path: str) -> bool:
        host = host.casefold().removeprefix("www.")
        path = path.casefold().rstrip("/")

        if "/calendar/" in path and not path.endswith("/calendar"):
            return True

        inside_prefix = "/services/messages/alle-interne-arrangementer/"
        if host == "inside.dtu.dk" and inside_prefix in path:
            return True

        return False

    @classmethod
    def _event_url(cls, base_url: str, anchor: Tag) -> str | None:
        href = anchor.get("href")
        if not href:
            return None
        detail_url = urljoin(base_url, href).split("#", 1)[0]
        parsed = urlparse(detail_url)
        if not cls._is_dtu_host(parsed.netloc):
            return None
        if not cls._is_event_detail_path(parsed.netloc, parsed.path):
            return None
        return detail_url

    @classmethod
    def _datetimes_from_text(
        cls,
        text: str,
        now: datetime,
    ) -> tuple[datetime | None, datetime | None, re.Match[str] | None]:
        match = DTU_EVENT_TIME_RE.search(text)
        if not match:
            return None, None, None

        start_month = cls._month_number(match.group("start_month"))
        if not start_month:
            return None, None, match

        start_year_text = match.group("start_year")
        title_years = TITLE_YEAR_RE.findall(text[: match.start()])
        title_year = int(title_years[-1]) if title_years else None
        start_year = int(start_year_text) if start_year_text else (title_year or now.year)
        try:
            start = datetime(
                start_year,
                start_month,
                int(match.group("start_day")),
                int(match.group("start_h")),
                int(match.group("start_m")),
                tzinfo=COPENHAGEN_TZ,
            )
        except ValueError:
            return None, None, match

        end: datetime | None = None
        if match.group("end_h"):
            end_day = int(match.group("end_day") or match.group("start_day"))
            end_month = cls._month_number(match.group("end_month")) or start_month
            end_year_text = match.group("end_year")
            end_year = int(end_year_text) if end_year_text else start_year
            start_day = int(match.group("start_day"))
            if not end_year_text and (end_month, end_day) < (start_month, start_day):
                end_year += 1
            try:
                end = datetime(
                    end_year,
                    end_month,
                    end_day,
                    int(match.group("end_h")),
                    int(match.group("end_m")),
                    tzinfo=COPENHAGEN_TZ,
                )
            except ValueError:
                end = None

        # DTU omits the year on most cards. Near New Year, a January event must
        # be interpreted as next year, while a recently expired card should not.
        if not start_year_text and title_year is None:
            event_finished = end < now if end is not None else start < now
            if event_finished and (now - start).days > 45:
                try:
                    start = start.replace(year=start.year + 1)
                    if end is not None:
                        end = end.replace(year=end.year + 1)
                except ValueError:
                    return None, None, match

        return start, end, match

    @classmethod
    def _candidate_from_anchor(
        cls,
        base_url: str,
        anchor: Tag,
        now: datetime,
    ) -> EventCandidate | None:
        detail_url = cls._event_url(base_url, anchor)
        if not detail_url:
            return None

        text = " ".join(anchor.stripped_strings).strip()
        if not text:
            return None

        start, end, match = cls._datetimes_from_text(text, now)
        if start is None or match is None:
            return None
        if end is not None:
            if end < now:
                return None
        elif start < now:
            return None

        title = text[: match.start()].strip()
        # Event cards start with one date badge; date-range cards can have two.
        for _ in range(2):
            cleaned = DTU_BADGE_RE.sub("", title, count=1).strip()
            if cleaned == title:
                break
            title = cleaned
        if not title:
            title = "DTU event"

        description = text[match.end() :].strip()
        description = description.lstrip("| ").strip() or None

        image_url = None
        image = anchor.find("img")
        if isinstance(image, Tag):
            raw_image = image.get("src") or image.get("data-src")
            if raw_image:
                image_url = urljoin(base_url, raw_image)

        combined = f"{title} {description or ''}".casefold()
        is_online = "webinar" in combined or " online" in combined or "virtual" in combined

        return EventCandidate(
            title=title[:400],
            description=description[:900] if description else None,
            start_datetime=start,
            end_datetime=end,
            city="Online" if is_online else "Denmark",
            venue_name="DTU / online" if is_online else "DTU",
            registration_url=detail_url,
            source_url=detail_url,
            source_name=cls.name,
            image_url=image_url,
            external_id=detail_url,
        )

    @staticmethod
    def _dedup_key(candidate: EventCandidate) -> str:
        """Deduplicate the same event when DTU publishes it in multiple feeds."""
        title = re.sub(r"\s+", " ", candidate.title).strip().casefold()
        start = candidate.start_datetime.astimezone(COPENHAGEN_TZ).strftime("%Y-%m-%dT%H:%M")
        return f"{title}|{start}"

    async def fetch(self, url: str) -> list[EventCandidate]:
        now = datetime.now(COPENHAGEN_TZ)
        pages: list[str] = []

        parsed = urlparse(url)
        if self._is_dtu_host(parsed.netloc):
            path = parsed.path.casefold().rstrip("/")
            # A detail page is not useful as a monitoring feed. A bare dtu.dk
            # homepage is covered by the richer English + Inside feeds below.
            is_detail = self._is_event_detail_path(parsed.netloc, path)
            is_bare_home = parsed.netloc.casefold().removeprefix("www.") == "dtu.dk" and path == ""
            if not is_detail and not is_bare_home:
                pages.append(url)

        for page in self.fallback_pages:
            if page not in pages:
                pages.append(page)

        found: dict[str, EventCandidate] = {}
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            for page_url in pages:
                try:
                    response = await client.get(page_url)
                    response.raise_for_status()
                except httpx.HTTPError:
                    continue

                soup = BeautifulSoup(response.text, "html.parser")
                for anchor in soup.find_all("a", href=True):
                    candidate = self._candidate_from_anchor(str(response.url), anchor, now)
                    if candidate is None:
                        continue
                    found.setdefault(self._dedup_key(candidate), candidate)

        events = list(found.values())
        events.sort(key=lambda item: item.start_datetime)
        return events
