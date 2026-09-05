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
- `app/services.py` — matching, scraping, notifications, reminders
- `app/main.py` — application startup and scheduler

## Render

This repo includes `render.yaml` for one background worker plus PostgreSQL. In Render, create a Blueprint from the repo, then set `TELEGRAM_BOT_TOKEN` as a secret environment variable. The worker uses Telegram long polling, so no public web service is needed for the MVP.

## Adding a new event source

Prefer stable structured data first (Schema.org `Event` JSON-LD, RSS, or an official API). If a site has no structured data, add a dedicated parser class and keep it isolated from Telegram handlers.

## Important next iteration

The current branch is an MVP scaffold. Before production launch we should add Alembic migrations, full custom source CRUD in Telegram, a stronger DAC-specific parser fallback if DAC's structured markup changes, per-user timezone conversion, reminder types (7 days / 1 day / morning-of), parser fixtures, more tests, and rate limiting.
