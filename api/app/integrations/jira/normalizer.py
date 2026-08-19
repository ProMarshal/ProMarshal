"""Jira task normalization to common schema"""
import re
from typing import Dict, Any, Optional
from datetime import datetime
from bson import ObjectId
from app.domain.work_items.types import classify_work_item_type
from app.integrations.base.normalizer import BaseTaskNormalizer



class JiraNormalizer(BaseTaskNormalizer):
    """Normalize Jira tasks to common schema"""

    def __init__(self, cloud_id: str, site_url: str):
        """
        Initialize Jira normalizer

        Args:
            cloud_id: Jira cloud ID
            site_url: Jira site URL (e.g., https://yourcompany.atlassian.net)
        """
        self.cloud_id = cloud_id
        self.site_url = site_url

    def normalize_status(self, jira_status: str) -> str:
        """
        Normalize Jira status to common values

        Args:
            jira_status: Original Jira status (e.g., "To Do", "In Progress")

        Returns:
            Normalized status: "todo", "in_progress", "done", "blocked"
        """
        status_lower = jira_status.lower()

        # Todo variants
        if status_lower in ["to do", "todo", "open", "backlog", "new"]:
            return "todo"

        # In Progress variants
        if status_lower in ["in progress", "in-progress", "inprogress", "doing", "in development", "in review"]:
            return "in_progress"

        # Done variants
        if status_lower in ["done", "closed", "resolved", "complete", "completed", "finished"]:
            return "done"

        # Blocked variants
        if status_lower in ["blocked", "on hold", "waiting", "paused"]:
            return "blocked"

        # Default: if unknown, treat as in_progress
        return "in_progress"

    def normalize_priority(self, jira_priority: Optional[str]) -> str:
        """
        Normalize Jira priority to common values

        Args:
            jira_priority: Original Jira priority (e.g., "Highest", "Medium")

        Returns:
            Normalized priority: "low", "medium", "high", "critical"
        """
        if not jira_priority:
            return "medium"

        priority_lower = jira_priority.lower()

        # Critical/Highest
        if priority_lower in ["critical", "highest", "blocker"]:
            return "critical"

        # High
        if priority_lower in ["high", "major"]:
            return "high"

        # Low
        if priority_lower in ["low", "lowest", "minor", "trivial"]:
            return "low"

        # Medium (default)
        return "medium"

    def normalize_task(self, raw_task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert Jira task to normalized schema

        Note: project_id is NOT stored in tasks - it's implicit from the brain collection name

        Args:
            raw_task: Raw Jira API response

        Returns:
            Normalized task document ready for MongoDB
        """
        fields = raw_task.get("fields", {})
        task_key = raw_task.get("key")

        # Extract assignee info
        assignee = fields.get("assignee")
        assignee_email = assignee.get("emailAddress") if assignee else None
        assignee_name = assignee.get("displayName") if assignee else None
        assignee_account_id = assignee.get("accountId") if assignee else None

        # Extract issue type and parent
        issuetype = fields.get("issuetype", {})
        issue_type = issuetype.get("name")  # "Epic", "Story", "Subtask", "Task", "Bug"
        provider_type = str(issue_type or "").strip() or None
        work_item_type = self._classify_work_item_type(provider_type)
        parent = fields.get("parent", {})
        parent_key = parent.get("key") if parent else None

        # Extract priority
        priority_obj = fields.get("priority")
        jira_priority = priority_obj.get("name") if priority_obj else None

        # Extract status
        status_obj = fields.get("status", {})
        jira_status = status_obj.get("name", "To Do")

        # Build Jira task URL
        url = f"{self.site_url}/browse/{task_key}"

        # Extract due date
        due_date_str = fields.get("duedate")
        due_date = None
        if due_date_str:
            try:
                due_date = datetime.fromisoformat(due_date_str)
            except (ValueError, TypeError):
                due_date = None

        # Extract optional start date.
        # Jira field name can vary by workspace/customization.
        start_date = self._extract_start_date(fields)
        provider_updated_at = self._parse_jira_datetime(fields.get("updated"))

        # Extract description (plain string on Jira Server; ADF object on Jira Cloud)
        description = self._extract_description(fields.get("description"))

        # Extract comments
        comments = self._extract_comments(fields.get("comment", {}))

        # Extract status changed date from Jira (used by sync path as initial value)
        # Jira returns format like "2026-02-19T10:26:17.366+0530" — timezone offset has no colon
        # which Python 3.10 fromisoformat() does not support. Normalize before parsing.
        status_changed_at_str = fields.get("statuscategorychangedate")
        status_changed_at = None
        if status_changed_at_str:
            try:
                normalized = re.sub(r'([+-])(\d{2})(\d{2})$', r'\1\2:\3', status_changed_at_str.replace("Z", "+00:00"))
                status_changed_at = datetime.fromisoformat(normalized)
            except (ValueError, TypeError):
                status_changed_at = None

        # Extract sprint information
        # Sprint field ID varies by Jira instance (e.g., customfield_10020, customfield_10001)
        # Try multiple possible field IDs
        sprint_data = None
        for field_key in ["customfield_10020", "customfield_10001", "customfield_10010", "sprint"]:
            if fields.get(field_key):
                sprint_data = fields.get(field_key)
                break

        sprint_name = None
        sprint_state = None
        sprint_id = None
        sprint_start_date = None
        sprint_end_date = None

        if sprint_data:
            # Handle single sprint object
            if isinstance(sprint_data, dict):
                sprint_name = sprint_data.get("name")
                sprint_state = sprint_data.get("state")  # "active", "future", "closed"
                sprint_id = sprint_data.get("id")
                sprint_start_date = self._parse_sprint_date(sprint_data.get("startDate"))
                sprint_end_date = self._parse_sprint_date(sprint_data.get("endDate"))
            # Handle array of sprints (task moved between sprints)
            elif isinstance(sprint_data, list) and len(sprint_data) > 0:
                # Prefer active sprint, fall back to most recent
                active_sprint = next(
                    (s for s in sprint_data if s.get("state") == "active"),
                    sprint_data[-1]  # Most recent sprint if no active
                )
                sprint_name = active_sprint.get("name")
                sprint_state = active_sprint.get("state")
                sprint_id = active_sprint.get("id")
                sprint_start_date = self._parse_sprint_date(active_sprint.get("startDate"))
                sprint_end_date = self._parse_sprint_date(active_sprint.get("endDate"))

        normalized_status = self.normalize_status(jira_status)
        assignee_envelope = None
        if assignee_email or assignee_name or assignee_account_id:
            assignee_envelope = {
                "email": assignee_email,
                "name": assignee_name,
                "account_id": assignee_account_id,
            }

        return {
            "integration_type": "jira",
            "provider": "jira",
            "entity_type": "work_item",
            "external_id": task_key,
            "title": fields.get("summary", ""),
            "provider_type": provider_type,
            "work_item_type": work_item_type,
            "issue_type": issue_type,
            "parent_key": parent_key,
            "status": normalized_status,
            "status_raw": jira_status,
            "status_display": jira_status,
            "is_closed": normalized_status == "done",
            "assignee": assignee_envelope,
            "assignee_email": assignee_email,
            "assignee_name": assignee_name,
            "assignee_account_id": assignee_account_id,
            "priority": self.normalize_priority(jira_priority),
            "due_date": due_date,
            "start_date": start_date,
            "url": url,
            "source": "jira",
            "schema_version": "1.0",
            "provider_updated_at": provider_updated_at,
            "description": description,
            "comments": comments,
            "sprint": sprint_name,
            "sprint_state": sprint_state,
            "sprint_id": sprint_id,
            "sprint_start_date": sprint_start_date,
            "sprint_end_date": sprint_end_date,
            #"raw_data": raw_task,  # Store complete original response
            "status_changed_at": status_changed_at,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "synced_at": datetime.utcnow()
        }

    def _classify_work_item_type(self, provider_type: Optional[str]) -> str:
        return classify_work_item_type(provider_type)

    def _parse_jira_datetime(self, raw_value: Any) -> Optional[datetime]:
        normalized = str(raw_value or "").strip()
        if not normalized:
            return None
        try:
            candidate = re.sub(r'([+-])(\d{2})(\d{2})$', r'\1\2:\3', normalized.replace("Z", "+00:00"))
            return datetime.fromisoformat(candidate)
        except (ValueError, TypeError):
            return None

    def _extract_description(self, raw: Any) -> Optional[str]:
        """Extract plain text from Jira description.

        Jira Cloud returns Atlassian Document Format (ADF) as a dict.
        Jira Server returns a plain string. Returns None if empty or absent.
        """
        if not raw:
            return None
        if isinstance(raw, str):
            return raw.strip() or None
        if isinstance(raw, dict):
            # ADF: recursively collect all text node values
            parts: list = []
            self._collect_adf_text(raw, parts)
            text = " ".join(parts).strip()
            return text or None
        return None

    def _collect_adf_text(self, node: Any, parts: list) -> None:
        """Recursively walk an ADF node tree and collect text values."""
        if not isinstance(node, dict):
            return
        if node.get("type") == "text":
            value = str(node.get("text") or "").strip()
            if value:
                parts.append(value)
        for child in node.get("content") or []:
            self._collect_adf_text(child, parts)

    def _extract_comments(self, comment_obj: Dict[str, Any]) -> list:
        """
        Extract and normalize comments from Jira comment object

        Args:
            comment_obj: Jira comment object

        Returns:
            List of normalized comments (latest 20)
        """
        comments = comment_obj.get("comments", [])

        normalized_comments = []

        # Get latest 20 comments
        for comment in comments[-20:]:
            # Extract author info
            author = comment.get("author", {})
            author_name = author.get("displayName", "Unknown")
            author_account_id = author.get("accountId")

            # Extract comment body (Jira uses Atlassian Document Format)
            body = comment.get("body", {})

            # Simple text extraction from ADF (Atlassian Document Format)
            comment_text = self._extract_text_from_adf(body)

            # Extract timestamps
            created = comment.get("created")
            updated = comment.get("updated")

            normalized_comments.append({
                "id": comment.get("id"),
                "author_name": author_name,
                "author_account_id": author_account_id,
                "text": comment_text,
                "created_at": created,
                "updated_at": updated
            })

        return normalized_comments

    def _extract_text_from_adf(self, adf_body: Dict[str, Any]) -> str:
        """
        Extract plain text from Atlassian Document Format (ADF)

        Args:
            adf_body: ADF document object

        Returns:
            Plain text string
        """
        if not adf_body:
            return ""

        text_parts = []

        def extract_content(node):
            """Recursively extract text from ADF nodes"""
            if isinstance(node, dict):
                # If node has text, add it
                if "text" in node:
                    text_parts.append(node["text"])

                # Recurse into content
                if "content" in node:
                    for child in node.get("content", []):
                        extract_content(child)

            elif isinstance(node, list):
                for item in node:
                    extract_content(item)

        extract_content(adf_body)

        return " ".join(text_parts).strip()

    def _parse_sprint_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """
        Parse sprint date from Jira format to datetime

        Args:
            date_str: ISO format date string from Jira (e.g., "2026-02-10T09:00:00.000Z")

        Returns:
            Parsed datetime object or None if invalid/missing
        """
        if not date_str:
            return None

        try:
            # Jira sprint dates are in ISO format with Z timezone
            # Normalize timezone format for Python's fromisoformat
            normalized = re.sub(r'([+-])(\d{2})(\d{2})$', r'\1\2:\3', date_str.replace("Z", "+00:00"))
            return datetime.fromisoformat(normalized)
        except (ValueError, TypeError):
            return None

    def _parse_jira_date(self, value: Any) -> Optional[datetime]:
        """Parse Jira date/date-time values."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return None
            try:
                # Date-only format (YYYY-MM-DD)
                if len(raw) == 10 and raw.count("-") == 2:
                    return datetime.fromisoformat(raw)
                normalized = re.sub(r'([+-])(\d{2})(\d{2})$', r'\1\2:\3', raw.replace("Z", "+00:00"))
                return datetime.fromisoformat(normalized)
            except (ValueError, TypeError):
                return None
        return None

    def _extract_start_date(self, fields: Dict[str, Any]) -> Optional[datetime]:
        """
        Extract a start date from known/default Jira fields.
        Falls back gracefully when field is unavailable.
        """
        candidate_keys = [
            "startdate",
            "startDate",
            "customfield_10015",
            "customfield_10016",
            "customfield_10017",
            "customfield_10018",
            "customfield_10019",
            "customfield_10021",
            "customfield_10022",
        ]

        for key in candidate_keys:
            raw = fields.get(key)
            if raw is None:
                continue
            if isinstance(raw, list):
                for entry in raw:
                    parsed = self._parse_jira_date(entry)
                    if parsed:
                        return parsed
                continue
            if isinstance(raw, dict):
                parsed = self._parse_jira_date(raw.get("startDate"))
                if parsed:
                    return parsed
                continue
            parsed = self._parse_jira_date(raw)
            if parsed:
                return parsed

        return None
