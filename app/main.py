import logging
import time

from sqlalchemy import text
from telegram.ext import Application

from app.bot import register_handlers
from app.config import settings
from app.db import engine, init_db
from app.services import refresh_curated_sources, send_due_reminders


POLLING_LOCK_ID = 918273645
LOCK_RETRY_SECONDS = 5


async def scrape_job(context):
    await refresh_curated_sources(context.bot)


async def reminder_job(context):
    await send_due_reminders(context.bot)


def wait_for_polling_lock(logger):
    """Hold a PostgreSQL advisory lock for the lifetime of this process.

    Render may briefly overlap the old and new worker during a deploy. Instead
    of exiting and getting restarted in a loop, the new worker stays alive and
    waits until the old worker releases the lock. This prevents Telegram
    getUpdates 409 conflicts while still allowing a clean handover.
    """
    connection = engine.connect()
    while True:
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": POLLING_LOCK_ID},
        ).scalar()
        if acquired:
            return connection
        logger.info(
            "Another Event Assistant instance still owns the Telegram polling lock; "
            "waiting %s seconds for deploy handover.",
            LOCK_RETRY_SECONDS,
        )
        time.sleep(LOCK_RETRY_SECONDS)


def main() -> None:
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    logger = logging.getLogger(__name__)

    init_db()
    lock_connection = wait_for_polling_lock(logger)
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
