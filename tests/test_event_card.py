from datetime import datetime
from types import SimpleNamespace

from app.event_display import event_card


def _event(**kw):
    base = dict(
        title="Talk & <Walk>",
        source_url="https://example.com/e",
        start_datetime=datetime(2026, 10, 26, 19, 0),
        end_datetime=None,
        venue_name="Danish Architecture Center",
        city="Copenhagen",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_card_is_calm_and_escaped():
    card = event_card(_event())
    assert card.splitlines() == [
        "<b>Talk &amp; &lt;Walk&gt;</b>",
        "26 Oct 2026, 19:00",
        "Danish Architecture Center",
    ]


def test_card_with_kicker_and_note_falls_back_to_city():
    card = event_card(_event(venue_name=None), note="Bring ID", kicker="Reminder")
    assert card.splitlines() == [
        "<i>Reminder</i>",
        "<b>Talk &amp; &lt;Walk&gt;</b>",
        "26 Oct 2026, 19:00",
        "Copenhagen",
        "",
        "<i>Bring ID</i>",
    ]
