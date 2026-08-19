"""User API routes"""

from fastapi import APIRouter, Depends, HTTPException, status
from typing import List

from app.core.auth_deps import get_current_user
from app.core.internal_auth import require_internal_service_auth
from app.users.schemas import UserCreate, UserUpdate, UserResponse
from app.users.service import UserService

router = APIRouter(prefix="/api/users", tags=["users"])


@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    user: UserCreate,
    _internal_ok: None = Depends(require_internal_service_auth),
):
    """Create a new user"""
    # Check if user with email already exists
    if await UserService.exists_by_email(user.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User with this email already exists"
        )

    created_user = await UserService.create(user.model_dump())
    return UserResponse(**created_user)


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Get a user by ID"""
    actor_user_id = str(current_user.get("user_id") or "").strip()
    if actor_user_id != str(user_id or "").strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only access your own user record",
        )
    user = await UserService.get_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    return UserResponse(**user)


@router.get("/email/{email}", response_model=UserResponse)
async def get_user_by_email(
    email: str,
    _internal_ok: None = Depends(require_internal_service_auth),
):
    """Get a user by email"""
    user = await UserService.get_by_email(email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    return UserResponse(**user)


@router.get("/", response_model=List[UserResponse])
async def list_users(
    skip: int = 0,
    limit: int = 100,
    _internal_ok: None = Depends(require_internal_service_auth),
):
    """List all users"""
    users = await UserService.list_all(skip, limit)
    return [UserResponse(**user) for user in users]


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    user_update: UserUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Update a user"""
    actor_user_id = str(current_user.get("user_id") or "").strip()
    if actor_user_id != str(user_id or "").strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only update your own user record",
        )
    # Check if user exists
    existing_user = await UserService.get_by_id(user_id)
    if not existing_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )

    updated_user = await UserService.update(user_id, user_update.model_dump())
    return UserResponse(**updated_user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Delete a user"""
    actor_user_id = str(current_user.get("user_id") or "").strip()
    if actor_user_id != str(user_id or "").strip():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own user record",
        )
    deleted = await UserService.delete(user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    return None
