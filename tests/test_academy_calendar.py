import pytest

from app.source_overrides import RoyalDanishAcademyCalendarSource


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
