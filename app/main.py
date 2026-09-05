import logging

from telegram.ext import Application

from app.bot import register_handlers
from app.config import settings
from app.db import init_db
from app.services import refresh_curated_sources, send_due_reminders


async def scrape_job(context):
    await refresh_curated_sources(context.bot)


async def reminder_job(context):
    await send_due_reminders(context.bot)


def main() -> None:
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    init_db()
    app = Application.builder().token(settings.telegram_bot_token).build()
    register_handlers(app)
    app.job_queue.run_repeating(scrape_job, interval=settings.scrape_interval_hours * 3600, first=10)
    app.job_queue.run_repeating(reminder_job, interval=3600, first=30)
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
