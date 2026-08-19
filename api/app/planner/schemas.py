"""Pydantic models for Project Planner API"""

from typing import List, Optional, Literal
from pydantic import BaseModel
from datetime import datetime


class ChatMessage(BaseModel):
    """Chat message request/response"""
    content: str
    stage: str = "goal"


class ChatResponse(BaseModel):
    """Response from PM Agent"""
    role: Literal["assistant"] = "assistant"
    content: str
    stage: str
    suggestions: Optional[List[str]] = None


class FinalizeRequest(BaseModel):
    """Request to finalize a planning stage"""
    content: List[str]  # List of points/items
    out_of_scope: Optional[List[str]] = None


class StageStatus(BaseModel):
    """Status of a planning stage"""
    stage: str
    status: Literal["pending", "in_progress", "finalized"]
    content: Optional[List[str]] = None
    finalized_at: Optional[datetime] = None


class PlannerStatus(BaseModel):
    """Overall planner status"""
    current_stage: str
    stages: dict  # {"goal": "finalized", "scope": "in_progress", ...}


class RevisionChatMessage(BaseModel):
    """Chat message for revision session"""
    content: str
    stage: str
    point_index: Optional[int] = None  # Which point is being revised


class UpdatePointRequest(BaseModel):
    """Request to update a specific point in finalized content"""
    point_index: int
    new_content: str


class UploadedDocument(BaseModel):
    """Metadata for an uploaded document"""
    file_id: str
    filename: str
    file_type: str
    upload_date: datetime
    extracted: bool = False


class DocumentExtractionRequest(BaseModel):
    """Request to extract content from uploaded documents"""
    file_ids: List[str]


class DocumentExtractionResponse(BaseModel):
    """Response from document extraction"""
    extracted_content: dict  # {"goal": [...], "scope": [...], ...}
    documents: List[UploadedDocument]


class CharterModeRequest(BaseModel):
    """Request to set/get charter mode"""
    mode: Optional[Literal["brainstorm", "document"]] = None


class DraftUpdateRequest(BaseModel):
    """Request to update draft content for a stage"""
    items: List[str]
    out_of_scope: Optional[List[str]] = None


# =============================================================================
# TOPIC REGISTRY SCHEMAS
# =============================================================================

class TopicGroup(BaseModel):
    """Topic group definition"""
    id: str
    name: str
    display_order: int
    description: str


class TopicConfig(BaseModel):
    """Topic configuration from registry"""
    id: str
    name: str
    group_id: str
    description: str
    display_order: int
    status: Literal["default", "optional", "na"]
    dependencies: List[str] = []


class ActiveTopic(BaseModel):
    """Active topic in a project"""
    topic_id: str
    name: str
    group_id: str
    status: Literal["not_started", "in_progress", "finalized"]
    is_default: bool
    dependencies: List[str] = []
    finalized_content: Optional[List[str]] = None
    updated_at: Optional[datetime] = None


class TopicRegistryResponse(BaseModel):
    """Full topic registry response"""
    groups: List[TopicGroup]
    topics: List[TopicConfig]
    project_types: List[str]
    dependencies: dict  # {topic_id: [dependent_topic_ids]}


class ProjectTopicsResponse(BaseModel):
    """Response for project's active topics"""
    project_id: str
    project_type: str
    active_topics: List[ActiveTopic]
    available_optional_topics: List[TopicConfig]


class ActivateTopicRequest(BaseModel):
    """Request to activate an optional topic"""
    topic_id: str

