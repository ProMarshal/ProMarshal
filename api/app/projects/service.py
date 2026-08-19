"""Project service - business logic for project operations"""

import logging
from urllib.parse import quote as url_quote
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any
from bson import ObjectId
from bson.errors import InvalidId

logger = logging.getLogger(__name__)
from app.core.database import get_database, get_brain_collection, generate_unique_project_id, get_brain_db
from app.core.email import send_invite_email
from app.core.redis_queue import get_redis_connection
from app.core.security import encryption_service
from app.integrations.channel_index import clear_active_project_rows
from app.integrations.jira.oauth import JiraOAuthService
from app.domain.capabilities.materialization import connected_pm_providers_from_project_context
from app.scheduler.service import (
    get_or_create_action_item_digest_schedule_for_project,
    get_or_create_cadence_schedule_for_project,
    realign_project_daily_schedules_for_timezone,
    upsert_action_item_digest_schedule_for_project,
    upsert_cadence_schedule_for_project,
)
from app.core.timezone import canonical_timezone_name, get_zoneinfo_or_utc


_DELETE_LOCK_PREFIX = "project_delete_lock:v1:"
_DELETE_LOCK_TTL_SECONDS = 120
_UPLOADS_ROOT_CANDIDATES = (
    Path("uploads"),
    Path(__file__).resolve().parents[2] / "uploads",
    Path(__file__).resolve().parents[3] / "uploads",
)
_PM_BOARD_JOB_STATUS_KEY_PREFIX = "pm_board_summary_job:v1:"
_PM_BOARD_JOB_LOCK_KEY_PREFIX = "pm_board_summary_job_lock:v1:"
_CORTEX_DEFAULT_PROJECT_PREFIX = "cortex:default_project:"
_JIRA_WEBHOOK_PATH = "/api/jira/webhooks/task-updated"


def serialize_project(project: dict) -> dict:
    """Convert MongoDB document to serializable format"""
    if project and "_id" in project:
        project["_id"] = str(project["_id"])
    return project


class ProjectService:
    """Service class for project operations"""

    @staticmethod
    async def create(project_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new project"""
        db = get_database()

        project_dict = project_data.copy()
        owner_id = project_dict.get("user_id")

        # Get owner info from users collection
        owner_info = await db.users.find_one({"user_id": owner_id}) if owner_id else None

        members = project_dict.get("members") or []
        # Ensure owner is present as project owner with full info
        owner_present = any(m.get("user_id") == owner_id for m in members)
        if not owner_present and owner_id:
            owner_member = {
                "user_id": owner_id,
                "email": owner_info.get("email") if owner_info else None,
                "name": owner_info.get("name") if owner_info else None,
                "role": "owner",
                "status": "active",
                "joined_at": datetime.utcnow()
            }
            members = [owner_member] + members

        # Convert invited_emails to pending member entries
        invited_emails = project_dict.get("invited_emails") or []
        for email in invited_emails:
            email_lower = email.lower().strip()
            # Check if not already in members
            if not any(m.get("email", "").lower() == email_lower for m in members):
                pending_member = {
                    "user_id": None,
                    "email": email_lower,
                    "name": None,
                    "role": "member",
                    "status": "pending",
                    "invite_token": str(uuid.uuid4()),
                    "invited_by": owner_id,
                    "invited_at": datetime.utcnow(),
                    "expires_at": datetime.utcnow() + timedelta(days=7),
                    "joined_at": None
                }
                members.append(pending_member)

        project_dict["members"] = members
        # Keep invited_emails for backward compatibility but it's now redundant
        project_dict["invited_emails"] = invited_emails

        # Set default tier if not provided
        if "tier" not in project_dict:
            project_dict["tier"] = "free"

        if "active_provider" not in project_dict:
            project_dict["active_provider"] = None

        project_dict["timezone"] = canonical_timezone_name(
            project_dict.get("timezone"),
            context="project_create",
        )

        project_dict["created_at"] = datetime.utcnow()
        project_dict["updated_at"] = datetime.utcnow()
        
        # Generate custom project_id
        project_dict["project_id"] = await generate_unique_project_id()

        result = await db.projects.insert_one(project_dict)
        created_project = await db.projects.find_one({"_id": result.inserted_id})

        # Create brain collection for this project with initial project document
        project_id = project_dict["project_id"]  # Use custom project_id, not ObjectId
        project_name = project_dict.get("project_name", "Project")
        brain_collection = get_brain_collection(project_id)
        await brain_collection.insert_one({
            "project_id": project_id,
            "project_name": project_name,
            "entity_type": "project",
            "created_at": datetime.utcnow()
        })
        print(f"Created brain collection: brain/{project_id}")

        # Strict scheduler mode: create cadence schedule row (source of truth for reminders).
        try:
            await upsert_cadence_schedule_for_project(
                db,
                project_id=project_id,
                enabled=True,
                time_hhmm="09:00",
            )
        except Exception as exc:
            print(f"[scheduler] Failed to create default cadence schedule for {project_id}: {exc}")

        # Send invitation emails for pending members
        owner_name = owner_info.get("name", "A team member") if owner_info else "A team member"
        for member in members:
            if member.get("status") == "pending" and member.get("invite_token"):
                await send_invite_email(
                    to_email=member.get("email"),
                    project_name=project_name,
                    inviter_name=owner_name,
                    invite_token=member.get("invite_token"),
                    role=member.get("role", "member")
                )

        return serialize_project(created_project)

    @staticmethod
    async def get_by_id(project_id: str) -> Optional[Dict[str, Any]]:
        """Get project by ID"""
        db = get_database()
        if db is None:
            logger.error("get_by_id called before database init")
            return None
        try:
            project = await db.projects.find_one({"_id": ObjectId(project_id)})
            return serialize_project(project) if project else None
        except (InvalidId, TypeError):
            return None
        except Exception:
            logger.exception("get_by_id failed for project_id=%s", project_id)
            return None

    @staticmethod
    async def get_by_user(user_id: str, skip: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
        """Get all projects where user is owner OR active member"""
        db = get_database()
        # Find projects where user is owner OR is an active member
        query = {
            "$or": [
                {"user_id": user_id},
                {"members": {"$elemMatch": {"user_id": user_id, "status": "active"}}}
            ]
        }
        projects = await db.projects.find(query).skip(skip).limit(limit).to_list(length=limit)
        return [serialize_project(project) for project in projects]

    @staticmethod
    async def list_all(user_id: str = None, skip: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
        """List projects with optional user filter (includes member projects)"""
        db = get_database()

        query = {}
        if user_id:
            # Find projects where user is owner OR is an active member
            query = {
                "$or": [
                    {"user_id": user_id},
                    {"members": {"$elemMatch": {"user_id": user_id, "status": "active"}}}
                ]
            }

        projects = await db.projects.find(query).skip(skip).limit(limit).to_list(length=limit)
        return [serialize_project(project) for project in projects]

    @staticmethod
    async def update(project_id: str, update_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Update a project"""
        db = get_database()
        existing_project = await db.projects.find_one(
            {"_id": ObjectId(project_id)},
            {"project_id": 1, "timezone": 1},
        )
        if not existing_project:
            return None

        # Filter out None values
        filtered_data = {k: v for k, v in update_data.items() if v is not None}

        timezone_changed = False
        if filtered_data:
            if "timezone" in filtered_data:
                filtered_data["timezone"] = canonical_timezone_name(
                    filtered_data.get("timezone"),
                    context="project_update",
                )
                old_timezone = str(existing_project.get("timezone") or "UTC").strip() or "UTC"
                timezone_changed = filtered_data["timezone"] != old_timezone
            filtered_data["updated_at"] = datetime.utcnow()
            await db.projects.update_one(
                {"_id": ObjectId(project_id)},
                {"$set": filtered_data}
            )

            if timezone_changed:
                custom_project_id = str(existing_project.get("project_id") or "").strip()
                if custom_project_id:
                    try:
                        await realign_project_daily_schedules_for_timezone(
                            db,
                            project_id=custom_project_id,
                        )
                    except Exception as exc:
                        print(
                            "project_timezone_schedule_realign_failed: "
                            f"project_id={custom_project_id} error={exc}"
                        )

        updated_project = await db.projects.find_one({"_id": ObjectId(project_id)})
        return serialize_project(updated_project) if updated_project else None

    @staticmethod
    async def get_cadence_schedule(project_id: str) -> Dict[str, Any]:
        """Return scheduler-backed cadence settings for a project."""
        db = get_database()
        schedule = await get_or_create_cadence_schedule_for_project(db, project_mongo_id=project_id)
        spec = dict(schedule.get("schedule_spec") or {})
        return {
            "enabled": bool(schedule.get("enabled", True)),
            "time": str(spec.get("time") or "09:00"),
            "skip_weekends": bool(spec.get("skip_weekends", False)),
            "ignore_followup_for_owner": bool(spec.get("ignore_followup_for_owner", False)),
        }

    @staticmethod
    async def update_cadence_schedule(
        project_id: str,
        *,
        enabled: bool,
        time: str,
        skip_weekends: bool = False,
        ignore_followup_for_owner: bool = False,
    ) -> Dict[str, Any]:
        """Update scheduler-backed cadence settings for a project."""
        db = get_database()
        project = await db.projects.find_one({"_id": ObjectId(project_id)}, {"project_id": 1})
        if not project:
            raise ValueError("Project not found")
        custom_project_id = str(project.get("project_id") or "").strip()
        if not custom_project_id:
            raise ValueError("Project custom ID missing")

        schedule = await upsert_cadence_schedule_for_project(
            db,
            project_id=custom_project_id,
            enabled=bool(enabled),
            time_hhmm=time,
            skip_weekends=bool(skip_weekends),
            ignore_followup_for_owner=bool(ignore_followup_for_owner),
        )
        spec = dict(schedule.get("schedule_spec") or {})
        return {
            "enabled": bool(schedule.get("enabled", True)),
            "time": str(spec.get("time") or "09:00"),
            "skip_weekends": bool(spec.get("skip_weekends", False)),
            "ignore_followup_for_owner": bool(spec.get("ignore_followup_for_owner", False)),
        }

    @staticmethod
    async def get_action_item_digest_schedule(project_id: str) -> Dict[str, Any]:
        """Return scheduler-backed action-item digest settings for a project."""
        db = get_database()
        schedule = await get_or_create_action_item_digest_schedule_for_project(
            db,
            project_mongo_id=project_id,
        )
        spec = dict(schedule.get("schedule_spec") or {})
        return {
            "enabled": bool(schedule.get("enabled", True)),
            "time": str(spec.get("time") or "12:30"),
            "skip_weekends": bool(spec.get("skip_weekends", False)),
            "ignore_followup_for_owner": bool(spec.get("ignore_followup_for_owner", False)),
        }

    @staticmethod
    async def update_action_item_digest_schedule(
        project_id: str,
        *,
        enabled: bool,
        time: str,
        skip_weekends: bool = False,
        ignore_followup_for_owner: bool = False,
    ) -> Dict[str, Any]:
        """Update scheduler-backed action-item digest settings for a project."""
        db = get_database()
        project = await db.projects.find_one({"_id": ObjectId(project_id)}, {"project_id": 1})
        if not project:
            raise ValueError("Project not found")
        custom_project_id = str(project.get("project_id") or "").strip()
        if not custom_project_id:
            raise ValueError("Project custom ID missing")

        schedule = await upsert_action_item_digest_schedule_for_project(
            db,
            project_id=custom_project_id,
            enabled=bool(enabled),
            time_hhmm=time,
            skip_weekends=bool(skip_weekends),
            ignore_followup_for_owner=bool(ignore_followup_for_owner),
        )
        spec = dict(schedule.get("schedule_spec") or {})
        return {
            "enabled": bool(schedule.get("enabled", True)),
            "time": str(spec.get("time") or "12:30"),
            "skip_weekends": bool(spec.get("skip_weekends", False)),
            "ignore_followup_for_owner": bool(spec.get("ignore_followup_for_owner", False)),
        }

    @staticmethod
    async def _delete_uploads_required(custom_project_id: str) -> None:
        normalized_project_id = str(custom_project_id or "").strip()
        if not normalized_project_id:
            return
        for root in _UPLOADS_ROOT_CANDIDATES:
            project_dir = root / normalized_project_id
            if not project_dir.exists():
                continue
            try:
                shutil.rmtree(project_dir)
            except FileNotFoundError:
                continue
            except Exception as exc:
                raise RuntimeError(
                    f"required_uploads_cleanup_failed: project_id={normalized_project_id} path={project_dir}"
                ) from exc

    @staticmethod
    def _delete_lock_key(custom_project_id: str) -> str:
        return f"{_DELETE_LOCK_PREFIX}{str(custom_project_id or '').strip()}"

    @staticmethod
    def _acquire_delete_lock(custom_project_id: str) -> tuple[bool, str]:
        normalized_project_id = str(custom_project_id or "").strip()
        if not normalized_project_id:
            return True, ""
        redis_conn = get_redis_connection()
        if redis_conn is None:
            logger.warning(
                "project_delete_lock_backend_unavailable project_id=%s proceeding_idempotent_mode=true",
                normalized_project_id,
            )
            return True, ""
        token = str(uuid.uuid4())
        try:
            created = redis_conn.set(
                ProjectService._delete_lock_key(normalized_project_id),
                token.encode("utf-8"),
                nx=True,
                ex=_DELETE_LOCK_TTL_SECONDS,
            )
            return bool(created), token
        except Exception as exc:
            logger.warning(
                "project_delete_lock_acquire_failed project_id=%s error=%s proceeding_idempotent_mode=true",
                normalized_project_id,
                exc,
            )
            return True, ""

    @staticmethod
    def _release_delete_lock(custom_project_id: str, token: str) -> None:
        normalized_project_id = str(custom_project_id or "").strip()
        if not normalized_project_id or not token:
            return
        redis_conn = get_redis_connection()
        if redis_conn is None:
            return
        script = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  redis.call('del', KEYS[1])
  return 1
end
return 0
"""
        try:
            redis_conn.eval(script, 1, ProjectService._delete_lock_key(normalized_project_id), token)
        except Exception as exc:
            logger.warning(
                "project_delete_lock_release_failed project_id=%s error=%s",
                normalized_project_id,
                exc,
            )

    @staticmethod
    async def _cleanup_required_pre_delete(db, custom_project_id: str) -> None:
        normalized_project_id = str(custom_project_id or "").strip()
        if not normalized_project_id:
            raise RuntimeError("required_cleanup_missing_custom_project_id")
        try:
            await db.project_schedules.delete_many({"project_id": normalized_project_id})
        except Exception as exc:
            raise RuntimeError(
                f"required_project_schedules_cleanup_failed: project_id={normalized_project_id}"
            ) from exc

        brain_db = get_brain_db()
        if brain_db is None:
            raise RuntimeError(f"required_brain_db_unavailable: project_id={normalized_project_id}")
        try:
            await brain_db.drop_collection(normalized_project_id)
        except Exception as exc:
            raise RuntimeError(
                f"required_brain_collection_drop_failed: project_id={normalized_project_id}"
            ) from exc

        await ProjectService._delete_uploads_required(normalized_project_id)

    @staticmethod
    async def _cleanup_mongo_best_effort(db, mongo_project_id: ObjectId, custom_project_id: str) -> None:
        normalized_project_id = str(custom_project_id or "").strip()
        cleanup_steps = [
            ("session_index", db.session_index.delete_many({"project_id": normalized_project_id})),
            ("pending_interactions", db.pending_interactions.delete_many({"project_id": normalized_project_id})),
            ("planner_data", db.planner_data.delete_many({"project_id": normalized_project_id})),
            ("project_documents", db.project_documents.delete_many({"project_id": normalized_project_id})),
            ("slack_messages", db.slack_messages.delete_many({"project_id": mongo_project_id})),
        ]
        for label, operation in cleanup_steps:
            try:
                result = await operation
                logger.info(
                    "project_delete_best_effort_cleanup_ok project_id=%s target=%s deleted_count=%s",
                    normalized_project_id,
                    label,
                    int(getattr(result, "deleted_count", 0) or 0),
                )
            except Exception as exc:
                logger.warning(
                    "project_delete_best_effort_cleanup_failed project_id=%s target=%s error=%s",
                    normalized_project_id,
                    label,
                    exc,
                )

        try:
            deleted_rows = await clear_active_project_rows(db=db, project_id=normalized_project_id)
            logger.info(
                "project_delete_best_effort_cleanup_ok project_id=%s target=channel_index deleted_count=%s",
                normalized_project_id,
                int(deleted_rows or 0),
            )
        except Exception as exc:
            logger.warning(
                "project_delete_best_effort_cleanup_failed project_id=%s target=channel_index error=%s",
                normalized_project_id,
                exc,
            )

    @staticmethod
    def _scan_delete_pattern(redis_conn, pattern: str) -> int:
        deleted = 0
        for key in redis_conn.scan_iter(match=pattern, count=200):
            try:
                deleted += int(redis_conn.delete(key) or 0)
            except Exception:
                continue
        return deleted

    @staticmethod
    def _cleanup_redis_best_effort(custom_project_id: str) -> None:
        normalized_project_id = str(custom_project_id or "").strip()
        if not normalized_project_id:
            return
        redis_conn = get_redis_connection()
        if redis_conn is None:
            return
        direct_keys = [
            f"pm_board_summary:v1:{normalized_project_id}",
            f"pm_board_summary:dirty:v1:{normalized_project_id}",
            f"{_PM_BOARD_JOB_STATUS_KEY_PREFIX}{normalized_project_id}",
            f"{_PM_BOARD_JOB_LOCK_KEY_PREFIX}{normalized_project_id}",
            f"reco:project:{normalized_project_id}",
            f"reco:meta:{normalized_project_id}",
            f"reco:api:project:{normalized_project_id}",
            f"reco:dirty:set:{normalized_project_id}",
        ]
        try:
            redis_conn.delete(*direct_keys)
        except Exception as exc:
            logger.warning(
                "project_delete_best_effort_cleanup_failed project_id=%s target=redis_direct_keys error=%s",
                normalized_project_id,
                exc,
            )

        scan_patterns = [
            f"reco:task:{normalized_project_id}:*",
            f"reco:dirty:{normalized_project_id}:*",
            f"cortex:session:v1:{normalized_project_id}:*",
            f"cortex:queue:{normalized_project_id}:*",
            f"cortex:run:{normalized_project_id}:*",
            f"cortex:inflight:{normalized_project_id}:*",
            f"cortex:dispatch:{normalized_project_id}:*",
        ]
        for pattern in scan_patterns:
            try:
                deleted = ProjectService._scan_delete_pattern(redis_conn, pattern)
                logger.info(
                    "project_delete_best_effort_cleanup_ok project_id=%s target=redis_scan pattern=%s deleted_count=%s",
                    normalized_project_id,
                    pattern,
                    deleted,
                )
            except Exception as exc:
                logger.warning(
                    "project_delete_best_effort_cleanup_failed project_id=%s target=redis_scan pattern=%s error=%s",
                    normalized_project_id,
                    pattern,
                    exc,
                )

        try:
            deleted = 0
            for key in redis_conn.scan_iter(match=f"{_CORTEX_DEFAULT_PROJECT_PREFIX}*", count=200):
                value = redis_conn.get(key)
                decoded = value.decode("utf-8", errors="ignore") if isinstance(value, bytes) else str(value or "")
                if decoded.strip() != normalized_project_id:
                    continue
                deleted += int(redis_conn.delete(key) or 0)
            logger.info(
                "project_delete_best_effort_cleanup_ok project_id=%s target=redis_default_project_keys deleted_count=%s",
                normalized_project_id,
                deleted,
            )
        except Exception as exc:
            logger.warning(
                "project_delete_best_effort_cleanup_failed project_id=%s target=redis_default_project_keys error=%s",
                normalized_project_id,
                exc,
            )

    @staticmethod
    async def _cleanup_jira_webhook_best_effort(project: Dict[str, Any]) -> None:
        jira_integration = ((project or {}).get("integrations") or {}).get("jira") or {}
        if str(jira_integration.get("status") or "").strip().lower() not in {"connected", "pending_project_selection"}:
            return
        encrypted_access_token = jira_integration.get("access_token")
        cloud_id = str(jira_integration.get("cloud_id") or "").strip()
        site_url = str(jira_integration.get("site_url") or "").strip()
        custom_project_id = str((project or {}).get("project_id") or "").strip()
        webhook_token = str(jira_integration.get("webhook_token") or "").strip()
        if not encrypted_access_token or not cloud_id:
            return
        try:
            access_token = encryption_service.decrypt(encrypted_access_token)
        except Exception as exc:
            logger.warning("project_delete_jira_webhook_cleanup_skipped reason=token_decrypt_failed error=%s", exc)
            return
        webhook_url_candidates = [f"{site_url}{_JIRA_WEBHOOK_PATH}" if site_url else "", _JIRA_WEBHOOK_PATH]
        if custom_project_id and webhook_token:
            encoded_project_id = url_quote(custom_project_id, safe="")
            encoded_token = url_quote(webhook_token, safe="")
            webhook_url_candidates.insert(
                0,
                f"{site_url}/api/jira/webhooks/{encoded_project_id}/task-updated?token={encoded_token}"
                if site_url
                else "",
            )
        jira_oauth = JiraOAuthService()
        for webhook_url in webhook_url_candidates:
            normalized_webhook_url = str(webhook_url or "").strip()
            if not normalized_webhook_url:
                continue
            try:
                deleted = await jira_oauth.delete_webhooks_by_url(
                    access_token=access_token,
                    cloud_id=cloud_id,
                    webhook_url=normalized_webhook_url,
                )
                logger.info(
                    "project_delete_best_effort_cleanup_ok target=jira_webhook cloud_id=%s deleted_count=%s",
                    cloud_id,
                    deleted,
                )
                if deleted > 0:
                    break
            except Exception as exc:
                logger.warning(
                    "project_delete_best_effort_cleanup_failed target=jira_webhook cloud_id=%s error=%s",
                    cloud_id,
                    exc,
                )

    @staticmethod
    async def _cleanup_neo4j_best_effort(custom_project_id: str) -> None:
        normalized_project_id = str(custom_project_id or "").strip()
        if not normalized_project_id:
            return
        try:
            from app.graph.neo4j_client import neo4j_client
        except Exception as exc:
            logger.info(
                "project_delete_best_effort_cleanup_skipped target=neo4j project_id=%s reason=neo4j_module_unavailable error=%s",
                normalized_project_id,
                exc,
            )
            return

        try:
            if not neo4j_client.is_connected:
                connected = await neo4j_client.connect()
                if not connected:
                    logger.info(
                        "project_delete_best_effort_cleanup_skipped target=neo4j project_id=%s reason=neo4j_not_configured_or_unavailable",
                        normalized_project_id,
                    )
                    return
            deleted = await neo4j_client.delete_project_scope(normalized_project_id)
            logger.info(
                "project_delete_best_effort_cleanup_ok target=neo4j project_id=%s deleted_count=%s",
                normalized_project_id,
                int(deleted or 0),
            )
        except Exception as exc:
            logger.warning(
                "project_delete_best_effort_cleanup_failed target=neo4j project_id=%s error=%s",
                normalized_project_id,
                exc,
            )

    @staticmethod
    async def delete(project_id: str, *, actor_user_id: str) -> Dict[str, Any]:
        """Delete a project with owner authorization and staged cleanup."""
        db = get_database()
        try:
            object_id = ObjectId(project_id)
        except Exception:
            return {"status": "not_found"}
        try:
            project = await db.projects.find_one(
                {"_id": object_id},
                {"project_id": 1, "user_id": 1, "integrations.jira": 1},
            )
            if not project:
                return {"status": "not_found"}
            normalized_actor = str(actor_user_id or "").strip()
            owner_user_id = str(project.get("user_id") or "").strip()
            if not normalized_actor or owner_user_id != normalized_actor:
                return {"status": "forbidden"}
            custom_project_id = str(project.get("project_id") or "").strip()
            if not custom_project_id:
                logger.warning("project_delete_missing_custom_project_id mongo_project_id=%s", str(object_id))
                return {"status": "cleanup_failed", "detail": "missing_custom_project_id"}

            lock_acquired, lock_token = ProjectService._acquire_delete_lock(custom_project_id)
            if not lock_acquired:
                return {"status": "delete_in_progress"}

            try:
                await ProjectService._cleanup_required_pre_delete(db, custom_project_id)
                await ProjectService._cleanup_mongo_best_effort(db, object_id, custom_project_id)
                ProjectService._cleanup_redis_best_effort(custom_project_id)
                await ProjectService._cleanup_jira_webhook_best_effort(project)
                await ProjectService._cleanup_neo4j_best_effort(custom_project_id)

                result = await db.projects.delete_one({"_id": object_id, "user_id": normalized_actor})
                if result.deleted_count > 0:
                    return {"status": "deleted"}
                return {"status": "not_found"}
            finally:
                ProjectService._release_delete_lock(custom_project_id, lock_token)
        except RuntimeError as exc:
            logger.error(
                "project_delete_required_cleanup_failed project_id=%s error=%s",
                project_id,
                exc,
            )
            return {"status": "cleanup_failed", "detail": str(exc)}
        except Exception as exc:
            logger.exception("project_delete_failed project_id=%s error=%s", project_id, exc)
            return {"status": "cleanup_failed", "detail": "unexpected_delete_failure"}

    @staticmethod
    async def user_exists(user_id: str) -> bool:
        """Check if user exists"""
        db = get_database()
        user = await db.users.find_one({"user_id": user_id})
        return user is not None

    @staticmethod
    async def update_tools(project_id: str, user_id: str, tool_name: str, tool_data: Dict[str, Any]) -> bool:
        """Update integration tools for a project"""
        db = get_database()
        result = await db.projects.update_one(
            {"_id": ObjectId(project_id), "user_id": user_id},
            {
                "$set": {
                    f"tools.{tool_name}": tool_data,
                    "updated_at": datetime.utcnow()
                }
            }
        )
        return result.modified_count > 0

    @staticmethod
    async def remove_tool(project_id: str, user_id: str, tool_name: str) -> bool:
        """Remove an integration tool from a project"""
        db = get_database()
        result = await db.projects.update_one(
            {"_id": ObjectId(project_id), "user_id": user_id},
            {
                "$unset": {f"tools.{tool_name}": ""},
                "$set": {"updated_at": datetime.utcnow()}
            }
        )
        return result.modified_count > 0

    @staticmethod
    def _connected_pm_providers(project: Dict[str, Any]) -> List[str]:
        return connected_pm_providers_from_project_context(project or {})

    @staticmethod
    def _role_for_user(project: Dict[str, Any], user_id: str) -> Optional[str]:
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            return None
        if str(project.get("user_id") or "").strip() == normalized_user_id:
            return "owner"
        members = project.get("members") or []
        for member in members:
            if not isinstance(member, dict):
                continue
            if str(member.get("status") or "").strip().lower() != "active":
                continue
            if str(member.get("user_id") or "").strip() != normalized_user_id:
                continue
            role = str(member.get("role") or "").strip().lower()
            if role:
                return role
        return None

    @staticmethod
    async def set_active_provider(
        project_id: str,
        *,
        user_id: str,
        active_provider: Optional[str],
    ) -> Dict[str, Any]:
        """Set or unset active provider with owner/admin authorization."""
        db = get_database()
        try:
            object_id = ObjectId(project_id)
        except Exception as exc:
            raise ValueError("Invalid project id") from exc

        project = await db.projects.find_one({"_id": object_id})
        if not project:
            raise ValueError("Project not found")

        caller_role = ProjectService._role_for_user(project, user_id)
        if caller_role not in {"owner", "admin"}:
            raise PermissionError("Only owner/admin can update active provider")

        connected = ProjectService._connected_pm_providers(project)
        normalized_active = str(active_provider or "").strip().lower() or None
        if normalized_active and normalized_active not in set(connected):
            raise ValueError(f"Provider `{normalized_active}` is not connected for this project")

        await db.projects.update_one(
            {"_id": object_id},
            {
                "$set": {
                    "active_provider": normalized_active,
                    "updated_at": datetime.utcnow(),
                }
            },
        )
        return {
            "project_id": str(project.get("project_id") or "").strip() or project_id,
            "active_provider": normalized_active,
            "connected_providers": connected,
        }

    @staticmethod
    async def maybe_set_active_provider_on_connect(
        project_id: str,
        *,
        provider: str,
    ) -> None:
        """Set active provider only when currently unset and provider is connected."""
        db = get_database()
        try:
            object_id = ObjectId(project_id)
        except Exception:
            return
        project = await db.projects.find_one({"_id": object_id})
        if not project:
            return
        normalized_provider = str(provider or "").strip().lower()
        if not normalized_provider:
            return
        existing_active = str(project.get("active_provider") or "").strip().lower()
        if existing_active:
            return
        connected = set(ProjectService._connected_pm_providers(project))
        if normalized_provider not in connected:
            return
        await db.projects.update_one(
            {"_id": object_id, "active_provider": {"$in": [None, ""]}},
            {"$set": {"active_provider": normalized_provider, "updated_at": datetime.utcnow()}},
        )

    # ==================== MEMBER/INVITE OPERATIONS ====================

    @staticmethod
    async def create_invite(
        project_id: str,
        email: str,
        invited_by: str,
        role: str = "member",
        jira_account_id: str = None
    ) -> Dict[str, Any]:
        """
        Create a pending invite for a project.
        Adds a new member entry with status='pending' to the members array.
        """
        db = get_database()

        # Normalize email
        email = email.lower().strip()

        # Get the project
        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise ValueError("Project not found")

        # Check if already a member or has pending invite
        members = project.get("members", [])
        for member in members:
            member_email = member.get("email", "").lower()
            if member_email == email:
                if member.get("status") == "active":
                    raise ValueError(f"{email} is already a member of this project")
                elif member.get("status") == "pending":
                    raise ValueError(f"{email} already has a pending invite")

        # Generate unique invite token
        invite_token = str(uuid.uuid4())

        # If jira_account_id not provided, search Jira API to get it
        if not jira_account_id:
            jira_integration = project.get("integrations", {}).get("jira", {})
            if jira_integration.get("status") == "connected":
                try:
                    from app.integrations.jira.oauth import jira_oauth_service, get_fresh_jira_access_token

                    # Get fresh Jira access token
                    access_token = await get_fresh_jira_access_token(db=db, project=project)
                    cloud_id = jira_integration.get("cloud_id")

                    # Search Jira for this email to get account_id
                    jira_account_id = await jira_oauth_service.search_user_by_email(
                        access_token=access_token,
                        cloud_id=cloud_id,
                        email=email
                    )

                    if jira_account_id:
                        print(f"[INVITE] Found Jira account ID for {email}: {jira_account_id}")
                    else:
                        print(f"[INVITE] No Jira user found for {email}")
                except Exception as e:
                    print(f"[INVITE] Failed to search Jira for {email}: {e}")
                    # Continue without jira_account_id - will try auto-linking on accept as fallback

        # Create pending member entry
        new_member = {
            "user_id": None,
            "email": email,
            "name": None,
            "role": role,
            "status": "pending",
            "invite_token": invite_token,
            "invited_by": invited_by,
            "invited_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(days=7),
            "reminder_count": 0,
            "joined_at": None,
            "integration_ids": {
                "jira_account_id": jira_account_id
            } if jira_account_id else {}
        }

        # Add to members array
        await db.projects.update_one(
            {"_id": ObjectId(project_id)},
            {
                "$push": {"members": new_member},
                "$set": {"updated_at": datetime.utcnow()}
            }
        )

        # Update tasks with this email if jira_account_id provided
        if jira_account_id:
            # Get custom project_id for brain collection access
            custom_project_id = project.get("project_id", str(project["_id"]))
            brain_collection = get_brain_collection(custom_project_id)
            await brain_collection.update_many(
                {
                    "integration_type": "jira",
                    "assignee_account_id": jira_account_id,
                    "assignee_email": None
                },
                {"$set": {"assignee_email": email}}
            )

        # Get inviter info for the email
        inviter = await db.users.find_one({"user_id": invited_by})
        inviter_name = inviter.get("name", "A team member") if inviter else "A team member"

        # Send invitation email
        await send_invite_email(
            to_email=email,
            project_name=project.get("project_name", "Project"),
            inviter_name=inviter_name,
            invite_token=invite_token,
            role=role
        )

        return {
            "project_id": project_id,
            "email": email,
            "role": role,
            "invite_token": invite_token,
            "status": "pending"
        }

    @staticmethod
    async def remind_invite(project_id: str, email: str) -> Dict[str, Any]:
        """
        Resend invite to a pending member.
        Refreshes the invite token and extends expires_at, then resends the email.
        """
        db = get_database()
        email = email.lower().strip()

        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise ValueError("Project not found")

        members = project.get("members", [])
        pending_member = next(
            (m for m in members if m.get("email", "").lower() == email and m.get("status") == "pending"),
            None
        )
        if not pending_member:
            raise ValueError(f"No pending invite found for {email}")

        new_token = str(uuid.uuid4())
        new_expires = datetime.utcnow() + timedelta(days=7)

        await db.projects.update_one(
            {"_id": ObjectId(project_id), "members.email": email},
            {
                "$set": {
                    "members.$.invite_token": new_token,
                    "members.$.expires_at": new_expires,
                    "members.$.last_reminded_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow()
                },
                "$inc": {"members.$.reminder_count": 1}
            }
        )

        inviter = await db.users.find_one({"user_id": pending_member.get("invited_by")})
        inviter_name = inviter.get("name", "A team member") if inviter else "A team member"

        await send_invite_email(
            to_email=email,
            project_name=project.get("project_name", "Project"),
            inviter_name=inviter_name,
            invite_token=new_token,
            role=pending_member.get("role", "member")
        )

        return {"status": "reminded", "email": email}

    @staticmethod
    async def create_bulk_invites(
        project_id: str,
        emails: List[str],
        invited_by: str,
        role: str = "member"
    ) -> Dict[str, Any]:
        """Create multiple invites at once. Returns success and failed lists."""
        results = {"success": [], "failed": []}

        for email in emails:
            try:
                result = await ProjectService.create_invite(
                    project_id=project_id,
                    email=email,
                    invited_by=invited_by,
                    role=role
                )
                results["success"].append(result)
            except ValueError as e:
                results["failed"].append({"email": email, "error": str(e)})

        return results

    @staticmethod
    async def verify_invite(token: str) -> Dict[str, Any]:
        """
        Verify an invite token and return public info.
        Used for the invite acceptance page (no auth required).
        """
        db = get_database()

        # Find project with this invite token
        project = await db.projects.find_one({"members.invite_token": token})

        if not project:
            return {
                "valid": False,
                "expired": False,
                "already_accepted": False
            }

        # Find the specific member
        member = None
        for m in project.get("members", []):
            if m.get("invite_token") == token:
                member = m
                break

        if not member:
            return {
                "valid": False,
                "expired": False,
                "already_accepted": False
            }

        # Check if already accepted
        if member.get("status") == "active":
            return {
                "valid": False,
                "expired": False,
                "already_accepted": True,
                "email": member.get("email")
            }

        # Check if expired
        expires_at = member.get("expires_at")
        if expires_at and expires_at < datetime.utcnow():
            return {
                "valid": False,
                "expired": True,
                "already_accepted": False,
                "email": member.get("email")
            }

        # Check if declined
        if member.get("status") == "declined":
            return {
                "valid": False,
                "expired": False,
                "already_accepted": False,
                "email": member.get("email")
            }

        # Get inviter info
        inviter = await db.users.find_one({"user_id": member.get("invited_by")}) if member.get("invited_by") else None

        return {
            "valid": True,
            "project_id": str(project["_id"]),
            "project_name": project.get("project_name"),
            "invited_by_name": inviter.get("name") if inviter else None,
            "role": member.get("role"),
            "email": member.get("email"),
            "expired": False,
            "already_accepted": False
        }

    @staticmethod
    async def accept_invite(
        token: str,
        user_id: str,
        name: str,
        email: str
    ) -> Dict[str, Any]:
        """
        Accept an invitation.
        Updates the member entry with user info and sets status to active.
        """
        db = get_database()

        # Find project with this invite token
        project = await db.projects.find_one({"members.invite_token": token})

        if not project:
            raise ValueError("Invalid invite token")

        # Find the specific member
        member_index = None
        member = None
        for i, m in enumerate(project.get("members", [])):
            if m.get("invite_token") == token:
                member_index = i
                member = m
                break

        if member is None:
            raise ValueError("Invalid invite token")

        # Check if already accepted
        if member.get("status") == "active":
            raise ValueError("Invite has already been accepted")

        # Check if expired
        expires_at = member.get("expires_at")
        if expires_at and expires_at < datetime.utcnow():
            raise ValueError("Invite has expired")

        # Verify email matches (case-insensitive)
        if member.get("email", "").lower() != email.lower():
            raise ValueError("Email does not match the invite")

        # Update the member entry
        await db.projects.update_one(
            {"_id": project["_id"], "members.invite_token": token},
            {
                "$set": {
                    f"members.{member_index}.user_id": user_id,
                    f"members.{member_index}.name": name,
                    f"members.{member_index}.status": "active",
                    f"members.{member_index}.invite_token": None,
                    f"members.{member_index}.expires_at": None,
                    f"members.{member_index}.joined_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow()
                }
            }
        )

        # Sync integration IDs if integrations are connected
        try:
            # Check if Jira is connected
            jira_integration = project.get("integrations", {}).get("jira", {})
            if jira_integration.get("status") == "connected":
                from app.integrations.jira.oauth import jira_oauth_service, get_fresh_jira_access_token

                cloud_id = jira_integration.get("cloud_id")

                # Fetch a valid (auto-refreshed if expired) Jira access token.
                access_token = await get_fresh_jira_access_token(db=db, project=project)

                # Sync this member's Jira account ID as fallback
                # (in case initial search at invite time failed)
                # Sync this member's Jira account ID
                await jira_oauth_service.sync_single_member_jira_id(
                    db=db,
                    project_id=str(project["_id"]),
                    member_email=email,
                    access_token=access_token,
                    cloud_id=cloud_id
                )
                # Note: Task updates already happened at invite time (line 433-442)
                # No need to update tasks again here

            # Check if Slack is connected and sync this member's Slack user ID.
            slack_integration = project.get("integrations", {}).get("slack", {})
            if slack_integration.get("status") == "connected":
                from app.integrations.slack.member_sync import sync_single_member_slack_id_detailed
                from app.integrations.slack.welcome import send_project_welcome_to_member_detailed

                slack_sync_result = await sync_single_member_slack_id_detailed(
                    db=db,
                    project_id=str(project["_id"]),
                    member_email=email,
                    overwrite=False,
                )
                print(
                    "Slack member sync after invite acceptance: "
                    f"project_id={project.get('project_id')} email={email} "
                    f"status={slack_sync_result.get('status')}"
                )

                welcome_result = await send_project_welcome_to_member_detailed(
                    db=db,
                    project_id=str(project["_id"]),
                    member_email=email,
                )
                print(
                    "Slack welcome after invite acceptance: "
                    f"project_id={project.get('project_id')} email={email} "
                    f"status={welcome_result.get('status')}"
                )

            # TODO: Add similar checks for ClickUp, Asana, etc. when implemented

        except Exception as e:
            print(f"Integration sync failed for {email}: {str(e)}")
            # Don't fail invite acceptance if integration sync fails

        return {
            "project_id": str(project["_id"]),
            "project_name": project.get("project_name"),
            "user_id": user_id,
            "email": email,
            "role": member.get("role"),
            "status": "active"
        }

    @staticmethod
    async def decline_invite(token: str) -> Dict[str, Any]:
        """Decline an invitation"""
        db = get_database()

        # Find project with this invite token
        project = await db.projects.find_one({"members.invite_token": token})

        if not project:
            raise ValueError("Invalid invite token")

        # Find the specific member
        member_index = None
        member = None
        for i, m in enumerate(project.get("members", [])):
            if m.get("invite_token") == token:
                member_index = i
                member = m
                break

        if member is None:
            raise ValueError("Invalid invite token")

        if member.get("status") != "pending":
            raise ValueError("Invite is no longer pending")

        # Update status to declined
        await db.projects.update_one(
            {"_id": project["_id"], "members.invite_token": token},
            {
                "$set": {
                    f"members.{member_index}.status": "declined",
                    f"members.{member_index}.invite_token": None,
                    f"members.{member_index}.expires_at": None,
                    "updated_at": datetime.utcnow()
                }
            }
        )

        return {
            "project_id": str(project["_id"]),
            "email": member.get("email"),
            "status": "declined"
        }

    @staticmethod
    async def resend_invite(project_id: str, email: str) -> Dict[str, Any]:
        """Resend an invitation - generates new token and extends expiry"""
        db = get_database()

        email = email.lower().strip()

        # Find the project
        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise ValueError("Project not found")

        # Find the pending member
        member_index = None
        member = None
        for i, m in enumerate(project.get("members", [])):
            if m.get("email", "").lower() == email and m.get("status") == "pending":
                member_index = i
                member = m
                break

        if member is None:
            raise ValueError("No pending invite found for this email")

        # Generate new token and extend expiry
        new_token = str(uuid.uuid4())

        await db.projects.update_one(
            {"_id": ObjectId(project_id)},
            {
                "$set": {
                    f"members.{member_index}.invite_token": new_token,
                    f"members.{member_index}.expires_at": datetime.utcnow() + timedelta(days=7),
                    "updated_at": datetime.utcnow()
                }
            }
        )

        # Get inviter info and resend email
        invited_by = member.get("invited_by")
        inviter = await db.users.find_one({"user_id": invited_by}) if invited_by else None
        inviter_name = inviter.get("name", "A team member") if inviter else "A team member"

        await send_invite_email(
            to_email=email,
            project_name=project.get("project_name", "Project"),
            inviter_name=inviter_name,
            invite_token=new_token,
            role=member.get("role", "member")
        )

        return {
            "project_id": project_id,
            "email": email,
            "invite_token": new_token,
            "status": "pending"
        }

    @staticmethod
    async def cancel_invite(project_id: str, email: str) -> bool:
        """Cancel/remove a pending invitation"""
        db = get_database()

        email = email.lower().strip()

        # Find the project
        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise ValueError("Project not found")

        # Check if there's a pending invite for this email
        has_pending = False
        for m in project.get("members", []):
            if m.get("email", "").lower() == email and m.get("status") == "pending":
                has_pending = True
                break

        if not has_pending:
            raise ValueError("No pending invite found for this email")

        # Remove the member from array
        result = await db.projects.update_one(
            {"_id": ObjectId(project_id)},
            {
                "$pull": {"members": {"email": email, "status": "pending"}},
                "$set": {"updated_at": datetime.utcnow()}
            }
        )

        return result.modified_count > 0

    @staticmethod
    async def get_members(
        project_id: str,
        status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get all members of a project, optionally filtered by status"""
        db = get_database()

        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise ValueError("Project not found")

        members = project.get("members", [])

        if status:
            members = [m for m in members if m.get("status") == status]

        return members

    @staticmethod
    async def update_member_role(
        project_id: str,
        user_id: str,
        new_role: str,
        *,
        actor_user_id: str,
    ) -> Dict[str, Any]:
        """Update a member's role"""
        db = get_database()

        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise ValueError("Project not found")

        normalized_actor_id = str(actor_user_id or "").strip()
        caller_role = ProjectService._role_for_user(project, normalized_actor_id)
        if caller_role != "owner":
            raise PermissionError("Only owner can update member role")

        normalized_role = str(new_role or "").strip().lower()
        if normalized_role not in {"owner", "member"}:
            raise ValueError("Role must be one of: owner, member")

        # Find the member
        member_index = None
        member = None
        for i, m in enumerate(project.get("members", [])):
            if m.get("user_id") == user_id and str(m.get("status") or "").strip().lower() == "active":
                member_index = i
                member = m
                break

        if member is None:
            raise ValueError("Active member not found")

        current_role = str(member.get("role") or "").strip().lower()
        if current_role == normalized_role:
            return {
                "project_id": project_id,
                "user_id": user_id,
                "role": normalized_role
            }

        active_owners = [
            m for m in (project.get("members") or [])
            if str(m.get("status") or "").strip().lower() == "active"
            and str(m.get("role") or "").strip().lower() == "owner"
        ]
        if current_role == "owner" and normalized_role != "owner" and len(active_owners) <= 1:
            raise ValueError("At least one owner must remain in the project")

        await db.projects.update_one(
            {"_id": ObjectId(project_id)},
            {
                "$set": {
                    f"members.{member_index}.role": normalized_role,
                    "updated_at": datetime.utcnow()
                }
            }
        )

        return {
            "project_id": project_id,
            "user_id": user_id,
            "role": normalized_role
        }

    @staticmethod
    async def remove_member(
        project_id: str,
        user_id: str,
        *,
        actor_user_id: str,
    ) -> bool:
        """Remove a member from the project (sets status to 'removed')"""
        db = get_database()

        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise ValueError("Project not found")

        normalized_actor_id = str(actor_user_id or "").strip()
        caller_role = ProjectService._role_for_user(project, normalized_actor_id)
        if caller_role != "owner":
            raise PermissionError("Only owner can remove members")

        # Find the member
        member_index = None
        member = None
        for i, m in enumerate(project.get("members", [])):
            if m.get("user_id") == user_id:
                member_index = i
                member = m
                break

        if member is None:
            raise ValueError("Member not found")

        if str(member.get("role") or "").strip().lower() == "owner":
            active_owners = [
                m for m in (project.get("members") or [])
                if str(m.get("status") or "").strip().lower() == "active"
                and str(m.get("role") or "").strip().lower() == "owner"
            ]
            if len(active_owners) <= 1:
                raise ValueError("At least one owner must remain in the project")

        # Set status to removed (soft delete)
        await db.projects.update_one(
            {"_id": ObjectId(project_id)},
            {
                "$set": {
                    f"members.{member_index}.status": "removed",
                    "updated_at": datetime.utcnow()
                }
            }
        )

        custom_project_id = str(project.get("project_id") or "").strip()
        slack_team_id = str(
            ((project.get("integrations") or {}).get("slack") or {}).get("team_id") or ""
        ).strip()
        member_slack_user_id = str(
            ((member.get("integration_ids") or {}).get("slack_user_id") or "")
        ).strip()
        if custom_project_id and slack_team_id and member_slack_user_id:
            try:
                deleted_rows = await clear_active_project_rows(
                    db=db,
                    provider="slack",
                    workspace_id=slack_team_id,
                    external_user_id=member_slack_user_id,
                    project_id=custom_project_id,
                )
                print(
                    "channel_index_cleanup_on_member_removed: "
                    f"project_id={custom_project_id} workspace_id={slack_team_id} "
                    f"external_user_id={member_slack_user_id} deleted_count={deleted_rows}"
                )
            except Exception as exc:
                print(
                    "channel_index_cleanup_on_member_removed_failed: "
                    f"project_id={custom_project_id} workspace_id={slack_team_id} "
                    f"external_user_id={member_slack_user_id} error={exc}"
                )

        return True

    @staticmethod
    async def is_member(project_id: str, user_id: str) -> bool:
        """Check if user is an active member of the project"""
        db = get_database()

        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            return False

        for m in project.get("members", []):
            if m.get("user_id") == user_id and m.get("status") == "active":
                return True

        return False

    @staticmethod
    async def get_orphan_assignees(project_id: str) -> List[Dict[str, Any]]:
        """
        Get task assignees who are not project members.
        Returns list of assignees with their task counts.
        """
        db = get_database()

        # Get project and its members' Jira account IDs
        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise ValueError("Project not found")

        # Collect all Jira account IDs from active and pending members
        member_jira_ids = set()
        for member in project.get("members", []):
            if member.get("status") in ["active", "pending"]:
                jira_id = member.get("integration_ids", {}).get("jira_account_id")
                if jira_id:
                    member_jira_ids.add(jira_id)

        # Get custom project_id for brain collection access
        custom_project_id = project.get("project_id", str(project["_id"]))

        # Get all tasks for this project from brain collection
        # Note: Tasks don't have project_id field - it's implicit from collection name
        brain_collection = get_brain_collection(custom_project_id)
        tasks = await brain_collection.find({
            "integration_type": "jira",
            "assignee_account_id": {"$ne": None, "$exists": True}
        }).to_list(length=None)

        # Group tasks by assignee_account_id
        assignee_tasks = {}
        for task in tasks:
            account_id = task.get("assignee_account_id")
            if not account_id:
                continue

            # Skip if this assignee is already a member
            if account_id in member_jira_ids:
                continue

            if account_id not in assignee_tasks:
                assignee_tasks[account_id] = {
                    "account_id": account_id,
                    "name": task.get("assignee_name") or "Unknown",
                    "task_count": 0
                }
            assignee_tasks[account_id]["task_count"] += 1

        return list(assignee_tasks.values())

    @staticmethod
    async def get_team_workload(project_id: str) -> List[Dict[str, Any]]:
        """
        Get team workload: task counts per member by status.
        Returns list with completed, in_progress, and todo counts for each member.

        Args:
            project_id: MongoDB _id of the project (ObjectId string)
        """
        db = get_database()

        # Get project and its active members
        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise ValueError("Project not found")

        # Get custom project_id for brain collection access
        custom_project_id = project.get("project_id")
        if not custom_project_id:
            raise ValueError("Project missing custom project_id")

        # Get all active members
        active_members = [
            member for member in project.get("members", [])
            if member.get("status") == "active"
        ]

        # Get all tasks from brain collection using custom project_id
        # Note: Tasks don't have project_id field - it's implicit from collection name
        brain_collection = get_brain_collection(custom_project_id)
        tasks = await brain_collection.find({
            "assignee_account_id": {"$ne": None, "$exists": True}
        }).to_list(length=None)

        # Initialize workload for each member
        workload = {}
        for member in active_members:
            jira_account_id = member.get("integration_ids", {}).get("jira_account_id")
            if jira_account_id:
                workload[jira_account_id] = {
                    "name": member.get("name") or member.get("email", "").split("@")[0],
                    "email": member.get("email"),
                    "completed": 0,
                    "in_progress": 0,
                    "todo": 0,
                    "total": 0
                }

        # Initialize task lists for each member
        for jira_id in workload.keys():
            workload[jira_id]["completed_tasks"] = []
            workload[jira_id]["in_progress_tasks"] = []
            workload[jira_id]["todo_tasks"] = []

        # Count tasks and store task details by status for each member
        for task in tasks:
            account_id = task.get("assignee_account_id")
            if not account_id or account_id not in workload:
                continue

            status = task.get("status", "").lower()
            workload[account_id]["total"] += 1

            # Create task detail object
            task_detail = {
                "external_id": task.get("external_id"),
                "title": task.get("title"),
                "url": task.get("url"),
                "priority": task.get("priority")
            }

            if status == "done":
                workload[account_id]["completed"] += 1
                workload[account_id]["completed_tasks"].append(task_detail)
            elif status == "in_progress":
                workload[account_id]["in_progress"] += 1
                workload[account_id]["in_progress_tasks"].append(task_detail)
            elif status == "todo":
                workload[account_id]["todo"] += 1
                workload[account_id]["todo_tasks"].append(task_detail)

        # Convert to list and calculate capacity percentage
        result = []
        for member_data in workload.values():
            total = member_data["total"]
            active_tasks = member_data["in_progress"] + member_data["todo"]

            # Calculate capacity (0-100%)
            # Assume max 10 active tasks = 100% capacity
            capacity_percentage = min(100, int((active_tasks / 10) * 100)) if active_tasks > 0 else 0

            member_data["capacity_percentage"] = capacity_percentage
            member_data["is_overloaded"] = active_tasks > 8  # Flag if >8 active tasks

            result.append(member_data)

        # Sort by capacity (most loaded first)
        result.sort(key=lambda x: x["capacity_percentage"], reverse=True)

        return result

    @staticmethod
    async def get_overdue_tasks(project_id: str) -> List[Dict[str, Any]]:
        """
        Get overdue tasks for a project.
        Returns tasks past their due date with severity levels.
        """
        db = get_database()
        
        # Get project
        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise ValueError("Project not found")
        
        # Get custom project_id for brain collection
        custom_project_id = project.get("project_id", str(project["_id"]))
        brain_collection = get_brain_collection(custom_project_id)
        
        # Get current date
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Query overdue tasks
        tasks = await brain_collection.find({
            "entity_type": "work_item",
            "integration_type": "jira",
            "status": {"$ne": "done"},
            "due_date": {"$lt": today, "$ne": None, "$exists": True}
        }).to_list(length=None)
        
        result = []
        for task in tasks:
            due_date = task.get("due_date")
            if not due_date:
                continue
            
            # Calculate days overdue
            days_overdue = (today - due_date).days
            
            # Determine severity
            if days_overdue > 7:
                severity = "critical"
            elif days_overdue >= 3:
                severity = "warning"
            else:
                severity = "recent"
            
            result.append({
                "task_key": task.get("external_id"),
                "title": task.get("title"),
                "assignee_name": task.get("assignee_name"),
                "assignee_email": task.get("assignee_email"),
                "due_date": due_date.isoformat() if due_date else None,
                "start_date": task.get("start_date").isoformat() if task.get("start_date") else None,
                "days_overdue": days_overdue,
                "severity": severity,
                "url": task.get("url"),
                "status": task.get("status")
            })
        
        # Sort by days overdue (most overdue first)
        result.sort(key=lambda x: x["days_overdue"], reverse=True)
        
        return result

    @staticmethod
    async def get_bottlenecks(project_id: str) -> List[Dict[str, Any]]:
        """
        Detect tasks stuck in progress for >3 days.
        Returns tasks that haven't been updated recently.
        """
        db = get_database()
        
        # Get project
        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise ValueError("Project not found")
        
        # Get custom project_id for brain collection
        custom_project_id = project.get("project_id", str(project["_id"]))
        brain_collection = get_brain_collection(custom_project_id)
        
        # Calculate threshold date (3 days ago)
        threshold_date = datetime.utcnow() - timedelta(days=3)
        
        # Query stuck tasks
        tasks = await brain_collection.find({
            "entity_type": "work_item",
            "integration_type": "jira",
            "status": "in_progress",
            "updated_at": {"$lt": threshold_date, "$exists": True}
        }).to_list(length=None)
        
        result = []
        for task in tasks:
            updated_at = task.get("updated_at")
            if not updated_at:
                continue
            
            # Calculate days stuck
            days_stuck = (datetime.utcnow() - updated_at).days
            
            result.append({
                "task_key": task.get("external_id"),
                "title": task.get("title"),
                "assignee_name": task.get("assignee_name"),
                "assignee_email": task.get("assignee_email"),
                "status": task.get("status"),
                "start_date": task.get("start_date").isoformat() if task.get("start_date") else None,
                "days_stuck": days_stuck,
                "last_updated": updated_at.isoformat() if updated_at else None,
                "url": task.get("url")
            })
        
        # Sort by days stuck (most stuck first)
        result.sort(key=lambda x: x["days_stuck"], reverse=True)
        
        return result

    @staticmethod
    async def get_burndown_data(project_id: str) -> Dict[str, Any]:
        """
        Get burndown chart data for current sprint (2-week period).
        Returns daily actual vs ideal remaining tasks.
        """
        db = get_database()
        
        # Get project
        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise ValueError("Project not found")
        
        # Get custom project_id for brain collection
        custom_project_id = project.get("project_id", str(project["_id"]))
        brain_collection = get_brain_collection(custom_project_id)
        
        # Define sprint (last 14 days for now - can be made configurable)
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        sprint_start = today - timedelta(days=13)  # 14 days total including today
        sprint_days = 14
        
        # Get all tasks
        all_tasks = await brain_collection.find({
            "integration_type": "jira"
        }).to_list(length=None)
        
        # Count total tasks at sprint start
        total_tasks = len([t for t in all_tasks if t.get("created_at") and t.get("created_at") <= sprint_start])
        
        # Generate daily data
        daily_data = []
        for day_offset in range(sprint_days):
            current_date = sprint_start + timedelta(days=day_offset)
            
            # Count remaining tasks (not done) at this date
            remaining = 0
            for task in all_tasks:
                created_at = task.get("created_at")
                updated_at = task.get("updated_at")
                status = task.get("status")
                
                # Task existed at this date
                if created_at and created_at <= current_date:
                    # Check if it was completed before this date
                    if status == "done" and updated_at and updated_at <= current_date:
                        continue  # Task was done, don't count
                    else:
                        remaining += 1
            
            # Calculate ideal remaining (linear burndown)
            ideal_remaining = total_tasks - (total_tasks * (day_offset / (sprint_days - 1)))
            
            daily_data.append({
                "date": current_date.isoformat(),
                "day": day_offset + 1,
                "actual_remaining": remaining,
                "ideal_remaining": round(ideal_remaining, 1)
            })
        
        # Calculate metrics
        completed_tasks = len([t for t in all_tasks if t.get("status") == "done"])
        remaining_tasks = total_tasks - completed_tasks
        
        # Calculate velocity (tasks per day)
        days_elapsed = (today - sprint_start).days + 1
        velocity = completed_tasks / days_elapsed if days_elapsed > 0 else 0
        
        # Predict completion date
        if velocity > 0 and remaining_tasks > 0:
            days_to_complete = remaining_tasks / velocity
            predicted_completion = today + timedelta(days=int(days_to_complete))
        else:
            predicted_completion = None
        
        # Check if on track (will complete within sprint)
        sprint_end = sprint_start + timedelta(days=sprint_days - 1)
        is_on_track = predicted_completion <= sprint_end if predicted_completion else False
        
        return {
            "daily_data": daily_data,
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "remaining_tasks": remaining_tasks,
            "velocity": round(velocity, 2),
            "predicted_completion_date": predicted_completion.isoformat() if predicted_completion else None,
            "is_on_track": is_on_track,
            "sprint_start": sprint_start.isoformat(),
            "sprint_end": sprint_end.isoformat()
        }

    @staticmethod
    async def get_velocity_trend(project_id: str) -> Dict[str, Any]:
        """
        Get velocity trend for last 5 sprints (2-week periods).
        Returns historical velocity and trend analysis.
        """
        db = get_database()
        
        # Get project
        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise ValueError("Project not found")
        
        # Get custom project_id for brain collection
        custom_project_id = project.get("project_id", str(project["_id"]))
        brain_collection = get_brain_collection(custom_project_id)
        
        # Get all completed tasks
        completed_tasks = await brain_collection.find({
            "integration_type": "jira",
            "status": "done",
            "updated_at": {"$exists": True}
        }).to_list(length=None)
        
        # Define 5 sprints (2 weeks each, going back 10 weeks)
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        sprint_data = []
        
        for sprint_num in range(5, 0, -1):  # Sprint 5 (oldest) to Sprint 1 (current)
            sprint_end = today - timedelta(days=(sprint_num - 1) * 14)
            sprint_start = sprint_end - timedelta(days=13)  # 14 days total
            
            # Count tasks completed in this sprint
            tasks_completed = len([
                t for t in completed_tasks
                if t.get("updated_at") and sprint_start <= t.get("updated_at") <= sprint_end
            ])
            
            sprint_data.append({
                "sprint_number": 6 - sprint_num,  # Sprint 1, 2, 3, 4, 5
                "tasks_completed": tasks_completed,
                "start_date": sprint_start.isoformat(),
                "end_date": sprint_end.isoformat()
            })
        
        # Calculate average velocity
        velocities = [s["tasks_completed"] for s in sprint_data]
        average_velocity = sum(velocities) / len(velocities) if velocities else 0
        
        # Calculate trend (compare last 2 sprints to first 2 sprints)
        if len(velocities) >= 4:
            recent_avg = (velocities[-1] + velocities[-2]) / 2
            older_avg = (velocities[0] + velocities[1]) / 2
            
            if older_avg > 0:
                trend_percentage = ((recent_avg - older_avg) / older_avg) * 100
            else:
                trend_percentage = 0
            
            if trend_percentage > 10:
                trend = "improving"
            elif trend_percentage < -10:
                trend = "declining"
            else:
                trend = "stable"
        else:
            trend = "insufficient_data"
            trend_percentage = 0
        
        # Predict next sprint (average of last 3 sprints)
        if len(velocities) >= 3:
            predicted_next_sprint = sum(velocities[-3:]) / 3
        else:
            predicted_next_sprint = average_velocity
        
        return {
            "sprint_data": sprint_data,
            "average_velocity": round(average_velocity, 1),
            "trend": trend,
            "trend_percentage": round(trend_percentage, 1),
            "predicted_next_sprint": round(predicted_next_sprint, 1)
        }


    @staticmethod
    async def get_team_activity_heatmap(project_id: str, week_start: str = None) -> dict:
        """
        Get team activity heatmap showing status changes by team member for a given week.

        Args:
            project_id: MongoDB ObjectId string
            week_start: ISO date string for Monday of the week (e.g. "2026-02-17").
                        Defaults to Monday of the current week.
        """
        from datetime import datetime, timedelta

        db = get_database()
        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise ValueError("Project not found")

        # Resolve week boundaries
        if week_start:
            try:
                week_start_dt = datetime.fromisoformat(week_start).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
            except (ValueError, TypeError):
                today = datetime.utcnow()
                week_start_dt = (today - timedelta(days=today.weekday())).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
        else:
            today = datetime.utcnow()
            week_start_dt = (today - timedelta(days=today.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            )

        week_end_dt = week_start_dt + timedelta(days=7)

        # Build list of date labels for each column (Mon–Sun)
        week_dates = [
            (week_start_dt + timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(7)
        ]

        # Get custom project_id for brain collection
        custom_project_id = project.get("project_id", str(project["_id"]))
        brain_collection = get_brain_collection(custom_project_id)

        # Fetch tasks whose status changed within this week
        tasks = await brain_collection.find({
            "integration_type": "jira",
            "status_changed_at": {
                "$gte": week_start_dt,
                "$lt": week_end_dt
            }
        }).to_list(length=None)

        # Build activity map: {assignee_name: [count_day0, ..., count_day6]}
        activity_map: dict = {}

        for task in tasks:
            assignee_name = task.get("assignee_name")
            if not assignee_name:
                continue

            status_changed_at = task.get("status_changed_at")
            if not status_changed_at:
                continue

            # Determine which day slot (0=Mon, 6=Sun) within the week
            day_index = (status_changed_at - week_start_dt).days
            if not (0 <= day_index <= 6):
                continue

            if assignee_name not in activity_map:
                activity_map[assignee_name] = [0] * 7

            activity_map[assignee_name][day_index] += 1

        # Convert to list format for frontend
        heatmap_data = []
        for assignee_name, daily_counts in activity_map.items():
            total_activity = sum(daily_counts)
            heatmap_data.append({
                "assignee_name": assignee_name,
                "total_activity": total_activity,
                "daily_counts": daily_counts
            })

        # Sort by total activity (most active first)
        heatmap_data.sort(key=lambda x: x["total_activity"], reverse=True)

        return {
            "heatmap_data": heatmap_data,
            "week_start": week_start_dt.strftime("%Y-%m-%d"),
            "week_dates": week_dates,
            "total_team_activity": sum(item["total_activity"] for item in heatmap_data)
        }

    @staticmethod
    async def get_tasks_due_24h(project_id: str) -> List[Dict[str, Any]]:
        """Tasks due within the next 24 hours (not yet done)."""
        db = get_database()
        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise ValueError("Project not found")

        custom_project_id = project.get("project_id", str(project["_id"]))
        brain_collection = get_brain_collection(custom_project_id)

        now = datetime.utcnow()
        in_24h = now + timedelta(hours=24)

        tasks = await brain_collection.find({
            "entity_type": "work_item",
            "integration_type": "jira",
            "status": {"$nin": ["done"]},
            "due_date": {"$gte": now, "$lte": in_24h, "$exists": True}
        }).to_list(length=None)

        result = []
        for task in tasks:
            due_date = task.get("due_date")
            hours_left = int((due_date - now).total_seconds() / 3600) if due_date else None
            result.append({
                "task_key": task.get("external_id"),
                "title": task.get("title"),
                "assignee_name": task.get("assignee_name"),
                "assignee_email": task.get("assignee_email"),
                "priority": task.get("priority"),
                "due_date": due_date.isoformat() if due_date else None,
                "hours_left": hours_left,
                "status": task.get("status"),
                "url": task.get("url"),
            })

        result.sort(key=lambda x: x["hours_left"] or 999)
        return result

    @staticmethod
    async def get_tasks_due_today(project_id: str) -> List[Dict[str, Any]]:
        """Tasks due today (not yet done)."""
        db = get_database()
        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise ValueError("Project not found")

        custom_project_id = project.get("project_id", str(project["_id"]))
        brain_collection = get_brain_collection(custom_project_id)

        # Project timezone (strict mode: source is projects.timezone)
        project_tz_str = project.get("timezone", "UTC")
        project_tz = get_zoneinfo_or_utc(project_tz_str, context="tasks_due_today")
        utc_tz = get_zoneinfo_or_utc("UTC", context="tasks_due_today_utc")

        # Get current time in project timezone
        now_local = datetime.now(project_tz)

        # Today window in project timezone (start of day to end of day)
        today_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end_local = now_local.replace(hour=23, minute=59, second=59, microsecond=999999)

        # Convert to UTC for MongoDB query (tasks stored as UTC)
        today_start = today_start_local.astimezone(utc_tz).replace(tzinfo=None)
        today_end = today_end_local.astimezone(utc_tz).replace(tzinfo=None)

        tasks = await brain_collection.find({
            "entity_type": "work_item",
            "integration_type": "jira",
            "status": {"$nin": ["done"]},
            "due_date": {
                "$exists": True,
                "$ne": None,
                "$gte": today_start,
                "$lte": today_end
            }
        }).to_list(length=None)

        result = []
        for task in tasks:
            result.append({
                "task_key": task.get("external_id"),
                "title": task.get("title"),
                "assignee_name": task.get("assignee_name"),
                "url": task.get("url"),
            })

        return result

    @staticmethod
    async def get_tasks_due_tomorrow(project_id: str) -> List[Dict[str, Any]]:
        """Tasks due tomorrow (not yet done)."""
        db = get_database()
        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise ValueError("Project not found")

        custom_project_id = project.get("project_id", str(project["_id"]))
        brain_collection = get_brain_collection(custom_project_id)

        # Project timezone (strict mode: source is projects.timezone)
        project_tz_str = project.get("timezone", "UTC")
        project_tz = get_zoneinfo_or_utc(project_tz_str, context="tasks_due_tomorrow")
        utc_tz = get_zoneinfo_or_utc("UTC", context="tasks_due_tomorrow_utc")

        # Get current time in project timezone
        now_local = datetime.now(project_tz)

        # Tomorrow window in project timezone
        tomorrow_local = now_local + timedelta(days=1)
        tomorrow_start_local = tomorrow_local.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_end_local = tomorrow_local.replace(hour=23, minute=59, second=59, microsecond=999999)

        # Convert to UTC for MongoDB query
        tomorrow_start = tomorrow_start_local.astimezone(utc_tz).replace(tzinfo=None)
        tomorrow_end = tomorrow_end_local.astimezone(utc_tz).replace(tzinfo=None)

        tasks = await brain_collection.find({
            "entity_type": "work_item",
            "integration_type": "jira",
            "status": {"$nin": ["done"]},
            "due_date": {
                "$exists": True,
                "$ne": None,
                "$gte": tomorrow_start,
                "$lte": tomorrow_end
            }
        }).to_list(length=None)

        result = []
        for task in tasks:
            result.append({
                "task_key": task.get("external_id"),
                "title": task.get("title"),
                "assignee_name": task.get("assignee_name"),
                "url": task.get("url"),
            })

        return result

    @staticmethod
    async def get_tomorrow_availability(project_id: str) -> List[Dict[str, Any]]:
        """Members who have no tasks due tomorrow — available capacity."""
        db = get_database()
        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise ValueError("Project not found")

        custom_project_id = project.get("project_id", str(project["_id"]))
        brain_collection = get_brain_collection(custom_project_id)

        # Tomorrow window
        tomorrow_start = (datetime.utcnow() + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        tomorrow_end = tomorrow_start + timedelta(days=1)

        # Active members only
        active_members = [
            m for m in project.get("members", [])
            if m.get("status") == "active" and m.get("email")
        ]

        # Find emails that HAVE tasks due tomorrow
        tasks_tomorrow = await brain_collection.find({
            "entity_type": "work_item",
            "integration_type": "jira",
            "status": {"$nin": ["done"]},
            "due_date": {"$gte": tomorrow_start, "$lt": tomorrow_end},
            "assignee_email": {"$exists": True, "$ne": None}
        }).to_list(length=None)

        busy_emails = {t.get("assignee_email", "").lower() for t in tasks_tomorrow}

        result = []
        for member in active_members:
            email = member.get("email", "").lower()
            if email not in busy_emails:
                result.append({
                    "name": member.get("name") or email.split("@")[0],
                    "email": email,
                    "role": member.get("role"),
                })

        return result

    @staticmethod
    async def get_unassigned_urgent(project_id: str) -> List[Dict[str, Any]]:
        """High/critical priority tasks with no assignee."""
        db = get_database()
        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise ValueError("Project not found")

        custom_project_id = project.get("project_id", str(project["_id"]))
        brain_collection = get_brain_collection(custom_project_id)

        tasks = await brain_collection.find({
            "entity_type": "work_item",
            "integration_type": "jira",
            "status": {"$nin": ["done"]},
            "priority": {"$in": ["high", "critical"]},
            "$or": [
                {"assignee_email": None},
                {"assignee_email": {"$exists": False}},
                {"assignee_email": ""},
            ]
        }).to_list(length=None)

        priority_order = {"critical": 0, "high": 1}
        result = [{
            "task_key": task.get("external_id"),
            "title": task.get("title"),
            "priority": task.get("priority"),
            "status": task.get("status"),
            "due_date": task.get("due_date").isoformat() if task.get("due_date") else None,
            "start_date": task.get("start_date").isoformat() if task.get("start_date") else None,
            "url": task.get("url"),
        } for task in tasks]

        result.sort(key=lambda x: priority_order.get(x["priority"], 99))
        return result

    @staticmethod
    async def get_silent_tasks(project_id: str) -> List[Dict[str, Any]]:
        """Tasks not touched in 5+ days and still not done."""
        db = get_database()
        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise ValueError("Project not found")

        custom_project_id = project.get("project_id", str(project["_id"]))
        brain_collection = get_brain_collection(custom_project_id)

        threshold = datetime.utcnow() - timedelta(days=5)

        tasks = await brain_collection.find({
            "entity_type": "work_item",
            "integration_type": "jira",
            "status": {"$nin": ["done"]},
            "updated_at": {"$lt": threshold, "$exists": True}
        }).to_list(length=None)

        result = []
        for task in tasks:
            updated_at = task.get("updated_at")
            days_silent = (datetime.utcnow() - updated_at).days if updated_at else None
            result.append({
                "task_key": task.get("external_id"),
                "title": task.get("title"),
                "assignee_name": task.get("assignee_name"),
                "assignee_email": task.get("assignee_email"),
                "priority": task.get("priority"),
                "status": task.get("status"),
                "start_date": task.get("start_date").isoformat() if task.get("start_date") else None,
                "days_silent": days_silent,
                "last_updated": updated_at.isoformat() if updated_at else None,
                "url": task.get("url"),
            })

        result.sort(key=lambda x: x["days_silent"] or 0, reverse=True)
        return result

    @staticmethod
    async def get_stale_reviews(project_id: str) -> List[Dict[str, Any]]:
        """Tasks stuck in a review-like status for 2+ days."""
        db = get_database()
        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        if not project:
            raise ValueError("Project not found")

        custom_project_id = project.get("project_id", str(project["_id"]))
        brain_collection = get_brain_collection(custom_project_id)

        threshold = datetime.utcnow() - timedelta(days=2)

        tasks = await brain_collection.find({
            "entity_type": "work_item",
            "integration_type": "jira",
            "status": {"$in": ["in_review", "review", "code_review", "pr_review", "testing", "qa"]},
            "updated_at": {"$lt": threshold, "$exists": True}
        }).to_list(length=None)

        result = []
        for task in tasks:
            updated_at = task.get("updated_at")
            days_waiting = (datetime.utcnow() - updated_at).days if updated_at else None
            result.append({
                "task_key": task.get("external_id"),
                "title": task.get("title"),
                "assignee_name": task.get("assignee_name"),
                "assignee_email": task.get("assignee_email"),
                "status": task.get("status"),
                "start_date": task.get("start_date").isoformat() if task.get("start_date") else None,
                "days_waiting": days_waiting,
                "url": task.get("url"),
            })

        result.sort(key=lambda x: x["days_waiting"] or 0, reverse=True)
        return result

