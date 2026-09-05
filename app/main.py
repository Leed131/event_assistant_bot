import logging
import sys

from sqlalchemy import text
from telegram.ext import Application

from app.bot import register_handlers
from app.config import settings
from app.db import engine, init_db
from app.services import refresh_curated_sources, send_due_reminders


POLLING_LOCK_ID = 918273645


async def scrape_job(context):
    await refresh_curated_sources(context.bot)


async def reminder_job(context):
    await send_due_reminders(context.bot)


def acquire_polling_lock():
    """Hold a PostgreSQL advisory lock for the lifetime of this process.

    This prevents two Render instances from polling Telegram with the same bot
    token during deploy overlap or accidental duplicate worker startup.
    """
    connection = engine.connect()
    acquired = connection.execute(
        text("SELECT pg_try_advisory_lock(:lock_id)"),
        {"lock_id": POLLING_LOCK_ID},
    ).scalar()
    if not acquired:
        connection.close()
        return None
    return connection


def main() -> None:
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    logger = logging.getLogger(__name__)

    init_db()
    lock_connection = acquire_polling_lock()
    if lock_connection is None:
        logger.error(
            "Another Event Assistant instance already owns the Telegram polling lock. "
            "Exiting this duplicate process to avoid getUpdates 409 conflicts."
        )
        sys.exit(0)

    logger.info("Telegram polling lock acquired; starting bot.")

    app = Application.builder().token(settings.telegram_bot_token).build()
    register_handlers(app)
    app.job_queue.run_repeating(
        scrape_job,
        interval=settings.scrape_interval_hours * 3600,
        first=10,
    )
    app.job_queue.run_repeating(reminder_job, interval=3600, first=30)

    try:
        app.run_polling(drop_pending_updates=False)
    finally:
        try:
            lock_connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": POLLING_LOCK_ID},
            )
        finally:
            lock_connection.close()


if __name__ == "__main__":
    main()
