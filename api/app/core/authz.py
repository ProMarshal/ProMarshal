"""Authorization helpers for project-scoped access control."""

from __future__ import annotations

from typing import Any, Dict

from bson import ObjectId
from fastapi import HTTPException, status


def _is_active_member(project: Dict[str, Any], user_id: str) -> bool:
    normalized_user_id = str(user_id or "").strip()
    if not normalized_user_id:
        return False
    if str(project.get("user_id") or "").strip() == normalized_user_id:
        return True
    members = project.get("members") or []
    if not isinstance(members, list):
        return False
    for member in members:
        if not isinstance(member, dict):
            continue
        member_user_id = str(member.get("user_id") or "").strip()
        member_status = str(member.get("status") or "").strip().lower()
        if member_user_id == normalized_user_id and member_status == "active":
            return True
    return False


async def assert_project_access(project_id: str, user_id: str, db) -> Dict[str, Any]:
    """
    Validate project access for routes that use Mongo ObjectId path params.
    """
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    try:
        object_id = ObjectId(normalized_project_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found") from exc

    project = await db.projects.find_one({"_id": object_id})
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if not _is_active_member(project, user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Project access denied")
    return project


async def assert_project_access_by_custom_id(custom_project_id: str, user_id: str, db) -> Dict[str, Any]:
    """
    Validate project access for routes that use custom project_id path params.
    """
    normalized_custom_id = str(custom_project_id or "").strip()
    if not normalized_custom_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    project = await db.projects.find_one({"project_id": normalized_custom_id})
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if not _is_active_member(project, user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Project access denied")
    return project


async def assert_project_access_any(project_ref: str, user_id: str, db) -> Dict[str, Any]:
    """
    Validate project access when route may carry ObjectId or custom project_id.
    """
    normalized_ref = str(project_ref or "").strip()
    if not normalized_ref:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    try:
        return await assert_project_access(normalized_ref, user_id, db)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_404_NOT_FOUND:
            raise
    return await assert_project_access_by_custom_id(normalized_ref, user_id, db)
