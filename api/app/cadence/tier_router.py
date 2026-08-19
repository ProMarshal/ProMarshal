"""Route projects to appropriate tier handler"""
from typing import Dict, Any, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.cadence.free_tier import process_free_tier_project
from app.cadence.checkin import process_project_checkin


async def route_project_by_tier(
    db: AsyncIOMotorDatabase,
    project: Dict[str, Any],
    correlation_id: Optional[str] = None
) -> int:
    """
    Route project to appropriate tier handler

    Args:
        db: MongoDB database instance
        project: Project document
        correlation_id: Cron run correlation ID

    Returns:
        Number of reminders sent
    """
    tier = project.get("tier", "free")

    if tier == "paid":
        return await process_project_checkin(db, project, correlation_id)
    else:
        return await process_free_tier_project(db, project, correlation_id)
