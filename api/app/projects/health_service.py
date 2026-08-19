"""
Health Service - Integration-Agnostic Health Calculation

Orchestrates hierarchy building and health calculation.
Works for any integration (Jira, Linear, ClickUp, etc.)
"""
from typing import Dict, List, Any
from app.domain.work_items.types import resolve_work_item_type_from_item
from app.projects.health_rules import HealthRules, HealthStatus


class HealthService:
    """
    Integration-agnostic health calculation service.
    Orchestrates hierarchy building and health calculation.
    """

    @staticmethod
    def _resolve_work_item_type(task: Dict[str, Any]) -> str:
        """Resolve canonical work-item type for runtime health logic."""
        return resolve_work_item_type_from_item(task)

    @staticmethod
    def _is_portfolio(task: Dict[str, Any]) -> bool:
        return HealthService._resolve_work_item_type(task) == "portfolio"

    @staticmethod
    def calculate_health(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Main entry point for health calculation.

        Args:
            tasks: All tasks from brain collection

        Returns:
            Health data structure ready to store
        """
        if not tasks:
            return {
                "health_type": "task",
                "tasks": [],
                "summary": {
                    "on_track": 0,
                    "at_risk": 0,
                    "critical": 0,
                    "total_tasks": 0
                }
            }

        # Step 1: Detect hierarchy type
        has_epics = any(HealthService._is_portfolio(task) for task in tasks)

        # Step 2: Build hierarchy
        if has_epics:
            hierarchy = HealthService._build_epic_hierarchy(tasks)
        else:
            hierarchy = HealthService._build_flat_hierarchy(tasks)

        # Step 3: Calculate health bottom-up
        HealthService._calculate_health_bottom_up(hierarchy)

        # Step 4: Format response
        return HealthService._format_response(hierarchy, has_epics)

    @staticmethod
    def _build_epic_hierarchy(tasks: List[Dict]) -> Dict:
        """
        Build Epic → Story/Task hierarchy.

        Returns:
            {
                "epics": [
                    {
                        "epic": {...},
                        "children": [...]
                    }
                ]
            }
        """
        # Get all epics
        epics = [task for task in tasks if HealthService._is_portfolio(task)]
        task_by_key = {
            str(task.get("external_id") or ""): task
            for task in tasks
            if task.get("external_id")
        }

        epic_children_map = {
            str(epic.get("external_id")): []
            for epic in epics
            if epic.get("external_id")
        }
        orphan_work_items: List[Dict] = []

        def _find_epic_ancestor(task: Dict) -> str:
            current_parent = str(task.get("parent_key") or "").strip()
            visited = set()
            while current_parent and current_parent not in visited:
                visited.add(current_parent)
                parent_task = task_by_key.get(current_parent)
                if not parent_task:
                    return ""
                if HealthService._is_portfolio(parent_task):
                    return current_parent
                current_parent = str(parent_task.get("parent_key") or "").strip()
            return ""

        for task in tasks:
            if HealthService._is_portfolio(task):
                continue
            epic_ancestor_key = _find_epic_ancestor(task)
            if epic_ancestor_key and epic_ancestor_key in epic_children_map:
                epic_children_map[epic_ancestor_key].append(task)
            else:
                orphan_work_items.append(task)

        epic_hierarchy = []
        for epic in epics:
            epic_key = str(epic.get("external_id") or "")
            epic_hierarchy.append({
                "epic": epic,
                "children": epic_children_map.get(epic_key, [])
            })

        return {
            "type": "epic",
            "epics": epic_hierarchy,
            "orphan_work_items": orphan_work_items
        }

    @staticmethod
    def _build_flat_hierarchy(tasks: List[Dict]) -> Dict:
        """
        Build flat task list (no epics).

        Returns:
            {
                "tasks": [...]
            }
        """
        # Filter out epics (if any) and get only tasks
        flat_tasks = [
            task for task in tasks
            if not HealthService._is_portfolio(task)
        ]

        return {
            "type": "task",
            "tasks": flat_tasks
        }

    @staticmethod
    def _generate_parent_reason(children: List[Dict]) -> str:
        """
        Generate a meaningful health reason for parent based on children.

        Args:
            children: List of child items with health_status and health_reason

        Returns:
            Human-readable reason string
        """
        if not children:
            return ""

        def _child_label(items: List[Dict]) -> str:
            work_item_types = {
                HealthService._resolve_work_item_type(item)
                for item in items
            }
            if len(work_item_types) == 1:
                item_type = next(iter(work_item_types))
                if item_type == "subtask":
                    return "subtask"
                if item_type == "defect":
                    return "defect"
                if item_type == "standard":
                    return "work item"
            return "item"

        # Count children by status
        critical_count = sum(1 for c in children if c.get("health_status") == HealthStatus.CRITICAL)
        at_risk_count = sum(1 for c in children if c.get("health_status") == HealthStatus.AT_RISK)
        critical_children = [c for c in children if c.get("health_status") == HealthStatus.CRITICAL]
        at_risk_children = [c for c in children if c.get("health_status") == HealthStatus.AT_RISK]
        critical_label = _child_label(critical_children or children)
        at_risk_label = _child_label(at_risk_children or children)

        # Collect reasons from critical children
        critical_reasons = [
            c.get("health_reason")
            for c in children
            if c.get("health_status") == HealthStatus.CRITICAL and c.get("health_reason")
        ]

        # Generate parent reason
        if critical_count > 0:
            # If multiple children have same reason, aggregate
            if critical_reasons:
                # Count occurrences of each reason
                reason_counts = {}
                for reason in critical_reasons:
                    reason_counts[reason] = reason_counts.get(reason, 0) + 1

                # Find most common reason
                most_common_reason = max(reason_counts.items(), key=lambda x: x[1])
                reason_text = most_common_reason[0].lower()
                count = most_common_reason[1]

                if count == critical_count:
                    # All critical tasks have same reason
                    if "overdue" in reason_text:
                        return f"{critical_count} {critical_label if critical_count == 1 else critical_label + 's'} overdue"
                    return f"{critical_count} {critical_label if critical_count == 1 else critical_label + 's'} {reason_text}"
                else:
                    # Mixed reasons
                    return f"{critical_count} critical {critical_label if critical_count == 1 else critical_label + 's'}"
            else:
                return f"{critical_count} critical {critical_label if critical_count == 1 else critical_label + 's'}"

        elif at_risk_count > 0:
            return f"{at_risk_count} {at_risk_label if at_risk_count == 1 else at_risk_label + 's'} at risk"

        return ""

    @staticmethod
    def _calculate_health_bottom_up(hierarchy: Dict):
        """
        Calculate health starting from leaf nodes.
        Uses HealthRules for consistent calculation.
        """
        if hierarchy["type"] == "epic":
            # Calculate health for each child task first
            for epic_data in hierarchy["epics"]:
                for child in epic_data["children"]:
                    status, reason, action = HealthRules.calculate_health(child)
                    child["health_status"] = status
                    child["health_reason"] = reason
                    child["health_action"] = action

                # Calculate epic health based on children
                epic = epic_data["epic"]
                if epic_data["children"]:
                    epic["health_status"] = HealthRules.calculate_parent_health(
                        epic_data["children"]
                    )
                    # Generate meaningful reason for parent based on children
                    epic["health_reason"] = HealthService._generate_parent_reason(
                        epic_data["children"]
                    )
                    # Generate action for parent based on worst child
                    epic["health_action"] = HealthService._generate_parent_action(
                        epic_data["children"]
                    )
                else:
                    # Epic with no children
                    status, reason, action = HealthRules.calculate_health(epic)
                    epic["health_status"] = status
                    epic["health_reason"] = reason
                    epic["health_action"] = action

            # Also calculate health for non-epic work items that are not under any epic.
            for orphan in hierarchy.get("orphan_work_items", []):
                status, reason, action = HealthRules.calculate_health(orphan)
                orphan["health_status"] = status
                orphan["health_reason"] = reason
                orphan["health_action"] = action

        elif hierarchy["type"] == "task":
            # Calculate health for each task
            for task in hierarchy["tasks"]:
                status, reason, action = HealthRules.calculate_health(task)
                task["health_status"] = status
                task["health_reason"] = reason
                task["health_action"] = action

    @staticmethod
    def _generate_parent_action(children: List[Dict]) -> str:
        """
        Generate action text for a parent (epic) based on its worst child.
        Uses the same priority as calculate_parent_health: Critical > At Risk > On Track.
        """
        from app.projects.health_rules import HealthStatus  # avoid circular at module level
        # Find worst child
        critical_children = [c for c in children if c.get("health_status") == HealthStatus.CRITICAL]
        if critical_children:
            return critical_children[0].get("health_action", "")
        at_risk_children = [c for c in children if c.get("health_status") == HealthStatus.AT_RISK]
        if at_risk_children:
            return at_risk_children[0].get("health_action", "")
        return ""

    @staticmethod
    def _format_response(hierarchy: Dict, has_epics: bool) -> Dict:
        """
        Format hierarchy into response structure for storage.

        Returns:
            {
                "health_type": "epic" | "task",
                "epics" | "tasks": [...],
                "summary": {...}
            }
        """
        if hierarchy["type"] == "epic":
            return HealthService._format_epic_response(hierarchy)
        else:
            return HealthService._format_task_response(hierarchy)

    @staticmethod
    def _to_iso(value: Any) -> Any:
        if value is None:
            return None
        try:
            return value.isoformat()
        except Exception:
            return value

    @staticmethod
    def _format_work_item(item: Dict[str, Any], epic_key: str = "") -> Dict[str, Any]:
        status_raw = str(item.get("status", "") or "")
        status_display = str(item.get("status_display") or item.get("status_raw") or status_raw or "")
        return {
            "task_key": item.get("external_id"),
            "title": item.get("title"),
            "work_item_type": item.get("work_item_type") or HealthService._resolve_work_item_type(item),
            "provider_type": item.get("provider_type") or item.get("issue_type"),
            "issue_type": item.get("issue_type"),
            "issue_type_icon_url": item.get("issue_type_icon_url"),
            "health_status": item.get("health_status"),
            "health_reason": item.get("health_reason", ""),
            "health_action": item.get("health_action", ""),
            "status": status_raw,
            "status_raw": str(item.get("status_raw") or "") or None,
            "status_display": status_display or None,
            "assignee": item.get("assignee_name"),
            "assignee_name": item.get("assignee_name"),
            "assignee_email": item.get("assignee_email"),
            "due_date": HealthService._to_iso(item.get("due_date")),
            "start_date": HealthService._to_iso(item.get("start_date")),
            "parent_key": item.get("parent_key"),
            "epic_key": epic_key or None,
            "url": item.get("url"),
            "priority": item.get("priority"),
        }

    @staticmethod
    def _format_epic_response(hierarchy: Dict) -> Dict:
        """Format epic-based health response"""
        epics_data = []
        summary = {
            "on_track": 0,
            "at_risk": 0,
            "critical": 0,
            "total_epics": 0
        }

        for epic_data in hierarchy["epics"]:
            epic = epic_data["epic"]
            children = epic_data["children"]

            # Format children (stories/tasks)
            formatted_children = []
            for child in children:
                formatted_children.append(HealthService._format_work_item(child, epic_key=str(epic.get("external_id") or "")))

            # Count tasks by status
            task_counts = {
                "on_track": sum(1 for c in children if c.get("health_status") == HealthStatus.ON_TRACK),
                "at_risk": sum(1 for c in children if c.get("health_status") == HealthStatus.AT_RISK),
                "critical": sum(1 for c in children if c.get("health_status") == HealthStatus.CRITICAL)
            }

            epics_data.append({
                "epic_key": epic.get("external_id"),
                "title": epic.get("title"),
                "issue_type_icon_url": epic.get("issue_type_icon_url"),
                "health_status": epic.get("health_status"),
                "health_reason": epic.get("health_reason", ""),
                "health_action": epic.get("health_action", ""),
                "stories": formatted_children,
                "task_counts": task_counts
            })

            # Update summary
            if epic.get("health_status") == HealthStatus.ON_TRACK:
                summary["on_track"] += 1
            elif epic.get("health_status") == HealthStatus.AT_RISK:
                summary["at_risk"] += 1
            else:
                summary["critical"] += 1

        summary["total_epics"] = len(epics_data)

        def _is_completed(status: str) -> bool:
            normalized = (status or "").strip().lower().replace(" ", "_")
            return normalized in {"done", "closed", "resolved", "completed"}

        all_work_items: List[Dict] = []
        formatted_work_items: List[Dict[str, Any]] = []
        for epic_data in hierarchy["epics"]:
            epic_key = str(epic_data["epic"].get("external_id") or "")
            for child in epic_data["children"]:
                all_work_items.append(child)
                formatted_work_items.append(HealthService._format_work_item(child, epic_key=epic_key))
        for orphan in hierarchy.get("orphan_work_items", []):
            all_work_items.append(orphan)
            formatted_work_items.append(HealthService._format_work_item(orphan))

        completed_work_items = [
            item for item in all_work_items
            if _is_completed(str(item.get("status", "")))
        ]
        open_work_items = [
            item for item in all_work_items
            if not _is_completed(str(item.get("status", "")))
        ]
        work_item_summary = {
            "total": len(all_work_items),
            "completed": len(completed_work_items),
            "on_track": sum(1 for item in open_work_items if item.get("health_status") == HealthStatus.ON_TRACK),
            "at_risk": sum(1 for item in open_work_items if item.get("health_status") == HealthStatus.AT_RISK),
            "critical": sum(1 for item in open_work_items if item.get("health_status") == HealthStatus.CRITICAL),
            "open_total": len(open_work_items),
        }

        return {
            "health_type": "epic",
            "epics": epics_data,
            "summary": summary,
            "work_item_summary": work_item_summary,
            "work_items": formatted_work_items,
        }

    @staticmethod
    def _format_task_response(hierarchy: Dict) -> Dict:
        """Format task-based health response"""
        all_tasks = hierarchy["tasks"]

        def _is_completed(status: str) -> bool:
            normalized = (status or "").strip().lower().replace(" ", "_")
            return normalized in {"done", "closed", "resolved", "completed"}

        standard_scope_tasks = [
            task for task in all_tasks
            if HealthService._resolve_work_item_type(task) == "standard"
        ]
        story_tasks = [
            task for task in standard_scope_tasks
            if str(task.get("provider_type") or task.get("issue_type") or "").strip().lower() == "story"
        ]
        summary_scope_tasks = standard_scope_tasks if standard_scope_tasks else all_tasks
        open_scope_tasks = [
            task for task in summary_scope_tasks
            if not _is_completed(str(task.get("status", "")))
        ]

        summary = {
            "on_track": 0,
            "at_risk": 0,
            "critical": 0,
            "total_tasks": 0
        }

        for task in open_scope_tasks:
            if task.get("health_status") == HealthStatus.ON_TRACK:
                summary["on_track"] += 1
            elif task.get("health_status") == HealthStatus.AT_RISK:
                summary["at_risk"] += 1
            else:
                summary["critical"] += 1

        summary["total_tasks"] = len(open_scope_tasks)

        formatted_tasks = []
        for task in all_tasks:
            formatted_tasks.append(HealthService._format_work_item(task))

        completed_tasks = [
            task for task in all_tasks
            if _is_completed(str(task.get("status", "")))
        ]
        open_tasks = [
            task for task in all_tasks
            if not _is_completed(str(task.get("status", "")))
        ]
        work_item_summary = {
            "total": len(all_tasks),
            "completed": len(completed_tasks),
            "on_track": sum(1 for task in open_tasks if task.get("health_status") == HealthStatus.ON_TRACK),
            "at_risk": sum(1 for task in open_tasks if task.get("health_status") == HealthStatus.AT_RISK),
            "critical": sum(1 for task in open_tasks if task.get("health_status") == HealthStatus.CRITICAL),
            "open_total": len(open_tasks),
        }

        return {
            "health_type": "task",
            "tasks": formatted_tasks,
            "task_counts": summary,
            "summary": summary,
            "kpi_scope": (
                "story"
                if story_tasks and len(story_tasks) == len(standard_scope_tasks) and standard_scope_tasks
                else "standard"
                if standard_scope_tasks
                else "task"
            ),
            "work_item_summary": work_item_summary,
            "work_items": formatted_tasks,
        }
