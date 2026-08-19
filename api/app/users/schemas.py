"""User schemas for API request/response validation"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    """Schema for creating a user"""
    user_id: str = Field(..., min_length=1, max_length=100, description="Username extracted from email")
    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr
    license_tier: Optional[str] = Field(None, description="Per-user license tier")
    slack_id: Optional[str] = Field(None, description="Slack user ID")
    jira_id: Optional[str] = Field(None, description="Jira account ID")

    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "shiva",
                "name": "Shiva Kumar",
                "email": "shiva@gmail.com",
                "license_tier": "pro",
                "slack_id": "U0123456",
                "jira_id": "abc-123"
            }
        }


class UserUpdate(BaseModel):
    """Schema for updating a user"""
    user_id: Optional[str] = Field(None, min_length=1, max_length=100)
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    email: Optional[EmailStr] = None
    license_tier: Optional[str] = Field(None, description="Per-user license tier")
    slack_id: Optional[str] = Field(None, description="Slack user ID")
    jira_id: Optional[str] = Field(None, description="Jira account ID")

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Shiva Updated",
                "license_tier": "enterprise"
            }
        }


class UserResponse(BaseModel):
    """Schema for user response"""
    id: str = Field(..., alias="_id")
    user_id: str
    name: str
    email: EmailStr
    license_tier: Optional[str] = None
    slack_id: Optional[str] = None
    jira_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "_id": "507f1f77bcf86cd799439011",
                "user_id": "shiva",
                "name": "Shiva Kumar",
                "email": "shiva@gmail.com",
                "license_tier": "pro",
                "slack_id": "U0123456",
                "jira_id": "abc-123",
                "created_at": "2025-11-13T10:00:00Z",
                "updated_at": "2025-11-13T10:00:00Z"
            }
        }
