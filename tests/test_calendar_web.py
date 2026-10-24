from datetime import datetime, timezone
from types import SimpleNamespace

from app import calendar_web


def _event():
    return SimpleNamespace(
        id=42,
        title="Test event",
        description="Test description",
        start_datetime=datetime(2026, 9, 15, 13, 0, tzinfo=timezone.utc),
        end_datetime=datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc),
        venue_name="Test venue",
        address="Test street 1",
        city="Copenhagen",
        registration_url="https://example.com/register",
        source_url="https://example.com/event",
    )


def test_calendar_page_uses_direct_ics_for_apple(monkeypatch):
    monkeypatch.setattr(calendar_web, "_get_event", lambda event_id: _event())

    request = SimpleNamespace(base_url="https://calendar.example.com/")
    html = calendar_web.calendar_page(42, request)

    assert "https://calendar.example.com/calendar/42/event.ics" in html
    assert "Apple Calendar" in html
    assert "shortcuts://" not in html


def test_apple_ics_response_has_calendar_mime_and_filename():
    response = calendar_web._apple_ics_response(_event())

    assert response.media_type == "text/calendar; charset=utf-8"
    assert response.headers["content-disposition"] == 'inline; filename="event-42.ics"'
    assert b"BEGIN:VCALENDAR\r\n" in response.body
    assert b"BEGIN:VEVENT\r\n" in response.body
    assert b"SUMMARY:Test event\r\n" in response.body
