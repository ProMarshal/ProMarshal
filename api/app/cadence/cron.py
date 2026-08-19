"""Local cadence scheduler tick wrapper."""

import asyncio
import logging
from pathlib import Path

from dotenv import load_dotenv

from app.core.database import close_mongo_connection, connect_to_mongo, get_database
from app.scheduler.engine import SchedulerEngine
from app.scheduler.service import ensure_cadence_schedules_for_projects


env_path = Path(__file__).parent.parent.parent / ".env"
load_dotenv(dotenv_path=env_path)
logger = logging.getLogger("app.cadence.cron")


async def run_task_reminders() -> None:
    """Run cadence reminder scheduler tick locally."""
    await connect_to_mongo()
    try:
        db = get_database()
        seeded = await ensure_cadence_schedules_for_projects(db)
        engine = SchedulerEngine(db)
        result = await engine.tick(job_type="cadence_reminder")
        logger.info(
            "Cadence scheduler tick completed | "
            f"seeded={seeded} processed={result.get('processed', 0)} "
            f"success={result.get('success', 0)} failed={result.get('failed', 0)}"
        )
    except Exception:
        logger.exception("Cadence scheduler tick failed")
        raise
    finally:
        await close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(run_task_reminders())
