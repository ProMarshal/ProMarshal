"""Project schemas for API request/response validation"""

from datetime import datetime
from typing import Optional, Dict, Any, List, Literal
from pydantic import BaseModel, Field, EmailStr


class WorkingHours(BaseModel):
    """Represents working hours for a project or member"""
    start_time: Optional[str] = Field(None, description="Start time in HH:MM format")
    end_time: Optional[str] = Field(None, description="End time in HH:MM format")


class ProjectMember(BaseModel):
    """Embedded project member info - supports both active members and pending invites"""
    # Identity fields - user_id is None for pending invites
    user_id: Optional[str] = Field(None, max_length=100, description="User ID (set after invite accepted)")
    email: Optional[str] = Field(None, description="Email address (required for invites)")
    name: Optional[str] = Field(None, description="Display name")

    # Role and status
    role: Literal["owner", "admin", "member"] = Field("member", description="Role within the project")
    status: Literal["active", "pending", "declined", "removed"] = Field("active", description="Membership status")

    # Invite-specific fields (only for pending invites)
    invite_token: Optional[str] = Field(None, description="Unique token for invite link")
    invited_by: Optional[str] = Field(None, description="User ID who sent the invite")
    invited_at: Optional[datetime] = Field(None, description="When invite was sent")
    expires_at: Optional[datetime] = Field(None, description="When invite expires")
    reminder_count: Optional[int] = Field(0, description="Number of reminder emails sent")
    last_reminded_at: Optional[datetime] = Field(None, description="When the last reminder was sent")

    # Timestamps
    joined_at: Optional[datetime] = Field(None, description="When member joined the project")

    # Optional overrides
    timezone: Optional[str] = Field(None, description="Member-specific timezone")
    working_hours: Optional[WorkingHours] = None
    integration_ids: Optional[Dict[str, Any]] = Field(
        None,
        description="User IDs from connected integrations (e.g., jira_account_id, clickup_user_id, slack_user_id, slack_welcome_sent_at)"
    )


class ProjectCreate(BaseModel):
    """Schema for creating a project"""
    project_name: str = Field(..., min_length=5, max_length=200)
    user_id: str = Field(..., description="ID of the user who owns this project")
    type: Optional[str] = Field(None, description="Type/category of the project")
    team_size: Optional[str] = Field(None, description="Estimated team size bucket")
    tier: Optional[Literal["free", "paid"]] = Field("free", description="Project tier (free or paid)")
    timezone: Optional[str] = Field("Asia/Kolkata", description="Project timezone for reminders")
    working_hours: Optional[WorkingHours] = None
    members: List[ProjectMember] = Field(default_factory=list)
    invited_emails: List[str] = Field(default_factory=list, description="Emails invited to the project")
    # Note: tools_selected and tools_summary are NOT in ProjectCreate
    # New projects use 'integrations' field added after creation
    # These fields only exist in ProjectResponse/ProjectUpdate for backward compatibility

    class Config:
        json_schema_extra = {
            "example": {
                "project_name": "AI Project Management Tool",
                "user_id": "shiva",
                "type": "client",
                "team_size": "2-5",
                "timezone": "America/Los_Angeles",
                "working_hours": {"start_time": "09:00", "end_time": "17:00"},
                "members": [
                    {"user_id": "shiva", "role": "owner"},
                    {"user_id": "alex", "role": "developer", "timezone": "UTC"}
                ],
                "invited_emails": ["a@example.com", "b@example.com"]
            }
        }


class ProjectUpdate(BaseModel):
    """Schema for updating a project"""
    project_name: Optional[str] = Field(None, min_length=5, max_length=200)
    type: Optional[str] = Field(None, description="Type/category of the project")
    team_size: Optional[str] = Field(None, description="Estimated team size bucket")
    tier: Optional[Literal["free", "paid"]] = Field(None, description="Project tier (free or paid)")
    timezone: Optional[str] = Field(None, description="Project timezone")
    working_hours: Optional[WorkingHours] = None
    members: Optional[List[ProjectMember]] = None
    invited_emails: Optional[List[str]] = None
    tools_selected: Optional[List[str]] = None
    tools_summary: Optional[Dict[str, Any]] = Field(
        None,
        description="Non-sensitive integration summary (e.g., team/site names)"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "project_name": "Updated Project Name",
                "timezone": "UTC",
                "team_size": "6-10",
                "invited_emails": ["team@example.com"],
                "members": [
                    {"user_id": "shiva", "role": "owner"},
                    {"user_id": "alex", "role": "developer", "status": "inactive"}
                ]
            }
        }


class ProjectResponse(BaseModel):
    """Schema for project response"""
    id: str = Field(..., alias="_id")
    project_id: str
    project_name: str
    user_id: str
    type: Optional[str] = None
    team_size: Optional[str] = None
    tier: Optional[str] = "free"
    timezone: Optional[str] = None
    working_hours: Optional[WorkingHours] = None
    members: List[ProjectMember] = Field(default_factory=list)
    invited_emails: List[str] = Field(default_factory=list)
    tools_selected: Optional[List[str]] = None  # Legacy field - only for old projects
    tools_summary: Optional[Dict[str, Any]] = None  # Legacy field - only for old projects
    integrations: Optional[Dict[str, Any]] = None  # New integration structure
    workflow_capabilities: Optional[Dict[str, Any]] = None  # Workflow capabilities (sprints, epics)
    active_provider: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "_id": "507f1f77bcf86cd799439012",
                "project_id": "proj-1234-5678",
                "project_name": "AI Project Management Tool",
                "user_id": "shiva",
                "type": "client",
                "team_size": "2-5",
                "timezone": "America/Los_Angeles",
                "working_hours": {"start_time": "09:00", "end_time": "17:00"},
                "members": [
                    {"user_id": "shiva", "role": "owner"},
                    {"user_id": "alex", "role": "developer", "timezone": "UTC"}
                ],
                "invited_emails": ["a@example.com", "b@example.com"],
                "tools_selected": ["slack", "jira"],
                "tools_summary": {
                    "slack": {"team_name": "My Workspace"},
                    "jira": {"site_name": "my-site.atlassian.net"}
                },
                "integrations": {
                    "slack": {
                        "team_id": "T12345",
                        "team_name": "My Workspace",
                        "status": "connected"
                    },
                    "jira": {
                        "site_name": "my-site",
                        "cloud_id": "abc123",
                        "status": "connected"
                    }
                },
                "created_at": "2025-11-13T10:00:00Z",
                "updated_at": "2025-11-13T10:00:00Z"
            }
        }


class CadenceScheduleResponse(BaseModel):
    """Scheduler-backed cadence reminder settings for a project."""
    enabled: bool = Field(True, description="Whether cadence reminders are enabled")
    time: str = Field("09:00", description="Cadence reminder time in HH:MM format")
    skip_weekends: bool = Field(False, description="Skip cadence reminders on Saturday/Sunday in project timezone")
    ignore_followup_for_owner: bool = Field(
        False,
        description="Suppress cadence reminders/follow-ups for project owner",
    )


class CadenceScheduleUpdate(BaseModel):
    """Update payload for scheduler-backed cadence reminder settings."""
    enabled: bool = Field(..., description="Whether cadence reminders are enabled")
    time: str = Field(..., description="Cadence reminder time in HH:MM format")
    skip_weekends: bool = Field(False, description="Skip cadence reminders on Saturday/Sunday in project timezone")
    ignore_followup_for_owner: bool = Field(
        False,
        description="Suppress cadence reminders/follow-ups for project owner",
    )


class ActionItemDigestScheduleResponse(BaseModel):
    """Scheduler-backed action-item digest settings for a project."""
    enabled: bool = Field(True, description="Whether action-item digest reminders are enabled")
    time: str = Field("12:30", description="Action-item digest time in HH:MM format")
    skip_weekends: bool = Field(False, description="Skip action-item digest on Saturday/Sunday in project timezone")
    ignore_followup_for_owner: bool = Field(
        False,
        description="Suppress owner-targeted follow-up escalation reminders for unresolved follow-up items",
    )


class ActionItemDigestScheduleUpdate(BaseModel):
    """Update payload for scheduler-backed action-item digest settings."""
    enabled: bool = Field(..., description="Whether action-item digest reminders are enabled")
    time: str = Field(..., description="Action-item digest time in HH:MM format")
    skip_weekends: bool = Field(False, description="Skip action-item digest on Saturday/Sunday in project timezone")
    ignore_followup_for_owner: bool = Field(
        False,
        description="Suppress owner-targeted follow-up escalation reminders for unresolved follow-up items",
    )


# ==================== INVITE SCHEMAS ====================

class InviteCreate(BaseModel):
    """Schema for creating an invite"""
    email: EmailStr = Field(..., description="Email address to invite")
    role: Literal["admin", "member"] = Field("member", description="Role for the invited member")
    invited_by: str = Field(..., description="User ID of the person sending the invite")
    jira_account_id: Optional[str] = Field(None, description="Jira account ID to link tasks to this member")

    class Config:
        json_schema_extra = {
            "example": {
                "email": "alice@example.com",
                "role": "member",
                "invited_by": "member1",
                "jira_account_id": "712020:93e32206-..."
            }
        }


class BulkInviteCreate(BaseModel):
    """Schema for creating multiple invites at once"""
    emails: List[EmailStr] = Field(..., description="List of email addresses to invite")
    role: Literal["admin", "member"] = Field("member", description="Role for all invitees")
    invited_by: str = Field(..., description="User ID of the person sending invites")

    class Config:
        json_schema_extra = {
            "example": {
                "emails": ["alice@example.com", "bob@example.com"],
                "role": "member",
                "invited_by": "member1"
            }
        }


class InviteAccept(BaseModel):
    """Schema for accepting an invite"""
    user_id: str = Field(..., description="User ID of the accepting user")
    name: str = Field(..., description="Display name of the accepting user")
    email: EmailStr = Field(..., description="Email of the accepting user (must match invite)")

    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "alice123",
                "name": "Alice Smith",
                "email": "alice@example.com"
            }
        }


class InviteVerifyResponse(BaseModel):
    """Schema for invite verification response"""
    valid: bool
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    invited_by_name: Optional[str] = None
    role: Optional[str] = None
    email: Optional[str] = None
    expired: bool = False
    already_accepted: bool = False

    class Config:
        json_schema_extra = {
            "example": {
                "valid": True,
                "project_id": "507f1f77bcf86cd799439012",
                "project_name": "Website Redesign",
                "invited_by_name": "Prabhu Raj",
                "role": "member",
                "email": "alice@example.com",
                "expired": False,
                "already_accepted": False
            }
        }


class MemberRoleUpdate(BaseModel):
    """Schema for updating a member's role"""
    role: Literal["owner", "member"] = Field(..., description="New role for the member")

    class Config:
        json_schema_extra = {
            "example": {
                "role": "owner"
            }
        }


class ActiveProviderUpdate(BaseModel):
    """Schema for explicit active provider updates."""

    user_id: str = Field(..., description="User ID requesting provider switch")
    active_provider: Optional[str] = Field(
        None,
        description="Provider key to set active (or null to unset)",
    )


class RecommendationFeedbackRequest(BaseModel):
    """Schema for recommendation feedback events."""

    task_key: str = Field(..., min_length=1, max_length=200, description="Task key")
    action: Literal["accepted", "dismissed", "completed"] = Field(
        ...,
        description="Feedback action",
    )
    recommendation_id: Optional[str] = Field(
        None,
        max_length=200,
        description="Optional recommendation identifier",
    )
    source: Optional[str] = Field(
        "api",
        max_length=100,
        description="Origin surface for this feedback event",
    )
    actor_user_id: Optional[str] = Field(
        None,
        max_length=200,
        description="Optional actor user id",
    )


class RecommendationThresholdsUpdateRequest(BaseModel):
    """Schema for recommendation threshold override updates."""

    overload_threshold: Optional[float] = Field(
        None,
        ge=0.0,
        le=100.0,
        description="R6 overload threshold score (0-100)",
    )
    due_soon_hours: Optional[int] = Field(
        None,
        ge=1,
        le=336,
        description="Due-soon window in hours",
    )
    sprint_end_soon_hours: Optional[int] = Field(
        None,
        ge=1,
        le=336,
        description="Sprint-end-soon window in hours",
    )
    sprint_open_critical_threshold: Optional[int] = Field(
        None,
        ge=1,
        le=20,
        description="Open critical work-item threshold for sprint risk",
    )
    clear_overrides: bool = Field(
        False,
        description="When true, remove all project-level threshold overrides",
    )
