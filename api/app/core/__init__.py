"""Core module - shared infrastructure components"""

from app.core.config import settings
from app.core.database import (
    get_database,
    connect_to_mongo,
    close_mongo_connection,
    get_session_index_collection,
    get_channel_index_collection,
    get_project_session_collection,
    ensure_project_session_indexes,
)
from app.core.security import encryption_service, oauth_state_service

__all__ = [
    "settings",
    "get_database",
    "connect_to_mongo",
    "close_mongo_connection",
    "get_session_index_collection",
    "get_channel_index_collection",
    "get_project_session_collection",
    "ensure_project_session_indexes",
    "encryption_service",
    "oauth_state_service",
]
