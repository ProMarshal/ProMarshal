"""Platform-agnostic data transfer objects for task operations"""

from enum import Enum
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class SprintDirective(str, Enum):
    """Sprint placement directive for write operations."""

    AUTO = "auto"
    ACTIVE_SPRINT = "active_sprint"
    BACKLOG = "backlog"


class SprintPlacementResultDTO(BaseModel):
    """Outcome metadata for optional sprint placement after Jira writes."""

    attempted: bool = False
    applied: bool = False
    directive: Optional[SprintDirective] = None
    reason: Optional[str] = None
    board_id: Optional[int] = None
    board_type: Optional[str] = None
    sprint_id: Optional[int] = None
    sprint_name: Optional[str] = None
    error: Optional[str] = None


class TaskAssigneeDTO(BaseModel):
    """Canonical assignee envelope."""

    email: Optional[str] = None
    name: Optional[str] = None
    account_id: Optional[str] = None


class TaskResponseDTO(BaseModel):
    """Interface-agnostic task response"""
    external_id: str  # PROJ-123 (Jira issue key)
    provider: str = "jira"
    title: str
    status: str
    status_raw: Optional[str] = None
    status_display: Optional[str] = None
    description: Optional[str] = None  # Task description (plain text, absent if not set in Jira)
    assignee: Optional[TaskAssigneeDTO] = None
    is_closed: Optional[bool] = None
    assignee_email: Optional[str] = None  # Some Jira users don't have emails
    assignee_name: Optional[str] = None   # Assignee might be unassigned
    priority: Optional[str] = None
    provider_type: Optional[str] = None  # Provider-native type label (Story, Epic, Bug, Issue, ...)
    due_date: Optional[datetime] = None
    start_date: Optional[datetime] = None
    url: str
    source: str = "jira"
    schema_version: str = "1.0"
    created_at: datetime
    updated_at: datetime


class ListTasksFilters(BaseModel):
    """Filters for listing tasks"""
    assignee_email: Optional[str] = None
    assignee_user_id: Optional[str] = None
    assignee_name_exact: Optional[str] = None
    # Backward-compatible alias field retained for Jira implementations.
    assignee_account_id: Optional[str] = None
    status_filter: Optional[List[str]] = None  # e.g., ["todo", "in_progress"]
    provider_types: Optional[List[str]] = None  # Provider-native type labels (e.g., ["Story", "Epic"])
    due_date_mode: Optional[str] = None  # any | missing | present
    is_closed: Optional[bool] = None
    limit: int = 50


class CreateTaskDTO(BaseModel):
    """Interface-agnostic task creation request"""
    title: str
    description: Optional[str] = None
    assignee_account_id: Optional[str] = None  # Jira account ID (optional for unassigned create)
    provider_type: Optional[str] = None  # Provider-native type label (e.g., Story, Epic, Bug, Sub-task)
    work_item_type: Optional[str] = None  # Canonical type hint (portfolio|standard|defect|subtask|service|custom)
    parent_external_id: Optional[str] = None  # Parent key/reference for child types (e.g., Jira Sub-task)
    priority: str = "medium"  # low, medium, high, critical
    project_id: str  # Custom project_id (proj-XXXX-YYYY)
    sprint_directive: Optional[SprintDirective] = None


class UpdateTaskDTO(BaseModel):
    """Interface-agnostic task update request"""
    task_id: str  # External ID (e.g., PROJ-123)
    status: Optional[str] = None  # todo, in_progress, in_review, done
    transition_id: Optional[str] = None
    comment: Optional[str] = None
    description: Optional[str] = None
    assignee_account_id: Optional[str] = None
    parent_external_id: Optional[str] = None
    due_date: Optional[datetime] = None
    start_date: Optional[datetime] = None
    clear_due_date: bool = False
    clear_start_date: bool = False
    project_id: str
    sprint_directive: Optional[SprintDirective] = None


class TaskMutationResultDTO(BaseModel):
    """Result contract for shared task mutation path."""
    task: TaskResponseDTO
    status_updated: bool = False
    comment_added: bool = False
    posted_comment_text: Optional[str] = None
    description_updated: bool = False
    assignee_updated: bool = False
    parent_updated: bool = False
    due_date_updated: bool = False
    start_date_updated: bool = False
    brain_sync_succeeded: bool = True
    sprint_placement: Optional[SprintPlacementResultDTO] = None


class TaskCreateResultDTO(BaseModel):
    """Result contract for shared task creation path."""
    task: TaskResponseDTO
    brain_sync_succeeded: bool = True
    sprint_placement: Optional[SprintPlacementResultDTO] = None
