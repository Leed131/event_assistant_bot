from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from app.db import Event, SessionLocal
from app.event_display import format_event_when

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
    when = format_event_when(event)
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
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(event.title)} · Add to calendar</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400&display=swap" rel="stylesheet">
  <style>
    :root {{ --paper:#F5F1EB; --ink:#1A1A1A; --muted:#6B665F; --line:#D9D2C7; --card:#FBF9F5; }}
    @media (prefers-color-scheme: dark) {{
      :root {{ --paper:#1A1A1A; --ink:#F5F1EB; --muted:#A39D94; --line:#3A3734; --card:#222120; }}
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--paper); color:var(--ink);
           font-family:"IBM Plex Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           font-size:16px; line-height:1.55; -webkit-font-smoothing:antialiased; }}
    main {{ max-width:520px; margin:0 auto; padding:40px 20px 56px; }}
    .label {{ font-family:"IBM Plex Mono", ui-monospace, monospace; font-size:12px; letter-spacing:.12em;
             text-transform:uppercase; color:var(--muted); margin:0 0 20px; }}
    h1 {{ font-size:28px; line-height:1.2; font-weight:600; letter-spacing:-.01em; margin:0 0 24px; }}
    dl {{ margin:0 0 32px; border-top:1px solid var(--line); }}
    .row {{ display:flex; gap:16px; padding:12px 0; border-bottom:1px solid var(--line); }}
    dt {{ width:72px; flex:none; color:var(--muted); font-size:14px; }}
    dd {{ margin:0; }}
    .btn {{ display:block; text-decoration:none; padding:15px 18px; margin:0 0 10px; border:1px solid var(--ink);
           color:var(--ink); font-weight:500; border-radius:2px; transition:background .15s, color .15s; }}
    .btn::after {{ content:"→"; float:right; }}
    .btn.primary {{ background:var(--ink); color:var(--paper); }}
    .btn:hover {{ background:var(--ink); color:var(--paper); }}
    .note {{ margin:32px 0 0; padding-top:20px; border-top:1px solid var(--line); font-size:14px; color:var(--muted); }}
    .note b {{ color:var(--ink); font-weight:500; }}
    .copy {{ font-family:"IBM Plex Mono", ui-monospace, monospace; font-size:12px; word-break:break-all;
            background:var(--card); border:1px solid var(--line); padding:10px 12px; margin:10px 0; user-select:all; }}
    .note a {{ color:var(--ink); }}
  </style>
</head>
<body>
  <main>
    <p class="label">Add to calendar</p>
    <h1>{escape(event.title)}</h1>
    <dl>
      <div class="row"><dt>When</dt><dd>{escape(when)}</dd></div>
      <div class="row"><dt>Where</dt><dd>{escape(location) or "—"}</dd></div>
    </dl>

    <a class="btn primary" href="{escape(safari_url)}">Apple Calendar (open in Safari)</a>
    <a class="btn" href="{escape(google_url)}">Google Calendar</a>
    <a class="btn" href="{escape(apple_https_url)}">Open the .ics file here</a>
    <a class="btn" href="{escape(register)}">Registration page</a>

    <div class="note">
      <b>On iPhone:</b> Telegram can preview calendar files without offering to add them.
      Use the Apple Calendar button to open the file in Safari, where iOS shows the usual import screen.
      If Telegram blocks it, copy this address into Safari:
      <div class="copy">{escape(apple_https_url)}</div>
      Or <a href="{fallback_url}">download the .ics file</a>.
    </div>
  </main>
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

