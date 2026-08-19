"""Base normalizer class for all integrations"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from datetime import datetime
from bson import ObjectId


class BaseTaskNormalizer(ABC):
    """
    Abstract base class for task normalization across different tools

    Each integration (Jira, ClickUp, Asana, etc.) must implement:
    - normalize_status()
    - normalize_priority()
    - normalize_task()
    """

    @abstractmethod
    def normalize_status(self, original_status: str) -> str:
        """
        Normalize tool-specific status to common values

        Args:
            original_status: Status from the tool (e.g., "To Do", "Open")

        Returns:
            Normalized status: "todo", "in_progress", "done", "blocked"
        """
        pass

    @abstractmethod
    def normalize_priority(self, original_priority: Optional[str]) -> str:
        """
        Normalize tool-specific priority to common values

        Args:
            original_priority: Priority from the tool (e.g., "Highest", "Urgent")

        Returns:
            Normalized priority: "low", "medium", "high", "critical"
        """
        pass

    @abstractmethod
    def normalize_task(self, raw_task: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert tool-specific task format to normalized schema

        Note: project_id is NOT included - it's implicit from the brain collection name

        Args:
            raw_task: Raw API response from the tool

        Returns:
            Normalized task document ready for MongoDB with schema:
            {
                "integration_type": str,
                "provider": str,
                "external_id": str,
                "title": str,
                "status": str,
                "assignee": Optional[dict],
                "assignee_email": Optional[str],
                "assignee_name": Optional[str],
                "priority": str,
                "due_date": Optional[datetime],
                "url": str,
                "source": str,
                "schema_version": str,
                "raw_data": dict,
                "created_at": datetime,
                "updated_at": datetime,
                "synced_at": datetime
            }
        """
        pass

    def get_integration_type(self) -> str:
        """
        Return the integration type identifier

        Returns:
            Integration type: "jira", "clickup", "asana", etc.
        """
        return self.__class__.__name__.lower().replace("normalizer", "")
