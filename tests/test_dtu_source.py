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


def test_dtu_inside_routes_to_dedicated_parser():
    parser = parser_for_source(
        "https://www.inside.dtu.dk/en/services/messages/alle-interne-arrangementer",
        "user",
    )
    assert isinstance(parser, DTUSource)


def test_dtu_source_monitors_calendar_news_and_inside():
    assert "https://www.dtu.dk/english/calendar" in DTUSource.fallback_pages
    assert "https://www.dtu.dk/english/news" in DTUSource.fallback_pages
    assert (
        "https://www.inside.dtu.dk/en/services/messages/alle-interne-arrangementer"
        in DTUSource.fallback_pages
    )


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
    assert event.registration_url.endswith(
        "/english/calendar/cyber-security-kick-off?id=abc"
    )


def test_parse_dtu_inside_event_card():
    anchor = _anchor(
        '<a href="/en/services/messages/alle-interne-arrangementer/'
        'webinar-introduction-to-silviai-for-literature-screening-and-reviews?msgid=abc">'
        "08 sep Webinar: Introduction to Silvi.ai for literature screening and reviews "
        "08. Sep 13:00 - 14:00 | - for DTU researchers"
        "</a>"
    )
    now = datetime(2026, 9, 6, 16, 0, tzinfo=COPENHAGEN_TZ)

    event = DTUSource._candidate_from_anchor("https://www.inside.dtu.dk/en/", anchor, now)

    assert event is not None
    assert event.title == "Webinar: Introduction to Silvi.ai for literature screening and reviews"
    assert event.start_datetime == datetime(2026, 9, 8, 13, 0, tzinfo=COPENHAGEN_TZ)
    assert event.end_datetime == datetime(2026, 9, 8, 14, 0, tzinfo=COPENHAGEN_TZ)
    assert event.city == "Online"
    assert "inside.dtu.dk" in event.registration_url


def test_inside_feed_root_is_not_mistaken_for_event_detail():
    anchor = _anchor(
        '<a href="/en/services/messages/alle-interne-arrangementer">All Internal Events</a>'
    )

    assert DTUSource._event_url("https://www.inside.dtu.dk/en/", anchor) is None


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


def test_title_year_is_used_when_card_date_omits_year():
    anchor = _anchor(
        '<a href="/english/calendar/family-day-2027?id=family">'
        "16 Sep DTU Family Day 2027 16 Sep 16:00 - 20:00 | Mark your calendar"
        "</a>"
    )
    now = datetime(2026, 9, 6, 16, 0, tzinfo=COPENHAGEN_TZ)

    event = DTUSource._candidate_from_anchor("https://www.dtu.dk/english", anchor, now)

    assert event is not None
    assert event.title == "DTU Family Day 2027"
    assert event.start_datetime == datetime(2027, 9, 16, 16, 0, tzinfo=COPENHAGEN_TZ)


def test_same_event_from_multiple_feeds_has_same_dedup_key():
    now = datetime(2026, 9, 6, 16, 0, tzinfo=COPENHAGEN_TZ)
    public_anchor = _anchor(
        '<a href="/english/calendar/inaugural-lecture-by-hamid-nick?id=public">'
        "07 Sep Inaugural lecture by Hamid Nick "
        "07. Sep 15:00 - 18:00 | Professor Hamid Nick is giving his inaugural lecture."
        "</a>"
    )
    inside_anchor = _anchor(
        '<a href="/en/services/messages/alle-interne-arrangementer/'
        'inaugural-lecture-by-hamid-nick?msgid=internal">'
        "07 sep Inaugural lecture by Hamid Nick "
        "07. Sep 15:00 - 18:00 | Join us September 7, 2026."
        "</a>"
    )

    public_event = DTUSource._candidate_from_anchor(
        "https://www.dtu.dk/english/news", public_anchor, now
    )
    inside_event = DTUSource._candidate_from_anchor(
        "https://www.inside.dtu.dk/en/", inside_anchor, now
    )

    assert public_event is not None
    assert inside_event is not None
    assert DTUSource._dedup_key(public_event) == DTUSource._dedup_key(inside_event)
