"""Action Items API routes."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.core.auth_deps import get_current_user
from app.core.authz import assert_project_access_any
from app.core.database import get_database
from app.action_items.schemas import (
    ActionItemCloseRequest,
    ActionItemCreateRequest,
    ActionItemListQuery,
    ActionItemListResponse,
    ActionItemResponse,
    ActionItemUpdateRequest,
)
from app.action_items.service import ActionItemService


router = APIRouter(prefix="/api/projects", tags=["action-items"])


async def _require_action_items_access(
    project_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
    db = get_database()
    actor_user_id = str(current_user.get("user_id") or "").strip()
    await assert_project_access_any(project_id, actor_user_id, db)
    return current_user


@router.post("/{project_id}/action-items", response_model=ActionItemResponse, status_code=status.HTTP_201_CREATED)
async def create_action_item(
    project_id: str,
    payload: ActionItemCreateRequest,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    current_user: dict = Depends(_require_action_items_access),
):
    """Create a new action item for a project."""
    try:
        actor_user_id = str(current_user.get("user_id") or "").strip()
        hinted_user_id = str(payload.user_id or "").strip()
        if hinted_user_id and hinted_user_id != actor_user_id:
            raise PermissionError("Actor identity mismatch between token and request payload")
        if x_user_id and str(x_user_id).strip() != actor_user_id:
            raise PermissionError("Actor identity mismatch between token and request headers")
        payload_dict = payload.model_dump()
        payload_dict["user_id"] = actor_user_id
        created = await ActionItemService.create(project_id, payload_dict)
        return ActionItemResponse(**created)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create action item: {exc}",
        )


@router.get("/{project_id}/action-items", response_model=ActionItemListResponse)
async def list_action_items(
    project_id: str,
    user_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    owner_user_id: Optional[str] = None,
    due_type: Optional[str] = None,
    source: Optional[str] = None,
    needs_followup: Optional[bool] = None,
    closed_window_hours: Optional[int] = Query(None, ge=1, le=168),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    current_user: dict = Depends(_require_action_items_access),
):
    """List action items for a project with role-aware visibility."""

    try:
        actor_user_id = str(current_user.get("user_id") or "").strip()
        if user_id and str(user_id).strip() != actor_user_id:
            raise PermissionError("Actor identity mismatch between token and query param")
        if x_user_id and str(x_user_id).strip() != actor_user_id:
            raise PermissionError("Actor identity mismatch between token and request headers")
        filters = ActionItemListQuery(
            user_id=actor_user_id,
            status=status_filter,
            owner_user_id=owner_user_id,
            due_type=due_type,
            source=source,
            needs_followup=needs_followup,
            closed_window_hours=closed_window_hours,
            limit=limit,
            offset=offset,
        )
        result = await ActionItemService.list(project_id, filters.model_dump())
        return ActionItemListResponse(
            items=[ActionItemResponse(**item) for item in result["items"]],
            total=int(result["total"]),
            limit=int(result["limit"]),
            offset=int(result["offset"]),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list action items: {exc}",
        )


@router.patch("/{project_id}/action-items/{action_item_id}", response_model=ActionItemResponse)
async def update_action_item(
    project_id: str,
    action_item_id: str,
    payload: ActionItemUpdateRequest,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    current_user: dict = Depends(_require_action_items_access),
):
    """Update an action item."""
    try:
        actor_user_id = str(current_user.get("user_id") or "").strip()
        hinted_user_id = str(payload.user_id or "").strip()
        if hinted_user_id and hinted_user_id != actor_user_id:
            raise PermissionError("Actor identity mismatch between token and request payload")
        if x_user_id and str(x_user_id).strip() != actor_user_id:
            raise PermissionError("Actor identity mismatch between token and request headers")
        # Preserve partial PATCH semantics. Unset optional fields must not be treated as explicit null clears.
        payload_dict = payload.model_dump(exclude_unset=True)
        payload_dict["user_id"] = actor_user_id
        updated = await ActionItemService.update(project_id, action_item_id, payload_dict)
        return ActionItemResponse(**updated)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update action item: {exc}",
        )


@router.post("/{project_id}/action-items/{action_item_id}/close", response_model=ActionItemResponse)
async def close_action_item(
    project_id: str,
    action_item_id: str,
    payload: ActionItemCloseRequest,
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    current_user: dict = Depends(_require_action_items_access),
):
    """Close/cancel an action item."""
    try:
        actor_user_id = str(current_user.get("user_id") or "").strip()
        hinted_user_id = str(payload.user_id or "").strip()
        if hinted_user_id and hinted_user_id != actor_user_id:
            raise PermissionError("Actor identity mismatch between token and request payload")
        if x_user_id and str(x_user_id).strip() != actor_user_id:
            raise PermissionError("Actor identity mismatch between token and request headers")
        payload_dict = payload.model_dump()
        payload_dict["user_id"] = actor_user_id
        updated = await ActionItemService.close(project_id, action_item_id, payload_dict)
        return ActionItemResponse(**updated)
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to close action item: {exc}",
        )


@router.delete("/{project_id}/action-items/{action_item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_action_item(
    project_id: str,
    action_item_id: str,
    user_id: Optional[str] = Query(None),
    x_user_id: Optional[str] = Header(None, alias="X-User-Id"),
    current_user: dict = Depends(_require_action_items_access),
):
    """Delete an action item (project owner only)."""
    try:
        actor_user_id = str(current_user.get("user_id") or "").strip()
        if user_id and str(user_id).strip() != actor_user_id:
            raise PermissionError("Actor identity mismatch between token and query param")
        if x_user_id and str(x_user_id).strip() != actor_user_id:
            raise PermissionError("Actor identity mismatch between token and request headers")
        await ActionItemService.delete(
            project_id,
            action_item_id,
            {"user_id": actor_user_id},
        )
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete action item: {exc}",
        )
