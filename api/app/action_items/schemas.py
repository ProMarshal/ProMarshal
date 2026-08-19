"""Action Items API schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


ActionItemStatus = Literal["open", "done", "cancelled"]
ActionItemSource = Literal["manual", "cadence_auto", "cortex_comment_auto", "slack_dm_extract"]
ActionItemRelatedType = Literal["work_item", "topic", "conversation", "meeting", "doc", "other"]
ActionItemDueType = Literal["today", "this_week", "no_due_date", "by_date"]


class ActionItemCreateRequest(BaseModel):
    """Create payload for an action item."""

    user_id: Optional[str] = Field(None, description="Actor user id creating the item")
    title: Optional[str] = Field(None, description="Action item title")
    owner_user_id: Optional[str] = Field(None, description="Assigned owner user id")
    related_to: Optional[str] = Field(None, description="Context/topic/task reference text")
    related_type: ActionItemRelatedType = Field("other")
    related_id: Optional[str] = Field(None, description="Optional linked id (task key/message id/etc)")
    source: ActionItemSource = Field("manual")
    due_type: ActionItemDueType = Field("no_due_date")
    due_date: Optional[datetime] = None
    target_datetime: Optional[datetime] = None
    note_color: Optional[str] = Field(
        None,
        description="Optional sticky-note color token/hex selected by user",
    )
    needs_followup: Optional[bool] = Field(
        None,
        description="Optional explicit override; service normalizes based on mandatory fields",
    )
    source_event_key: Optional[str] = Field(
        None,
        description="Optional key used for ingestion dedupe (auto-detection path)",
    )
    ingest_idempotency_key: Optional[str] = Field(
        None,
        description="Deprecated alias for source_event_key (backward compatibility)",
    )


class ActionItemUpdateRequest(BaseModel):
    """Patch payload for an action item."""

    user_id: Optional[str] = Field(None, description="Actor user id updating the item")
    title: Optional[str] = None
    owner_user_id: Optional[str] = None
    related_to: Optional[str] = None
    related_type: Optional[ActionItemRelatedType] = None
    related_id: Optional[str] = None
    status: Optional[ActionItemStatus] = None
    due_type: Optional[ActionItemDueType] = None
    due_date: Optional[datetime] = None
    target_datetime: Optional[datetime] = None
    note_color: Optional[str] = None
    needs_followup: Optional[bool] = None


class ActionItemCloseRequest(BaseModel):
    """Close payload for mark-done/cancel actions."""

    user_id: Optional[str] = Field(None, description="Actor user id closing/cancelling the item")
    status: Literal["done", "cancelled"] = Field(..., description="Terminal state")


class ActionItemListQuery(BaseModel):
    """Filter query for list endpoint."""

    user_id: str = Field(..., description="Actor user id requesting list")
    status: Optional[ActionItemStatus] = None
    owner_user_id: Optional[str] = None
    due_type: Optional[ActionItemDueType] = None
    source: Optional[ActionItemSource] = None
    needs_followup: Optional[bool] = None
    closed_window_hours: Optional[int] = Field(
        None,
        ge=1,
        le=168,
        description="Filter closed items updated in last N hours",
    )
    limit: int = Field(100, ge=1, le=500)
    offset: int = Field(0, ge=0)


class ActionItemResponse(BaseModel):
    """API response shape for action items."""

    entity_type: Literal["action_item"]
    action_item_id: str
    display_key: str
    project_id: str
    title: Optional[str] = None
    status: ActionItemStatus
    owner_user_id: Optional[str] = None
    created_by_user_id: str
    source: ActionItemSource
    related_to: Optional[str] = None
    related_type: ActionItemRelatedType
    related_id: Optional[str] = None
    due_type: ActionItemDueType
    due_date: Optional[datetime] = None
    target_datetime: Optional[datetime] = None
    note_color: Optional[str] = None
    needs_followup: bool = False
    needs_followup_set_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime] = None
    closed_by_user_id: Optional[str] = None


class ActionItemListResponse(BaseModel):
    """List response with paging metadata."""

    items: list[ActionItemResponse]
    total: int
    limit: int
    offset: int
