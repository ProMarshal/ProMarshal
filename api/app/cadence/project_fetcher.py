"""Fetch projects that need reminders"""
from typing import List, Dict, Any
from motor.motor_asyncio import AsyncIOMotorDatabase


async def fetch_projects_for_reminders(db: AsyncIOMotorDatabase, tier: str = None) -> List[Dict[str, Any]]:
    """
    Fetch projects where reminders are enabled

    Args:
        db: MongoDB database instance
        tier: Optional tier filter ("free" or "paid")

    Returns:
        List of project documents
    """
    schedule_query: Dict[str, Any] = {
        "job_type": "cadence_reminder",
        "enabled": True,
        "project_id": {"$ne": None},
    }
    try:
        schedules = await db.project_schedules.find(
            schedule_query,
            {"project_id": 1},
        ).to_list(length=None)
        project_ids = [
            str(item.get("project_id") or "").strip()
            for item in schedules
            if str(item.get("project_id") or "").strip()
        ]
        if not project_ids:
            return []

        project_query: Dict[str, Any] = {
            "project_id": {"$in": project_ids},
            "integrations.slack.status": "connected",
        }
        if tier:
            project_query["tier"] = tier

        projects = await db.projects.find(project_query).to_list(length=None)
        return projects
    except Exception as e:
        print(f"Error fetching projects for reminders: {str(e)}")
        return []


async def get_project_members(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Get active members from a project

    Args:
        project: Project document

    Returns:
        List of active members
    """
    members = project.get("members", [])

    # Filter only active members
    active_members = [m for m in members if m.get("status") == "active"]

    return active_members
