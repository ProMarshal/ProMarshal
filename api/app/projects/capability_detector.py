"""
Workflow Capability Detection Service

Auto-detects what workflow features a project uses:
- Sprints (Scrum workflow)
- Epics (Hierarchical structure)
- Releases (Future)
- Story Points (Future)

Runs after each sync to keep capabilities up-to-date.
"""
from typing import Dict, List, Any, Optional
from datetime import datetime
from app.domain.work_items.types import resolve_work_item_type_from_item


class WorkflowCapabilityDetector:
    """Detect workflow capabilities from synced task data"""

    @staticmethod
    def _resolve_work_item_type(task: Dict[str, Any]) -> str:
        return resolve_work_item_type_from_item(task)

    @staticmethod
    def detect_capabilities(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Analyze tasks to detect workflow capabilities.

        Args:
            tasks: List of normalized tasks from brain collection

        Returns:
            Capabilities dict with detected features
        """
        if not tasks:
            return {
                "has_sprints": False,
                "has_epics": False,
                "has_releases": False,
                "detected_at": datetime.utcnow(),
                "task_count": 0
            }

        # Detect sprint support
        has_sprints = any(
            task.get("sprint") is not None
            for task in tasks
        )

        # Detect hierarchy support using canonical semantics.
        has_epics = any(
            WorkflowCapabilityDetector._resolve_work_item_type(task) == "portfolio"
            for task in tasks
        )

        # Detect releases (future - placeholder)
        has_releases = any(
            task.get("release") is not None
            for task in tasks
        )

        # Build grouping hierarchy based on detected capabilities
        grouping_hierarchy = WorkflowCapabilityDetector._build_grouping_hierarchy(
            has_epics=has_epics,
            has_sprints=has_sprints,
            has_releases=has_releases
        )

        return {
            "has_sprints": has_sprints,
            "has_epics": has_epics,
            "has_releases": has_releases,
            "grouping_hierarchy": grouping_hierarchy,
            "detected_at": datetime.utcnow(),
            "task_count": len(tasks)
        }

    @staticmethod
    def _build_grouping_hierarchy(
        has_epics: bool,
        has_sprints: bool,
        has_releases: bool
    ) -> List[str]:
        """
        Build optimal grouping hierarchy based on detected capabilities.

        Priority order: Epic → Sprint → Release → Status

        Returns:
            List of grouping levels (e.g., ["epic", "sprint", "status"])
        """
        hierarchy = []

        # Add grouping levels in priority order
        if has_epics:
            hierarchy.append("epic")

        if has_sprints:
            hierarchy.append("sprint")

        if has_releases:
            hierarchy.append("release")

        # Always end with status grouping
        hierarchy.append("status")

        return hierarchy

    @staticmethod
    def get_sprint_statistics(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate sprint-related statistics if sprints are detected.

        Args:
            tasks: List of tasks with sprint data

        Returns:
            Sprint statistics or empty dict if no sprints
        """
        sprint_tasks = [t for t in tasks if t.get("sprint") is not None]

        if not sprint_tasks:
            return {}

        # Count tasks by sprint
        sprint_counts = {}
        active_sprints = set()
        future_sprints = set()
        closed_sprints = set()

        for task in sprint_tasks:
            sprint_name = task.get("sprint")
            sprint_state = task.get("sprint_state")

            if sprint_name:
                sprint_counts[sprint_name] = sprint_counts.get(sprint_name, 0) + 1

                if sprint_state == "active":
                    active_sprints.add(sprint_name)
                elif sprint_state == "future":
                    future_sprints.add(sprint_name)
                elif sprint_state == "closed":
                    closed_sprints.add(sprint_name)

        return {
            "total_sprint_tasks": len(sprint_tasks),
            "total_backlog_tasks": len(tasks) - len(sprint_tasks),
            "total_sprints": len(sprint_counts),
            "active_sprints": list(active_sprints),
            "future_sprints": list(future_sprints),
            "closed_sprints": list(closed_sprints),
            "sprint_task_distribution": sprint_counts
        }

    @staticmethod
    def get_epic_statistics(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Calculate epic-related statistics if epics are detected.

        Args:
            tasks: List of tasks with epic data

        Returns:
            Epic statistics or empty dict if no epics
        """
        # Count epics
        epic_tasks = [
            t for t in tasks
            if WorkflowCapabilityDetector._resolve_work_item_type(t) == "portfolio"
        ]

        # Count tasks with parent (belong to an epic)
        child_tasks = [t for t in tasks if t.get("parent_key") is not None]

        if not epic_tasks and not child_tasks:
            return {}

        # Count tasks by epic
        epic_counts = {}
        for task in child_tasks:
            parent_key = task.get("parent_key")
            if parent_key:
                epic_counts[parent_key] = epic_counts.get(parent_key, 0) + 1

        return {
            "total_epics": len(epic_tasks),
            "total_epic_children": len(child_tasks),
            "total_direct_tasks": len(tasks) - len(epic_tasks) - len(child_tasks),
            "epic_task_distribution": epic_counts
        }
