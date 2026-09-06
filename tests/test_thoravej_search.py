from datetime import datetime
from zoneinfo import ZoneInfo

from app.source_overrides import Thoravej29Source, parser_for_source
from app.thoravej_search import _events_from_payload


COPENHAGEN_TZ = ZoneInfo("Europe/Copenhagen")


def test_thoravej_host_uses_dynamic_discovery_source():
    parser = parser_for_source("https://thoravej29.dk/da/events", "user")
    assert isinstance(parser, Thoravej29Source)


def test_thoravej_payload_keeps_upcoming_and_running_programmes():
    payload = {
        "events": [
            {
                "title": "Forandringskrafts Spireforløb",
                "start_datetime": "2026-09-09T08:00:00+02:00",
                "end_datetime": "2027-01-14T17:00:00+01:00",
                "source_url": "https://example.org/spire",
                "registration_url": "https://example.org/spire",
                "description": "A multi-day programme at Thoravej 29.",
                "is_free": True,
            },
            {
                "title": "Åben AkademiMorgen",
                "start_datetime": "2026-09-15T08:30:00+02:00",
                "end_datetime": "2026-09-15T10:00:00+02:00",
                "source_url": "https://example.org/akademimorgen",
                "registration_url": "https://example.org/akademimorgen",
            },
            {
                "title": "Already finished",
                "start_datetime": "2026-08-01T10:00:00+02:00",
                "end_datetime": "2026-08-01T11:00:00+02:00",
                "source_url": "https://example.org/past",
                "registration_url": "https://example.org/past",
            },
        ]
    }

    events = _events_from_payload(
        payload,
        now=datetime(2026, 9, 10, 12, 0, tzinfo=COPENHAGEN_TZ),
    )

    assert [event.title for event in events] == [
        "Forandringskrafts Spireforløb",
        "Åben AkademiMorgen",
    ]
    assert events[0].venue_name == "Thoravej 29"
    assert events[0].address == "Thoravej 29, 2400 Copenhagen NV"
