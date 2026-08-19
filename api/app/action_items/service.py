"""Action Items service: CRUD, RBAC, and project-scoped sequence allocation."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
import logging
import re
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.core.database import (
    ensure_project_action_item_indexes,
    get_brain_collection,
    get_database,
)
from app.core.config import settings
from app.core.pending_interactions import delete_pending_interactions_by_context
from app.core.pending_interactions import create_pending_interaction
from app.core.sequence import next_sequence
from app.integrations.slack.assignment_notifications import send_action_item_assignment_notification
from app.projects.pm_board_cache import invalidate_pm_board_summary_cache
from app.action_items.temporal_parser import parse_temporal_hints
from app.action_items.due_inference_policy import infer_due


ACTION_ITEM_ENTITY_TYPE = "action_item"
ACTION_ITEM_SEQUENCE_NAME = "sequence:action_item:display_key"
LEGACY_ACTION_ITEM_SEQUENCE_KEYS = (
    "action_item_display_key",
    "act_display_key",
)
ACTION_ITEM_DISPLAY_PREFIX = "ACTION"
MANDATORY_CREATE_FIELDS = ("title", "owner_user_id")
logger = logging.getLogger(__name__)
_RELATIVE_TITLE_DATE_RE = re.compile(
    r"\b(today|tomorrow|this\s+week|this\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|next\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
_TITLE_TIME_TOKEN_RE = re.compile(
    r"\b(?:at\s*)?\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
    re.IGNORECASE,
)
_TRAILING_PREPOSITION_RE = re.compile(r"\b(?:on|by|for|at)\s*$", re.IGNORECASE)


class ActionItemService:
    """Service methods for action item domain operations."""

    @staticmethod
    def _normalize_text(value: Optional[str]) -> Optional[str]:
        text = str(value or "").strip()
        return text or None

    @staticmethod
    def _normalize_bool(value: Any) -> Optional[bool]:
        if value is None:
            return None
        return bool(value)

    @staticmethod
    def _role_for_user(project_doc: Dict[str, Any], user_id: str) -> Optional[str]:
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            return None
        if str(project_doc.get("user_id") or "").strip() == normalized_user_id:
            return "owner"
        for member in project_doc.get("members") or []:
            if not isinstance(member, dict):
                continue
            if str(member.get("status") or "").strip().lower() != "active":
                continue
            if str(member.get("user_id") or "").strip() != normalized_user_id:
                continue
            role = str(member.get("role") or "").strip().lower()
            return role or None
        return None

    @staticmethod
    def _is_active_member(project_doc: Dict[str, Any], user_id: str) -> bool:
        role = ActionItemService._role_for_user(project_doc, user_id)
        return role in {"owner", "admin", "member"}

    @staticmethod
    async def _resolve_project_context(project_id: str) -> Dict[str, Any]:
        db = get_database()
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            raise ValueError("project_id is required")

        project_doc = None
        try:
            object_id = ObjectId(normalized_project_id)
            project_doc = await db.projects.find_one({"_id": object_id})
        except Exception:
            project_doc = None
        if project_doc is None:
            project_doc = await db.projects.find_one({"project_id": normalized_project_id})
        if not isinstance(project_doc, dict):
            raise ValueError("Project not found")
        custom_project_id = str(project_doc.get("project_id") or "").strip()
        if not custom_project_id:
            raise ValueError("Project custom id missing")
        return {"custom_project_id": custom_project_id, "project_doc": project_doc}

    @staticmethod
    def _validate_owner_membership(project_doc: Dict[str, Any], owner_user_id: Optional[str]) -> None:
        normalized_owner = str(owner_user_id or "").strip()
        if not normalized_owner:
            return
        if not ActionItemService._is_active_member(project_doc, normalized_owner):
            raise ValueError("owner_user_id must be an active project member")

    @staticmethod
    def _serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(doc or {})
        payload.pop("_id", None)
        payload["entity_type"] = ACTION_ITEM_ENTITY_TYPE
        return payload

    @staticmethod
    def _compact_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
        return {key: value for key, value in (doc or {}).items() if value is not None}

    @staticmethod
    def _needs_followup_from_payload(payload: Dict[str, Any]) -> bool:
        for key in MANDATORY_CREATE_FIELDS:
            value = payload.get(key)
            if not str(value or "").strip():
                return True
        return False

    @staticmethod
    def _has_explicit_due_instruction(text: str) -> bool:
        normalized = str(text or "").strip().lower()
        if not normalized:
            return False
        # Explicit due intent only; "for/on <time>" should be treated as target context.
        return bool(re.search(r"\b(due|deadline|by|before)\b", normalized))

    @staticmethod
    def _canonicalize_title_with_target_datetime(
        *,
        title: Optional[str],
        target_datetime: Optional[datetime],
        due_date: Optional[datetime],
        timezone_name: str,
        raw_temporal_text: str,
    ) -> Optional[str]:
        normalized_title = ActionItemService._normalize_text(title)
        if not normalized_title:
            return normalized_title
        if re.search(r"\b20\d{2}-\d{2}-\d{2}\b", normalized_title):
            return normalized_title
        has_relative_phrase = bool(_RELATIVE_TITLE_DATE_RE.search(normalized_title))
        if not has_relative_phrase:
            return normalized_title

        # Prefer target datetime; fallback to inferred explicit due datetime if target is absent.
        effective_datetime = target_datetime or due_date
        if effective_datetime is None:
            return normalized_title

        had_time_hint = bool(_TITLE_TIME_TOKEN_RE.search(normalized_title)) or bool(
            _TITLE_TIME_TOKEN_RE.search(raw_temporal_text or "")
        )
        base_title = _RELATIVE_TITLE_DATE_RE.sub("", normalized_title)
        if had_time_hint:
            base_title = _TITLE_TIME_TOKEN_RE.sub("", base_title)
        base_title = re.sub(r"\b(for|by)\s+(on|by)\b", r"\1", base_title, flags=re.IGNORECASE)
        base_title = _TRAILING_PREPOSITION_RE.sub("", base_title).strip(" ,.-")
        base_title = re.sub(r"\s+", " ", base_title).strip()
        if not base_title:
            base_title = "Follow up"

        try:
            tz = ZoneInfo(str(timezone_name or "UTC").strip() or "UTC")
        except Exception:
            tz = ZoneInfo("UTC")

        target = effective_datetime
        if target.tzinfo is None:
            target = target.replace(tzinfo=ZoneInfo("UTC"))
        local_target = target.astimezone(tz)
        def _ordinal(day: int) -> str:
            if 10 <= day % 100 <= 20:
                suffix = "th"
            else:
                suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
            return f"{day}{suffix}"

        date_text = f"{_ordinal(local_target.day)} {local_target.strftime('%B %Y')}"
        suffix_connector = "by" if re.search(r"\bby\b", normalized_title, re.IGNORECASE) else "on"
        suffix = f"{suffix_connector} {date_text}"
        if had_time_hint:
            suffix = f"{suffix} at {local_target.strftime('%H:%M %Z')}"

        return re.sub(r"\s+", " ", f"{base_title} {suffix}".strip())[:180]

    @staticmethod
    async def _ensure_action_item_sequence_key_migration(
        collection: Any,
        *,
        project_id: str,
    ) -> None:
        """
        Migrate legacy action-item counter keys to the canonical namespaced key.

        Keeps sequence continuity for existing projects while allowing shared,
        feature-agnostic counter naming convention.
        """
        canonical = await collection.find_one(
            {
                "entity_type": "counter",
                "counter_key": ACTION_ITEM_SEQUENCE_NAME,
            },
            {"_id": 1},
        )
        if isinstance(canonical, dict):
            return

        for legacy_key in LEGACY_ACTION_ITEM_SEQUENCE_KEYS:
            legacy = await collection.find_one(
                {
                    "entity_type": "counter",
                    "counter_key": legacy_key,
                },
                {"_id": 1},
            )
            if not isinstance(legacy, dict):
                continue
            await collection.update_one(
                {"_id": legacy["_id"]},
                {
                    "$set": {
                        "counter_key": ACTION_ITEM_SEQUENCE_NAME,
                        "project_id": str(project_id or "").strip(),
                        "updated_at": datetime.utcnow(),
                    }
                },
            )
            return

    @staticmethod
    async def create(project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        context = await ActionItemService._resolve_project_context(project_id)
        project_doc = context["project_doc"]
        custom_project_id = context["custom_project_id"]
        actor_user_id = str(payload.get("user_id") or "").strip()
        if not actor_user_id:
            raise ValueError("user_id is required")
        if not ActionItemService._is_active_member(project_doc, actor_user_id):
            raise PermissionError("Only active project members can create action items")

        await ensure_project_action_item_indexes(custom_project_id)
        collection = get_brain_collection(custom_project_id)

        source_event_key = ActionItemService._normalize_text(
            payload.get("source_event_key") or payload.get("ingest_idempotency_key")
        )
        now = datetime.utcnow()
        dedupe_window_hours = max(1, int(getattr(settings, "action_items_ingest_dedupe_window_hours", 168)))
        dedupe_cutoff = now - timedelta(hours=dedupe_window_hours)
        if source_event_key:
            existing = await collection.find_one(
                {
                    "entity_type": ACTION_ITEM_ENTITY_TYPE,
                    "source_event_key": source_event_key,
                    "created_at": {"$gte": dedupe_cutoff},
                },
                sort=[("created_at", -1)],
            )
            if isinstance(existing, dict):
                return ActionItemService._serialize(existing)

        title = ActionItemService._normalize_text(payload.get("title"))
        owner_user_id = ActionItemService._normalize_text(payload.get("owner_user_id"))
        related_to = ActionItemService._normalize_text(payload.get("related_to"))
        source = str(payload.get("source") or "manual").strip().lower() or "manual"
        due_type_input = str(payload.get("due_type") or "no_due_date").strip().lower() or "no_due_date"
        due_date_input = payload.get("due_date")
        target_datetime_input = payload.get("target_datetime")
        note_color = ActionItemService._normalize_text(payload.get("note_color"))
        related_type = str(payload.get("related_type") or "other").strip().lower() or "other"
        related_id = ActionItemService._normalize_text(payload.get("related_id"))
        timezone_name = str(project_doc.get("timezone") or "UTC").strip() or "UTC"

        raw_temporal_text = ActionItemService._normalize_text(
            payload.get("raw_text") or title or related_to or ""
        ) or ""
        temporal = parse_temporal_hints(
            text=raw_temporal_text,
            timezone_name=timezone_name,
        )
        target_datetime = target_datetime_input or temporal.target_datetime
        has_explicit_due_text = ActionItemService._has_explicit_due_instruction(raw_temporal_text)
        if raw_temporal_text and not has_explicit_due_text:
            # Ignore model/tool-provided due fields when user text expresses event target context
            # (for example "for Tuesday at 10am") rather than explicit due instruction.
            explicit_due_type = temporal.explicit_due_type
            explicit_due_date = temporal.explicit_due_datetime
        else:
            explicit_due_type = (
                temporal.explicit_due_type
                or (due_type_input if due_type_input in {"today", "this_week", "by_date"} else None)
            )
            explicit_due_date = temporal.explicit_due_datetime or due_date_input
        inferred_due = infer_due(
            action_text=raw_temporal_text or (title or ""),
            timezone_name=timezone_name,
            explicit_due_type=explicit_due_type,
            explicit_due_date=explicit_due_date,
            target_datetime=target_datetime,
        )
        due_type = inferred_due.due_type
        due_date = inferred_due.due_date
        title = ActionItemService._canonicalize_title_with_target_datetime(
            title=title,
            target_datetime=target_datetime,
            due_date=due_date,
            timezone_name=timezone_name,
            raw_temporal_text=raw_temporal_text,
        )

        ActionItemService._validate_owner_membership(project_doc, owner_user_id)
        detected_needs_followup = ActionItemService._needs_followup_from_payload(
            {
                "title": title,
                "owner_user_id": owner_user_id,
                "related_to": related_to,
            }
        )
        explicit_followup = ActionItemService._normalize_bool(payload.get("needs_followup"))
        needs_followup = explicit_followup if explicit_followup is not None else detected_needs_followup

        await ActionItemService._ensure_action_item_sequence_key_migration(
            collection,
            project_id=custom_project_id,
        )
        sequence_number = await next_sequence(
            collection,
            project_id=custom_project_id,
            sequence_name=ACTION_ITEM_SEQUENCE_NAME,
        )
        doc = ActionItemService._compact_doc({
            "entity_type": ACTION_ITEM_ENTITY_TYPE,
            "action_item_id": str(uuid.uuid4()),
            "display_key": f"{ACTION_ITEM_DISPLAY_PREFIX}-{sequence_number}",
            "project_id": custom_project_id,
            "title": title,
            "status": "open",
            "owner_user_id": owner_user_id,
            "created_by_user_id": actor_user_id,
            "source": source,
            "related_to": related_to,
            "related_type": related_type,
            "related_id": related_id,
            "due_type": due_type,
            "due_date": due_date,
            "target_datetime": target_datetime,
            "note_color": note_color,
            "needs_followup": bool(needs_followup),
            "needs_followup_set_at": now if needs_followup else None,
            "source_event_key": source_event_key,
            "created_at": now,
            "updated_at": now,
            "closed_at": None,
            "closed_by_user_id": None,
        })
        try:
            await collection.insert_one(doc)
        except DuplicateKeyError:
            # Defensive retry path for concurrent inserts using the same source event key.
            if source_event_key:
                existing = await collection.find_one(
                    {
                        "entity_type": ACTION_ITEM_ENTITY_TYPE,
                        "source_event_key": source_event_key,
                        "created_at": {"$gte": dedupe_cutoff},
                    },
                    sort=[("created_at", -1)],
                )
                if isinstance(existing, dict):
                    return ActionItemService._serialize(existing)
            raise
        if bool(doc.get("needs_followup")):
            target_user_id = str(doc.get("created_by_user_id") or "").strip()
            if target_user_id:
                followup_window_hours = max(
                    1,
                    int(getattr(settings, "action_items_followup_pm_escalation_hours", 4) or 4),
                )
                try:
                    await create_pending_interaction(
                        interaction_type="action_item_clarification",
                        project_id=custom_project_id,
                        target_user_id=target_user_id,
                        source=source,
                        priority=50,
                        escalate_at=now + timedelta(hours=followup_window_hours),
                        expires_at=now + timedelta(hours=followup_window_hours),
                        context_payload={
                            "action_item_id": str(doc.get("action_item_id") or "").strip(),
                            "display_key": str(doc.get("display_key") or "").strip(),
                            "expected_field": "owner_user_id",
                        },
                    )
                except Exception:
                    logger.warning(
                        "action_item_pending_interaction_create_failed project_id=%s action_item_id=%s",
                        custom_project_id,
                        str(doc.get("action_item_id") or "").strip(),
                    )
        invalidate_pm_board_summary_cache(custom_project_id)
        logger.info(
            "action_item_created project_id=%s action_item_id=%s display_key=%s owner_user_id=%s source=%s needs_followup=%s",
            custom_project_id,
            doc.get("action_item_id"),
            doc.get("display_key"),
            doc.get("owner_user_id"),
            doc.get("source"),
            bool(doc.get("needs_followup")),
        )
        try:
            await send_action_item_assignment_notification(
                db=get_database(),
                project=project_doc,
                item=doc,
                old_owner_user_id=None,
                actor_user_id=actor_user_id,
            )
        except Exception as exc:
            logger.warning(
                "action_item_assignment_notification_failed project_id=%s action_item_id=%s error=%s",
                custom_project_id,
                str(doc.get("action_item_id") or "").strip(),
                exc,
            )
        return ActionItemService._serialize(doc)

    @staticmethod
    async def list(project_id: str, filters: Dict[str, Any]) -> Dict[str, Any]:
        context = await ActionItemService._resolve_project_context(project_id)
        project_doc = context["project_doc"]
        custom_project_id = context["custom_project_id"]
        actor_user_id = str(filters.get("user_id") or "").strip()
        if not actor_user_id:
            raise ValueError("user_id is required")
        actor_role = ActionItemService._role_for_user(project_doc, actor_user_id)
        if actor_role not in {"owner", "admin", "member"}:
            raise PermissionError("Only active project members can list action items")

        query: Dict[str, Any] = {"entity_type": ACTION_ITEM_ENTITY_TYPE}
        if actor_role not in {"owner", "admin"}:
            query["owner_user_id"] = actor_user_id

        status = ActionItemService._normalize_text(filters.get("status"))
        owner_user_id = ActionItemService._normalize_text(filters.get("owner_user_id"))
        due_type = ActionItemService._normalize_text(filters.get("due_type"))
        source = ActionItemService._normalize_text(filters.get("source"))
        needs_followup = ActionItemService._normalize_bool(filters.get("needs_followup"))
        closed_window_hours = filters.get("closed_window_hours")

        if status:
            normalized_status = str(status).strip().lower()
            if normalized_status == "closed":
                query["status"] = {"$in": ["done", "cancelled"]}
            elif normalized_status in {"all", "any", "*"}:
                pass
            else:
                query["status"] = normalized_status
        if owner_user_id:
            query["owner_user_id"] = owner_user_id
        if due_type:
            query["due_type"] = due_type
        if source:
            query["source"] = source
        if needs_followup is not None:
            query["needs_followup"] = needs_followup
        if closed_window_hours and status in {"done", "cancelled"}:
            cutoff = datetime.utcnow() - timedelta(hours=int(closed_window_hours))
            query["updated_at"] = {"$gte": cutoff}

        limit = int(filters.get("limit") or 100)
        offset = int(filters.get("offset") or 0)

        collection = get_brain_collection(custom_project_id)
        total = await collection.count_documents(query)
        docs = (
            await collection.find(query)
            .sort([("updated_at", -1), ("created_at", -1)])
            .skip(offset)
            .limit(limit)
            .to_list(length=limit)
        )
        items = [ActionItemService._serialize(doc) for doc in docs]
        logger.info(
            "action_item_listed project_id=%s actor_user_id=%s actor_role=%s status=%s owner_user_id=%s due_type=%s source=%s needs_followup=%s total=%s limit=%s offset=%s",
            custom_project_id,
            actor_user_id,
            actor_role,
            status,
            owner_user_id,
            due_type,
            source,
            needs_followup,
            int(total),
            limit,
            offset,
        )
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @staticmethod
    async def update(project_id: str, action_item_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        context = await ActionItemService._resolve_project_context(project_id)
        project_doc = context["project_doc"]
        custom_project_id = context["custom_project_id"]
        actor_user_id = str(payload.get("user_id") or "").strip()
        if not actor_user_id:
            raise ValueError("user_id is required")
        actor_role = ActionItemService._role_for_user(project_doc, actor_user_id)
        if actor_role not in {"owner", "admin", "member"}:
            raise PermissionError("Only active project members can update action items")

        collection = get_brain_collection(custom_project_id)
        existing = await collection.find_one(
            {
                "entity_type": ACTION_ITEM_ENTITY_TYPE,
                "action_item_id": str(action_item_id or "").strip(),
            }
        )
        if not isinstance(existing, dict):
            raise ValueError("Action item not found")
        if actor_role not in {"owner", "admin"} and str(existing.get("owner_user_id") or "").strip() != actor_user_id:
            raise PermissionError("Only owner/admin or item owner can update this action item")

        set_updates: Dict[str, Any] = {}
        unset_updates: Dict[str, str] = {}
        for key in ("title", "owner_user_id", "related_to", "related_id", "note_color"):
            if key in payload and payload.get(key) is not None:
                normalized_value = ActionItemService._normalize_text(payload.get(key))
                if normalized_value is None:
                    unset_updates[key] = ""
                else:
                    set_updates[key] = normalized_value
        for key in ("related_type", "status", "due_type"):
            if key in payload and payload.get(key) is not None:
                normalized_value = str(payload.get(key)).strip().lower()
                if not normalized_value:
                    unset_updates[key] = ""
                else:
                    set_updates[key] = normalized_value
        if "due_date" in payload:
            due_date_value = payload.get("due_date")
            if due_date_value is None:
                unset_updates["due_date"] = ""
            else:
                set_updates["due_date"] = due_date_value
        if "target_datetime" in payload:
            target_datetime_value = payload.get("target_datetime")
            if target_datetime_value is None:
                unset_updates["target_datetime"] = ""
            else:
                set_updates["target_datetime"] = target_datetime_value
        if "needs_followup" in payload:
            set_updates["needs_followup"] = bool(payload.get("needs_followup"))

        if "owner_user_id" in set_updates:
            ActionItemService._validate_owner_membership(project_doc, set_updates.get("owner_user_id"))

        now = datetime.utcnow()
        prev_followup = bool(existing.get("needs_followup"))
        next_followup = bool(set_updates.get("needs_followup", prev_followup))
        if not prev_followup and next_followup:
            set_updates["needs_followup_set_at"] = now
        elif prev_followup and not next_followup:
            unset_updates["needs_followup_set_at"] = ""
        elif prev_followup and next_followup and "needs_followup" in set_updates:
            set_updates["needs_followup_set_at"] = now

        if set_updates.get("status") in {"done", "cancelled"}:
            set_updates["closed_at"] = now
            set_updates["closed_by_user_id"] = actor_user_id
        elif set_updates.get("status") == "open":
            unset_updates["closed_at"] = ""
            unset_updates["closed_by_user_id"] = ""

        set_updates["updated_at"] = now
        update_ops: Dict[str, Any] = {"$set": set_updates}
        if unset_updates:
            update_ops["$unset"] = unset_updates
        await collection.update_one(
            {"_id": existing["_id"]},
            update_ops,
        )
        updated = await collection.find_one({"_id": existing["_id"]})
        if not prev_followup and next_followup:
            target_user_id = str((updated or existing).get("created_by_user_id") or "").strip()
            if target_user_id:
                followup_window_hours = max(
                    1,
                    int(getattr(settings, "action_items_followup_pm_escalation_hours", 4) or 4),
                )
                try:
                    await create_pending_interaction(
                        interaction_type="action_item_clarification",
                        project_id=custom_project_id,
                        target_user_id=target_user_id,
                        source="action_item_update",
                        priority=50,
                        escalate_at=now + timedelta(hours=followup_window_hours),
                        expires_at=now + timedelta(hours=followup_window_hours),
                        context_payload={
                            "action_item_id": str(existing.get("action_item_id") or "").strip(),
                            "display_key": str((updated or existing).get("display_key") or "").strip(),
                            "expected_field": "owner_user_id",
                        },
                    )
                except Exception:
                    logger.warning(
                        "action_item_pending_interaction_create_failed project_id=%s action_item_id=%s",
                        custom_project_id,
                        str(existing.get("action_item_id") or "").strip(),
                    )
        latest = updated or existing
        latest_status = str(latest.get("status") or "").strip().lower()
        latest_followup = bool(latest.get("needs_followup"))
        action_item_ref = str(existing.get("action_item_id") or "").strip()
        if action_item_ref and (latest_status in {"done", "cancelled"} or not latest_followup):
            try:
                await delete_pending_interactions_by_context(
                    project_id=custom_project_id,
                    interaction_type="action_item_clarification",
                    context_key="action_item_id",
                    context_value=action_item_ref,
                )
            except Exception:
                logger.warning(
                    "action_item_pending_interaction_cleanup_failed project_id=%s action_item_id=%s",
                    custom_project_id,
                    action_item_ref,
                )
        invalidate_pm_board_summary_cache(custom_project_id)
        logger.info(
            "action_item_updated project_id=%s action_item_id=%s actor_user_id=%s status=%s owner_user_id=%s needs_followup=%s",
            custom_project_id,
            str(existing.get("action_item_id") or "").strip(),
            actor_user_id,
            str(latest.get("status") or existing.get("status") or "").strip(),
            str(latest.get("owner_user_id") or existing.get("owner_user_id") or "").strip(),
            bool(latest.get("needs_followup", existing.get("needs_followup"))),
        )
        try:
            await send_action_item_assignment_notification(
                db=get_database(),
                project=project_doc,
                item=latest,
                old_owner_user_id=str(existing.get("owner_user_id") or "").strip() or None,
                actor_user_id=actor_user_id,
            )
        except Exception as exc:
            logger.warning(
                "action_item_assignment_notification_failed project_id=%s action_item_id=%s error=%s",
                custom_project_id,
                str(existing.get("action_item_id") or "").strip(),
                exc,
            )
        return ActionItemService._serialize(latest)

    @staticmethod
    async def close(project_id: str, action_item_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        close_status = str(payload.get("status") or "").strip().lower()
        if close_status not in {"done", "cancelled"}:
            raise ValueError("status must be done or cancelled")
        return await ActionItemService.update(
            project_id,
            action_item_id,
            {
                "user_id": payload.get("user_id"),
                "status": close_status,
            },
        )

    @staticmethod
    async def delete(project_id: str, action_item_id: str, payload: Dict[str, Any]) -> None:
        context = await ActionItemService._resolve_project_context(project_id)
        project_doc = context["project_doc"]
        custom_project_id = context["custom_project_id"]
        actor_user_id = str(payload.get("user_id") or "").strip()
        if not actor_user_id:
            raise ValueError("user_id is required")

        actor_role = ActionItemService._role_for_user(project_doc, actor_user_id)
        if actor_role != "owner":
            raise PermissionError("Only project owner can delete action items")

        collection = get_brain_collection(custom_project_id)
        existing = await collection.find_one(
            {
                "entity_type": ACTION_ITEM_ENTITY_TYPE,
                "action_item_id": str(action_item_id or "").strip(),
            }
        )
        if not isinstance(existing, dict):
            raise ValueError("Action item not found")

        await collection.delete_one({"_id": existing["_id"]})

        action_item_ref = str(existing.get("action_item_id") or "").strip()
        if action_item_ref:
            try:
                await delete_pending_interactions_by_context(
                    project_id=custom_project_id,
                    interaction_type="action_item_clarification",
                    context_key="action_item_id",
                    context_value=action_item_ref,
                )
            except Exception:
                logger.warning(
                    "action_item_pending_interaction_cleanup_failed project_id=%s action_item_id=%s",
                    custom_project_id,
                    action_item_ref,
                )

        invalidate_pm_board_summary_cache(custom_project_id)
        logger.info(
            "action_item_deleted project_id=%s action_item_id=%s actor_user_id=%s",
            custom_project_id,
            action_item_ref,
            actor_user_id,
        )
