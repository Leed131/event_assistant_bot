import os

os.environ.setdefault("DATABASE_URL", "sqlite://")

from app.brave_discovery import _host_allowed, _jsonld_events


def test_host_filter_accepts_subdomains():
    assert _host_allowed("https://www.dac.dk/events/test", ("dac.dk",))
    assert _host_allowed("https://events.dac.dk/test", ("dac.dk",))
    assert not _host_allowed("https://example.com/test", ("dac.dk",))


def test_jsonld_event_is_extracted_without_openai():
    html = '''
    <html><head>
      <script type="application/ld+json">
      {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": "Architecture Talk",
        "startDate": "2026-09-18T18:00:00+02:00",
        "location": {
          "@type": "Place",
          "name": "DAC",
          "address": {
            "streetAddress": "Bryghuspladsen 10",
            "postalCode": "1473",
            "addressLocality": "Copenhagen"
          }
        },
        "url": "/events/architecture-talk",
        "offers": {"price": "0"}
      }
      </script>
    </head><body></body></html>
    '''

    events = _jsonld_events(html, "https://dac.dk/calendar")

    assert len(events) == 1
    assert events[0]["title"] == "Architecture Talk"
    assert events[0]["registration_url"] == "https://dac.dk/events/architecture-talk"
    assert events[0]["is_free"] is True
    assert "Copenhagen" in events[0]["address"]
