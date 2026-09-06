from datetime import datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from app.dtu_source import DTUSource
from app.source_overrides import parser_for_source


COPENHAGEN_TZ = ZoneInfo("Europe/Copenhagen")


def _anchor(html: str):
    return BeautifulSoup(html, "html.parser").find("a")


def test_dtu_homepage_routes_to_dedicated_parser():
    parser = parser_for_source("https://www.dtu.dk/", "user")
    assert isinstance(parser, DTUSource)


def test_parse_regular_dtu_event_card():
    anchor = _anchor(
        '<a href="/english/calendar/cyber-security-kick-off?id=abc">'
        "01 Oct Cyber security Kick Off "
        "01. Oct 12:00 - 14:00 | An event to mark Cyber Security Month"
        "</a>"
    )
    now = datetime(2026, 9, 6, 16, 0, tzinfo=COPENHAGEN_TZ)

    event = DTUSource._candidate_from_anchor("https://www.dtu.dk/english", anchor, now)

    assert event is not None
    assert event.title == "Cyber security Kick Off"
    assert event.start_datetime == datetime(2026, 10, 1, 12, 0, tzinfo=COPENHAGEN_TZ)
    assert event.end_datetime == datetime(2026, 10, 1, 14, 0, tzinfo=COPENHAGEN_TZ)
    assert event.registration_url.endswith("/english/calendar/cyber-security-kick-off?id=abc")


def test_parse_ongoing_dtu_date_range():
    anchor = _anchor(
        '<a href="/english/calendar/next-generation-digital-action?id=xyz">'
        "15 May 08 Nov Next Generation Digital Action 2026 "
        "15. May 23:59 - 08. Nov 11:25 | A global youth initiative"
        "</a>"
    )
    now = datetime(2026, 9, 6, 16, 0, tzinfo=COPENHAGEN_TZ)

    event = DTUSource._candidate_from_anchor("https://www.dtu.dk/english/news", anchor, now)

    assert event is not None
    assert event.title == "Next Generation Digital Action 2026"
    assert event.start_datetime == datetime(2026, 5, 15, 23, 59, tzinfo=COPENHAGEN_TZ)
    assert event.end_datetime == datetime(2026, 11, 8, 11, 25, tzinfo=COPENHAGEN_TZ)
