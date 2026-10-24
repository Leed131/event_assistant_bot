from datetime import timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


def format_event_when(event) -> str:
    """Show Academy exhibition periods without presenting fallback noon as a fact."""
    host = urlparse(event.source_url or "").hostname or ""
    academy = any(
        host == domain or host.endswith("." + domain)
        for domain in ("kglakademi.dk", "royaldanishacademy.com")
    )
    if academy and event.end_datetime is not None:
        def local(value):
            # Database timestamps are UTC when returned without tzinfo.
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone(ZoneInfo("Europe/Copenhagen"))

        start, end = local(event.start_datetime), local(event.end_datetime)
        if end.date() > start.date():
            return f"{start:%d.%m.%Y} – {end:%d.%m.%Y}"
    return f"{event.start_datetime:%d %b %Y, %H:%M}"


def event_card(event, note: str | None = None, kicker: str | None = None) -> str:
    """Calm, typographic event card (Telegram HTML): title, when, where, optional note."""
    from html import escape

    lines = []
    if kicker:
        lines.append(f"<i>{escape(kicker)}</i>")
    lines.append(f"<b>{escape(event.title or 'Untitled event')}</b>")
    lines.append(escape(format_event_when(event)))
    place = event.venue_name or event.city
    if place:
        lines.append(escape(place))
    if note:
        lines.append("")
        lines.append(f"<i>{escape(note)}</i>")
    return "\n".join(lines)
