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

The bot deliberately separates cheap deterministic scraping from expensive OpenAI web discovery:

- HTML / JSON-LD / dedicated parsers run every `SCRAPE_INTERVAL_HOURS` (default: 6 hours).
- OpenAI web-search discovery for dynamic/noisy sources such as Eventbrite, DAC fallback discovery, Royal Danish Academy discovery and Thoravej 29 runs at most once every `AI_DISCOVERY_INTERVAL_DAYS` (default: 7 days).
- The last AI discovery timestamp is stored in PostgreSQL, so Render deploys and restarts do not reset the weekly clock.
- Multiple rows for the same AI source family are collapsed to one OpenAI discovery call per weekly run.

This keeps frequently updated deterministic sources fresh without paying for web search several times per day.

## Local setup

1. Create a Telegram bot with BotFather and copy its token.
2. Copy `.env.example` to `.env` and set `TELEGRAM_BOT_TOKEN`.
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
- `app/services.py` — matching, scraping, weekly AI discovery, notifications, reminders
- `app/main.py` — application startup and scheduler

## Environment

```text
TELEGRAM_BOT_TOKEN
DATABASE_URL
OPENAI_API_KEY
SCRAPE_INTERVAL_HOURS=6
AI_DISCOVERY_INTERVAL_DAYS=7
```

## Render

This repo includes `render.yaml` for one background worker plus PostgreSQL. In Render, create a Blueprint from the repo, then set `TELEGRAM_BOT_TOKEN` and `OPENAI_API_KEY` as secret environment variables. The worker uses Telegram long polling, so no public web service is needed for the bot itself.

`AI_DISCOVERY_INTERVAL_DAYS` defaults to 7 in code. It can be overridden in Render if a different cadence is needed later.

## Adding a new event source

Prefer stable structured data first (Schema.org `Event` JSON-LD, RSS, or an official API). If a site has no structured data, add a dedicated parser class and keep it isolated from Telegram handlers. OpenAI web search should be a discovery fallback or a low-frequency scheduled source, not part of every routine scrape.

## Important next iteration

The current branch is an MVP scaffold. Before production launch we should add Alembic migrations, full custom source CRUD in Telegram, per-user timezone conversion, reminder types (7 days / 1 day / morning-of), parser fixtures, more tests, and rate limiting.
