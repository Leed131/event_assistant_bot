from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response

from app.db import Event, SessionLocal


app = FastAPI(title="Event Assistant Calendar")


def _ics_escape(value: str | None) -> str:
    text = (value or "").replace("\\", "\\\\")
    text = text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    return text.replace(",", "\\,").replace(";", "\\;")


def _ics_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _event_ics(event: Event) -> bytes:
    location = ", ".join(part for part in [event.venue_name, event.address, event.city] if part)
    description_parts = []
    if event.description:
        description_parts.append(event.description[:1200])
    if event.registration_url:
        description_parts.append(f"Registration: {event.registration_url}")
    description = "\n\n".join(description_parts)

    end = event.end_datetime or (event.start_datetime + timedelta(hours=2))
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Event Assistant Bot//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "BEGIN:VEVENT",
        f"UID:event-{event.id}@event-assistant-bot",
        f"DTSTAMP:{_ics_datetime(datetime.now(timezone.utc))}",
        f"DTSTART:{_ics_datetime(event.start_datetime)}",
        f"DTEND:{_ics_datetime(end)}",
        f"SUMMARY:{_ics_escape(event.title)}",
        f"LOCATION:{_ics_escape(location)}",
        f"DESCRIPTION:{_ics_escape(description)}",
        f"URL:{event.registration_url or event.source_url or ''}",
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ]
    return "\r\n".join(lines).encode("utf-8")


def _google_calendar_url(event: Event) -> str:
    end = event.end_datetime or (event.start_datetime + timedelta(hours=2))
    location = ", ".join(part for part in [event.venue_name, event.address, event.city] if part)
    details = event.description or ""
    if event.registration_url:
        details = f"{details}\n\nRegistration: {event.registration_url}".strip()
    params = {
        "action": "TEMPLATE",
        "text": event.title,
        "dates": f"{_ics_datetime(event.start_datetime).removesuffix('Z')}/{_ics_datetime(end).removesuffix('Z')}",
        "details": details,
        "location": location,
    }
    return "https://calendar.google.com/calendar/render?" + urlencode(params)


def _get_event(event_id: int) -> Event:
    with SessionLocal() as db:
        event = db.get(Event, event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
        db.expunge(event)
        return event


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/calendar/{event_id}", response_class=HTMLResponse)
def calendar_page(event_id: int) -> str:
    event = _get_event(event_id)
    when = event.start_datetime.strftime("%d %b %Y, %H:%M")
    location = ", ".join(part for part in [event.venue_name, event.address, event.city] if part)
    google_url = _google_calendar_url(event)
    apple_url = f"/calendar/{event.id}.ics"
    register = event.registration_url or event.source_url or "#"

    return f"""
<!doctype html>
<html lang="en">
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Add event to calendar</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; margin:0; background:#f5f5f5; color:#111; }}
    .card {{ max-width:560px; margin:40px auto; background:white; padding:28px; border-radius:20px; box-shadow:0 6px 30px rgba(0,0,0,.08); }}
    h1 {{ font-size:26px; margin:0 0 14px; }}
    .meta {{ line-height:1.5; color:#444; margin-bottom:24px; }}
    .btn {{ display:block; text-decoration:none; text-align:center; padding:16px 18px; border-radius:14px; margin:12px 0; font-weight:700; background:#111; color:white; }}
    .btn.secondary {{ background:#e9e9ee; color:#111; }}
    .small {{ font-size:13px; color:#666; margin-top:18px; line-height:1.4; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{escape(event.title)}</h1>
    <div class="meta">📅 {escape(when)}<br>📍 {escape(location)}</div>
    <a class="btn" href="{apple_url}"> Add to Apple Calendar</a>
    <a class="btn secondary" href="{escape(google_url)}">Add to Google Calendar</a>
    <a class="btn secondary" href="{escape(register)}">Open registration page</a>
    <div class="small">On iPhone, tap “Add to Apple Calendar”. The .ics file is served as a calendar file over HTTPS so iOS can import it directly.</div>
  </div>
</body>
</html>
"""


@app.get("/calendar/{event_id}.ics")
def apple_calendar(event_id: int) -> Response:
    event = _get_event(event_id)
    filename = f"event-{event.id}.ics"
    return Response(
        content=_event_ics(event),
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
