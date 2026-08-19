"""MongoDB database connection management"""

import random
from typing import Any, Dict, List, Optional, Set, Tuple
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase, AsyncIOMotorCollection
from pymongo.errors import OperationFailure
from app.core.config import settings

# MongoDB client instance
client: AsyncIOMotorClient = None
database: AsyncIOMotorDatabase = None
brain_db: AsyncIOMotorDatabase = None
_DATABASE_INDEXES_READY: bool = False
_CLIENT_RUNTIME_OWNER: Optional[str] = None


SESSION_ENTITY_TYPES = ("cadence_session", "cortex_session", "team_poll_session")
_PROJECT_SESSION_INDEXES_READY: Set[str] = set()
CADENCE_DAILY_SUMMARY_ENTITY_TYPE = "cadence_daily_summary"
_PROJECT_CADENCE_SUMMARY_INDEXES_READY: Set[str] = set()
ACTION_ITEM_ENTITY_TYPE = "action_item"
_PROJECT_ACTION_ITEM_INDEXES_READY: Set[str] = set()


async def connect_to_mongo(runtime_owner: Optional[str] = None):
    """
    Establish connection to MongoDB with connection pooling.

    Connection Pool Settings:
    - maxPoolSize: Maximum connections in pool
    - minPoolSize: Minimum connections to keep alive
    - maxIdleTimeMS: Close idle connections after 10 minutes
    - serverSelectionTimeoutMS: Fail fast if server unreachable
    """
    global client, database, brain_db, _DATABASE_INDEXES_READY, _CLIENT_RUNTIME_OWNER
    try:
        client = AsyncIOMotorClient(
            settings.mongodb_uri,
            maxPoolSize=settings.mongodb_max_pool_size,
            minPoolSize=settings.mongodb_min_pool_size,
            maxIdleTimeMS=settings.mongodb_max_idle_time_ms,
            serverSelectionTimeoutMS=settings.mongodb_server_selection_timeout_ms,
            connectTimeoutMS=settings.mongodb_connect_timeout_ms,
            socketTimeoutMS=settings.mongodb_socket_timeout_ms,
        )
        database = client.get_default_database()
        brain_db = client[settings.brain_db_name]  # Brain database for project-specific data

        # Test the connection
        await client.admin.command('ping')
        _CLIENT_RUNTIME_OWNER = str(runtime_owner or "").strip() or None
        print(f"Connected to MongoDB: {database.name}")
        print(f"Brain database: {settings.brain_db_name}")
        print(
            "Connection pool: "
            f"min={settings.mongodb_min_pool_size}, "
            f"max={settings.mongodb_max_pool_size}, "
            f"idle_timeout_ms={settings.mongodb_max_idle_time_ms}"
        )

        # Initialize database indexes once per process lifecycle.
        if not _DATABASE_INDEXES_READY:
            await init_database_indexes()
            _DATABASE_INDEXES_READY = True
    except Exception as e:
        print(f"Could not connect to MongoDB: {e}")
        raise


async def init_database_indexes():
    """Initialize database indexes for collections"""
    try:
        # OTPs collection indexes
        # TTL index for auto-deletion of expired OTPs
        await database.otps.create_index(
            [("expires_at", 1)],
            expireAfterSeconds=0,
            name="otp_ttl_index"
        )
        
        # Query performance index for OTP lookups
        await database.otps.create_index(
            [("email", 1), ("created_at", -1)],
            name="otp_email_created_index"
        )
        
        # Projects collection - unique index on project_id
        await database.projects.create_index(
            [("project_id", 1)],
            unique=True,
            name="project_id_unique_index"
        )

        # Slack resolver hot-path indexes for multi-project workspace routing.
        await database.projects.create_index(
            [
                ("integrations.slack.team_id", 1),
                ("integrations.slack.status", 1),
                ("project_id", 1),
            ],
            name="projects_slack_team_status_project_idx",
        )
        await database.projects.create_index(
            [
                ("integrations.slack.team_id", 1),
                ("integrations.slack.status", 1),
                ("integrations.slack.selected_channels.channel_id", 1),
            ],
            name="projects_slack_team_status_channel_idx",
        )
        await database.projects.create_index(
            [
                ("integrations.slack.team_id", 1),
                ("integrations.slack.status", 1),
                ("members.email", 1),
            ],
            name="projects_slack_team_status_member_email_idx",
        )
        await database.projects.create_index(
            [
                ("integrations.slack.team_id", 1),
                ("integrations.slack.status", 1),
                ("members.integration_ids.slack_user_id", 1),
            ],
            name="projects_slack_team_status_member_slack_id_idx",
        )

        # User-context indexes used by DM active-project resolution.
        await database.users.create_index(
            [("email", 1)],
            name="users_email_idx",
        )
        await database.users.create_index(
            [
                ("channel_contexts.provider", 1),
                ("channel_contexts.workspace_id", 1),
                ("channel_contexts.external_user_id", 1),
            ],
            name="users_channel_context_provider_workspace_user_idx",
        )

        # Provider-agnostic DM active-project context index.
        await database.channel_index.create_index(
            [
                ("provider", 1),
                ("workspace_id", 1),
                ("external_user_id", 1),
            ],
            unique=True,
            name="channel_index_provider_workspace_user_uq_idx",
        )
        await database.channel_index.create_index(
            [
                ("provider", 1),
                ("workspace_id", 1),
                ("active_project_id", 1),
                ("updated_at", -1),
            ],
            name="channel_index_provider_workspace_project_updated_idx",
        )

        # Shared session index for project-scoped session payload resolution.
        await database.session_index.create_index(
            [("source", 1), ("session_id", 1)],
            unique=True,
            name="session_index_source_session_id_uq_idx",
        )
        await database.session_index.create_index(
            [("source", 1), ("session_key", 1)],
            unique=True,
            name="session_index_source_session_key_uq_idx",
        )
        await database.session_index.create_index(
            [
                ("source", 1),
                ("user_id", 1),
                ("project_id", 1),
                ("status", 1),
                ("expires_at", 1),
                ("updated_at", -1),
            ],
            name="session_index_source_user_project_status_expires_updated_idx",
        )
        await database.session_index.create_index(
            [
                ("source", 1),
                ("project_id", 1),
                ("status", 1),
                ("expires_at", 1),
                ("updated_at", -1),
            ],
            name="session_index_source_project_status_expires_updated_idx",
        )
        await database.session_index.create_index(
            [
                ("source", 1),
                ("project_id", 1),
                ("correlation_id", 1),
                ("status", 1),
                ("updated_at", -1),
            ],
            name="session_index_source_project_correlation_status_updated_idx",
        )
        await database.session_index.create_index(
            [
                ("source", 1),
                ("project_id", 1),
                ("correlation_id", 1),
                ("terminal", 1),
                ("updated_at", -1),
            ],
            name="session_index_source_project_correlation_terminal_updated_idx",
        )
        await database.session_index.create_index(
            [
                ("source", 1),
                ("project_id", 1),
                ("poll_id", 1),
                ("status", 1),
                ("updated_at", -1),
            ],
            name="session_index_source_project_poll_status_updated_idx",
        )
        await database.session_index.create_index(
            [
                ("source", 1),
                ("project_id", 1),
                ("poll_id", 1),
                ("member_user_id", 1),
                ("status", 1),
            ],
            name="session_index_source_project_poll_member_status_idx",
        )
        await database.session_index.create_index(
            [
                ("source", 1),
                ("provider", 1),
                ("workspace_id", 1),
                ("external_user_id", 1),
                ("status", 1),
                ("expires_at", 1),
                ("updated_at", -1),
            ],
            name="session_index_source_provider_workspace_user_status_expires_updated_idx",
        )
        await database.session_index.create_index(
            [
                ("source", 1),
                ("provider", 1),
                ("workspace_id", 1),
                ("external_user_id", 1),
                ("channel_id", 1),
                ("status", 1),
                ("expires_at", 1),
                ("updated_at", -1),
            ],
            name="session_index_source_provider_workspace_user_channel_status_expires_updated_idx",
        )
        await database.pending_interactions.create_index(
            [("interaction_id", 1)],
            unique=True,
            name="pending_interactions_interaction_id_uq_idx",
        )
        await database.pending_interactions.create_index(
            [
                ("project_id", 1),
                ("target_user_id", 1),
                ("status", 1),
                ("priority", -1),
                ("created_at", -1),
            ],
            name="pending_interactions_project_target_status_priority_created_idx",
        )
        await database.pending_interactions.create_index(
            [("status", 1), ("escalate_at", 1)],
            name="pending_interactions_status_escalate_idx",
        )
        await database.pending_interactions.create_index(
            [("status", 1), ("expires_at", 1)],
            name="pending_interactions_status_expires_idx",
        )
        await database.pending_interactions.create_index(
            [("expires_at", 1)],
            expireAfterSeconds=0,
            name="pending_interactions_expires_ttl_idx",
        )
        await database.pending_interactions.create_index(
            [
                ("provider", 1),
                ("workspace_id", 1),
                ("external_user_id", 1),
                ("status", 1),
                ("type", 1),
                ("priority", -1),
                ("created_at", -1),
            ],
            name="pending_interactions_provider_workspace_external_status_type_priority_created_idx",
        )

        # Centralized scheduler collections.
        await database.project_schedules.create_index(
            [("schedule_id", 1)],
            unique=True,
            name="project_schedules_schedule_id_uq_idx",
        )
        await database.project_schedules.create_index(
            [("enabled", 1), ("next_run_at", 1), ("job_type", 1)],
            name="project_schedules_enabled_next_job_idx",
        )
        await database.project_schedules.create_index(
            [("project_id", 1), ("job_type", 1)],
            unique=True,
            name="project_schedules_project_job_uq_idx",
        )
        await database.schedule_runs.create_index(
            [("run_id", 1)],
            unique=True,
            name="schedule_runs_run_id_uq_idx",
        )
        await database.schedule_runs.create_index(
            [("idempotency_key", 1)],
            unique=True,
            name="schedule_runs_idempotency_uq_idx",
        )
        await database.schedule_runs.create_index(
            [("schedule_id", 1), ("created_at", -1)],
            name="schedule_runs_schedule_created_idx",
        )
        await database.schedule_runs.create_index(
            [("created_at", 1)],
            name="schedule_runs_created_at_cleanup_idx",
        )
        
        print("Database indexes initialized successfully")
    except Exception as e:
        print(f"Error initializing database indexes: {e}")


async def close_mongo_connection():
    """Close MongoDB connection"""
    global client, database, brain_db, _CLIENT_RUNTIME_OWNER
    if client:
        client.close()
        client = None
        database = None
        brain_db = None
        _CLIENT_RUNTIME_OWNER = None
        print("Closed MongoDB connection")


def is_mongo_client_bound_to_current_loop(runtime_owner: Optional[str] = None) -> bool:
    """Return True when the Mongo client is initialized for the current runtime owner."""
    if client is None or database is None:
        return False
    normalized_owner = str(runtime_owner or "").strip() or None
    if normalized_owner is not None:
        if _CLIENT_RUNTIME_OWNER is None:
            return False
        return _CLIENT_RUNTIME_OWNER == normalized_owner
    return True


async def ensure_mongo_ready(runtime_owner: Optional[str] = None) -> str:
    """Ensure Mongo is connected and healthy; reconnect on failed ping."""
    if get_database() is None:
        await connect_to_mongo(runtime_owner=runtime_owner)
        return "connected"

    if not is_mongo_client_bound_to_current_loop(runtime_owner=runtime_owner):
        await close_mongo_connection()
        await connect_to_mongo(runtime_owner=runtime_owner)
        return "reconnected_runtime_owner_mismatch"

    try:
        await get_database().command("ping")
        return "reused"
    except Exception:
        await close_mongo_connection()
        await connect_to_mongo(runtime_owner=runtime_owner)
        return "reconnected_ping_failure"


async def generate_unique_project_id() -> str:
    """
    Generate a unique random project ID with format: proj-XXXX-YYYY
    Retries if duplicate is found.
    
    Returns:
        str: Unique project ID (e.g., 'proj-1234-5678')
    """
    max_attempts = 10  # Prevent infinite loops
    
    for attempt in range(max_attempts):
        # Generate random 8-digit number split into two parts
        part1 = random.randint(1000, 9999)  # 1000-9999
        part2 = random.randint(1000, 9999)  # 1000-9999
        project_id = f"proj-{part1}-{part2}"
        
        # Check if already exists
        exists = await database.projects.find_one({"project_id": project_id})
        if not exists:
            return project_id
    
    # Fallback: add timestamp if somehow we hit max attempts
    import time
    timestamp = int(time.time()) % 10000
    return f"proj-{timestamp}-{random.randint(1000, 9999)}"


def get_database() -> AsyncIOMotorDatabase:
    """Get database instance"""
    return database


def get_brain_db() -> AsyncIOMotorDatabase:
    """Get brain database instance"""
    return brain_db


def get_session_index_collection() -> AsyncIOMotorCollection:
    """Get shared session index collection in the default database."""
    return database["session_index"]


def get_channel_index_collection() -> AsyncIOMotorCollection:
    """Get provider-agnostic channel index collection in the default database."""
    return database["channel_index"]


def get_pending_interactions_collection() -> AsyncIOMotorCollection:
    """Get shared pending interactions collection in the default database."""
    return database["pending_interactions"]


def get_brain_collection(project_id: str) -> AsyncIOMotorCollection:
    """
    Get collection for a project in brain database.
    Collection is created automatically on first document insert.
    
    Args:
        project_id: Project ID (ObjectId string)
    
    Returns:
        AsyncIOMotorCollection for the project
    """
    return brain_db[str(project_id)]


def get_project_session_collection(project_id: str) -> AsyncIOMotorCollection:
    """
    Return the project-scoped Brain collection used for session documents.

    Sessions coexist with other project documents and are isolated via
    ``entity_type`` plus partial indexes.
    """
    return get_brain_collection(project_id)


def _normalize_index_key(key_spec: Any) -> List[Tuple[str, int]]:
    """Normalize index key specs to a comparable list of (field, direction) tuples."""
    if key_spec is None:
        return []
    if isinstance(key_spec, list):
        return [(str(k), int(v)) for k, v in key_spec]
    if hasattr(key_spec, "items"):
        return [(str(k), int(v)) for k, v in key_spec.items()]
    return []


def _index_spec_matches(
    existing: Dict[str, Any],
    expected_keys: List[Tuple[str, int]],
    *,
    unique: bool = False,
    partial_filter_expression: Optional[Dict[str, Any]] = None,
    expire_after_seconds: Optional[int] = None,
) -> bool:
    existing_keys = _normalize_index_key(existing.get("key"))
    if existing_keys != expected_keys:
        return False
    if bool(existing.get("unique", False)) != bool(unique):
        return False
    existing_partial = existing.get("partialFilterExpression")
    if partial_filter_expression is None:
        partial_matches = existing_partial is None
    else:
        partial_matches = existing_partial == partial_filter_expression
    if not partial_matches:
        return False
    existing_expire = existing.get("expireAfterSeconds")
    if expire_after_seconds is None:
        return existing_expire is None
    return int(existing_expire) == int(expire_after_seconds)


async def _ensure_named_index(
    collection: AsyncIOMotorCollection,
    *,
    existing_indexes: Dict[str, Dict[str, Any]],
    keys: List[Tuple[str, int]],
    name: str,
    unique: bool = False,
    partial_filter_expression: Optional[Dict[str, Any]] = None,
    expire_after_seconds: Optional[int] = None,
) -> None:
    """Create a named index if missing; replace it if same-name spec drift is detected."""
    existing = existing_indexes.get(name)
    if existing and _index_spec_matches(
        existing,
        keys,
        unique=unique,
        partial_filter_expression=partial_filter_expression,
        expire_after_seconds=expire_after_seconds,
    ):
        return

    if existing:
        try:
            await collection.drop_index(name)
        except OperationFailure as exc:
            # Another worker may have already dropped/replaced it.
            if getattr(exc, "code", None) != 27:  # IndexNotFound
                raise

    def _build_create_index_kwargs() -> Dict[str, Any]:
        kwargs: Dict[str, Any] = {
            "unique": unique,
            "name": name,
        }
        if partial_filter_expression is not None:
            kwargs["partialFilterExpression"] = partial_filter_expression
        if expire_after_seconds is not None:
            kwargs["expireAfterSeconds"] = int(expire_after_seconds)
        return kwargs

    try:
        try:
            await collection.create_index(
                keys,
                **_build_create_index_kwargs(),
            )
        except OperationFailure as recreate_exc:
            if getattr(recreate_exc, "code", None) != 86:  # IndexKeySpecsConflict
                raise
            final_indexes = {
                idx.get("name"): idx
                async for idx in collection.list_indexes()
                if idx.get("name")
            }
            final_refreshed = final_indexes.get(name)
            if not final_refreshed or not _index_spec_matches(
                final_refreshed,
                keys,
                unique=unique,
                partial_filter_expression=partial_filter_expression,
                expire_after_seconds=expire_after_seconds,
            ):
                raise
    except OperationFailure as exc:
        # Handle concurrent workers creating/replacing the same index.
        if getattr(exc, "code", None) != 86:  # IndexKeySpecsConflict
            raise
        refreshed_indexes = {
            idx.get("name"): idx
            async for idx in collection.list_indexes()
            if idx.get("name")
        }
        refreshed = refreshed_indexes.get(name)
        if refreshed and _index_spec_matches(
            refreshed,
            keys,
            unique=unique,
            partial_filter_expression=partial_filter_expression,
            expire_after_seconds=expire_after_seconds,
        ):
            return

        # Existing same-name index has stale spec. Heal by replacing once.
        try:
            await collection.drop_index(name)
        except OperationFailure as drop_exc:
            if getattr(drop_exc, "code", None) != 27:  # IndexNotFound
                raise
        await collection.create_index(
            keys,
            **_build_create_index_kwargs(),
        )


async def ensure_project_session_indexes(project_id: str) -> None:
    """
    Ensure session-specific partial indexes exist on a project's Brain collection.

    Session docs are isolated using:
    - entity_type in {"cadence_session", "cortex_session"}
    """
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        raise ValueError("project_id is required")
    if normalized_project_id in _PROJECT_SESSION_INDEXES_READY:
        return

    collection = get_project_session_collection(normalized_project_id)
    session_partial_filter = {"entity_type": {"$in": list(SESSION_ENTITY_TYPES)}}
    existing_indexes = {
        idx.get("name"): idx
        async for idx in collection.list_indexes()
        if idx.get("name")
    }

    # Unique session identifiers scoped only to session docs.
    await _ensure_named_index(
        collection,
        existing_indexes=existing_indexes,
        keys=[("entity_type", 1), ("source", 1), ("session_id", 1)],
        name="session_entity_source_session_id_uq_idx",
        unique=True,
        partial_filter_expression={
            **session_partial_filter,
            "source": {"$exists": True},
            "session_id": {"$exists": True},
        },
    )
    await _ensure_named_index(
        collection,
        existing_indexes=existing_indexes,
        keys=[("entity_type", 1), ("source", 1), ("session_key", 1)],
        name="session_entity_source_session_key_uq_idx",
        unique=True,
        partial_filter_expression={
            **session_partial_filter,
            "source": {"$exists": True},
            "session_key": {"$exists": True},
        },
    )

    # Active-session lookup hot paths.
    await _ensure_named_index(
        collection,
        existing_indexes=existing_indexes,
        keys=[
            ("entity_type", 1),
            ("source", 1),
            ("user_id", 1),
            ("project_id", 1),
            ("status", 1),
            ("expires_at", 1),
            ("created_at", -1),
        ],
        name="session_entity_source_user_project_status_expires_created_idx",
        partial_filter_expression=session_partial_filter,
    )
    await _ensure_named_index(
        collection,
        existing_indexes=existing_indexes,
        keys=[
            ("entity_type", 1),
            ("source", 1),
            ("user_id", 1),
            ("status", 1),
            ("expires_at", 1),
            ("created_at", -1),
        ],
        name="session_entity_source_user_status_expires_created_idx",
        partial_filter_expression=session_partial_filter,
    )
    await _ensure_named_index(
        collection,
        existing_indexes=existing_indexes,
        keys=[("entity_type", 1), ("source", 1), ("expires_at", 1), ("status", 1)],
        name="session_entity_source_expires_status_idx",
        partial_filter_expression=session_partial_filter,
    )

    # Team poll lookup/finalization paths.
    await _ensure_named_index(
        collection,
        existing_indexes=existing_indexes,
        keys=[("entity_type", 1), ("source", 1), ("project_id", 1), ("poll_id", 1), ("status", 1)],
        name="team_poll_entity_source_project_poll_status_idx",
        partial_filter_expression={"entity_type": "team_poll_session"},
    )
    await _ensure_named_index(
        collection,
        existing_indexes=existing_indexes,
        keys=[("entity_type", 1), ("source", 1), ("project_id", 1), ("poll_id", 1), ("user_id", 1)],
        name="team_poll_entity_source_project_poll_user_idx",
        partial_filter_expression={"entity_type": "team_poll_session"},
    )
    await _ensure_named_index(
        collection,
        existing_indexes=existing_indexes,
        keys=[("entity_type", 1), ("correlation_id", 1), ("status", 1)],
        name="team_poll_entity_correlation_status_idx",
        partial_filter_expression={"entity_type": "team_poll_session"},
    )
    await _ensure_named_index(
        collection,
        existing_indexes=existing_indexes,
        keys=[("entity_type", 1), ("poll_id", 1)],
        name="team_poll_result_entity_poll_uq_idx",
        unique=True,
        partial_filter_expression={"entity_type": "team_poll_result"},
    )

    _PROJECT_SESSION_INDEXES_READY.add(normalized_project_id)


async def ensure_project_cadence_summary_indexes(project_id: str) -> None:
    """
    Ensure cadence_daily_summary indexes exist on a project's Brain collection.

    Summary docs are isolated via:
    - entity_type == "cadence_daily_summary"
    """
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        raise ValueError("project_id is required")
    if normalized_project_id in _PROJECT_CADENCE_SUMMARY_INDEXES_READY:
        return

    collection = get_brain_collection(normalized_project_id)
    summary_partial_filter = {"entity_type": CADENCE_DAILY_SUMMARY_ENTITY_TYPE}

    await collection.create_index(
        [("entity_type", 1), ("date", 1), ("generated_at", -1)],
        name="cadence_daily_summary_entity_date_generated_idx",
        partialFilterExpression={
            **summary_partial_filter,
            "date": {"$exists": True},
            "generated_at": {"$exists": True},
        },
    )
    await collection.create_index(
        [("entity_type", 1), ("owner_notified", 1), ("generated_at", -1)],
        name="cadence_daily_summary_entity_notified_generated_idx",
        partialFilterExpression={
            **summary_partial_filter,
            "owner_notified": {"$exists": True},
            "generated_at": {"$exists": True},
        },
    )
    try:
        await collection.create_index(
            [("entity_type", 1), ("correlation_id", 1)],
            unique=True,
            name="cadence_daily_summary_entity_correlation_uq_idx",
            partialFilterExpression={
                **summary_partial_filter,
                "correlation_id": {"$exists": True},
            },
        )
    except Exception as exc:
        print(
            "[database] cadence_daily_summary unique correlation index creation "
            f"failed for project {normalized_project_id}: {exc}"
        )
        try:
            await collection.create_index(
                [("entity_type", 1), ("correlation_id", 1)],
                name="cadence_daily_summary_entity_correlation_idx",
                partialFilterExpression={
                    **summary_partial_filter,
                    "correlation_id": {"$exists": True},
                },
            )
        except Exception as fallback_exc:
            print(
                "[database] cadence_daily_summary correlation index fallback "
                f"failed for project {normalized_project_id}: {fallback_exc}"
            )

    _PROJECT_CADENCE_SUMMARY_INDEXES_READY.add(normalized_project_id)


async def ensure_project_action_item_indexes(project_id: str) -> None:
    """
    Ensure action_item indexes exist on a project's Brain collection.

    Action item docs are isolated via:
    - entity_type == "action_item"
    """

    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        raise ValueError("project_id is required")
    if normalized_project_id in _PROJECT_ACTION_ITEM_INDEXES_READY:
        return

    collection = get_brain_collection(normalized_project_id)
    action_item_partial_filter = {"entity_type": ACTION_ITEM_ENTITY_TYPE}
    existing_indexes = {
        idx.get("name"): idx
        async for idx in collection.list_indexes()
        if idx.get("name")
    }
    for legacy_name in (
        "action_item_entity_ingest_key_uq_idx",
        "action_item_ingest_ledger_key_uq_idx",
        "action_item_ingest_ledger_expires_ttl_idx",
    ):
        if legacy_name not in existing_indexes:
            continue
        try:
            await collection.drop_index(legacy_name)
        except OperationFailure as exc:
            if getattr(exc, "code", None) != 27:  # IndexNotFound
                raise
        existing_indexes.pop(legacy_name, None)

    await _ensure_named_index(
        collection,
        existing_indexes=existing_indexes,
        keys=[("entity_type", 1), ("status", 1), ("updated_at", -1)],
        name="action_item_entity_status_updated_idx",
        partial_filter_expression={
            **action_item_partial_filter,
            "status": {"$exists": True},
            "updated_at": {"$exists": True},
        },
    )
    await _ensure_named_index(
        collection,
        existing_indexes=existing_indexes,
        keys=[("entity_type", 1), ("owner_user_id", 1), ("status", 1), ("updated_at", -1)],
        name="action_item_entity_owner_status_updated_idx",
        partial_filter_expression={
            **action_item_partial_filter,
            "owner_user_id": {"$exists": True},
            "status": {"$exists": True},
            "updated_at": {"$exists": True},
        },
    )
    await _ensure_named_index(
        collection,
        existing_indexes=existing_indexes,
        keys=[("entity_type", 1), ("due_date", 1), ("status", 1)],
        name="action_item_entity_due_status_idx",
        partial_filter_expression={
            **action_item_partial_filter,
            "due_date": {"$exists": True},
            "status": {"$exists": True},
        },
    )
    await _ensure_named_index(
        collection,
        existing_indexes=existing_indexes,
        keys=[("entity_type", 1), ("action_item_id", 1)],
        unique=True,
        name="action_item_entity_id_uq_idx",
        partial_filter_expression={
            **action_item_partial_filter,
            "action_item_id": {"$exists": True},
        },
    )
    await _ensure_named_index(
        collection,
        existing_indexes=existing_indexes,
        keys=[("entity_type", 1), ("display_key", 1)],
        unique=True,
        name="action_item_entity_display_key_uq_idx",
        partial_filter_expression={
            **action_item_partial_filter,
            "display_key": {"$exists": True},
        },
    )
    await _ensure_named_index(
        collection,
        existing_indexes=existing_indexes,
        keys=[("entity_type", 1), ("source_event_key", 1), ("created_at", -1)],
        name="action_item_entity_source_event_key_created_idx",
        partial_filter_expression={
            **action_item_partial_filter,
            "source_event_key": {"$exists": True},
        },
    )

    _PROJECT_ACTION_ITEM_INDEXES_READY.add(normalized_project_id)


def get_planner_discussion_collection(project_id: str) -> AsyncIOMotorCollection:
    """
    Get the project-specific collection in brain database for planner data.
    Each project stores its planner discussions in its own collection.

    Args:
        project_id: Project ID (ObjectId string)

    Returns:
        AsyncIOMotorCollection for the project's planner discussions
    """
    return brain_db[str(project_id)]
