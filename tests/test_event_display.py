from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.event_display import format_event_when


def test_academy_exhibition_shows_period_without_fallback_noon():
    tz = ZoneInfo("Europe/Copenhagen")
    event = SimpleNamespace(
        source_url="https://kglakademi.dk/da/kalender/mediering-komposition",
        start_datetime=datetime(2026, 9, 1, 12, tzinfo=tz),
        end_datetime=datetime(2026, 10, 29, tzinfo=tz),
    )
    assert format_event_when(event) == "01.09.2026 – 29.10.2026"
    # PostgreSQL can return the same instants in UTC: retain the local end date.
    event.start_datetime = event.start_datetime.astimezone(timezone.utc)
    event.end_datetime = event.end_datetime.astimezone(timezone.utc)
    assert format_event_when(event) == "01.09.2026 – 29.10.2026"


def test_single_day_event_keeps_confirmed_time():
    event = SimpleNamespace(
        source_url="https://kglakademi.dk/da/kalender/book-launch",
        start_datetime=datetime(2026, 9, 15, 15),
        end_datetime=datetime(2026, 9, 15, 17),
    )
    assert format_event_when(event) == "15 Sep 2026, 15:00"


def test_other_source_keeps_existing_format():
    event = SimpleNamespace(
        source_url="https://example.com/event",
        start_datetime=datetime(2026, 9, 1, 12),
        end_datetime=datetime(2026, 10, 29),
    )
    assert format_event_when(event) == "01 Sep 2026, 12:00"
