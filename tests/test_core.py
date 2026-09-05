from datetime import datetime, timezone

from app.sources import EventCandidate


def test_dedup_key_is_stable():
    event = EventCandidate(title="Architecture Talk", start_datetime=datetime(2026, 9, 18, 18, tzinfo=timezone.utc), city="Copenhagen", registration_url="https://example.com/e", source_url="https://example.com/e", source_name="test", venue_name="DAC")
    assert event.dedup_key == event.dedup_key
    assert len(event.dedup_key) == 64
