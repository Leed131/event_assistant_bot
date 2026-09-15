import pytest
from bs4 import BeautifulSoup

from app.source_overrides import RoyalDanishAcademyCalendarSource


@pytest.mark.parametrize("separator", ["-", "–", "—"])
def test_exhibition_title_is_not_replaced_by_longer_date_range(separator):
    # Reconstructed calendar card using the user's exhibition example.
    soup = BeautifulSoup(
        f'<a href="/da/kalender/mediering-komposition"><span>UDSTILLING</span>'
        f'<span>01.09.2026 {separator} 29.10.2026</span>'
        '<span>Mediering &amp; komposition</span></a>',
        "html.parser",
    )
    assert RoyalDanishAcademyCalendarSource._extract_title(soup.a, soup.a) == (
        "Mediering & komposition"
    )


def test_heading_wins_over_long_description():
    soup = BeautifulSoup(
        '<a href="/event"><h3>Mediering &amp; komposition</h3>'
        '<p>A much longer description of the exhibition and all its projects</p>'
        '<span>01.09.2026 - 29.10.2026</span></a>', "html.parser"
    )
    assert RoyalDanishAcademyCalendarSource._extract_title(soup.a, soup.a) == (
        "Mediering & komposition"
    )


def test_dates_without_title_use_neutral_fallback():
    soup = BeautifulSoup('<a>01.09.2026 - 29.10.2026</a>', "html.parser")
    assert RoyalDanishAcademyCalendarSource._extract_title(soup.a, soup.a) == (
        "Royal Danish Academy event"
    )


def test_academy_date_range_is_not_misread_as_clock_time():
    start, end = RoyalDanishAcademyCalendarSource._event_datetimes(
        "EXHIBITION 26.06.2026 - 12.02.2027 Bachelor Exhibition 2026: Crafts in Glass and Ceramics"
    )

    assert start is not None
    assert end is not None
    assert (start.year, start.month, start.day, start.hour, start.minute) == (2026, 6, 26, 12, 0)
    assert (end.year, end.month, end.day, end.hour, end.minute) == (2027, 2, 12, 0, 0)


@pytest.mark.parametrize(
    ("text", "expected_start", "expected_end"),
    [
        (
            "EVENT 08.09.2026 15.30 - 17.30 Embodiments of Glass - Conversation on working with glass in art and architecture",
            (2026, 9, 8, 15, 30),
            (2026, 9, 8, 17, 30),
        ),
        (
            "PHD DEFENSE 10.09.2026 13.00 - 16.00 Building back differently",
            (2026, 9, 10, 13, 0),
            (2026, 9, 10, 16, 0),
        ),
        (
            "EVENT 15.09.2026 15.00 - 17.00 Book Launch: Contact Zones: Architectural Encounters in the Post-Anthropocene",
            (2026, 9, 15, 15, 0),
            (2026, 9, 15, 17, 0),
        ),
    ],
)
def test_academy_calendar_event_times(text, expected_start, expected_end):
    start, end = RoyalDanishAcademyCalendarSource._event_datetimes(text)

    assert start is not None
    assert end is not None
    assert (start.year, start.month, start.day, start.hour, start.minute) == expected_start
    assert (end.year, end.month, end.day, end.hour, end.minute) == expected_end
