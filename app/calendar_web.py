import json
from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import quote, urlencode

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response

from app.db import Event, SessionLocal


app = FastAPI(title="Event Assistant Calendar")
SHORTCUT_NAME = "Event Assistant Calendar"


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
        "STATUS:CONFIRMED",
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


def _shortcut_url(event: Event) -> str:
    """Build a Shortcuts deep link carrying all event fields as JSON text.

    The user creates one reusable iPhone Shortcut named Event Assistant Calendar.
    After that, this link launches the Shortcut directly from the browser and the
    Shortcut creates a native Apple Calendar event.
    """
    end = event.end_datetime or (event.start_datetime + timedelta(hours=2))
    location = ", ".join(part for part in [event.venue_name, event.address, event.city] if part)
    notes = event.description or ""
    if event.registration_url:
        notes = f"{notes}\n\nRegistration: {event.registration_url}".strip()
    payload = {
        "title": event.title,
        "start": event.start_datetime.isoformat(),
        "end": end.isoformat(),
        "location": location,
        "notes": notes,
        "url": event.registration_url or event.source_url or "",
    }
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return (
        "shortcuts://run-shortcut?"
        f"name={quote(SHORTCUT_NAME)}&input=text&text={quote(text, safe='')}"
    )


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
    shortcut_url = _shortcut_url(event)
    apple_ics_url = f"/calendar/{event.id}/apple"
    fallback_url = f"/calendar/{event.id}/download"
    register = event.registration_url or event.source_url or "#"

    return f"""
<!doctype html>
<html lang="en">
<head>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Add event to calendar</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; margin:0; background:#f5f5f5; color:#111; }}
    .card {{ max-width:560px; margin:28px auto; background:white; padding:26px; border-radius:20px; box-shadow:0 6px 30px rgba(0,0,0,.08); }}
    h1 {{ font-size:26px; margin:0 0 14px; }}
    h2 {{ font-size:18px; margin:28px 0 10px; }}
    .meta {{ line-height:1.5; color:#444; margin-bottom:24px; }}
    .btn {{ display:block; text-decoration:none; text-align:center; padding:16px 18px; border-radius:14px; margin:12px 0; font-weight:700; background:#111; color:white; }}
    .btn.apple {{ background:#111; }}
    .btn.secondary {{ background:#e9e9ee; color:#111; }}
    .small {{ font-size:13px; color:#666; margin-top:14px; line-height:1.5; }}
    .setup {{ background:#f3f3f6; border-radius:14px; padding:16px; font-size:14px; line-height:1.55; }}
    .setup ol {{ padding-left:22px; margin:8px 0 0; }}
    .fallback {{ font-size:13px; text-align:center; margin-top:14px; }}
    .fallback a {{ color:#555; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{escape(event.title)}</h1>
    <div class="meta">📅 {escape(when)}<br>📍 {escape(location)}</div>

    <a class="btn apple" href="{escape(shortcut_url)}"> Add to Apple Calendar</a>
    <a class="btn secondary" href="{escape(google_url)}">Add to Google Calendar</a>
    <a class="btn secondary" href="{escape(register)}">Open registration page</a>

    <div class="small">
      Apple button uses one reusable iPhone Shortcut. After the Shortcut is set up once,
      tapping this button can create the event directly in Apple Calendar without saving files.
    </div>

    <h2>First-time Apple setup</h2>
    <div class="setup">
      Create one Shortcut on your iPhone named <b>{SHORTCUT_NAME}</b>:
      <ol>
        <li>Open the <b>Shortcuts</b> app and create a new shortcut.</li>
        <li>Name it <b>{SHORTCUT_NAME}</b>.</li>
        <li>Add <b>Get Dictionary from Input</b> (Shortcut Input).</li>
        <li>Read the dictionary keys: <b>title</b>, <b>start</b>, <b>end</b>, <b>location</b>, <b>notes</b>.</li>
        <li>Add <b>Add New Event</b> and map those values to Title, Start Date, End Date, Location and Notes.</li>
        <li>Choose your Apple Calendar and enable <b>Show When Run</b> if you want confirmation before saving.</li>
      </ol>
      After that, return here and tap <b> Add to Apple Calendar</b> again.
    </div>

    <div class="fallback">
      No Shortcut yet? <a href="{apple_ics_url}">open the .ics preview</a> or
      <a href="{fallback_url}">download the .ics file</a>.
    </div>
  </div>
</body>
</html>
"""


@app.get("/calendar/{event_id}/apple")
def apple_calendar_inline(event_id: int) -> Response:
    event = _get_event(event_id)
    filename = f"event-{event.id}.ics"
    return Response(
        content=_event_ics(event),
        media_type="text/calendar; charset=utf-8; method=PUBLISH",
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "no-store, max-age=0",
        },
    )


@app.get("/calendar/{event_id}/download")
def apple_calendar_download(event_id: int) -> Response:
    event = _get_event(event_id)
    filename = f"event-{event.id}.ics"
    return Response(
        content=_event_ics(event),
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store, max-age=0",
        },
    )
