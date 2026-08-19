"""Brain database synchronization for tasks"""

from typing import Any, Dict, Optional, Tuple

from app.core.database import get_brain_collection
from app.domain.work_items.types import canonicalize_work_item_payload
from app.projects.pm_board_cache import invalidate_pm_board_summary_cache
from app.projects.recommendations.recommendation_orchestrator import mark_project_task_dirty


class BrainSync:
    """Sync tasks to brain database"""

    @staticmethod
    async def sync_task_to_brain(
        project_id: str,
        jira_task: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """
        Sync Jira task to brain collection.

        Args:
            project_id: Custom project_id (proj-XXXX-YYYY)
            jira_task: Jira issue data (already normalized)
        """
        brain_collection = get_brain_collection(project_id)
        normalized_task = canonicalize_work_item_payload(jira_task)
        provider = str(
            normalized_task.get("provider")
            or normalized_task.get("integration_type")
            or normalized_task.get("source")
            or "unknown"
        ).strip().lower() or "unknown"
        query = {
            "integration_type": provider,
            "external_id": normalized_task["external_id"],
        }
        existing = await brain_collection.find_one(query)

        # Upsert in brain collection
        await brain_collection.update_one(
            query,
            {"$set": normalized_task},
            upsert=True
        )
        task_key = str(normalized_task.get("external_id") or "").strip()
        if task_key:
            mark_project_task_dirty(project_id, task_key)
        invalidate_pm_board_summary_cache(project_id)
        return normalized_task, (dict(existing) if isinstance(existing, dict) else None)
