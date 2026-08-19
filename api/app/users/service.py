"""User service - business logic for user operations"""

from datetime import datetime
from typing import List, Optional, Dict, Any
from app.core.database import get_database


def serialize_user(user: dict) -> dict:
    """Convert MongoDB document to serializable format"""
    if user and "_id" in user:
        user["_id"] = str(user["_id"])
    return user


class UserService:
    """Service class for user operations"""

    @staticmethod
    async def create(user_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new user"""
        db = get_database()

        user_dict = user_data.copy()
        user_dict["created_at"] = datetime.utcnow()
        user_dict["updated_at"] = datetime.utcnow()

        result = await db.users.insert_one(user_dict)
        created_user = await db.users.find_one({"_id": result.inserted_id})

        return serialize_user(created_user)

    @staticmethod
    async def get_by_id(user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by user_id"""
        db = get_database()
        user = await db.users.find_one({"_id": user_id})
        return serialize_user(user) if user else None

    @staticmethod
    async def get_by_email(email: str) -> Optional[Dict[str, Any]]:
        """Get user by email"""
        db = get_database()
        user = await db.users.find_one({"email": email})
        return serialize_user(user) if user else None

    @staticmethod
    async def get_by_user_id(user_id: str) -> Optional[Dict[str, Any]]:
        """Get user by user_id field"""
        db = get_database()
        user = await db.users.find_one({"user_id": user_id})
        return serialize_user(user) if user else None

    @staticmethod
    async def list_all(skip: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
        """List all users with pagination"""
        db = get_database()
        users = await db.users.find().skip(skip).limit(limit).to_list(length=limit)
        return [serialize_user(user) for user in users]

    @staticmethod
    async def update(user_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a user"""
        db = get_database()

        # Filter out None values
        filtered_data = {k: v for k, v in update_data.items() if v is not None}

        if filtered_data:
            filtered_data["updated_at"] = datetime.utcnow()
            await db.users.update_one({"_id": user_id}, {"$set": filtered_data})

        updated_user = await db.users.find_one({"_id": user_id})
        return serialize_user(updated_user) if updated_user else None

    @staticmethod
    async def delete(user_id: str) -> bool:
        """Delete a user"""
        db = get_database()
        result = await db.users.delete_one({"_id": user_id})
        return result.deleted_count > 0

    @staticmethod
    async def exists_by_email(email: str) -> bool:
        """Check if user with email exists"""
        db = get_database()
        user = await db.users.find_one({"email": email})
        return user is not None
