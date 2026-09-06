from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
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


def _get_event(event_id: int) -> Event:
    with SessionLocal() as db:
        event = db.get(Event, event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Event not found")
        db.expunge(event)
        return event


def _apple_ics_response(event: Event, *, attachment: bool = False) -> Response:
    filename = f"event-{event.id}.ics"
    disposition = "attachment" if attachment else "inline"
    return Response(
        content=_event_ics(event),
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Cache-Control": "no-store, max-age=0",
        },
    )


def _safari_url(https_url: str) -> str:
    """Best-effort iOS Safari handoff from an in-app browser.

    x-safari-https is understood by current iOS Safari installations. If iOS or
    the host app refuses the scheme, the page also exposes the normal HTTPS link
    so users can copy/open it in Safari manually.
    """
    if https_url.startswith("https://"):
        return "x-safari-https://" + https_url.removeprefix("https://")
    return https_url


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/calendar/{event_id}", response_class=HTMLResponse)
def calendar_page(event_id: int, request: Request) -> str:
    event = _get_event(event_id)
    when = event.start_datetime.strftime("%d %b %Y, %H:%M")
    location = ", ".join(part for part in [event.venue_name, event.address, event.city] if part)
    google_url = _google_calendar_url(event)
    apple_path = f"/calendar/{event.id}/event.ics"
    apple_https_url = str(request.base_url).rstrip("/") + apple_path
    safari_url = _safari_url(apple_https_url)
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
    .meta {{ line-height:1.5; color:#444; margin-bottom:24px; }}
    .btn {{ display:block; text-decoration:none; text-align:center; padding:16px 18px; border-radius:14px; margin:12px 0; font-weight:700; background:#111; color:white; }}
    .btn.apple {{ background:#0a84ff; }}
    .btn.secondary {{ background:#e9e9ee; color:#111; }}
    .hint {{ background:#eef5ff; border-radius:12px; padding:12px 14px; margin:14px 0; font-size:14px; line-height:1.45; }}
    .fallback {{ font-size:13px; text-align:center; margin-top:14px; color:#666; line-height:1.5; }}
    .fallback a {{ color:#555; }}
    .copybox {{ word-break:break-all; font-size:12px; padding:10px; background:#f3f3f3; border-radius:10px; margin-top:10px; user-select:all; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>{escape(event.title)}</h1>
    <div class="meta">📅 {escape(when)}<br>📍 {escape(location)}</div>

    <div class="hint">
      <b>iPhone / Apple Calendar:</b> Telegram can preview .ics files without offering Calendar.
      Use the Safari button first. In Safari, open the calendar file and iOS should offer the normal calendar import flow.
    </div>

    <a class="btn apple" href="{escape(safari_url)}">Open Apple Calendar link in Safari</a>
    <a class="btn secondary" href="{escape(apple_https_url)}">Open .ics here instead</a>
    <a class="btn secondary" href="{escape(google_url)}">Add to Google Calendar</a>
    <a class="btn secondary" href="{escape(register)}">Open registration page</a>

    <div class="fallback">
      If the Safari button is blocked by Telegram, copy this address and paste it into Safari:
      <div class="copybox">{escape(apple_https_url)}</div>
      Or <a href="{fallback_url}">download the .ics file</a> as a fallback.
    </div>
  </div>
</body>
</html>
"""


@app.get("/calendar/{event_id}/event.ics")
def apple_calendar_ics(event_id: int) -> Response:
    """Serve a real .ics URL so iOS/macOS can hand it to Apple Calendar."""
    event = _get_event(event_id)
    return _apple_ics_response(event)


@app.get("/calendar/{event_id}/apple")
def apple_calendar_inline(event_id: int) -> Response:
    """Legacy Apple URL kept for links that were already sent to users."""
    event = _get_event(event_id)
    return _apple_ics_response(event)


@app.get("/calendar/{event_id}/download")
def apple_calendar_download(event_id: int) -> Response:
    event = _get_event(event_id)
    return _apple_ics_response(event, attachment=True)
