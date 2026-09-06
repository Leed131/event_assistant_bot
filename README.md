# Event Assistant Bot

A multi-user Telegram assistant that discovers local events early, keeps registrations and manually uploaded tickets/QR codes together, and reminds users before an event.

## MVP

- Multi-user Telegram profiles
- City and interest preferences
- Curated DAC / Copenhagen source
- Generic JSON-LD Event parser for event pages
- Event deduplication
- Discover / This week / My registrations
- Manual `I registered` flow
- Manual ticket upload (Telegram `file_id`, no email access)
- Notes attached to registrations
- Configurable reminder days (database field; default 7)
- PostgreSQL persistence
- Docker + Render blueprint

Registration itself always happens manually on the organizer's website. The bot only provides the link.

## Cost-aware event discovery

The bot separates frequent low-cost parsing from low-frequency web discovery:

- HTML / JSON-LD / dedicated parsers run every `SCRAPE_INTERVAL_HOURS` (default: 6 hours).
- Brave Search discovery for dynamic/noisy sources such as Eventbrite, DAC supplementary discovery, Royal Danish Academy discovery and Thoravej 29 runs at most once every `AI_DISCOVERY_INTERVAL_DAYS` (default: 7 days).
- Brave returns candidate URLs. The bot fetches a small number of those pages directly and first tries to read Schema.org `Event` JSON-LD locally.
- If structured event data exists, no OpenAI call is made for that discovery result.
- Only when the fetched pages do not expose structured event data, one compact OpenAI call can structure the supplied page evidence. OpenAI does **not** receive the `web_search` tool.
- The last weekly discovery timestamp is stored in PostgreSQL, so Render deploys and restarts do not reset the weekly clock.
- Multiple rows for the same source family are collapsed to one weekly discovery pass.

This architecture keeps routine sources fresh while avoiding the large token and web-search usage caused by having OpenAI browse the web several times per day.

## Local setup

1. Create a Telegram bot with BotFather and copy its token.
2. Copy `.env.example` to `.env` and set the required variables.
3. Start PostgreSQL and the bot:

```bash
docker compose up --build
```

Or run Python locally after installing the project:

```bash
python -m venv .venv
# activate the venv
pip install -e ".[dev]"
python -m app.main
```

## Architecture

- `app/bot.py` — Telegram handlers and keyboards
- `app/db.py` — SQLAlchemy models and persistence
- `app/sources.py` — normalized event source interface + JSON-LD/DAC parser
- `app/brave_discovery.py` — Brave search, compact page fetching, JSON-LD extraction and optional OpenAI fallback
- `app/services.py` — matching, frequent scraping, weekly discovery, notifications and reminders
- `app/main.py` — application startup and scheduler

## Environment

```text
TELEGRAM_BOT_TOKEN
DATABASE_URL
BRAVE_SEARCH_API_KEY
OPENAI_API_KEY
OPENAI_DISCOVERY_MODEL=gpt-5.6-luna
SCRAPE_INTERVAL_HOURS=6
AI_DISCOVERY_INTERVAL_DAYS=7
```

`BRAVE_SEARCH_API_KEY` is required for the weekly dynamic-source discovery. `OPENAI_API_KEY` remains useful as a fallback for pages that do not expose structured event data, but it is no longer used for OpenAI Web Search.

## Render

This repo includes `render.yaml` for one background worker plus PostgreSQL. In Render, set `TELEGRAM_BOT_TOKEN`, `BRAVE_SEARCH_API_KEY` and `OPENAI_API_KEY` as secret environment variables. The worker uses Telegram long polling, so no public web service is needed for the bot itself.

`AI_DISCOVERY_INTERVAL_DAYS` defaults to 7. It can be overridden in Render if a different cadence is needed later.

## Adding a new event source

Prefer stable structured data first (Schema.org `Event` JSON-LD, RSS, or an official API). If a site has no structured data, add a dedicated parser class and keep it isolated from Telegram handlers. Brave Search should be a discovery fallback or low-frequency scheduled source, not part of every routine scrape. OpenAI should only structure a small, already-fetched evidence set when deterministic extraction is insufficient.

## Important next iteration

The current branch is an MVP scaffold. Before production launch we should add Alembic migrations, full custom source CRUD in Telegram, per-user timezone conversion, reminder types (7 days / 1 day / morning-of), parser fixtures, more tests, and rate limiting.
