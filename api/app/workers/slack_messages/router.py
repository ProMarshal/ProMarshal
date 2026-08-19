"""API endpoints for Slack message operations"""

from fastapi import APIRouter, Depends, HTTPException, Query
from datetime import datetime, timedelta

from app.core.auth_deps import get_current_user
from app.core.authz import assert_project_access
from app.core.database import get_database
from app.core.internal_auth import require_internal_service_auth
from app.workers.slack_messages.hourly_reader import fetch_messages_for_project
from app.workers.slack_messages.storage_service import SlackMessageStorage

router = APIRouter(prefix="/api/slack-messages", tags=["slack-messages"])


@router.post("/fetch-now/{project_id}")
async def trigger_manual_fetch(
    project_id: str,
    hours_back: int = Query(0, ge=0, le=24, description="Hours back to fetch (0=current hour, 1=previous hour, etc.)"),
    current_user: dict = Depends(get_current_user),
):
    """
    Manually trigger message fetch for a project (for testing).

    Args:
        project_id: MongoDB project ID
        hours_back: Number of hours back to fetch
            - 0 = Current hour (e.g., if now is 3:45 PM, fetch 3:00-4:00 PM)
            - 1 = Previous hour (e.g., if now is 3:45 PM, fetch 2:00-3:00 PM)
            - 2+ = Earlier hours

    Returns:
        Dict with fetch stats
    """
    try:
        db = get_database()
        await assert_project_access(project_id, str(current_user.get("user_id") or ""), db)
        result = await fetch_messages_for_project(project_id, hours_back)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch messages: {str(e)}"
        )


@router.get("/{project_id}")
async def get_project_messages(
    project_id: str,
    hours: int = Query(24, ge=1, le=168, description="Get messages from last X hours (max 7 days)"),
    current_user: dict = Depends(get_current_user),
):
    """
    Retrieve stored messages for a project.
    Used for debugging or displaying message stats.

    Args:
        project_id: MongoDB project ID
        hours: Number of hours to look back (default 24, max 168/7 days)

    Returns:
        List of message documents
    """
    try:
        db = get_database()
        await assert_project_access(project_id, str(current_user.get("user_id") or ""), db)
        messages = await SlackMessageStorage.get_project_messages(
            project_id=project_id,
            hours_back=hours
        )
        return {
            "project_id": project_id,
            "hours_back": hours,
            "document_count": len(messages),
            "documents": messages
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to retrieve messages: {str(e)}"
        )


@router.get("/{project_id}/stats")
async def get_message_stats(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Get statistics about stored messages for a project.

    Args:
        project_id: MongoDB project ID

    Returns:
        Dict with message stats
    """
    try:
        db = get_database()
        await assert_project_access(project_id, str(current_user.get("user_id") or ""), db)
        stats = await SlackMessageStorage.get_message_stats(project_id)
        stats["project_id"] = project_id
        return stats
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get stats: {str(e)}"
        )


@router.delete("/cleanup/{channel_id}")
async def cleanup_channel_messages(
    channel_id: str,
    hours_old: int = Query(24, ge=1, description="Delete messages older than X hours"),
    _internal_ok: None = Depends(require_internal_service_auth),
):
    """
    Delete a specific channel's messages from documents older than X hours.

    Args:
        channel_id: Slack channel ID to clean up
        hours_old: Delete from documents older than this many hours

    Returns:
        Dict with cleanup stats
    """
    try:
        cutoff = datetime.utcnow() - timedelta(hours=hours_old)

        modified_count = await SlackMessageStorage.delete_channel_from_documents(
            channel_id=channel_id,
            older_than=cutoff
        )

        # Clean up empty documents
        deleted_count = await SlackMessageStorage.delete_empty_documents()

        return {
            "channel_id": channel_id,
            "cutoff_hours": hours_old,
            "cutoff_date": cutoff.isoformat(),
            "documents_modified": modified_count,
            "empty_documents_deleted": deleted_count
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to cleanup messages: {str(e)}"
        )


@router.post("/cleanup/old-messages")
async def cleanup_old_messages(
    days_old: int = Query(7, ge=1, le=30, description="Delete messages older than X days"),
    _internal_ok: None = Depends(require_internal_service_auth),
):
    """
    Clean up messages older than specified days across all projects.
    Removes individual channel objects, then deletes empty documents.

    Args:
        days_old: Delete messages older than this many days (default 7, max 30)

    Returns:
        Dict with cleanup stats
    """
    try:
        result = await SlackMessageStorage.cleanup_old_messages(days_old)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to cleanup old messages: {str(e)}"
        )
