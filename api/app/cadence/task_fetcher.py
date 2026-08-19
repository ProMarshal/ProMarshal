"""Fetch tasks from database"""
from typing import List, Dict, Any
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.database import get_brain_collection


async def fetch_tasks_for_project(
    db: AsyncIOMotorDatabase,
    collection_name: str,
    *,
    provider: str | None = None,
) -> List[Dict[str, Any]]:
    """
    Fetch all pending tasks for a project from brain database

    Note: collection_name IS the project identifier (format: proj-XXXX-YYYY)
          Tasks are implicitly scoped to the project by the collection they're in

    Args:
        db: MongoDB database instance (kept for compatibility)
        collection_name: Brain collection name (format: proj-XXXX-YYYY)
        provider: Optional provider filter (e.g. jira) for session-pinned fetch scoping.

    Returns:
        List of task documents
    """
    try:
        # Get tasks from brain/{collection_name} collection
        brain_collection = get_brain_collection(collection_name)

        # Query: No project_id filter needed - collection name is the scope
        query = {
            "entity_type": "work_item",
            "status": {"$in": ["todo", "in_progress"]},
            "assignee_account_id": {"$ne": None, "$exists": True}
        }
        normalized_provider = str(provider or "").strip().lower()
        if normalized_provider:
            query["integration_type"] = normalized_provider

        tasks = await brain_collection.find(query).to_list(length=None)

        return tasks
    except Exception as e:
        print(f"Error fetching tasks from brain/{collection_name}: {str(e)}")
        return []


def serialize_tasks(tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Serialize tasks for JSON response (convert ObjectId to string)

    Args:
        tasks: List of task documents

    Returns:
        Serialized tasks
    """
    serialized = []
    for task in tasks:
        task_copy = task.copy()
        if "_id" in task_copy:
            task_copy["_id"] = str(task_copy["_id"])
        if "project_id" in task_copy:
            task_copy["project_id"] = str(task_copy["project_id"])
        serialized.append(task_copy)
    return serialized
