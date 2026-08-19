"""Integration routes for OAuth flows (Slack, Jira, etc.)."""

import asyncio
import hashlib
import json as json_lib
import random
import re
import secrets
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import quote as url_quote
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from fastapi.responses import RedirectResponse, JSONResponse
from bson import ObjectId
from slack_sdk.web.async_client import AsyncWebClient

from app.core.config import settings
from app.core.auth_deps import get_current_user
from app.core.authz import assert_project_access_any
from app.core.database import get_database, get_brain_collection
from app.core.redis_queue import get_redis_connection
from app.core.security import encryption_service, oauth_state_service
from app.cortex.response import send_stage3_ack_best_effort
from app.cortex.sessions import CortexSessionManager, build_session_key
from app.cortex.slack_events import (
    SlackEventEnvelope,
    classify_event_for_handoff,
    handoff_slack_event_to_cortex,
)
from app.cortex.ingress import classify_message_intent
from app.team_poll.response_writer import compose_team_poll_response
from app.team_poll.ingress import handle_owner_poll_ingress
from app.domain.scope import (
    SCOPE_GATE_UNCERTAIN,
    SCOPE_GATE_OUT_OF_SCOPE,
    build_scope_refusal_response,
    classify_project_scope_gate,
)
from app.integrations.slack import slack_oauth_service, slack_bot_service
from app.integrations.pending_interactions_router import route_pending_dm_interactions
from app.core.pending_interactions import list_pending_interactions_by_external_identity
from app.integrations.slack.member_sync import (
    link_member_slack_id_detailed,
    sync_project_slack_member_ids_detailed,
)
from app.integrations.base.webhook_registry import require_webhook_auth
from app.integrations.slack.welcome import send_project_welcome_to_all_active_members_detailed
from app.integrations.common.errors import IntegrationError
from app.integrations.channel_index import clear_active_project_rows
from app.integrations.slack.delivery import ResilientSlackWebClient
from app.core.queue_backends.dramatiq_backend import enqueue as dramatiq_enqueue
from app.core.queue_runtime import QUEUE_CADENCE_DM
from app.cadence.canary import should_route_to_queue
from app.integrations.slack.project_resolver import (
    list_slack_member_projects,
    persist_slack_active_project,
    resolve_slack_project,
)
from app.integrations.slack.context_footer import (
    append_project_context_footer,
    build_project_context_footer,
)
from app.integrations.slack.message_reader import SlackMessageReader
from app.integrations.jira import jira_oauth_service
from app.domain.capabilities.provider_selection_policy import (
    connected_pm_providers_from_project_context,
    resolve_active_provider_after_disconnect,
)
from app.domain.work_items.types import canonicalize_work_item_payload
from app.tasks.providers.jira_adapter import JiraAdapter
from app.projects.service import ProjectService
from app.projects.pm_board_cache import (
    invalidate_pm_board_summary_cache,
    invalidate_pm_board_summary_cache_by_object_id,
    mark_pm_board_summary_dirty,
)
from app.projects.recommendations.recommendation_orchestrator import (
    generate_project_recommendations,
    mark_project_task_dirty,
)
from app.projects.recommendations.recommendation_runtime_cache import (
    invalidate_cached_api_project_recommendations,
)
from app.scheduler.service import ensure_recommendation_schedules_for_projects


async def _require_integrations_authz(
    project_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
) -> dict:
    if project_id:
        db = get_database()
        await assert_project_access_any(project_id, str(current_user.get("user_id") or ""), db)
    return current_user


router = APIRouter(
    prefix="/api/integrations",
    tags=["integrations"],
    dependencies=[Depends(_require_integrations_authz)],
)

SLACK_EVENT_DEDUPE_TTL_SECONDS = 5 * 60
_seen_slack_event_ids: Dict[str, datetime] = {}
SLACK_SEMANTIC_EVENT_DEDUPE_TTL_SECONDS = 5 * 60
_seen_slack_semantic_event_keys: Dict[str, datetime] = {}
SLACK_RESPONSE_DEDUPE_TTL_SECONDS = 45
_seen_slack_response_keys: Dict[str, datetime] = {}
SLACK_USER_MENTION_RE = re.compile(r"<@([A-Z0-9]+)>")
SLACK_WORD_RE = re.compile(r"[a-z0-9']+")
_cortex_session_manager = CortexSessionManager()
_STATUS_MAP_REFRESH_COOLDOWN = timedelta(minutes=10)
_PM_BOARD_SNAPSHOT_ENTITY_TYPE = "project_insights_snapshot"
_PM_BOARD_SNAPSHOT_EXTERNAL_ID = "project-insights:v1"
_JIRA_WEBHOOK_TOKEN_BYTES = 32


def _generate_jira_webhook_token() -> str:
    return secrets.token_urlsafe(_JIRA_WEBHOOK_TOKEN_BYTES)


def _build_jira_webhook_url(*, custom_project_id: str, token: str) -> str:
    clean_project_id = str(custom_project_id or "").strip()
    clean_token = str(token or "").strip()
    if not clean_project_id or not clean_token:
        raise ValueError("custom_project_id and token are required for Jira webhook URL")
    encoded_project_id = url_quote(clean_project_id, safe="")
    encoded_token = url_quote(clean_token, safe="")
    return (
        f"{settings.backend_url}/api/jira/webhooks/"
        f"{encoded_project_id}/task-updated?token={encoded_token}"
    )


async def _run_slack_post_connect_tasks_in_background(*, db, project_id: str) -> None:
    """Best-effort async post-connect tasks: member sync and welcome delivery."""
    try:
        bulk_sync = await sync_project_slack_member_ids_detailed(
            db=db,
            project_id=project_id,
            overwrite=True,
        )
        print(
            "Slack callback member sync outcome: "
            f"project_id={project_id} status={bulk_sync.get('status')} "
            f"member_count={bulk_sync.get('member_count')} counts={bulk_sync.get('counts')}"
        )
    except Exception as sync_exc:
        print(f"Slack callback member sync failed: project_id={project_id} error={sync_exc}")

    try:
        welcome_result = await send_project_welcome_to_all_active_members_detailed(
            db=db,
            project_id=project_id,
        )
        print(
            "Slack callback welcome delivery outcome: "
            f"project_id={project_id} status={welcome_result.get('status')} "
            f"member_count={welcome_result.get('member_count')} counts={welcome_result.get('counts')}"
        )
    except Exception as welcome_exc:
        print(f"Slack callback welcome delivery failed: project_id={project_id} error={welcome_exc}")


async def _upsert_jira_tasks_to_brain_and_mark_dirty(
    *,
    brain_collection,
    tasks: List[Dict[str, Any]],
    normalizer,
    custom_project_id: str,
) -> int:
    """Upsert Jira tasks into Brain and emit dirty markers for incremental recommendation recompute."""
    synced_count = 0
    for task in tasks or []:
        normalized_task = canonicalize_work_item_payload(normalizer.normalize_task(task))
        await brain_collection.update_one(
            {
                "integration_type": "jira",
                "external_id": task.get("key"),
            },
            {"$set": normalized_task},
            upsert=True,
        )
        task_key = str(
            task.get("key")
            or normalized_task.get("external_id")
            or ""
        ).strip()
        if task_key:
            try:
                mark_project_task_dirty(custom_project_id, task_key)
            except Exception as dirty_exc:
                print(
                    "[Recommendation] Dirty marker failed during Jira import "
                    f"(project_id={custom_project_id}, task_key={task_key}): {dirty_exc}"
                )
        synced_count += 1
    return synced_count


async def _force_pm_board_rebuild_after_jira_sync(
    *,
    project_object_id: Any,
    custom_project_id: str,
) -> None:
    """
    Sync-now hard rebuild contract:
    - clear Redis PM summary cache
    - clear PM summary snapshot doc
    - mark summary dirty
    - enqueue async recompute
    """
    normalized_project_id = str(custom_project_id or "").strip()
    if not normalized_project_id:
        return

    invalidate_pm_board_summary_cache(normalized_project_id)

    try:
        brain_collection = get_brain_collection(normalized_project_id)
        await brain_collection.delete_one(
            {
                "entity_type": _PM_BOARD_SNAPSHOT_ENTITY_TYPE,
                "external_id": _PM_BOARD_SNAPSHOT_EXTERNAL_ID,
            }
        )
    except Exception as snapshot_exc:
        print(
            "[PM Summary] Snapshot clear failed during Jira sync force rebuild: "
            f"project_id={normalized_project_id} error={snapshot_exc}"
        )

    mark_pm_board_summary_dirty(
        normalized_project_id,
        reason="sync_now_force_rebuild",
        source="jira_sync",
    )

    try:
        from app.projects.router import trigger_pm_board_summary_recompute

        enqueue_result = trigger_pm_board_summary_recompute(
            project_id=str(project_object_id or ""),
            custom_project_id=normalized_project_id,
            trigger="jira_sync",
        )
        print(
            "[PM Summary] Jira sync force-rebuild enqueue: "
            f"project_id={normalized_project_id} accepted={bool(enqueue_result.get('accepted'))} "
            f"reason={enqueue_result.get('reason') or 'n/a'}"
        )
    except Exception as pm_exc:
        print(
            "[PM Summary] Jira sync force-rebuild enqueue failed: "
            f"project_id={normalized_project_id} error={pm_exc}"
        )


def _normalize_status_key(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _normalize_status_category(value: Any) -> str:
    normalized = _normalize_status_key(value)
    if normalized in {"not_started", "in_progress", "blocked", "done"}:
        return normalized
    return "unknown"


def _work_item_delete_filter() -> Dict[str, Any]:
    """Canonical filter for all project work-item docs in Brain collections."""
    return {
        "entity_type": "work_item",
    }


def _is_connected_integration_status(status_value: Any) -> bool:
    normalized = str(status_value or "").strip().lower()
    return normalized in {
        "connected",
        "pending_site_selection",
        "pending_project_selection",
    }


def _is_workitem_integration_record(name: str, record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    category = str(record.get("category") or "").strip().lower()
    if category == "workitem":
        return True
    # Backward compatibility for integrations created before category normalization.
    return str(name or "").strip().lower() == "jira"


async def _invalidate_work_item_read_caches(
    *,
    db,
    project_object_id: str,
    custom_project_id: str,
    trigger: str = "workitems_cleanup",
) -> None:
    """Invalidate PM/recommendation read caches after work-item lifecycle changes."""
    normalized_project_id = str(custom_project_id or "").strip()
    normalized_object_id = str(project_object_id or "").strip()

    try:
        await invalidate_pm_board_summary_cache_by_object_id(db, normalized_object_id)
    except Exception as cache_exc:
        print(
            "[Jira Cleanup] PM summary cache invalidation failed: "
            f"project_object_id={project_object_id} project_id={custom_project_id} error={cache_exc}"
        )

    # Clear PM snapshot doc as part of lifecycle cleanup to avoid stale snapshot rehydrate.
    if normalized_project_id:
        try:
            brain_collection = get_brain_collection(normalized_project_id)
            await brain_collection.delete_one(
                {
                    "entity_type": _PM_BOARD_SNAPSHOT_ENTITY_TYPE,
                    "external_id": _PM_BOARD_SNAPSHOT_EXTERNAL_ID,
                }
            )
        except Exception as snapshot_exc:
            print(
                "[Jira Cleanup] PM summary snapshot clear failed: "
                f"project_id={normalized_project_id} error={snapshot_exc}"
            )

    try:
        mark_pm_board_summary_dirty(
            normalized_project_id,
            reason="workitems_lifecycle",
            source=str(trigger or "workitems_cleanup").strip().lower() or "workitems_cleanup",
        )
    except Exception as dirty_exc:
        print(
            "[Jira Cleanup] PM summary dirty mark failed: "
            f"project_id={normalized_project_id} error={dirty_exc}"
        )

    # Best-effort async recompute request so next PM load converges quickly.
    if normalized_project_id and normalized_object_id:
        try:
            from app.projects.router import trigger_pm_board_summary_recompute

            enqueue_result = trigger_pm_board_summary_recompute(
                project_id=normalized_object_id,
                custom_project_id=normalized_project_id,
                trigger=str(trigger or "workitems_cleanup").strip().lower() or "workitems_cleanup",
            )
            print(
                "[Jira Cleanup] PM summary recompute enqueue: "
                f"project_id={normalized_project_id} accepted={bool(enqueue_result.get('accepted'))} "
                f"reason={enqueue_result.get('reason') or 'n/a'}"
            )
        except Exception as enqueue_exc:
            print(
                "[Jira Cleanup] PM summary recompute enqueue failed: "
                f"project_id={normalized_project_id} error={enqueue_exc}"
            )

    try:
        invalidate_cached_api_project_recommendations(normalized_project_id)
    except Exception as reco_cache_exc:
        print(
            "[Jira Cleanup] Recommendation cache invalidation failed: "
            f"project_id={normalized_project_id} error={reco_cache_exc}"
        )


def _status_category_from_jira(status_row: Dict[str, Any]) -> str:
    category = status_row.get("statusCategory")
    if isinstance(category, dict):
        category_key = _normalize_status_key(category.get("key") or category.get("name"))
        if category_key in {"new", "to_do", "todo"}:
            return "not_started"
        if category_key in {"indeterminate", "in_progress", "inprogress"}:
            return "in_progress"
        if category_key in {"done", "completed", "closed", "resolved"}:
            return "done"
    name_token = _normalize_status_key(status_row.get("name"))
    if name_token in {"todo", "to_do", "open", "new", "backlog", "selected_for_development", "selected"}:
        return "not_started"
    if name_token in {"in_progress", "doing", "in_review", "review", "qa", "testing"}:
        return "in_progress"
    if name_token in {"done", "completed", "closed", "resolved"}:
        return "done"
    if name_token in {"blocked", "on_hold", "hold", "waiting", "waiting_on_dependency"}:
        return "blocked"
    return "unknown"


def _build_status_category_map_from_jira_statuses(statuses: List[Dict[str, Any]]) -> Dict[str, str]:
    status_map: Dict[str, str] = {}
    for status in statuses or []:
        if not isinstance(status, dict):
            continue
        name = str(status.get("name") or "").strip()
        if not name:
            continue
        category = _status_category_from_jira(status)
        status_map[name] = category
        normalized_name = _normalize_status_key(name)
        if normalized_name:
            status_map[normalized_name] = category
        normalized_with_space = normalized_name.replace("_", " ")
        if normalized_with_space:
            status_map[normalized_with_space] = category
    return status_map


def _extract_status_names_from_jira_issues(tasks: List[Dict[str, Any]]) -> List[str]:
    seen = set()
    result: List[str] = []
    for task in tasks or []:
        fields = task.get("fields") if isinstance(task, dict) else None
        if not isinstance(fields, dict):
            continue
        status = fields.get("status")
        status_name = str((status or {}).get("name") or "").strip() if isinstance(status, dict) else ""
        if not status_name:
            continue
        if status_name in seen:
            continue
        seen.add(status_name)
        result.append(status_name)
    return result


def _map_contains_status(status_map: Dict[str, Any], status_name: str) -> bool:
    if not isinstance(status_map, dict):
        return False
    raw = str(status_name or "").strip()
    if not raw:
        return False
    candidates = {
        raw,
        raw.lower(),
        _normalize_status_key(raw),
        _normalize_status_key(raw).replace("_", " "),
    }
    return any(candidate in status_map for candidate in candidates if candidate)


async def _fetch_jira_project_status_category_map(
    *,
    access_token: str,
    cloud_id: str,
    project_key: str,
) -> Dict[str, str]:
    statuses = await jira_oauth_service.fetch_project_statuses(
        access_token=access_token,
        cloud_id=cloud_id,
        project_key_or_id=project_key,
    )
    return _build_status_category_map_from_jira_statuses(statuses)


def _is_duplicate_slack_event(event_id: Optional[str]) -> bool:
    """
    Best-effort dedupe to avoid duplicate Slack retry side effects.

    Priority order:
    1) Redis shared dedupe (multi-instance safe when Redis is available)
    2) In-memory fallback (single-instance best effort)
    """
    if not event_id:
        return False

    redis_conn = get_redis_connection()
    if redis_conn is not None:
        try:
            key = f"cortex:slack:event:{event_id}"
            created = redis_conn.set(
                key,
                b"1",
                ex=SLACK_EVENT_DEDUPE_TTL_SECONDS,
                nx=True,
            )
            # NX set succeeds only for first delivery.
            return not bool(created)
        except Exception as exc:
            print(f"Slack dedupe Redis check failed; falling back to memory: {exc}")

    now = datetime.utcnow()
    stale_before = now - timedelta(seconds=SLACK_EVENT_DEDUPE_TTL_SECONDS)
    stale_ids = [eid for eid, seen_at in _seen_slack_event_ids.items() if seen_at < stale_before]
    for eid in stale_ids:
        _seen_slack_event_ids.pop(eid, None)

    if event_id in _seen_slack_event_ids:
        return True

    _seen_slack_event_ids[event_id] = now
    return False


def _build_slack_semantic_event_key(
    *,
    team_id: Optional[str],
    channel_id: Optional[str],
    user_id: Optional[str],
    event_type: Optional[str],
    event_ts: Optional[str],
    thread_ts: Optional[str],
    client_msg_id: Optional[str],
    text: Optional[str],
) -> str:
    normalized_team = str(team_id or "").strip()
    normalized_channel = str(channel_id or "").strip()
    normalized_user = str(user_id or "").strip()
    normalized_type = str(event_type or "").strip().lower()
    normalized_ts = str(event_ts or "").strip()
    normalized_thread = str(thread_ts or "").strip()
    normalized_client_msg_id = str(client_msg_id or "").strip().lower()
    normalized_text = re.sub(r"\s+", " ", str(text or "").strip().lower())
    text_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()[:16] if normalized_text else "empty"

    if not (normalized_team and normalized_channel and normalized_user):
        return ""
    message_anchor = normalized_client_msg_id or f"ts:{normalized_ts}"
    return (
        f"{normalized_team}|{normalized_channel}|{normalized_user}|{normalized_type}|"
        f"{normalized_thread}|{message_anchor}|{text_hash}"
    )


def _is_duplicate_slack_semantic_event(semantic_key: str) -> bool:
    normalized_key = str(semantic_key or "").strip()
    if not normalized_key:
        return False

    redis_conn = get_redis_connection()
    if redis_conn is not None:
        try:
            key = f"cortex:slack:semantic:{normalized_key}"
            created = redis_conn.set(
                key,
                b"1",
                ex=SLACK_SEMANTIC_EVENT_DEDUPE_TTL_SECONDS,
                nx=True,
            )
            return not bool(created)
        except Exception as exc:
            print(f"Slack semantic dedupe Redis check failed; falling back to memory: {exc}")

    now = datetime.utcnow()
    stale_before = now - timedelta(seconds=SLACK_SEMANTIC_EVENT_DEDUPE_TTL_SECONDS)
    stale_keys = [
        key for key, seen_at in _seen_slack_semantic_event_keys.items()
        if seen_at < stale_before
    ]
    for key in stale_keys:
        _seen_slack_semantic_event_keys.pop(key, None)

    if normalized_key in _seen_slack_semantic_event_keys:
        return True

    _seen_slack_semantic_event_keys[normalized_key] = now
    return False


def _prune_stale_slack_response_keys(now: datetime) -> None:
    stale_before = now - timedelta(seconds=SLACK_RESPONSE_DEDUPE_TTL_SECONDS)
    stale_keys = [
        key
        for key, seen_at in _seen_slack_response_keys.items()
        if seen_at < stale_before
    ]
    for key in stale_keys:
        _seen_slack_response_keys.pop(key, None)


def _build_slack_response_gate_key(
    *,
    kind: str,
    team_id: Optional[str],
    channel_id: Optional[str],
    user_id: Optional[str],
    thread_ts: Optional[str],
    event_ts: Optional[str],
    normalized_text: Optional[str],
) -> str:
    compact_text = re.sub(r"\s+", " ", str(normalized_text or "").strip().lower())
    text_hash = hashlib.sha256(compact_text.encode("utf-8")).hexdigest()[:16] if compact_text else "empty"
    event_token = str(event_ts or "").strip()
    if str(thread_ts or "").strip():
        anchor = f"thread:{str(thread_ts).strip()}"
    elif str(channel_id or "").strip().upper().startswith("D"):
        anchor = f"dm:{str(channel_id).strip()}"
    else:
        anchor = f"event:{event_token}"
    if event_token:
        anchor = f"{anchor}|evt:{event_token}"
    return (
        f"{str(kind or '').strip().lower()}|{str(team_id or '').strip()}|"
        f"{str(channel_id or '').strip()}|{str(user_id or '').strip()}|{anchor}|{text_hash}"
    )


def _slack_response_slot_exists(gate_key: str) -> bool:
    normalized_key = str(gate_key or "").strip()
    if not normalized_key:
        return False

    redis_conn = get_redis_connection()
    if redis_conn is not None:
        try:
            return bool(redis_conn.get(f"cortex:slack:response:{normalized_key}"))
        except Exception as exc:
            print(f"Slack response dedupe Redis exists check failed; falling back to memory: {exc}")

    now = datetime.utcnow()
    _prune_stale_slack_response_keys(now)
    return normalized_key in _seen_slack_response_keys


def _try_acquire_slack_response_slot(gate_key: str) -> bool:
    normalized_key = str(gate_key or "").strip()
    if not normalized_key:
        return True

    redis_conn = get_redis_connection()
    if redis_conn is not None:
        try:
            created = redis_conn.set(
                f"cortex:slack:response:{normalized_key}",
                b"1",
                ex=SLACK_RESPONSE_DEDUPE_TTL_SECONDS,
                nx=True,
            )
            return bool(created)
        except Exception as exc:
            print(f"Slack response dedupe Redis acquire failed; falling back to memory: {exc}")

    now = datetime.utcnow()
    _prune_stale_slack_response_keys(now)
    if normalized_key in _seen_slack_response_keys:
        return False
    _seen_slack_response_keys[normalized_key] = now
    return True


def _build_cortex_failure_reply(reason: str) -> str:
    normalized_reason = str(reason or "").strip().lower()
    if normalized_reason == "session_bootstrap_failed":
        return (
            "I ran into a temporary session issue while processing that request. "
            "Please try again."
        )
    return (
        "I ran into a temporary issue while processing that request. "
        "Please try again."
    )


def _should_send_cortex_failure_reply(reason: str) -> bool:
    normalized_reason = str(reason or "").strip().lower()
    if not normalized_reason:
        return True
    if normalized_reason in {"ingress_not_accepted", "unsupported_event_type"}:
        return False
    return True


def _should_append_cortex_context_footer(
    *,
    is_dm_channel: bool,
    response_text: str,
    message_intent: str,
    used_tool_call: bool,
) -> bool:
    if not is_dm_channel:
        return False
    if not str(response_text or "").strip():
        return False
    if str(message_intent or "").strip() == "greeting_only":
        return False
    if not bool(used_tool_call):
        return False
    return True


def _is_slack_mention_only_text(text: str) -> bool:
    cleaned = SLACK_USER_MENTION_RE.sub("", str(text or ""))
    cleaned = re.sub(r"[\s\.,!?:;`'\"()\[\]{}\-_~]+", "", cleaned)
    return not bool(cleaned.strip())


def _extract_slack_user_mention_ids(text: str) -> List[str]:
    seen: set[str] = set()
    ids: List[str] = []
    for raw_id in SLACK_USER_MENTION_RE.findall(str(text or "")):
        candidate = str(raw_id or "").strip().upper()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        ids.append(candidate)
    return ids


async def _has_active_cortex_thread_session(
    *,
    project_id: str,
    slack_user_id: str,
    thread_anchor: str,
) -> bool:
    """Return True when this thread has an active Cortex channel session for the actor."""
    try:
        session_key = build_session_key(
            project_id=str(project_id or "").strip(),
            user_id=str(slack_user_id or "").strip(),
            source="slack_channel",
            anchor=str(thread_anchor or "").strip(),
        )
    except Exception:
        return False

    try:
        session = await _cortex_session_manager.get_session(session_key=session_key)
    except Exception as exc:
        print(
            "Slack thread-followup session lookup failed: "
            f"project_id={project_id} user={slack_user_id} anchor={thread_anchor} error={exc}"
        )
        return False
    return bool(session)


def _resolve_assignee_hint_candidates_from_mentions(
    *,
    project: Dict[str, Any],
    mentioned_user_ids: List[str],
    actor_email: Optional[str],
    actor_slack_user_id: Optional[str],
    bot_user_id: Optional[str],
) -> List[Dict[str, str]]:
    if not mentioned_user_ids:
        return []

    actor_email_norm = str(actor_email or "").strip().lower()
    actor_slack_norm = str(actor_slack_user_id or "").strip().upper()
    bot_slack_norm = str(bot_user_id or "").strip().upper()
    members = project.get("members") or []
    if not isinstance(members, list):
        return []

    member_by_slack_id: Dict[str, Dict[str, Any]] = {}
    for member in members:
        if not isinstance(member, dict):
            continue
        if str(member.get("status") or "").strip().lower() != "active":
            continue
        integration_ids = member.get("integration_ids") or {}
        slack_id = str(integration_ids.get("slack_user_id") or "").strip().upper()
        if slack_id and slack_id not in member_by_slack_id:
            member_by_slack_id[slack_id] = member

    candidates: List[Dict[str, str]] = []
    seen: set[str] = set()
    for mention_id in mentioned_user_ids:
        mention_norm = str(mention_id or "").strip().upper()
        if not mention_norm:
            continue
        if actor_slack_norm and mention_norm == actor_slack_norm:
            continue
        if bot_slack_norm and mention_norm == bot_slack_norm:
            continue
        member = member_by_slack_id.get(mention_norm)
        if not member:
            continue
        email = str(member.get("email") or "").strip().lower()
        if actor_email_norm and email and email == actor_email_norm:
            continue
        candidate_key = email or mention_norm
        if not candidate_key or candidate_key in seen:
            continue
        seen.add(candidate_key)
        candidates.append(
            {
                "email": email,
                "name": str(member.get("name") or "").strip(),
                "user_id": str(member.get("user_id") or "").strip(),
                "slack_user_id": mention_norm,
                "source": "slack_mention",
            }
        )
    return candidates


def _build_member_lookup_by_slack_id(project: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    members = project.get("members") or []
    if not isinstance(members, list):
        return {}
    lookup: Dict[str, Dict[str, Any]] = {}
    for member in members:
        if not isinstance(member, dict):
            continue
        if str(member.get("status") or "").strip().lower() != "active":
            continue
        integration_ids = member.get("integration_ids") or {}
        slack_id = str(integration_ids.get("slack_user_id") or "").strip().upper()
        if slack_id and slack_id not in lookup:
            lookup[slack_id] = member
    return lookup


def _normalize_slack_event_text_for_cortex(
    *,
    text: str,
    project: Dict[str, Any],
    bot_user_id: Optional[str],
) -> str:
    """
    Normalize Slack mention tokens into natural text for LLM ingestion.

    - Remove bot mention token(s) from text.
    - Replace member mention tokens with best display label.
    - Replace unknown mention tokens with generic teammate label.
    """
    source_text = str(text or "")
    if not source_text:
        return ""

    bot_id = str(bot_user_id or "").strip().upper()
    member_lookup = _build_member_lookup_by_slack_id(project)

    def _replace(match: re.Match[str]) -> str:
        mention_id = str(match.group(1) or "").strip().upper()
        if not mention_id:
            return " "
        if bot_id and mention_id == bot_id:
            return " "
        member = member_lookup.get(mention_id)
        if not member:
            return " teammate "
        name = str(member.get("name") or "").strip()
        email = str(member.get("email") or "").strip().lower()
        user_id = str(member.get("user_id") or "").strip()
        label = name or email or user_id or "teammate"
        return f" {label} "

    normalized = SLACK_USER_MENTION_RE.sub(_replace, source_text)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _slack_response_to_dict(response: Any) -> Dict[str, Any]:
    """Normalize Slack SDK response objects into plain dict payloads."""
    if isinstance(response, dict):
        return response
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        return data
    try:
        mapped = dict(response)
        if isinstance(mapped, dict):
            return mapped
    except Exception:
        pass
    return {}


async def _fetch_thread_root_text_best_effort(
    *,
    slack_client: Any,
    channel: str,
    thread_ts: str,
) -> Optional[str]:
    if not slack_client or not channel or not thread_ts:
        return None
    try:
        response = await slack_client.conversations_replies(
            channel=channel,
            ts=thread_ts,
            limit=10,
            inclusive=True,
        )
    except Exception as exc:
        print(f"Slack thread root fetch failed: channel={channel} thread_ts={thread_ts} error={exc}")
        return None

    payload = _slack_response_to_dict(response)
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return None

    anchor_ts = str(thread_ts or "").strip()
    root_message: Optional[Dict[str, Any]] = None
    for message in messages:
        if not isinstance(message, dict):
            continue
        msg_ts = str(message.get("ts") or "").strip()
        if msg_ts and msg_ts == anchor_ts:
            root_message = message
            break

    if isinstance(root_message, dict) and not root_message.get("bot_id"):
        root_text = str(root_message.get("text") or "").strip()
        if root_text and not _is_slack_mention_only_text(root_text):
            return root_text

    scanned_human = 0
    for message in messages:
        if not isinstance(message, dict):
            continue
        if message.get("bot_id"):
            continue
        scanned_human += 1
        text = str(message.get("text") or "").strip()
        if not text:
            continue
        if _is_slack_mention_only_text(text):
            continue
        return text
    return None


async def _fetch_thread_context_window_best_effort(
    *,
    slack_client: Any,
    channel: str,
    thread_ts: str,
    project: Dict[str, Any],
    bot_user_id: Optional[str],
    current_event_ts: Optional[str],
    max_messages: int = 5,
) -> Optional[str]:
    """
    Fetch compact thread context (root + recent human messages) for mention routing.
    """
    if not slack_client or not channel or not thread_ts:
        return None
    try:
        response = await slack_client.conversations_replies(
            channel=channel,
            ts=thread_ts,
            limit=25,
            inclusive=True,
        )
    except Exception as exc:
        print(
            f"Slack thread context fetch failed: channel={channel} "
            f"thread_ts={thread_ts} error={exc}"
        )
        return None

    payload = _slack_response_to_dict(response)
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return None

    current_ts = str(current_event_ts or "").strip()
    root_ts = str(thread_ts or "").strip()
    normalized: List[str] = []
    scanned_human = 0
    skipped_root = 0
    skipped_current = 0
    skipped_empty = 0
    skipped_mention_only = 0
    for item in messages:
        if not isinstance(item, dict):
            continue
        if item.get("bot_id"):
            continue
        scanned_human += 1
        msg_ts = str(item.get("ts") or "").strip()
        if msg_ts and msg_ts == root_ts:
            # Root line is handled separately for deterministic anchoring.
            skipped_root += 1
            continue
        if current_ts and msg_ts and msg_ts == current_ts:
            skipped_current += 1
            continue
        raw_text = str(item.get("text") or "").strip()
        if not raw_text:
            skipped_empty += 1
            continue
        rendered = _normalize_slack_event_text_for_cortex(
            text=raw_text,
            project=project,
            bot_user_id=bot_user_id,
        )
        if not rendered:
            skipped_empty += 1
            continue
        if _is_slack_mention_only_text(rendered):
            skipped_mention_only += 1
            continue
        normalized.append(rendered)

    if not normalized:
        return None
    tail = normalized[-max(1, int(max_messages)) :]
    lines = ["Thread context:"] + [f"- {line}" for line in tail]
    return "\n".join(lines)


async def _send_operational_fallback_best_effort(
    *,
    slack_client,
    channel_id: str,
    event_type: str,
    thread_ts: Optional[str],
    reason: str,
) -> None:
    """Send a user-visible operational fallback reply for Cortex handoff failures."""
    try:
        payload = {
            "channel": channel_id,
            "text": _build_cortex_failure_reply(reason),
        }
        if event_type == "app_mention" and thread_ts:
            payload["thread_ts"] = thread_ts
        await slack_client.chat_postMessage(**payload)
    except Exception as e:
        print(f"Error sending operational fallback response: {str(e)}")


async def _send_greeting_response_best_effort(
    *,
    slack_client,
    channel_id: str,
    event_type: str,
    thread_ts: Optional[str],
    member_name: Optional[str] = None,
    member_role: Optional[str] = None,
) -> None:
    """Send a single greeting response for greeting-only messages."""
    try:
        display_name = (member_name or "").strip()
        first_name = display_name.split()[0] if display_name else ""
        normalized_role = str(member_role or "").strip().lower()
        is_owner = normalized_role == "owner"

        if is_owner:
            owner_variants_named = [
                f"Hello {first_name}! What would you like me to handle today?" if first_name else "",
                f"Hi {first_name}, what should I prioritize for you?" if first_name else "",
                "Hello boss. What should I take care of first?",
                "Hi boss, how can I support you right now?",
            ]
            owner_variants = [item for item in owner_variants_named if item]
            text = random.choice(owner_variants)
        else:
            neutral_variants_named = [
                f"Hello {first_name}! How can I assist you today?" if first_name else "",
                f"Hi {first_name}! What can I help you with?" if first_name else "",
                "Hello! How can I assist you today?",
                "Hi there! How can I help?",
            ]
            neutral_variants = [item for item in neutral_variants_named if item]
            text = random.choice(neutral_variants)

        payload = {
            "channel": channel_id,
            "text": text,
        }
        if event_type == "app_mention" and thread_ts:
            payload["thread_ts"] = thread_ts
        await slack_client.chat_postMessage(**payload)
    except Exception as e:
        print(f"Error sending greeting response: {str(e)}")


async def _send_scope_refusal_best_effort(
    *,
    slack_client,
    channel_id: str,
    event_type: str,
    thread_ts: Optional[str],
) -> None:
    try:
        payload = {
            "channel": channel_id,
            "text": build_scope_refusal_response(),
        }
        if event_type == "app_mention" and thread_ts:
            payload["thread_ts"] = thread_ts
        await slack_client.chat_postMessage(**payload)
    except Exception as e:
        print(f"Error sending scope refusal response: {str(e)}")


def _build_context_clarification_response(
    *,
    reason_code: str,
    intent_text: str,
) -> str:
    normalized_text = str(intent_text or "").strip()
    quoted = f"\"{normalized_text}\"" if normalized_text else "that input"
    if reason_code == "missing_target":
        return (
            f"I need a bit more context to help with {quoted}. "
            "Please tell me which work item, project area, or team scope this refers to."
        )
    if reason_code == "missing_action":
        return (
            f"I need a bit more context to help with {quoted}. "
            "Please tell me what you want to do with it."
        )
    return (
        f"I need a bit more context to help with {quoted}. "
        "Please describe what you want me to check or update."
    )


def _should_request_context_clarification(
    *,
    intent_text: str,
    stage1_scope_gate,
) -> Dict[str, Any]:
    normalized = str(intent_text or "").strip().lower()
    if not normalized:
        return {"should_clarify": False, "reason_code": ""}
    tokens = SLACK_WORD_RE.findall(normalized)
    if not tokens:
        return {"should_clarify": False, "reason_code": ""}

    signals = set(stage1_scope_gate.signals or [])
    has_work_item_ref = "work_item_key" in signals
    has_entity_signal = "project_entity_hint" in signals
    has_action_signal = "project_action_hint" in signals
    has_question = "?" in normalized

    if has_work_item_ref:
        return {"should_clarify": False, "reason_code": ""}
    if len(tokens) == 1:
        reason_code = "missing_target" if has_action_signal and not has_entity_signal else "insufficient_context"
        return {"should_clarify": True, "reason_code": reason_code}
    if len(tokens) == 2 and has_action_signal and not has_entity_signal:
        return {"should_clarify": True, "reason_code": "missing_target"}
    if (
        stage1_scope_gate.classification == SCOPE_GATE_UNCERTAIN
        and len(tokens) <= 2
    ):
        return {"should_clarify": True, "reason_code": "insufficient_context"}
    if len(tokens) <= 3 and (not has_question) and has_action_signal and not has_entity_signal:
        return {"should_clarify": True, "reason_code": "missing_target"}
    if len(tokens) <= 3 and (not has_question) and has_entity_signal and (not has_action_signal):
        return {"should_clarify": True, "reason_code": "missing_action"}
    return {"should_clarify": False, "reason_code": ""}


async def _send_context_clarification_best_effort(
    *,
    slack_client,
    channel_id: str,
    event_type: str,
    thread_ts: Optional[str],
    clarification_text: str,
) -> None:
    try:
        payload = {
            "channel": channel_id,
            "text": str(clarification_text or "").strip(),
        }
        if event_type == "app_mention" and thread_ts:
            payload["thread_ts"] = thread_ts
        await slack_client.chat_postMessage(**payload)
    except Exception as e:
        print(f"Error sending context clarification response: {str(e)}")


def _normalize_project_lookup_key(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", str(value or "").strip().lower())


def _extract_project_switch_hint(text: str) -> Optional[str]:
    raw = str(text or "").strip()
    if not raw:
        return None
    numeric = raw.strip()
    if numeric.isdigit():
        return numeric
    patterns = [
        r"^\s*use\s+project\s+(.+?)\s*$",
        r"^\s*switch\s+to\s+(.+?)\s*$",
        r"^\s*switch\s+project\s+(.+?)\s*$",
        r"^\s*set\s+project\s+(.+?)\s*$",
    ]
    for pattern in patterns:
        match = re.match(pattern, raw, flags=re.IGNORECASE)
        if not match:
            continue
        candidate = str(match.group(1) or "").strip().strip("\"'")
        return candidate or None
    return None


def _find_selected_project_from_hint(
    *,
    hint: str,
    candidate_projects: List[Dict[str, str]],
) -> Optional[Dict[str, str]]:
    clean_hint = str(hint or "").strip()
    if not clean_hint:
        return None
    if clean_hint.isdigit():
        idx = int(clean_hint) - 1
        if 0 <= idx < len(candidate_projects):
            return candidate_projects[idx]
        return None

    hint_key = _normalize_project_lookup_key(clean_hint)
    by_id = [
        item
        for item in candidate_projects
        if _normalize_project_lookup_key(item.get("project_id", "")) == hint_key
    ]
    if len(by_id) == 1:
        return by_id[0]
    if len(by_id) > 1:
        return None

    by_name = [
        item
        for item in candidate_projects
        if _normalize_project_lookup_key(item.get("project_name", "")) == hint_key
    ]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        return None

    prefix_matches = [
        item
        for item in candidate_projects
        if _normalize_project_lookup_key(item.get("project_id", "")).startswith(hint_key)
        or _normalize_project_lookup_key(item.get("project_name", "")).startswith(hint_key)
    ]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    return None


def _display_project_name(project: Dict[str, str], *, fallback_index: Optional[int] = None) -> str:
    """Render user-facing project names without leaking internal IDs."""
    project_name = str((project or {}).get("project_name") or "").strip()
    project_id = str((project or {}).get("project_id") or "").strip()
    normalized_name = _normalize_project_lookup_key(project_name)
    normalized_id = _normalize_project_lookup_key(project_id)
    if project_name and (not project_id or normalized_name != normalized_id):
        return project_name
    if fallback_index is not None:
        return f"Project {fallback_index}"
    return "Project"


def _build_ambiguous_project_message(
    *,
    candidate_projects: List[Dict[str, str]],
) -> str:
    intro = (
        "I found multiple ProMarshal projects for your workspace DM. "
        "Set one active project using a slash command."
    )
    lines = [
        intro,
        "",
        'Type "switch project" to choose a project.',
    ]
    if candidate_projects:
        lines.append("Available projects:")
        for idx, item in enumerate(candidate_projects, start=1):
            project_name = _display_project_name(item, fallback_index=idx)
            lines.append(f"{idx}. {project_name}")
        lines.append("If names are similar, use the number.")
    return "\n".join(lines)


def _build_project_resolution_failure_message(reason: str) -> str:
    normalized = str(reason or "").strip().lower()
    if normalized == "user_not_member":
        return (
            "You are not an active member of any ProMarshal project in this workspace yet. "
            "Please accept your invite first, then try again."
        )
    if normalized == "no_project_for_team":
        return "I couldn't find any ProMarshal projects connected to this Slack workspace."
    if normalized == "channel_not_mapped":
        return (
            "This Slack channel is not mapped to a ProMarshal project. "
            "Use a mapped channel or DM the bot after selecting an active project."
        )
    return "I couldn't resolve your project access for this request. Please try again."


def _parse_dm_project_context_command(text: str) -> Optional[Dict[str, str]]:
    raw = str(text or "").strip()
    if not raw:
        return None
    lowered = raw.lower()

    if lowered in {"projects", "list projects", "show projects"}:
        return {"intent": "list_projects"}
    if lowered == "switch project":
        return {"intent": "switch_list"}
    if lowered in {"current project", "active project"}:
        return {"intent": "current_project"}

    select_match = re.match(
        r"^\s*(?:switch\s+project|use\s+project|switch\s+to|set\s+project)\s+(.+?)\s*$",
        raw,
        flags=re.IGNORECASE,
    )
    if select_match:
        target = str(select_match.group(1) or "").strip().strip("\"'")
        if target:
            return {"intent": "switch_select", "target": target, "source": "explicit"}

    if re.fullmatch(r"\d{1,3}", raw):
        return {"intent": "switch_select", "target": raw, "source": "index_reply"}

    return None


def _build_project_switch_list_message(
    *,
    candidate_projects: List[Dict[str, str]],
    active_project_id: Optional[str],
    heading: Optional[str] = None,
) -> str:
    lines: List[str] = []
    lines.append(
        heading
        or "Choose a project for this DM context."
    )
    lines.append("")
    if not candidate_projects:
        lines.append("No available projects found for your user in this workspace.")
        return "\n".join(lines)

    lines.append("Available projects:")
    for idx, item in enumerate(candidate_projects, start=1):
        project_id = str(item.get("project_id") or "").strip()
        project_name = _display_project_name(item, fallback_index=idx)
        active_marker = " [active]" if active_project_id and project_id and project_id == active_project_id else ""
        lines.append(f"{idx}. {project_name}{active_marker}")
    lines.append("")
    lines.append("Reply with the number (recommended), or use `switch to <project name>`.")
    lines.append("If names are similar, use the number.")
    return "\n".join(lines)


def _build_project_set_confirmation_message(project_name: str) -> str:
    clean_name = str(project_name or "").strip() or "Project"
    return (
        f"Active project set to {clean_name}. "
        "You can switch it any time using `switch project`."
    )


def _is_exact_switch_project_command(text: str) -> bool:
    return " ".join(str(text or "").strip().lower().split()) == "switch project"


async def _has_non_terminal_pending_clarification_for_identity(
    *,
    team_id: str,
    slack_user_id: str,
) -> bool:
    for pending_type in (
        "work_item_parent_clarification",
        "provider_type_clarification",
        "action_item_clarification",
    ):
        rows = await list_pending_interactions_by_external_identity(
            provider="slack",
            workspace_id=str(team_id or "").strip(),
            external_user_id=str(slack_user_id or "").strip(),
            interaction_type=pending_type,
            limit=1,
        )
        if rows:
            return True
    return False


async def _send_switch_project_modal_button_prompt(
    *,
    db,
    team_id: str,
    channel: str,
    thread_ts: Optional[str],
) -> bool:
    slack_client = await _get_workspace_slack_client(
        db=db,
        team_id=str(team_id or "").strip(),
    )
    if not slack_client:
        return False
    try:
        await slack_client.chat_postMessage(
            channel=str(channel or "").strip(),
            text="Open project switcher",
            thread_ts=thread_ts,
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Use the button below to open the project switcher.",
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Open Project Switcher"},
                            "action_id": "project_switch_open_modal",
                            "value": "open_project_switch_modal",
                        }
                    ],
                },
            ],
        )
        return True
    except Exception as exc:
        print(
            "Slack switch-project modal button prompt failed: "
            f"team_id={team_id} channel={channel} error={exc}"
        )
        return False


async def _handle_slack_dm_project_context_command(
    *,
    db,
    team_id: str,
    slack_user_id: str,
    channel: str,
    thread_ts: Optional[str],
    text: str,
) -> bool:
    command = _parse_dm_project_context_command(text)
    if not command:
        return False

    listing = await list_slack_member_projects(
        db=db,
        team_id=team_id,
        slack_user_id=slack_user_id,
    )

    async def _send_context_reply(
        *,
        reply_text: str,
        reason_label: str,
        preferred_project_id: Optional[str] = None,
        candidate_projects: Optional[List[Dict[str, str]]] = None,
    ) -> None:
        sent = await _send_slack_dm_text_with_workspace_fallback(
            db=db,
            team_id=team_id,
            channel=channel,
            text=reply_text,
            thread_ts=thread_ts,
            reason_label=reason_label,
            preferred_project_id=preferred_project_id,
            candidate_projects=candidate_projects,
        )
        if not sent:
            print(
                "Slack project context reply delivery exhausted: "
                f"reason={reason_label} team_id={team_id} user={slack_user_id} channel={channel}"
            )

    if not listing.ok:
        if listing.reason == "no_project_for_team":
            reply = "I couldn't find any ProMarshal projects connected to this Slack workspace."
        elif listing.reason == "user_not_member":
            reply = (
                "I couldn't find any active ProMarshal project memberships for your Slack user. "
                "Ask the project owner to add you as a member."
            )
        else:
            reply = "I couldn't resolve your project context right now. Please try again."
        await _send_context_reply(
            reply_text=reply,
            reason_label=f"project_context_{listing.reason or 'unknown'}",
        )
        return True

    candidate_projects = [item for item in listing.projects if isinstance(item, dict)]
    active_project_id = str(listing.active_project_id or "").strip() or None
    intent = command.get("intent")

    if intent in {"list_projects", "switch_list"}:
        heading = (
            "Project switch requested. Choose a project for this DM."
            if intent == "switch_list"
            else "Here are your available projects for this workspace."
        )
        reply = _build_project_switch_list_message(
            candidate_projects=candidate_projects,
            active_project_id=active_project_id,
            heading=heading,
        )
        print(
            "Slack project context command handled: "
            f"intent={intent} team_id={team_id} user={slack_user_id} count={len(candidate_projects)}"
        )
        await _send_context_reply(
            reply_text=reply,
            reason_label=f"project_context_{intent}",
            preferred_project_id=active_project_id,
            candidate_projects=candidate_projects,
        )
        return True

    if intent == "current_project":
        if active_project_id:
            selected = next(
                (item for item in candidate_projects if str(item.get("project_id") or "").strip() == active_project_id),
                None,
            )
            project_name = _display_project_name(selected or {})
            reply = f"Current active project: {project_name}."
        else:
            reply = _build_project_switch_list_message(
                candidate_projects=candidate_projects,
                active_project_id=None,
                heading="No active project is set for this DM yet.",
            )
        print(
            "Slack project context command handled: "
            f"intent=current_project team_id={team_id} user={slack_user_id} active={active_project_id or 'none'}"
        )
        await _send_context_reply(
            reply_text=reply,
            reason_label="project_context_current_project",
            preferred_project_id=active_project_id,
            candidate_projects=candidate_projects,
        )
        return True

    if intent == "switch_select":
        target = str(command.get("target") or "").strip()
        source = str(command.get("source") or "").strip().lower()
        # Guard against accidental numeric messages unrelated to project switching.
        if source == "index_reply" and len(candidate_projects) <= 1:
            return False

        selected_project = _find_selected_project_from_hint(
            hint=target,
            candidate_projects=candidate_projects,
        )
        if not selected_project:
            reply = _build_project_switch_list_message(
                candidate_projects=candidate_projects,
                active_project_id=active_project_id,
                heading=f"I couldn't match `{target}` to your project list.",
            )
            print(
                "Slack project context switch failed: "
                f"team_id={team_id} user={slack_user_id} target={target}"
            )
            await _send_context_reply(
                reply_text=reply,
                reason_label="project_context_switch_select_unmatched",
                preferred_project_id=active_project_id,
                candidate_projects=candidate_projects,
            )
            return True

        project_id = str(selected_project.get("project_id") or "").strip()
        project_name = _display_project_name(selected_project)
        if active_project_id and project_id == active_project_id:
            reply = f"{project_name} is already your active project for this DM."
            await _send_context_reply(
                reply_text=reply,
                reason_label="project_context_switch_select_already_active",
                preferred_project_id=project_id,
                candidate_projects=candidate_projects,
            )
            return True

        persisted = await persist_slack_active_project(
            db=db,
            team_id=team_id,
            slack_user_id=slack_user_id,
            user_email=listing.user_email,
            project_id=project_id,
            project_name=project_name,
            updated_by="slack_dm_project_context_command",
        )
        if persisted:
            reply = _build_project_set_confirmation_message(project_name)
        else:
            reply = (
                f"I selected {project_name}, but couldn't persist it reliably. "
                "Please try `switch project` again if routing is inconsistent."
            )
        print(
            "Slack project context switch handled: "
            f"team_id={team_id} user={slack_user_id} project_id={project_id} persisted={persisted}"
        )
        await _send_context_reply(
            reply_text=reply,
            reason_label="project_context_switch_select_applied",
            preferred_project_id=project_id,
            candidate_projects=candidate_projects,
        )
        return True

    return False


async def _get_workspace_slack_client(
    *,
    db,
    team_id: str,
    preferred_project_id: Optional[str] = None,
):
    query = {
        "integrations.slack.team_id": team_id,
        "integrations.slack.status": "connected",
    }
    if preferred_project_id:
        query["project_id"] = preferred_project_id
    project = await db.projects.find_one(query, {"integrations.slack.access_token": 1})
    if not project and preferred_project_id:
        project = await db.projects.find_one(
            {
                "integrations.slack.team_id": team_id,
                "integrations.slack.status": "connected",
            },
            {"integrations.slack.access_token": 1},
        )
    if not project:
        return None
    encrypted_token = ((project.get("integrations") or {}).get("slack") or {}).get("access_token")
    if not encrypted_token:
        return None
    try:
        return slack_bot_service.get_slack_client(encrypted_token)
    except Exception as exc:
        print(f"Failed to create Slack client for workspace context prompt: {exc}")
        return None


def _build_slack_client_from_access_token(access_token: Optional[str]) -> Optional[Any]:
    """Create resilient Slack client from plain access token."""
    token = str(access_token or "").strip()
    if not token:
        return None
    try:
        return ResilientSlackWebClient(AsyncWebClient(token=token))
    except Exception as exc:
        print(f"Failed to create Slack client from resolver access token: {exc}")
        return None


def _extract_slack_delivery_error_details(exc: Exception) -> Dict[str, Any]:
    """Normalize Slack delivery errors for actionable logs."""
    details: Dict[str, Any] = {
        "error_type": exc.__class__.__name__,
        "error_message": str(exc),
        "error_code": None,
        "http_status": None,
        "retryable": None,
    }
    if isinstance(exc, IntegrationError):
        details["error_code"] = str(exc.error_code or "").strip() or None
        details["http_status"] = exc.http_status
        details["retryable"] = bool(exc.retryable)
        return details

    response = getattr(exc, "response", None)
    if response is not None:
        status_code = getattr(response, "status_code", None)
        details["http_status"] = status_code
        data = getattr(response, "data", None)
        if isinstance(data, dict):
            details["error_code"] = str(data.get("error") or "").strip() or None
    return details


async def _send_slack_dm_text_with_workspace_fallback(
    *,
    db,
    team_id: str,
    channel: str,
    text: str,
    thread_ts: Optional[str] = None,
    reason_label: str,
    preferred_project_id: Optional[str] = None,
    fallback_access_token: Optional[str] = None,
    candidate_projects: Optional[List[Dict[str, str]]] = None,
) -> bool:
    """Best-effort DM send using resolver token + workspace project-token fallbacks."""
    normalized_team = str(team_id or "").strip()
    normalized_channel = str(channel or "").strip()
    normalized_preferred = str(preferred_project_id or "").strip() or None

    attempts: List[tuple[str, Optional[Any]]] = []
    attempts.append(
        (
            "resolver_access_token",
            _build_slack_client_from_access_token(fallback_access_token),
        )
    )

    if normalized_preferred:
        attempts.append(
            (
                f"workspace_project:{normalized_preferred}",
                await _get_workspace_slack_client(
                    db=db,
                    team_id=normalized_team,
                    preferred_project_id=normalized_preferred,
                ),
            )
        )

    seen_project_ids: set[str] = set()
    for item in candidate_projects or []:
        project_id = str((item or {}).get("project_id") or "").strip()
        if not project_id or project_id == normalized_preferred or project_id in seen_project_ids:
            continue
        seen_project_ids.add(project_id)
        attempts.append(
            (
                f"workspace_candidate:{project_id}",
                await _get_workspace_slack_client(
                    db=db,
                    team_id=normalized_team,
                    preferred_project_id=project_id,
                ),
            )
        )

    attempts.append(
        (
            "workspace_generic",
            await _get_workspace_slack_client(db=db, team_id=normalized_team),
        )
    )

    for source_label, slack_client in attempts:
        if not slack_client:
            continue
        try:
            await slack_client.chat_postMessage(
                channel=normalized_channel,
                text=text,
                thread_ts=thread_ts,
            )
            print(
                "Slack DM fallback send succeeded: "
                f"reason={reason_label} team_id={normalized_team} "
                f"channel={normalized_channel} source={source_label}"
            )
            return True
        except Exception as exc:
            details = _extract_slack_delivery_error_details(exc)
            print(
                "Slack DM fallback send failed: "
                f"reason={reason_label} team_id={normalized_team} "
                f"channel={normalized_channel} source={source_label} "
                f"error_type={details.get('error_type')} "
                f"error_code={details.get('error_code')} "
                f"http_status={details.get('http_status')} "
                f"retryable={details.get('retryable')} "
                f"error={details.get('error_message')}"
            )
    return False


def _choose_default_jira_board(available_boards: List[Dict[str, Any]]) -> tuple[Optional[Dict[str, Any]], str]:
    normalized = [item for item in available_boards if isinstance(item, dict)]
    scrum = [item for item in normalized if str(item.get("type") or "").strip().lower() == "scrum"]
    simple = [item for item in normalized if str(item.get("type") or "").strip().lower() == "simple"]
    kanban = [item for item in normalized if str(item.get("type") or "").strip().lower() == "kanban"]

    if len(scrum) == 1:
        return scrum[0], "single_scrum_board"
    if len(scrum) > 1:
        return None, "ambiguous_multiple_scrum_boards"
    if len(simple) == 1:
        return simple[0], "single_simple_board"
    if len(simple) > 1:
        return None, "ambiguous_multiple_simple_boards"
    if len(kanban) == 1:
        return kanban[0], "single_kanban_board"
    if len(kanban) > 1:
        return None, "ambiguous_multiple_kanban_boards"
    if len(normalized) == 1:
        return normalized[0], "single_board_unknown_type"
    if len(normalized) > 1:
        return None, "ambiguous_multiple_boards"
    return None, "no_board_found"


def _resolve_project_numeric_id(
    *,
    available_projects: List[Dict[str, Any]],
    project_key: str,
) -> Optional[str]:
    normalized_key = str(project_key or "").strip().upper()
    if not normalized_key:
        return None
    for item in available_projects:
        if not isinstance(item, dict):
            continue
        item_key = str(item.get("key") or "").strip().upper()
        if item_key != normalized_key:
            continue
        item_id = str(item.get("id") or "").strip()
        if item_id:
            return item_id
    return None


async def _fetch_jira_boards_with_fallback(
    *,
    access_token: str,
    cloud_id: str,
    project_key: str,
    project_numeric_id: Optional[str],
) -> tuple[List[Dict[str, Any]], str]:
    boards = await jira_oauth_service.get_boards_for_project(
        access_token=access_token,
        cloud_id=cloud_id,
        project_key=project_key,
    )
    if boards:
        return boards, "project_key"

    numeric_id = str(project_numeric_id or "").strip()
    if numeric_id and numeric_id != str(project_key or "").strip():
        boards = await jira_oauth_service.get_boards_for_project(
            access_token=access_token,
            cloud_id=cloud_id,
            project_key=numeric_id,
        )
        if boards:
            return boards, "project_id"

    return [], "none"


def _normalize_accessible_jira_sites(
    accessible_resources: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Normalize Atlassian accessible resources into deterministic site candidates."""
    candidates: List[Dict[str, str]] = []
    seen_ids: set[str] = set()
    for resource in accessible_resources or []:
        if not isinstance(resource, dict):
            continue
        cloud_id = str(resource.get("id") or "").strip()
        if not cloud_id or cloud_id in seen_ids:
            continue
        seen_ids.add(cloud_id)
        candidates.append(
            {
                "id": cloud_id,
                "name": str(resource.get("name") or "").strip(),
                "url": str(resource.get("url") or "").strip(),
            }
        )
    return candidates


async def _fetch_available_jira_projects_for_site(
    *,
    access_token: str,
    cloud_id: str,
) -> List[Dict[str, Any]]:
    """Fetch Jira projects available in the selected site."""
    import httpx

    async with httpx.AsyncClient() as client:
        proj_response = await client.get(
            f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/project",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
            timeout=30.0,
        )
        if proj_response.status_code != 200:
            raise ValueError(
                f"jira_project_fetch_failed status={proj_response.status_code}"
            )
        projects = proj_response.json()
    return [
        {
            "key": p.get("key"),
            "name": p.get("name"),
            "id": p.get("id"),
        }
        for p in projects
        if isinstance(p, dict)
    ]


# ==================== SLACK INTEGRATION ====================

@router.get("/slack/connect")
async def connect_slack(
    project_id: str = Query(..., description="Project ID to integrate with Slack"),
    current_user: dict = Depends(get_current_user),
    return_nav: str = Query(None, description="Navigation state to return to"),
    return_tab: str = Query(None, description="Tab state to return to")
):
    """Initiate Slack OAuth flow"""
    try:
        db = get_database()
        user_id = str(current_user.get("user_id") or "")
        project = await db.projects.find_one({
            "_id": ObjectId(project_id),
            "user_id": user_id
        })

        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found or access denied"
            )

        state_token = oauth_state_service.generate_state_token(
            project_id, user_id, return_nav, return_tab
        )
        redirect_uri = f"{settings.backend_url}/api/integrations/slack/callback"
        auth_url = slack_oauth_service.get_authorization_url(state_token, redirect_uri)

        return RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initiate Slack OAuth: {str(e)}"
        )


@router.get("/slack/callback")
async def slack_callback(
    code: str = Query(..., description="Authorization code from Slack"),
    state: str = Query(..., description="State token for CSRF protection")
):
    """Handle Slack OAuth callback"""
    try:
        try:
            state_payload = oauth_state_service.verify_state_token(state)
            project_id = state_payload["project_id"]
            user_id = state_payload["user_id"]
            return_nav = state_payload.get("return_nav")
            return_tab = state_payload.get("return_tab")
        except ValueError:
            return RedirectResponse(
                url=f"{settings.frontend_url}/projects?error=invalid_state",
                status_code=status.HTTP_302_FOUND
            )

        redirect_uri = f"{settings.backend_url}/api/integrations/slack/callback"

        try:
            token_response = await slack_oauth_service.exchange_code_for_token(code, redirect_uri)
        except Exception:
            return RedirectResponse(
                url=f"{settings.frontend_url}/projects?error=slack_auth_failed",
                status_code=status.HTTP_302_FOUND
            )

        access_token = token_response.get("access_token")
        team_id = token_response.get("team", {}).get("id")
        team_name = token_response.get("team", {}).get("name")
        bot_user_id = token_response.get("bot_user_id")

        if not access_token:
            return RedirectResponse(
                url=f"{settings.frontend_url}/projects?error=no_access_token",
                status_code=status.HTTP_302_FOUND
            )

        encrypted_token = encryption_service.encrypt(access_token)

        # New structure: Store everything in projects.integrations.slack
        slack_integration = {
            "provider": "slack",
            "category": "communication",
            "team_id": team_id,
            "team_name": team_name,
            "bot_user_id": bot_user_id,
            "status": "connected",
            "connected_at": datetime.utcnow(),
            "connected_by": user_id,
            "access_token": encrypted_token,
            "last_sync_at": None
        }

        db = get_database()
        result = await db.projects.update_one(
            {"_id": ObjectId(project_id), "user_id": user_id},
            {
                "$set": {
                    "integrations.slack": slack_integration,
                    "updated_at": datetime.utcnow()
                }
            }
        )

        if result.modified_count == 0 and not result.upserted_id:
            return RedirectResponse(
                url=f"{settings.frontend_url}/projects?error=update_failed",
                status_code=status.HTTP_302_FOUND
            )
        await invalidate_pm_board_summary_cache_by_object_id(db, project_id)

        # Best-effort member identity sync so DM resolver can route across projects
        # immediately after connect.
        try:
            authed_slack_user_id = (
                (token_response.get("authed_user") or {}).get("id")
                or (token_response.get("authorizing_user") or {}).get("id")
            )
            if authed_slack_user_id:
                owner_link = await link_member_slack_id_detailed(
                    db=db,
                    project_id=project_id,
                    member_email=user_id,
                    slack_user_id=authed_slack_user_id,
                    overwrite=True,
                    validate_with_slack=True,
                )
                print(
                    "Slack callback owner mapping outcome: "
                    f"project_id={project_id} email={user_id} status={owner_link.get('status')}"
                )

            asyncio.create_task(
                _run_slack_post_connect_tasks_in_background(db=db, project_id=project_id)
            )
        except Exception as sync_exc:
            print(f"Slack callback member sync failed: project_id={project_id} error={sync_exc}")

        # Redirect to projects page with slack_connected flag to auto-open channel modal
        redirect_url = f"{settings.frontend_url}/projects?slack_connected=true&project_id={project_id}&nav=Control Panel&tab=Integrate Tools"

        return RedirectResponse(
            url=redirect_url,
            status_code=status.HTTP_302_FOUND
        )

    except Exception as e:
        print(f"Slack callback error: {str(e)}")
        return RedirectResponse(
            url=f"{settings.frontend_url}/projects?error=unexpected",
            status_code=status.HTTP_302_FOUND
        )


@router.post("/slack/events")
async def slack_events(
    raw_body: bytes = Depends(require_webhook_auth("slack")),
):
    """Handle Slack Events API webhooks"""
    # Parse the JSON payload
    try:
        payload = json_lib.loads(raw_body.decode("utf-8"))
    except json_lib.JSONDecodeError as e:
        print(f"JSON decode error: {str(e)}")
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    # Handle URL verification challenge
    if payload.get("type") == "url_verification":
        return JSONResponse({"challenge": payload.get("challenge")})

    # Handle event callbacks
    if payload.get("type") == "event_callback":
        event_id = payload.get("event_id")
        if _is_duplicate_slack_event(event_id):
            print(f"Slack duplicate event ignored: event_id={event_id}")
            return JSONResponse({"ok": True})

        event = payload.get("event", {})
        event_type = event.get("type")
        print(
            "Slack event callback: "
            f"event_id={event_id} type={event_type} team={payload.get('team_id')} "
            f"channel={event.get('channel')} user={event.get('user')} subtype={event.get('subtype')}"
        )

        if event_type == "app_home_opened":
            # Slack requires acknowledging app_home_opened when the Messages tab is enabled.
            return JSONResponse({"ok": True})

        if event_type in {"app_mention", "message"}:
            print(
                "Slack event received: "
                f"type={event_type} team={payload.get('team_id')} "
                f"channel={event.get('channel')} user={event.get('user')} "
                f"subtype={event.get('subtype')}"
            )
            # Ignore bot-originated events in shared ingress path.
            if event.get("bot_id"):
                return JSONResponse({"ok": True})

            team_id = payload.get("team_id")
            text = event.get("text", "")
            channel = event.get("channel")
            is_dm_channel = bool((channel or "").startswith("D"))
            if event_type == "app_mention":
                thread_ts = event.get("thread_ts") or event.get("ts")
            else:
                # Keep DM replies in the main conversation stream unless the user explicitly replies in a thread.
                thread_ts = event.get("thread_ts")
            slack_user_id = event.get("user")

            # For message events, handle:
            # - plain DMs
            # - thread follow-ups in channels only when an active thread session exists
            if event_type == "message":
                if event.get("subtype"):
                    print(
                        "Slack message event ignored: subtype present "
                        f"subtype={event.get('subtype')} channel={channel} user={slack_user_id}"
                    )
                    return JSONResponse({"ok": True})
                if not is_dm_channel and not thread_ts:
                    print(
                        "Slack message event ignored: non-DM non-thread message "
                        f"channel={channel} user={slack_user_id}"
                    )
                    return JSONResponse({"ok": True})

            if not team_id or not channel or not slack_user_id:
                print(
                    f"Slack event missing routing keys: type={event_type} "
                    f"team={team_id} channel={channel} user={slack_user_id}"
                )
                return JSONResponse({"ok": True})

            semantic_event_key = _build_slack_semantic_event_key(
                team_id=team_id,
                channel_id=channel,
                user_id=slack_user_id,
                event_type=event_type,
                event_ts=event.get("ts"),
                thread_ts=thread_ts,
                client_msg_id=event.get("client_msg_id"),
                text=text,
            )
            if _is_duplicate_slack_semantic_event(semantic_event_key):
                print(
                    "Slack semantic duplicate event ignored: "
                    f"event_id={event_id} key={semantic_event_key}"
                )
                return JSONResponse({"ok": True})

            db = get_database()
            if event_type == "message" and is_dm_channel:
                try:
                    from app.cadence.orchestrator import (
                        find_active_sessions_by_identity,
                        handle_cadence_dm,
                    )

                    cadence_sessions = await find_active_sessions_by_identity(
                        db,
                        provider="slack",
                        workspace_id=str(team_id or ""),
                        external_user_id=str(slack_user_id or ""),
                        limit=2,
                    )
                    cadence_count = len(cadence_sessions)
                    if cadence_count > 1:
                        print(
                            "cadence_route_conflict_multi_active_sessions: "
                            f"provider=slack workspace_id={team_id} external_user_id={slack_user_id} "
                            f"count={cadence_count}"
                        )
                        conflict_client = await _get_workspace_slack_client(
                            db=db,
                            team_id=team_id,
                        )
                        if conflict_client:
                            try:
                                await conflict_client.chat_postMessage(
                                    channel=channel,
                                    text=(
                                        "I found more than one active cadence session for your DM identity. "
                                        "Please wait for a minute and try again, or ask the project owner to rerun cadence."
                                    ),
                                    thread_ts=thread_ts,
                                )
                            except Exception as exc:
                                print(f"Cadence conflict guidance send failed: {exc}")
                        return JSONResponse({"ok": True})

                    if cadence_count == 1:
                        cadence_session = cadence_sessions[0]
                        cadence_project_id = str(cadence_session.get("project_id") or "").strip() or None
                        cadence_slack_client = await _get_workspace_slack_client(
                            db=db,
                            team_id=team_id,
                            preferred_project_id=cadence_project_id,
                        )
                        if cadence_slack_client:
                            cadence_session_id = str(cadence_session.get("session_id") or "").strip()
                            print(
                                "cadence_route_hit_identity: "
                                f"session={cadence_session_id} provider=slack "
                                f"workspace_id={team_id} external_user_id={slack_user_id} "
                                f"project={cadence_project_id or 'n/a'} channel={channel}"
                            )
                            if should_route_to_queue(cadence_session_id):
                                enqueue_result = dramatiq_enqueue(
                                    queue_name=QUEUE_CADENCE_DM,
                                    actor_name="handle_cadence_dm",
                                    kwargs={
                                        "session_id": cadence_session_id,
                                        "project_id": str(cadence_project_id or "").strip(),
                                        "workspace_id": str(team_id or "").strip(),
                                        "channel": str(channel or "").strip(),
                                        "text": str(text or ""),
                                        "actor_slack_user_id": str(slack_user_id or "").strip(),
                                    },
                                )
                                if not bool(enqueue_result.get("accepted")):
                                    print(
                                        "cadence_route_enqueue_failed: "
                                        f"session={cadence_session_id} "
                                        f"reason={enqueue_result.get('reason')} "
                                        f"runtime={enqueue_result.get('runtime')} "
                                        f"job_id={enqueue_result.get('job_id')}"
                                    )
                                    try:
                                        await cadence_slack_client.chat_postMessage(
                                            channel=channel,
                                            text="I couldn't process this right now. Please try again in a moment.",
                                            thread_ts=thread_ts,
                                        )
                                    except Exception as exc:
                                        print(f"Cadence enqueue failure fallback send failed: {exc}")
                            else:
                                asyncio.create_task(
                                    handle_cadence_dm(
                                        db,
                                        session=cadence_session,
                                        text=text or "",
                                        slack_client=cadence_slack_client,
                                        channel=channel,
                                        actor_slack_user_id=str(slack_user_id or "").strip() or None,
                                    )
                                )
                            return JSONResponse({"ok": True})
                        print(
                            "cadence_route_miss_client_unavailable: "
                            f"session={cadence_session.get('session_id')} provider=slack "
                            f"workspace_id={team_id} external_user_id={slack_user_id}"
                        )
                        # Hard lock: active Cadence session exists but Slack client is
                        # unavailable. Do NOT fall through to Cortex — return early.
                        return JSONResponse({"ok": True})
                    else:
                        print(
                            "cadence_route_miss_no_active_session: "
                            f"provider=slack workspace_id={team_id} external_user_id={slack_user_id}"
                        )
                        try:
                            from app.sessions.repository import SessionRepository
                            from app.cadence.interaction_handler import send_cadence_expiry_notice

                            timeout_session = await SessionRepository(db=db).find_latest_timeout_session_by_identity(
                                entity_type="cadence_session",
                                source="cadence",
                                provider="slack",
                                workspace_id=str(team_id or ""),
                                external_user_id=str(slack_user_id or ""),
                                lookback_hours=48,
                            )
                            if timeout_session:
                                notified = await send_cadence_expiry_notice(
                                    db,
                                    session=timeout_session,
                                )
                                if notified:
                                    return JSONResponse({"ok": True})
                        except Exception as expiry_notice_exc:
                            print(f"Cadence expiry notice fallback error: {expiry_notice_exc}")
                except Exception as cadence_route_exc:
                    # Hard lock: any exception in Cadence routing must NOT fall through to Cortex.
                    # If a Cadence session is active for this user, the message belongs to Cadence.
                    # Swallow the error and return early — Cortex is never reached.
                    print(f"Cadence identity routing error (hard stop, not falling through): {cadence_route_exc}")
                    return JSONResponse({"ok": True})

            if (
                event_type == "message"
                and is_dm_channel
            ):
                pending_pre_resolution = await route_pending_dm_interactions(
                    db,
                    team_id=str(team_id or ""),
                    channel=str(channel or ""),
                    slack_user_id=str(slack_user_id or ""),
                    text=str(text or ""),
                    thread_ts=thread_ts,
                    get_workspace_slack_client=_get_workspace_slack_client,
                    project_resolution=None,
                    project_slack_client=None,
                    enable_team_poll=True,
                    enable_work_item_parent=True,
                    enable_provider_type=False,
                    enable_action_item=False,
                )
                if bool((pending_pre_resolution or {}).get("handled")):
                    return JSONResponse({"ok": True})

                if _is_exact_switch_project_command(str(text or "")):
                    try:
                        blocked_by_pending = await _has_non_terminal_pending_clarification_for_identity(
                            team_id=str(team_id or ""),
                            slack_user_id=str(slack_user_id or ""),
                        )
                    except Exception as pending_check_exc:
                        print(
                            "switch_project_pending_check_failed: "
                            f"team_id={team_id} user={slack_user_id} error={pending_check_exc}"
                        )
                        blocked_by_pending = False

                    if blocked_by_pending:
                        sent = await _send_slack_dm_text_with_workspace_fallback(
                            db=db,
                            team_id=team_id,
                            channel=channel,
                            text=(
                                "You have a pending clarification in progress. "
                                "Please finish or cancel it before switching project."
                            ),
                            thread_ts=thread_ts,
                            reason_label="switch_project_blocked_pending_clarification",
                        )
                        if not sent:
                            print(
                                "switch_project_pending_block_delivery_failed: "
                                f"team_id={team_id} channel={channel} user={slack_user_id}"
                            )
                        return JSONResponse({"ok": True})

                    button_sent = await _send_switch_project_modal_button_prompt(
                        db=db,
                        team_id=str(team_id or ""),
                        channel=str(channel or ""),
                        thread_ts=thread_ts,
                    )
                    if not button_sent:
                        sent = await _send_slack_dm_text_with_workspace_fallback(
                            db=db,
                            team_id=team_id,
                            channel=channel,
                            text=(
                                "Project switcher is ready. Run `/promarshal switch` "
                                "or `/promarshal project switch` in this DM."
                            ),
                            thread_ts=thread_ts,
                            reason_label="switch_project_modal_prompt_fallback",
                        )
                        if not sent:
                            print(
                                "switch_project_modal_prompt_fallback_delivery_failed: "
                                f"team_id={team_id} channel={channel} user={slack_user_id}"
                            )
                    return JSONResponse({"ok": True})

            resolution = await resolve_slack_project(
                db=db,
                team_id=team_id,
                slack_user_id=slack_user_id,
                channel_id=channel,
            )
            if not resolution.ok or not resolution.project:
                print(
                    f"Slack event unresolved: type={event_type} reason={resolution.reason} "
                    f"team_id={team_id} channel={channel}"
                )
                if (
                    event_type == "message"
                    and (channel or "").startswith("D")
                    and resolution.reason == "ambiguous_project"
                ):
                    candidate_projects = [
                        item for item in (resolution.candidate_projects or []) if isinstance(item, dict)
                    ]
                    if not candidate_projects and resolution.candidates:
                        candidate_projects = [
                            {
                                "project_id": str(project_id),
                                "project_name": f"Project {idx}",
                            }
                            for idx, project_id in enumerate(resolution.candidates, start=1)
                            if str(project_id).strip()
                        ]

                    prompt_text = _build_ambiguous_project_message(
                        candidate_projects=candidate_projects,
                    )
                    sent = await _send_slack_dm_text_with_workspace_fallback(
                        db=db,
                        team_id=team_id,
                        channel=channel,
                        text=prompt_text,
                        thread_ts=thread_ts,
                        reason_label="ambiguous_project_prompt",
                        fallback_access_token=resolution.slack_access_token,
                        candidate_projects=candidate_projects,
                    )
                    if not sent:
                        print(
                            "Slack ambiguous-project prompt delivery exhausted: "
                            f"team_id={team_id} channel={channel}"
                        )
                elif event_type == "message" and (channel or "").startswith("D"):
                    denial_text = _build_project_resolution_failure_message(resolution.reason)
                    sent = await _send_slack_dm_text_with_workspace_fallback(
                        db=db,
                        team_id=team_id,
                        channel=channel,
                        text=denial_text,
                        thread_ts=thread_ts,
                        reason_label=f"project_resolution_{resolution.reason or 'unknown'}",
                        fallback_access_token=resolution.slack_access_token,
                        candidate_projects=[
                            item for item in (resolution.candidate_projects or []) if isinstance(item, dict)
                        ],
                    )
                    if not sent:
                        print(
                            "Slack project-resolution denial delivery exhausted: "
                            f"team_id={team_id} channel={channel} reason={resolution.reason}"
                        )
                return JSONResponse({"ok": True})

            slack_integration = resolution.project.get("integrations", {}).get("slack", {})
            encrypted_token = slack_integration.get("access_token")
            slack_client = None
            if encrypted_token:
                try:
                    slack_client = slack_bot_service.get_slack_client(encrypted_token)
                except Exception as e:
                    print(f"Failed to create Slack client for Cortex event: {str(e)}")

            allow_channel_thread_followup = False
            if event_type == "message" and not is_dm_channel:
                thread_anchor = str(thread_ts or event.get("ts") or "").strip()
                allow_channel_thread_followup = await _has_active_cortex_thread_session(
                    project_id=str(resolution.project.get("project_id") or ""),
                    slack_user_id=str(slack_user_id or ""),
                    thread_anchor=thread_anchor,
                )
                if not allow_channel_thread_followup:
                    print(
                        "Slack message event ignored: no active Cortex thread session "
                        f"channel={channel} user={slack_user_id} thread_ts={thread_ts}"
                    )
                    return JSONResponse({"ok": True})

            actor_email = (
                str(resolution.user_email or "").strip().lower()
                or str((resolution.member or {}).get("email") or "").strip().lower()
                or None
            )
            member_role = str((resolution.member or {}).get("role") or "").strip().lower() or None
            bot_user_id = str(slack_integration.get("bot_user_id") or "").strip().upper() or None
            mentioned_user_ids = _extract_slack_user_mention_ids(text or "")
            mention_member_candidates = _resolve_assignee_hint_candidates_from_mentions(
                project=resolution.project,
                mentioned_user_ids=mentioned_user_ids,
                actor_email=actor_email,
                actor_slack_user_id=slack_user_id,
                bot_user_id=bot_user_id,
            )
            effective_text = _normalize_slack_event_text_for_cortex(
                text=text or "",
                project=resolution.project,
                bot_user_id=bot_user_id,
            )
            # Intent classification should use the current user request text only,
            # not expanded thread context, to avoid greeting/action drift.
            intent_text = str(effective_text or "")
            message_context_source = "message_text"
            pending_resume_target_refs: List[str] = []
            normalized_thread_root: Optional[str] = None

            if (
                event_type == "message"
                and is_dm_channel
            ):
                pending_post_resolution = await route_pending_dm_interactions(
                    db,
                    team_id=str(team_id or ""),
                    channel=str(channel or ""),
                    slack_user_id=str(slack_user_id or ""),
                    text=intent_text,
                    thread_ts=thread_ts,
                    get_workspace_slack_client=_get_workspace_slack_client,
                    project_resolution=resolution,
                    project_slack_client=slack_client,
                    enable_team_poll=False,
                    enable_work_item_parent=True,
                    enable_provider_type=True,
                    enable_action_item=True,
                )
                if bool((pending_post_resolution or {}).get("handled")):
                    return JSONResponse({"ok": True})
                if bool((pending_post_resolution or {}).get("resume_to_cortex")):
                    resume_text = str((pending_post_resolution or {}).get("resume_message_text") or "").strip()
                    pending_resume_target_refs = [
                        str(item or "").strip().upper()
                        for item in (pending_post_resolution or {}).get("resume_target_refs") or []
                        if str(item or "").strip()
                    ]
                    if resume_text:
                        effective_text = resume_text
                        intent_text = resume_text
                        message_context_source = "pending_resume_work_item_parent"
                        print(
                            "pending_work_item_parent_resume_to_cortex: "
                            f"project_id={resolution.project.get('project_id')} "
                            f"user_id={slack_user_id}"
                        )

            if event_type == "app_mention" and thread_ts and slack_client:
                thread_root_text = await _fetch_thread_root_text_best_effort(
                    slack_client=slack_client,
                    channel=channel,
                    thread_ts=thread_ts,
                )
                if thread_root_text:
                    normalized_thread_root = _normalize_slack_event_text_for_cortex(
                        text=thread_root_text,
                        project=resolution.project,
                        bot_user_id=bot_user_id,
                    )

            if (event_type == "app_mention" or allow_channel_thread_followup) and thread_ts and slack_client:
                thread_context = await _fetch_thread_context_window_best_effort(
                    slack_client=slack_client,
                    channel=channel,
                    thread_ts=thread_ts,
                    project=resolution.project,
                    bot_user_id=bot_user_id,
                    current_event_ts=event.get("ts"),
                    max_messages=5,
                )
                if event_type == "app_mention":
                    parts: List[str] = []
                    if normalized_thread_root:
                        parts.append(f"Thread root: {normalized_thread_root}")
                    if thread_context:
                        parts.append(thread_context)
                    parts.append(f"Current request: {effective_text}")
                    effective_text = "\n\n".join([part for part in parts if str(part or "").strip()]).strip()
                    if normalized_thread_root and thread_context:
                        message_context_source = "thread_root_and_window"
                    elif normalized_thread_root:
                        message_context_source = "thread_root"
                    elif thread_context:
                        message_context_source = "thread_window"
                elif thread_context:
                    effective_text = f"{thread_context}\n\nCurrent request: {effective_text}".strip()
                    if message_context_source == "message_text":
                        message_context_source = "thread_window"

            envelope_raw_event = dict(event)
            if allow_channel_thread_followup:
                envelope_raw_event["_cortex_allow_channel_thread_followup"] = True

            envelope = SlackEventEnvelope(
                team_id=team_id or "",
                channel_id=channel or "",
                user_id=slack_user_id or "",
                text=effective_text,
                event_ts=event.get("ts") or "",
                thread_ts=thread_ts,
                raw_event=envelope_raw_event,
            )
            ingress_classification = classify_event_for_handoff(envelope)
            message_intent = classify_message_intent(intent_text)
            ack_gate_key = _build_slack_response_gate_key(
                kind="ack",
                team_id=team_id,
                channel_id=channel,
                user_id=slack_user_id,
                thread_ts=thread_ts,
                event_ts=event.get("ts"),
                normalized_text=intent_text,
            )

            if message_intent == "greeting_only":
                print(
                    f"Slack greeting-only message handled without Cortex: "
                    f"event_type={event_type} team_id={team_id} channel={channel}"
                )
                if slack_client:
                    if _slack_response_slot_exists(ack_gate_key):
                        print(
                            "Slack greeting suppressed because stage3 ack already sent recently: "
                            f"team_id={team_id} channel={channel} user={slack_user_id}"
                        )
                    else:
                        greeting_gate_key = _build_slack_response_gate_key(
                            kind="greeting",
                            team_id=team_id,
                            channel_id=channel,
                            user_id=slack_user_id,
                            thread_ts=thread_ts,
                            event_ts=event.get("ts"),
                            normalized_text=intent_text,
                        )
                        if _try_acquire_slack_response_slot(greeting_gate_key):
                            asyncio.create_task(
                                _send_greeting_response_best_effort(
                                    slack_client=slack_client,
                                    channel_id=channel,
                                    event_type=event_type,
                                    thread_ts=thread_ts,
                                    member_name=str((resolution.member or {}).get("name") or ""),
                                    member_role=str((resolution.member or {}).get("role") or ""),
                                )
                            )
                        else:
                            print(
                                "Slack duplicate greeting response suppressed: "
                                f"team_id={team_id} channel={channel} user={slack_user_id}"
                            )
                return JSONResponse({"ok": True})

            if (
                event_type == "message"
                and is_dm_channel
                and bool(getattr(settings, "team_poll_enabled", True))
                and not bool(getattr(settings, "team_poll_owner_free_text_via_cortex", True))
            ):
                owner_role = str((resolution.member or {}).get("role") or "").strip().lower()
                if owner_role == "owner":
                    team_poll_route = await handle_owner_poll_ingress(
                        db,
                        intent_text=intent_text,
                        project=resolution.project or {},
                        owner_member=resolution.member or {},
                        workspace_id=str(team_id or ""),
                    )
                    if bool(team_poll_route.get("handled")):
                        if slack_client:
                            route_kind = str(team_poll_route.get("kind") or "").strip().lower()
                            route_payload = team_poll_route.get("payload") or {}
                            action = "owner_poll_trigger_error"
                            fallback_text = "I couldn't process that team poll request right now."
                            if route_kind == "clarification":
                                action = "owner_clarification_required"
                                fallback_text = (
                                    "I couldn't confidently infer the team poll action. "
                                    "Use `/promarshal team_poll create <question>`, "
                                    "`/promarshal team_poll status [poll_id]`, or "
                                    "`/promarshal team_poll close [poll_id]`."
                                )
                            elif route_kind == "create":
                                if bool(route_payload.get("ok")):
                                    action = "owner_poll_created"
                                    fallback_text = (
                                        "Team poll started.\n"
                                        f"- Poll ID: {route_payload.get('poll_id')}\n"
                                        f"- Question: {route_payload.get('question_text')}"
                                    )
                                else:
                                    action = "owner_poll_create_failed"
                                    fallback_text = (
                                        "I couldn't start a team poll from that message. "
                                        "Use `/promarshal team_poll create <question>`."
                                    )
                            elif route_kind == "status":
                                if bool(route_payload.get("ok")):
                                    action = "owner_poll_status"
                                    fallback_text = str((route_payload.get("status") or {}).get("text") or "")
                                elif str(route_payload.get("reason") or "") == "ambiguous_poll":
                                    action = "owner_poll_status_ambiguous"
                                    candidates = [str(item) for item in (route_payload.get("candidates") or []) if str(item).strip()]
                                    fallback_text = (
                                        "Multiple active polls matched. Please specify poll ID.\n- "
                                        + "\n- ".join(candidates)
                                    ) if candidates else "Multiple active polls matched. Please specify poll ID."
                                else:
                                    action = "owner_poll_status_not_found"
                                    fallback_text = "No active team poll found for status."
                            elif route_kind == "close":
                                if bool(route_payload.get("ok")):
                                    action = "owner_poll_closed"
                                    fallback_text = f"Team poll closed. Poll ID: {route_payload.get('poll_id')}"
                                elif str(route_payload.get("reason") or "") == "ambiguous_poll":
                                    action = "owner_poll_close_ambiguous"
                                    candidates = [str(item) for item in (route_payload.get("candidates") or []) if str(item).strip()]
                                    fallback_text = (
                                        "Multiple active polls matched. Please specify poll ID to close.\n- "
                                        + "\n- ".join(candidates)
                                    ) if candidates else "Multiple active polls matched. Please specify poll ID to close."
                                else:
                                    action = "owner_poll_close_not_found"
                                    fallback_text = "No active team poll found to close."

                            try:
                                response_text = await compose_team_poll_response(
                                    action=action,
                                    user_message=intent_text,
                                    outcome_payload=route_payload,
                                    fallback_text=fallback_text,
                                )
                                await slack_client.chat_postMessage(
                                    channel=channel,
                                    text=response_text,
                                    thread_ts=thread_ts,
                                )
                            except Exception as notify_exc:
                                print(f"Team poll owner response send failed: {notify_exc}")

                        print(
                            "team_poll_owner_route_terminal: "
                            f"kind={team_poll_route.get('kind')} "
                            f"team_id={team_id} channel={channel} user={slack_user_id}"
                        )
                        return JSONResponse({"ok": True})

            promarshal_global_scope_policy_enabled = bool(
                getattr(settings, "promarshal_global_scope_policy_enabled", True)
            )
            stage1_scope_gate = classify_project_scope_gate(intent_text)

            clarification_decision = _should_request_context_clarification(
                intent_text=intent_text,
                stage1_scope_gate=stage1_scope_gate,
            )
            if bool(clarification_decision.get("should_clarify")):
                clarification_reason = str(clarification_decision.get("reason_code") or "insufficient_context")
                clarification_text = _build_context_clarification_response(
                    reason_code=clarification_reason,
                    intent_text=intent_text,
                )
                print(
                    "ProMarshal ingress clarification gate hit: "
                    f"reason={clarification_reason} event_type={event_type} "
                    f"team_id={team_id} channel={channel}"
                )
                if slack_client:
                    clarify_gate_key = _build_slack_response_gate_key(
                        kind="clarify",
                        team_id=team_id,
                        channel_id=channel,
                        user_id=slack_user_id,
                        thread_ts=thread_ts,
                        event_ts=event.get("ts"),
                        normalized_text=intent_text,
                    )
                    if _try_acquire_slack_response_slot(clarify_gate_key):
                        asyncio.create_task(
                            _send_context_clarification_best_effort(
                                slack_client=slack_client,
                                channel_id=channel,
                                event_type=event_type,
                                thread_ts=thread_ts,
                                clarification_text=clarification_text,
                            )
                        )
                    else:
                        print(
                            "Slack duplicate clarification response suppressed: "
                            f"team_id={team_id} channel={channel} user={slack_user_id}"
                        )
                return JSONResponse({"ok": True})

            if (
                promarshal_global_scope_policy_enabled
                and stage1_scope_gate.classification == SCOPE_GATE_OUT_OF_SCOPE
            ):
                print(
                    "ProMarshal scope policy blocked message at ingress: "
                    f"reason={stage1_scope_gate.reason_code} event_type={event_type} "
                    f"team_id={team_id} channel={channel}"
                )
                if slack_client:
                    scope_gate_key = _build_slack_response_gate_key(
                        kind="scope",
                        team_id=team_id,
                        channel_id=channel,
                        user_id=slack_user_id,
                        thread_ts=thread_ts,
                        event_ts=event.get("ts"),
                        normalized_text=intent_text,
                    )
                    if _try_acquire_slack_response_slot(scope_gate_key):
                        asyncio.create_task(
                            _send_scope_refusal_best_effort(
                                slack_client=slack_client,
                                channel_id=channel,
                                event_type=event_type,
                                thread_ts=thread_ts,
                            )
                        )
                    else:
                        print(
                            "Slack duplicate scope refusal suppressed: "
                            f"team_id={team_id} channel={channel} user={slack_user_id}"
                        )
                return JSONResponse({"ok": True})

            if ingress_classification.accepted and slack_client:
                if _try_acquire_slack_response_slot(ack_gate_key):
                    asyncio.create_task(
                        send_stage3_ack_best_effort(
                            slack_client,
                            channel_id=channel,
                            thread_ts=thread_ts,
                            interaction_type=ingress_classification.interaction_type,
                        )
                    )
                else:
                    print(
                        "Slack duplicate stage3 ack suppressed: "
                        f"team_id={team_id} channel={channel} user={slack_user_id}"
                    )

            handoff = await handoff_slack_event_to_cortex(
                envelope,
                project_id=resolution.project.get("project_id", ""),
                identity_context={
                    "actor_email": actor_email,
                    "member_role": member_role,
                    "identity_resolution_path": resolution.identity_resolution_path,
                    "message_context_source": message_context_source,
                    "mentioned_user_ids": mentioned_user_ids,
                    "member_candidates": mention_member_candidates,
                    "scope_gate_classification": stage1_scope_gate.classification,
                    "scope_gate_reason_code": stage1_scope_gate.reason_code,
                    "scope_gate_signals": stage1_scope_gate.signals,
                    "resume_target_refs": pending_resume_target_refs,
                },
            )
            print(
                f"Cortex handoff result: accepted={handoff.accepted} "
                f"reason={handoff.reason or 'n/a'} event_type={event_type} "
                f"team_id={team_id} channel={channel} "
                f"identity_path={resolution.identity_resolution_path or 'n/a'}"
            )
            if handoff.accepted:
                if handoff.response_text and slack_client:
                    try:
                        response_text = str(handoff.response_text or "").strip()
                        footer = None
                        if _should_append_cortex_context_footer(
                            is_dm_channel=is_dm_channel,
                            response_text=response_text,
                            message_intent=message_intent,
                            used_tool_call=bool(getattr(handoff, "used_tool_call", False)),
                        ):
                            try:
                                footer = await build_project_context_footer(
                                    db=db,
                                    team_id=str(team_id or ""),
                                    slack_user_id=str(slack_user_id or ""),
                                    project=resolution.project or {},
                                )
                            except Exception as footer_exc:
                                print(
                                    "cortex_context_footer_build_failed: "
                                    f"team_id={team_id} channel={channel} user={slack_user_id} error={footer_exc}"
                                )
                        await slack_client.chat_postMessage(
                            channel=channel,
                            text=append_project_context_footer(response_text, footer),
                            thread_ts=thread_ts
                        )
                    except Exception as e:
                        print(f"Error sending Cortex inline response: {str(e)}")
                else:
                    print("Cortex handoff accepted without inline response text")
                return JSONResponse({"ok": True})
            else:
                print(f"Cortex handoff not accepted for {event_type}; reason={handoff.reason}")

            if (
                _should_send_cortex_failure_reply(handoff.reason)
                and event_type in {"app_mention", "message"}
                and slack_client
            ):
                failure_gate_key = _build_slack_response_gate_key(
                    kind="failure",
                    team_id=team_id,
                    channel_id=channel,
                    user_id=slack_user_id,
                    thread_ts=thread_ts,
                    event_ts=event.get("ts"),
                    normalized_text=intent_text,
                )
                if _try_acquire_slack_response_slot(failure_gate_key):
                    asyncio.create_task(
                        _send_operational_fallback_best_effort(
                            slack_client=slack_client,
                            channel_id=channel,
                            event_type=event_type,
                            thread_ts=thread_ts,
                            reason=str(handoff.reason or ""),
                        )
                    )
                else:
                    print(
                        "Slack duplicate operational fallback suppressed: "
                        f"team_id={team_id} channel={channel} user={slack_user_id}"
                    )
                return JSONResponse({"ok": True})

    return JSONResponse({"ok": True})


@router.post("/slack/sync-members/{project_id}")
async def sync_slack_members(
    project_id: str,
    current_user: dict = Depends(get_current_user),
    overwrite: bool = Query(False, description="Overwrite conflicting existing Slack ID mappings"),
):
    """Owner endpoint: sync Slack user IDs for all active members in a project."""
    try:
        db = get_database()
        user_id = str(current_user.get("user_id") or "")
        project = await db.projects.find_one({"_id": ObjectId(project_id), "user_id": user_id})
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found or access denied",
            )

        result = await sync_project_slack_member_ids_detailed(
            db=db,
            project_id=project_id,
            overwrite=overwrite,
        )
        if result.get("status") != "ok":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Slack member sync failed: {result.get('status')}",
            )
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to sync Slack members: {str(e)}",
        )


@router.post("/slack/member-link/{project_id}")
async def link_slack_member(
    project_id: str,
    current_user: dict = Depends(get_current_user),
    member_email: str = Query(..., description="Project member email"),
    slack_user_id: str = Query(..., description="Slack user ID (for example U123...)"),
    overwrite: bool = Query(False, description="Allow overwrite when an existing mapping differs"),
    validate_with_slack: bool = Query(
        True,
        description="Validate user ID against Slack users.info using workspace token",
    ),
):
    """Owner endpoint: manually link one project member to a Slack user ID."""
    try:
        db = get_database()
        user_id = str(current_user.get("user_id") or "")
        project = await db.projects.find_one({"_id": ObjectId(project_id), "user_id": user_id})
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found or access denied",
            )

        result = await link_member_slack_id_detailed(
            db=db,
            project_id=project_id,
            member_email=member_email,
            slack_user_id=slack_user_id,
            overwrite=overwrite,
            validate_with_slack=validate_with_slack,
        )
        status_code = status.HTTP_200_OK
        if result.get("status") == "conflict":
            status_code = status.HTTP_409_CONFLICT
        if result.get("status") in {"project_not_found", "member_not_found"}:
            status_code = status.HTTP_404_NOT_FOUND
        if result.get("status") in {"invalid_member_email", "invalid_slack_user_id", "validation_email_mismatch"}:
            status_code = status.HTTP_400_BAD_REQUEST
        return JSONResponse(status_code=status_code, content=result)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to link Slack member identity: {str(e)}",
        )


@router.delete("/slack/disconnect/{project_id}")
async def disconnect_slack(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Disconnect Slack integration from a project"""
    try:
        db = get_database()
        user_id = str(current_user.get("user_id") or "")
        existing_project = await db.projects.find_one(
            {"_id": ObjectId(project_id), "user_id": user_id},
            {"project_id": 1, "integrations.slack.team_id": 1},
        )
        if not existing_project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found or access denied",
            )

        custom_project_id = str(existing_project.get("project_id") or "").strip()
        slack_team_id = str(
            ((existing_project.get("integrations") or {}).get("slack") or {}).get("team_id") or ""
        ).strip()
        
        # Remove from new structure (integrations.slack) and old structure (tools_summary.slack)
        result = await db.projects.update_one(
            {"_id": ObjectId(project_id), "user_id": user_id},
            {
                "$unset": {
                    "integrations.slack": "",
                    "tools_summary.slack": "",
                    "tools.slack": "",
                    "members.$[].integration_ids.slack_user_id": "",
                    "members.$[].integration_ids.slack_welcome_sent_at": "",
                    "members.$[].integration_ids.slack_welcome_sent_project_id": "",
                    "members.$[].integration_ids.slack_welcome_sent_team_id": "",
                    "members.$[].integration_ids.slack_welcome_sent_channel": "",
                },
                "$set": {"updated_at": datetime.utcnow()}
            }
        )

        if result.modified_count == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found or already disconnected"
            )
        await invalidate_pm_board_summary_cache_by_object_id(db, project_id)

        if custom_project_id:
            try:
                deleted_rows = await clear_active_project_rows(
                    db=db,
                    provider="slack",
                    workspace_id=slack_team_id or None,
                    project_id=custom_project_id,
                )
                print(
                    "channel_index_cleanup_on_disconnect: "
                    f"project_id={custom_project_id} workspace_id={slack_team_id or 'n/a'} "
                    f"deleted_count={deleted_rows}"
                )
            except Exception as cleanup_exc:
                print(
                    "channel_index_cleanup_on_disconnect_failed: "
                    f"project_id={custom_project_id} workspace_id={slack_team_id or 'n/a'} "
                    f"error={cleanup_exc}"
                )

        return {"message": "Slack disconnected successfully"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to disconnect Slack: {str(e)}"
        )


@router.get("/slack/check-channel/{project_id}")
async def check_channel_conflict(
    project_id: str,
    channel_id: str = Query(...),
    current_user: dict = Depends(get_current_user),
):
    """
    Check if a Slack channel is already associated with another project of the same user.
    Returns conflict info so the frontend can warn before the user saves.
    """
    try:
        db = get_database()
        user_id = str(current_user.get("user_id") or "")

        project = await db.projects.find_one(
            {"_id": ObjectId(project_id), "user_id": user_id}
        )
        if not project:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

        team_id = project.get("integrations", {}).get("slack", {}).get("team_id")
        if not team_id:
            return {"conflict": False}

        conflicting = await db.projects.find_one(
            {
                "_id": {"$ne": ObjectId(project_id)},
                "user_id": user_id,
                "integrations.slack.team_id": team_id,
                "integrations.slack.status": "connected",
                "integrations.slack.selected_channels.channel_id": channel_id,
            },
            {"project_name": 1, "project_id": 1}
        )

        if conflicting:
            return {
                "conflict": True,
                "project_name": conflicting.get("project_name") or conflicting.get("project_id")
            }

        return {"conflict": False}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to check channel: {str(e)}"
        )


@router.get("/slack/channels/{project_id}")
async def get_slack_channels(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Fetch available channels from Slack workspace for a project.
    Returns list of channels with their IDs, names, and bot membership status.
    """
    try:
        db = get_database()
        user_id = str(current_user.get("user_id") or "")

        # Get project and verify access
        project = await db.projects.find_one({
            "_id": ObjectId(project_id),
            "user_id": user_id
        })

        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found or access denied"
            )

        # Check if Slack is connected
        slack_integration = project.get("integrations", {}).get("slack", {})
        if slack_integration.get("status") != "connected":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Slack not connected for this project"
            )

        team_id = slack_integration.get("team_id")
        if not team_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Slack team ID is missing for this project"
            )

        # Get access token and fetch channels
        access_token = encryption_service.decrypt(slack_integration["access_token"])
        channels = await SlackMessageReader.get_workspace_channels(access_token)

        # Format response with relevant info
        channels_data = [
            {
                "id": ch.get("id"),
                "name": ch.get("name"),
                "is_private": ch.get("is_private", False),
                "is_member": ch.get("is_member", False),
                "num_members": ch.get("num_members", 0)
            }
            for ch in channels
        ]

        return {"channels": channels_data}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch Slack channels: {str(e)}"
        )


@router.post("/slack/channels/{project_id}")
async def save_selected_channels(
    project_id: str,
    current_user: dict = Depends(get_current_user),
    channel_ids: list[str] = Query(default=[])
):
    """
    Save selected channels for monitoring.
    Updates the project's Slack integration with selected channels.
    If channel_ids is empty, marks channels_configured as false (skip scenario).
    """
    try:
        db = get_database()
        user_id = str(current_user.get("user_id") or "")

        # Get project and verify access
        project = await db.projects.find_one({
            "_id": ObjectId(project_id),
            "user_id": user_id
        })

        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found or access denied"
            )

        # Check if Slack is connected
        slack_integration = project.get("integrations", {}).get("slack", {})
        if slack_integration.get("status") != "connected":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Slack not connected for this project"
            )

        team_id = slack_integration.get("team_id")
        if not team_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Slack team ID is missing for this project"
            )

        # Handle skip scenario (empty channel_ids)
        if not channel_ids:
            # User skipped channel selection
            result = await db.projects.update_one(
                {"_id": ObjectId(project_id), "user_id": user_id},
                {
                    "$set": {
                        "integrations.slack.selected_channels": [],
                        "integrations.slack.channels_configured": False,
                        "updated_at": datetime.utcnow()
                    }
                }
            )

            if result.modified_count == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to update channel selection"
                )

            return {
                "message": "Channel selection skipped",
                "selected_channels": []
            }

        # Fetch channels to get channel names
        access_token = encryption_service.decrypt(slack_integration["access_token"])
        all_channels = await SlackMessageReader.get_workspace_channels(access_token)

        # Build selected_channels array with names
        selected_channels = []
        for channel_id in channel_ids:
            matching_channel = next((ch for ch in all_channels if ch.get("id") == channel_id), None)
            if matching_channel:
                selected_channels.append({
                    "channel_id": channel_id,
                    "channel_name": matching_channel.get("name"),
                    "selected_at": datetime.utcnow()
                })

        selected_channel_ids = [item["channel_id"] for item in selected_channels]
        if selected_channel_ids:
            conflicting_projects = await db.projects.find(
                {
                    "_id": {"$ne": ObjectId(project_id)},
                    "integrations.slack.team_id": team_id,
                    "integrations.slack.status": "connected",
                    "integrations.slack.selected_channels.channel_id": {"$in": selected_channel_ids},
                },
                {
                    "project_id": 1,
                    "project_name": 1,
                    "integrations.slack.selected_channels": 1,
                },
            ).to_list(length=100)

            conflict_messages = []
            for other in conflicting_projects:
                existing_ids = {
                    str(ch.get("channel_id"))
                    for ch in other.get("integrations", {}).get("slack", {}).get("selected_channels", [])
                    if isinstance(ch, dict) and ch.get("channel_id")
                }
                overlap = sorted(existing_ids.intersection(set(selected_channel_ids)))
                if overlap:
                    conflict_messages.append(
                        f"{other.get('project_name') or other.get('project_id')}: {', '.join(overlap)}"
                    )

            if conflict_messages:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "One or more channels are already mapped to another project in this Slack workspace. "
                        + "Conflicts: "
                        + " | ".join(conflict_messages)
                    ),
                )

        # Update project with selected channels
        result = await db.projects.update_one(
            {"_id": ObjectId(project_id), "user_id": user_id},
            {
                "$set": {
                    "integrations.slack.selected_channels": selected_channels,
                    "integrations.slack.channels_configured": True,
                    "updated_at": datetime.utcnow()
                }
            }
        )

        if result.modified_count == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to update channel selection"
            )

        return {
            "message": "Channels saved successfully",
            "selected_channels": selected_channels
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save selected channels: {str(e)}"
        )


@router.get("/slack/selected-channels/{project_id}")
async def get_selected_channels(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Get currently selected channels for a project.
    Returns the list of channels that are configured for monitoring.
    """
    try:
        db = get_database()
        user_id = str(current_user.get("user_id") or "")

        # Get project and verify access
        project = await db.projects.find_one({
            "_id": ObjectId(project_id),
            "user_id": user_id
        })

        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found or access denied"
            )

        # Get selected channels from Slack integration
        slack_integration = project.get("integrations", {}).get("slack", {})
        selected_channels = slack_integration.get("selected_channels", [])
        channels_configured = slack_integration.get("channels_configured", False)

        return {
            "selected_channels": selected_channels,
            "channels_configured": channels_configured
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get selected channels: {str(e)}"
        )


# ==================== JIRA INTEGRATION ====================

@router.get("/jira/connect")
async def connect_jira(
    project_id: str = Query(..., description="Project ID to integrate with Jira"),
    current_user: dict = Depends(get_current_user),
    return_nav: str = Query(None, description="Navigation state to return to"),
    return_tab: str = Query(None, description="Tab state to return to")
):
    """Initiate Jira OAuth flow"""
    try:
        db = get_database()
        user_id = str(current_user.get("user_id") or "")
        project = await db.projects.find_one({
            "_id": ObjectId(project_id),
            "user_id": user_id
        })

        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found or access denied"
            )

        state_token = oauth_state_service.generate_state_token(
            project_id, user_id, return_nav, return_tab
        )
        redirect_uri = f"{settings.backend_url}/api/integrations/jira/oauth"
        auth_url = jira_oauth_service.get_authorization_url(state_token, redirect_uri)

        return RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to initiate Jira OAuth: {str(e)}"
        )


@router.get("/jira/oauth")
async def jira_callback(
    code: str = Query(..., description="Authorization code from Jira"),
    state: str = Query(..., description="State token for CSRF protection")
):
    """Handle Jira OAuth callback"""
    try:
        try:
            state_payload = oauth_state_service.verify_state_token(state)
            project_id = state_payload["project_id"]
            user_id = state_payload["user_id"]
            return_nav = state_payload.get("return_nav")
            return_tab = state_payload.get("return_tab")
        except ValueError:
            return RedirectResponse(
                url=f"{settings.frontend_url}/projects?error=invalid_state",
                status_code=status.HTTP_302_FOUND
            )

        redirect_uri = f"{settings.backend_url}/api/integrations/jira/oauth"

        try:
            token_response = await jira_oauth_service.exchange_code_for_token(code, redirect_uri)
        except Exception as e:
            print(f"Jira token exchange error: {str(e)}")
            return RedirectResponse(
                url=f"{settings.frontend_url}/projects?error=jira_auth_failed",
                status_code=status.HTTP_302_FOUND
            )

        access_token = token_response.get("access_token")
        refresh_token = token_response.get("refresh_token")
        expires_in = token_response.get("expires_in")

        if not access_token:
            return RedirectResponse(
                url=f"{settings.frontend_url}/projects?error=no_access_token",
                status_code=status.HTTP_302_FOUND
            )

        accessible_resources = await jira_oauth_service.get_accessible_resources(access_token)

        if not accessible_resources:
            return RedirectResponse(
                url=f"{settings.frontend_url}/projects?error=no_jira_sites",
                status_code=status.HTTP_302_FOUND
            )

        available_sites = _normalize_accessible_jira_sites(accessible_resources)
        if not available_sites:
            return RedirectResponse(
                url=f"{settings.frontend_url}/projects?error=no_jira_sites",
                status_code=status.HTTP_302_FOUND
            )

        encrypted_access_token = encryption_service.encrypt(access_token)
        encrypted_refresh_token = encryption_service.encrypt(refresh_token) if refresh_token else None

        db = get_database()
        existing_project = await db.projects.find_one(
            {"_id": ObjectId(project_id), "user_id": user_id},
            {"project_id": 1, "integrations.jira.cloud_id": 1, "integrations.jira.webhook_token": 1},
        )
        custom_project_id = str((existing_project or {}).get("project_id") or "").strip()
        if not custom_project_id:
            raise ValueError("Project custom id is required for Jira webhook registration")
        previous_cloud_id = str(
            (((existing_project or {}).get("integrations") or {}).get("jira") or {}).get("cloud_id") or ""
        ).strip()
        existing_webhook_token = str(
            (((existing_project or {}).get("integrations") or {}).get("jira") or {}).get("webhook_token") or ""
        ).strip()
        webhook_token = existing_webhook_token or _generate_jira_webhook_token()

        auto_selected_site: Optional[Dict[str, str]] = None
        if len(available_sites) == 1:
            auto_selected_site = available_sites[0]
        elif previous_cloud_id:
            auto_selected_site = next(
                (
                    site for site in available_sites
                    if str(site.get("id") or "").strip() == previous_cloud_id
                ),
                None,
            )

        jira_integration = {
            "provider": "jira",
            "category": "workitem",
            "site_name": None,
            "site_url": None,
            "cloud_id": None,
            "user_account_id": None,
            "user_display_name": None,
            "status": "pending_site_selection",
            "connected_at": datetime.utcnow(),
            "connected_by": user_id,
            "access_token": encrypted_access_token,
            "refresh_token": encrypted_refresh_token,
            "expires_at": datetime.utcnow() + timedelta(seconds=expires_in) if expires_in else None,
            "last_sync_at": None,
            "webhook_registered": False,
            "available_sites": available_sites,
            "available_projects": [],
            "default_project_key": None,
            "default_project_name": None,
            "available_boards": [],
            "default_board_id": None,
            "default_board_name": None,
            "default_board_type": None,
            "board_selection_status": "pending_site_selection",
            "sprint_policy": "auto",
            "sprint_field_id": None,
            "webhook_auth_mode": "url_token_v1",
            "webhook_token": webhook_token,
            "webhook_token_created_at": datetime.utcnow(),
        }

        if auto_selected_site:
            try:
                cloud_id = str(auto_selected_site.get("id") or "").strip()
                site_name = str(auto_selected_site.get("name") or "").strip()
                site_url = str(auto_selected_site.get("url") or "").strip()
                current_user = await jira_oauth_service.get_current_user(access_token, cloud_id)
                available_jira_projects = await _fetch_available_jira_projects_for_site(
                    access_token=access_token,
                    cloud_id=cloud_id,
                )

                webhook_registered = False

                sprint_field_id = await jira_oauth_service.detect_sprint_field_id(
                    access_token,
                    cloud_id,
                )

                jira_integration.update(
                    {
                        "site_name": site_name,
                        "site_url": site_url,
                        "cloud_id": cloud_id,
                        "user_account_id": current_user.get("accountId") if current_user else None,
                        "user_display_name": current_user.get("displayName") if current_user else None,
                        "status": "pending_project_selection",
                        "webhook_registered": webhook_registered,
                        "available_projects": available_jira_projects,
                        "board_selection_status": "pending_project_selection",
                        "sprint_field_id": sprint_field_id,
                    }
                )
                print(
                    "Jira site auto-selection applied: "
                    f"project_id={project_id} cloud_id={cloud_id} site={site_name or site_url}"
                )
            except Exception as auto_select_exc:
                print(
                    "Jira site auto-selection failed; falling back to explicit site selection: "
                    f"project_id={project_id} error={auto_select_exc}"
                )

        result = await db.projects.update_one(
            {"_id": ObjectId(project_id), "user_id": user_id},
            {"$set": {"integrations.jira": jira_integration, "updated_at": datetime.utcnow()}},
        )

        if result.modified_count == 0 and not result.upserted_id:
            return RedirectResponse(
                url=f"{settings.frontend_url}/projects?error=update_failed",
                status_code=status.HTTP_302_FOUND
            )
        await invalidate_pm_board_summary_cache_by_object_id(db, project_id)

        # Redirect to Control Panel with project selection modal
        from urllib.parse import quote
        nav_param = quote("Control Panel")
        tab_param = quote("Integrate Tools")

        # Get the MongoDB _id for the project to preserve selected project in URL
        db = get_database()
        project_doc = await db.projects.find_one({"_id": ObjectId(project_id)})
        mongo_project_id = str(project_doc["_id"]) if project_doc else project_id

        redirect_url = f"{settings.frontend_url}/projects?jira_pending_selection=true&project_id={project_id}&projectId={mongo_project_id}&nav={nav_param}&tab={tab_param}"

        return RedirectResponse(
            url=redirect_url,
            status_code=status.HTTP_302_FOUND
        )

    except Exception as e:
        print(f"Jira callback error: {str(e)}")
        return RedirectResponse(
            url=f"{settings.frontend_url}/projects?error=unexpected",
            status_code=status.HTTP_302_FOUND
        )


@router.post("/jira/select-site/{project_id}")
async def select_jira_site(
    project_id: str,
    current_user: dict = Depends(get_current_user),
    selected_cloud_id: str = Query(...),
):
    """
    Handle Jira site selection after OAuth.
    Fetches available Jira projects for the selected site and prepares
    project selection step.
    """
    try:
        db = get_database()
        user_id = str(current_user.get("user_id") or "")
        project = await db.projects.find_one(
            {"_id": ObjectId(project_id), "user_id": user_id}
        )
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found or access denied",
            )

        jira_integration = (project.get("integrations") or {}).get("jira") or {}
        integration_status = str(jira_integration.get("status") or "").strip().lower()
        if integration_status not in {"pending_site_selection", "pending_project_selection"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Jira integration is not awaiting site selection",
            )

        selected_id = str(selected_cloud_id or "").strip()
        if not selected_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="selected_cloud_id is required",
            )

        available_sites = jira_integration.get("available_sites") or []
        selected_site = next(
            (
                site for site in available_sites
                if str(site.get("id") or "").strip() == selected_id
            ),
            None,
        )
        if not selected_site:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Selected Jira site not found in accessible sites",
            )

        access_token = encryption_service.decrypt(jira_integration["access_token"])
        cloud_id = str(selected_site.get("id") or "").strip()
        site_name = str(selected_site.get("name") or "").strip()
        site_url = str(selected_site.get("url") or "").strip()
        custom_project_id = str(project.get("project_id") or "").strip()
        if not custom_project_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Project custom id is required for Jira webhook registration",
            )
        webhook_token = str(jira_integration.get("webhook_token") or "").strip() or _generate_jira_webhook_token()

        current_user = await jira_oauth_service.get_current_user(access_token, cloud_id)
        available_jira_projects = await _fetch_available_jira_projects_for_site(
            access_token=access_token,
            cloud_id=cloud_id,
        )
        if not available_jira_projects:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No Jira projects found for selected site",
            )

        webhook_registered = False

        sprint_field_id = await jira_oauth_service.detect_sprint_field_id(
            access_token,
            cloud_id,
        )
        print(f"[Jira Site Selection] Sprint field ID detected: {sprint_field_id}")
        print(
            f"Fetched {len(available_jira_projects)} Jira projects: "
            f"{[p.get('key') for p in available_jira_projects]}"
        )

        await db.projects.update_one(
            {"_id": ObjectId(project_id), "user_id": user_id},
            {
                "$set": {
                    "integrations.jira.cloud_id": cloud_id,
                    "integrations.jira.site_name": site_name,
                    "integrations.jira.site_url": site_url,
                    "integrations.jira.provider": "jira",
                    "integrations.jira.category": "workitem",
                    "integrations.jira.user_account_id": current_user.get("accountId") if current_user else None,
                    "integrations.jira.user_display_name": current_user.get("displayName") if current_user else None,
                    "integrations.jira.status": "pending_project_selection",
                    "integrations.jira.webhook_registered": webhook_registered,
                    "integrations.jira.available_projects": available_jira_projects,
                    "integrations.jira.sprint_field_id": sprint_field_id,
                    "integrations.jira.board_selection_status": "pending_project_selection",
                    "integrations.jira.webhook_auth_mode": "url_token_v1",
                    "integrations.jira.webhook_token": webhook_token,
                    "integrations.jira.webhook_token_created_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                }
            },
        )
        await invalidate_pm_board_summary_cache_by_object_id(db, project_id)

        return {
            "message": "Jira site selected successfully",
            "site_name": site_name,
            "site_url": site_url,
            "projects_count": len(available_jira_projects),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to select Jira site: {str(e)}",
        )


@router.post("/jira/select-project/{project_id}")
async def select_jira_project(
    project_id: str,
    current_user: dict = Depends(get_current_user),
    selected_project_key: str = Query(...)
):
    """
    Handle Jira project selection after OAuth.
    Fetches tasks from selected project and completes integration.
    """
    try:
        db = get_database()
        user_id = str(current_user.get("user_id") or "")

        # Get project and verify access
        project = await db.projects.find_one({
            "_id": ObjectId(project_id),
            "user_id": user_id
        })

        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found or access denied"
            )

        # Check if Jira integration is pending
        jira_integration = project.get("integrations", {}).get("jira", {})
        if jira_integration.get("status") != "pending_project_selection":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Jira integration is not pending project selection"
            )

        # Validate selected project exists in available projects
        available_projects = jira_integration.get("available_projects", [])
        selected_project = next(
            (p for p in available_projects if p.get("key") == selected_project_key),
            None
        )

        if not selected_project:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Selected project {selected_project_key} not found in available projects"
            )

        # Decrypt tokens
        access_token = encryption_service.decrypt(jira_integration["access_token"])
        cloud_id = jira_integration["cloud_id"]
        site_url = jira_integration["site_url"]

        print(f"\nSelected Jira project: {selected_project_key} - {selected_project.get('name')}")

        # Fetch tasks from SELECTED project only
        custom_project_id = project.get("project_id", str(project["_id"]))
        try:
            from app.integrations.jira.normalizer import JiraNormalizer

            # Fetch tasks with JQL filter for selected project
            jql = f"project = {selected_project_key}"
            tasks = await jira_oauth_service.search_issues(access_token, cloud_id, jql)

            # Initialize Jira normalizer
            normalizer = JiraNormalizer(cloud_id=cloud_id, site_url=site_url)

            # Store tasks in brain collection and mark recommendation dirty for each imported task.
            brain_collection = get_brain_collection(custom_project_id)
            synced_count = await _upsert_jira_tasks_to_brain_and_mark_dirty(
                brain_collection=brain_collection,
                tasks=tasks,
                normalizer=normalizer,
                custom_project_id=custom_project_id,
            )
            print(f"Stored {synced_count} tasks from project {selected_project_key} in brain/{custom_project_id}")

            # Create project node document for Neo4j sync
            project_name = project.get("project_name", "Unknown Project")
            await brain_collection.update_one(
                {"entity_type": "project"},
                {
                    "$set": {
                        "integration_type": "project",
                        "entity_type": "project",
                        "name": project_name,
                        "project_id": custom_project_id,
                        "created_at": datetime.utcnow()
                    }
                },
                upsert=True
            )
            print(f"Created project node document in brain/{custom_project_id}")

        except Exception as e:
            print(f"Task fetch failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to fetch tasks from {selected_project_key}: {str(e)}"
            )

        # Sync Jira account IDs for all existing members
        try:
            members = project.get("members", [])
            print(f"Syncing Jira account IDs for {len(members)} members...")

            for member in members:
                member_email = member.get("email")
                if member_email:
                    await jira_oauth_service.sync_single_member_jira_id(
                        db=db,
                        project_id=project_id,
                        member_email=member_email,
                        access_token=access_token,
                        cloud_id=cloud_id
                    )

            print(f"Member sync completed")
        except Exception as e:
            print(f"Member sync failed: {str(e)}")

        selected_project_numeric_id = str(selected_project.get("id") or "").strip() or None
        available_boards, board_fetch_source = await _fetch_jira_boards_with_fallback(
            access_token=access_token,
            cloud_id=cloud_id,
            project_key=selected_project_key,
            project_numeric_id=selected_project_numeric_id,
        )
        default_board, board_selection_status = _choose_default_jira_board(available_boards)
        if default_board:
            print(
                "Selected default Jira board: "
                f"id={default_board.get('id')} name={default_board.get('name')} type={default_board.get('type')}"
            )
        else:
            print(
                "No deterministic default Jira board selected "
                f"(status={board_selection_status}, count={len(available_boards)}, source={board_fetch_source})"
            )

        # Detect workflow capabilities from synced tasks
        from app.projects.capability_detector import WorkflowCapabilityDetector

        # Fetch all tasks from brain collection for analysis
        all_tasks = await brain_collection.find({"entity_type": "work_item"}).to_list(length=None)

        # Detect capabilities (sprints, epics, etc.)
        capabilities = WorkflowCapabilityDetector.detect_capabilities(all_tasks)
        print(f"[Project Selection] Detected capabilities: Sprints={capabilities['has_sprints']}, Epics={capabilities['has_epics']}")
        status_category_map: Dict[str, str] = {}
        status_sync_error: Optional[str] = None
        try:
            status_category_map = await _fetch_jira_project_status_category_map(
                access_token=access_token,
                cloud_id=cloud_id,
                project_key=selected_project_key,
            )
        except Exception as status_exc:
            status_sync_error = str(status_exc)
            print(
                "[Jira StatusMap] Initial status mapping fetch failed "
                f"project={selected_project_key} error={status_exc}"
            )
        webhook_token = str(jira_integration.get("webhook_token") or "").strip() or _generate_jira_webhook_token()
        webhook_url = _build_jira_webhook_url(
            custom_project_id=custom_project_id,
            token=webhook_token,
        )
        webhook_registered = False
        try:
            webhook_response = await jira_oauth_service.register_webhook(
                access_token,
                cloud_id,
                webhook_url,
            )
            webhook_registered = webhook_response is not None
            print(f"Webhook registered for selected Jira project: {webhook_response}")
        except Exception as webhook_exc:
            print(f"Webhook registration failed for selected Jira project: {webhook_exc}")

        # Update integration with selected project and set status to connected
        result = await db.projects.update_one(
            {"_id": ObjectId(project_id), "user_id": user_id},
            {
                "$set": {
                    "integrations.jira.provider": "jira",
                    "integrations.jira.category": "workitem",
                    "integrations.jira.status": "connected",
                    "integrations.jira.default_project_key": selected_project_key,
                    "integrations.jira.default_project_name": selected_project.get("name"),
                    "integrations.jira.webhook_registered": webhook_registered,
                    "integrations.jira.webhook_auth_mode": "url_token_v1",
                    "integrations.jira.webhook_token": webhook_token,
                    "integrations.jira.webhook_token_created_at": datetime.utcnow(),
                    "integrations.jira.available_boards": available_boards,
                    "integrations.jira.default_board_id": (
                        int(default_board.get("id")) if isinstance(default_board, dict) and default_board.get("id") is not None else None
                    ),
                    "integrations.jira.default_board_name": (
                        str(default_board.get("name") or "").strip() if isinstance(default_board, dict) else None
                    ),
                    "integrations.jira.default_board_type": (
                        str(default_board.get("type") or "").strip().lower() if isinstance(default_board, dict) else None
                    ),
                    "integrations.jira.board_selection_status": board_selection_status,
                    "integrations.jira.board_fetch_source": board_fetch_source,
                    "integrations.jira.sprint_policy": str(jira_integration.get("sprint_policy") or "auto").strip().lower() or "auto",
                    "integrations.jira.last_sync_at": datetime.utcnow(),
                    "workflow_capabilities": capabilities,  # Store detected capabilities
                    "recommendation_status_category_map": status_category_map,
                    "status_map_last_synced_at": datetime.utcnow(),
                    "status_map_pending_unknowns": [],
                    "status_map_refresh_in_progress": False,
                    "status_map_last_error": status_sync_error,
                    "updated_at": datetime.utcnow()
                }
            }
        )

        if result.modified_count == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to update project selection"
            )
        await invalidate_pm_board_summary_cache_by_object_id(db, project_id)
        try:
            from app.projects.router import trigger_pm_board_summary_recompute

            enqueue_result = trigger_pm_board_summary_recompute(
                project_id=project_id,
                custom_project_id=custom_project_id,
                trigger="jira_connect_post_import",
            )
            print(
                "[PM Summary] Jira connect recompute enqueue: "
                f"project_id={custom_project_id} accepted={bool(enqueue_result.get('accepted'))} "
                f"reason={enqueue_result.get('reason') or 'n/a'}"
            )
        except Exception as pm_exc:
            print(
                "[PM Summary] Jira connect recompute enqueue failed: "
                f"project_id={custom_project_id} error={pm_exc}"
            )
        try:
            await generate_project_recommendations(
                custom_project_id,
                trigger="post_import",
            )
        except Exception as reco_exc:
            print(
                "[Recommendation] Post-import generation failed "
                f"for project_id={custom_project_id}: {reco_exc}"
            )
        await ProjectService.maybe_set_active_provider_on_connect(
            project_id=project_id,
            provider="jira",
        )
        try:
            await ensure_recommendation_schedules_for_projects(db)
        except Exception as schedule_exc:
            print(
                "[Recommendation] Schedule ensure failed after Jira connect "
                f"for project_id={custom_project_id}: {schedule_exc}"
            )

        return {
            "message": "Jira project selected and tasks synced successfully",
            "selected_project": {
                "key": selected_project_key,
                "name": selected_project.get("name")
            },
            "tasks_synced": len(tasks) if 'tasks' in locals() else 0,
            "default_board": default_board,
            "board_selection_status": board_selection_status,
            "board_fetch_source": board_fetch_source,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to select Jira project: {str(e)}"
        )


@router.post("/jira/select-board/{project_id}")
async def select_jira_board(
    project_id: str,
    current_user: dict = Depends(get_current_user),
    board_id: int = Query(..., description="Jira board ID to set as default"),
):
    """Set default Jira board for sprint-aware automation."""
    try:
        db = get_database()
        user_id = str(current_user.get("user_id") or "")
        project = await db.projects.find_one({"_id": ObjectId(project_id), "user_id": user_id})
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found or access denied",
            )

        jira_integration = (project.get("integrations") or {}).get("jira") or {}
        if str(jira_integration.get("status") or "").strip().lower() != "connected":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Jira integration must be connected before selecting a board",
            )

        available_boards = jira_integration.get("available_boards") or []
        selected_board = None
        for item in available_boards:
            if not isinstance(item, dict):
                continue
            try:
                if int(item.get("id")) == int(board_id):
                    selected_board = item
                    break
            except Exception:
                continue

        if selected_board is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Board {board_id} not found in available Jira boards for this project",
            )

        result = await db.projects.update_one(
            {"_id": ObjectId(project_id), "user_id": user_id},
            {
                "$set": {
                    "integrations.jira.default_board_id": int(selected_board.get("id")),
                    "integrations.jira.default_board_name": str(selected_board.get("name") or "").strip() or None,
                    "integrations.jira.default_board_type": str(selected_board.get("type") or "").strip().lower() or None,
                    "integrations.jira.board_selection_status": "manual_selected",
                    "updated_at": datetime.utcnow(),
                }
            },
        )
        if result.modified_count == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to persist selected Jira board",
            )

        return {
            "message": "Jira board selected successfully",
            "selected_board": {
                "id": int(selected_board.get("id")),
                "name": str(selected_board.get("name") or "").strip(),
                "type": str(selected_board.get("type") or "").strip().lower(),
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to select Jira board: {str(e)}",
        )


@router.post("/jira/refresh-boards/{project_id}")
async def refresh_jira_boards(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Refresh Jira boards for the configured default project and select deterministic default when possible."""
    try:
        db = get_database()
        user_id = str(current_user.get("user_id") or "")
        project = await db.projects.find_one({"_id": ObjectId(project_id), "user_id": user_id})
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found or access denied",
            )

        jira_integration = (project.get("integrations") or {}).get("jira") or {}
        if str(jira_integration.get("status") or "").strip().lower() != "connected":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Jira integration must be connected before refreshing boards",
            )

        project_key = str(jira_integration.get("default_project_key") or "").strip()
        cloud_id = str(jira_integration.get("cloud_id") or "").strip()
        if not project_key or not cloud_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Jira default project is not configured",
            )

        adapter = JiraAdapter(project)
        await adapter.ensure_token_valid()
        access_token = adapter.access_token

        available_projects = jira_integration.get("available_projects") or []
        project_numeric_id = _resolve_project_numeric_id(
            available_projects=available_projects,
            project_key=project_key,
        )
        available_boards, board_fetch_source = await _fetch_jira_boards_with_fallback(
            access_token=access_token,
            cloud_id=cloud_id,
            project_key=project_key,
            project_numeric_id=project_numeric_id,
        )

        existing_default_board_id = jira_integration.get("default_board_id")
        selected_board = None
        try:
            existing_default_int = int(existing_default_board_id) if existing_default_board_id is not None else None
        except Exception:
            existing_default_int = None
        if existing_default_int is not None:
            for item in available_boards:
                if not isinstance(item, dict):
                    continue
                try:
                    if int(item.get("id")) == existing_default_int:
                        selected_board = item
                        break
                except Exception:
                    continue

        board_selection_status = "retained_existing_default"
        if selected_board is None:
            selected_board, board_selection_status = _choose_default_jira_board(available_boards)

        await db.projects.update_one(
            {"_id": ObjectId(project_id), "user_id": user_id},
            {
                "$set": {
                    "integrations.jira.available_boards": available_boards,
                    "integrations.jira.default_board_id": (
                        int(selected_board.get("id")) if isinstance(selected_board, dict) and selected_board.get("id") is not None else None
                    ),
                    "integrations.jira.default_board_name": (
                        str(selected_board.get("name") or "").strip() if isinstance(selected_board, dict) else None
                    ),
                    "integrations.jira.default_board_type": (
                        str(selected_board.get("type") or "").strip().lower() if isinstance(selected_board, dict) else None
                    ),
                    "integrations.jira.board_selection_status": board_selection_status,
                    "integrations.jira.board_fetch_source": board_fetch_source,
                    "updated_at": datetime.utcnow(),
                }
            },
        )

        return {
            "message": "Jira boards refreshed successfully",
            "board_selection_status": board_selection_status,
            "board_fetch_source": board_fetch_source,
            "available_boards_count": len(available_boards),
            "default_board": selected_board,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to refresh Jira boards: {str(e)}",
        )


@router.post("/jira/refresh-status-map/{project_id}")
async def refresh_jira_status_map(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Manually refresh Jira status->category map for a project.
    Optional endpoint for admin/support use; not wired to UI by default.
    """
    try:
        db = get_database()
        user_id = str(current_user.get("user_id") or "")
        project = await db.projects.find_one({"_id": ObjectId(project_id), "user_id": user_id})
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found or access denied",
            )

        jira_integration = (project.get("integrations") or {}).get("jira") or {}
        if str(jira_integration.get("status") or "").strip().lower() != "connected":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Jira integration must be connected before refreshing status map",
            )

        default_project_key = str(jira_integration.get("default_project_key") or "").strip()
        cloud_id = str(jira_integration.get("cloud_id") or "").strip()
        if not default_project_key or not cloud_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Jira default project is not configured",
            )

        access_token = encryption_service.decrypt(jira_integration.get("access_token"))
        refresh_token = encryption_service.decrypt(jira_integration.get("refresh_token"))
        expires_at = jira_integration.get("expires_at")

        # Refresh token if expired/about to expire (same policy as sync path).
        if expires_at and datetime.utcnow() >= (expires_at - timedelta(minutes=5)):
            token_response = await jira_oauth_service.refresh_access_token(refresh_token)
            access_token = token_response.get("access_token")
            new_refresh_token = token_response.get("refresh_token")
            expires_in = token_response.get("expires_in")
            await db.projects.update_one(
                {"_id": ObjectId(project_id), "user_id": user_id},
                {
                    "$set": {
                        "integrations.jira.access_token": encryption_service.encrypt(access_token),
                        "integrations.jira.refresh_token": encryption_service.encrypt(new_refresh_token) if new_refresh_token else jira_integration.get("refresh_token"),
                        "integrations.jira.expires_at": datetime.utcnow() + timedelta(seconds=expires_in) if expires_in else None,
                    }
                },
            )

        status_map = await _fetch_jira_project_status_category_map(
            access_token=access_token,
            cloud_id=cloud_id,
            project_key=default_project_key,
        )
        if not isinstance(status_map, dict):
            status_map = {}

        await db.projects.update_one(
            {"_id": ObjectId(project_id), "user_id": user_id},
            {
                "$set": {
                    "recommendation_status_category_map": status_map,
                    "status_map_pending_unknowns": [],
                    "status_map_refresh_in_progress": False,
                    "status_map_last_error": None,
                    "status_map_last_synced_at": datetime.utcnow(),
                    "updated_at": datetime.utcnow(),
                }
            },
        )
        await invalidate_pm_board_summary_cache_by_object_id(db, project_id)
        return {
            "message": "Jira status map refreshed successfully",
            "status_count": len(status_map),
            "default_project_key": default_project_key,
        }
    except HTTPException:
        raise
    except Exception as e:
        try:
            db = get_database()
            await db.projects.update_one(
                {"_id": ObjectId(project_id), "user_id": user_id},
                {
                    "$set": {
                        "status_map_refresh_in_progress": False,
                        "status_map_last_error": str(e),
                        "updated_at": datetime.utcnow(),
                    }
                },
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to refresh Jira status map: {str(e)}",
        )


@router.post("/{project_id}/jira/sync")
async def sync_jira_tasks(project_id: str):
    """
    Manually sync tasks from Jira to brain database.
    Can be called multiple times without creating duplicates (uses upsert).
    """
    try:
        db = get_database()

        # Get project by custom project_id
        project = await db.projects.find_one({"project_id": project_id})

        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )

        # Check if Jira is connected
        jira_integration = project.get("integrations", {}).get("jira", {})
        if jira_integration.get("status") != "connected":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Jira integration is not connected"
            )

        # Get default project key
        default_project_key = jira_integration.get("default_project_key")
        if not default_project_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No default Jira project selected"
            )

        # Decrypt tokens
        access_token = encryption_service.decrypt(jira_integration["access_token"])
        refresh_token = encryption_service.decrypt(jira_integration["refresh_token"])
        cloud_id = jira_integration["cloud_id"]
        site_url = jira_integration["site_url"]
        expires_at = jira_integration.get("expires_at")

        # Check if token is expired or about to expire (within 5 minutes)
        if expires_at and datetime.utcnow() >= (expires_at - timedelta(minutes=5)):
            print(f"[Sync] Jira token expired, refreshing...")

            try:
                # Refresh token
                token_response = await jira_oauth_service.refresh_access_token(refresh_token)

                # Update tokens
                access_token = token_response.get("access_token")
                new_refresh_token = token_response.get("refresh_token")
                expires_in = token_response.get("expires_in")

                # Update project document with new tokens
                await db.projects.update_one(
                    {"project_id": project_id},
                    {
                        "$set": {
                            "integrations.jira.access_token": encryption_service.encrypt(access_token),
                            "integrations.jira.refresh_token": encryption_service.encrypt(new_refresh_token) if new_refresh_token else refresh_token,
                            "integrations.jira.expires_at": datetime.utcnow() + timedelta(seconds=expires_in) if expires_in else None
                        }
                    }
                )

                print(f"[Sync] Jira token refreshed successfully")

            except Exception as e:
                print(f"[Sync] Error refreshing Jira token: {str(e)}")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Jira token expired and refresh failed. Please reconnect Jira integration."
                )

        print(f"\n[Sync] Syncing tasks for project {project_id} from Jira project {default_project_key}")

        # Get sprint field ID (if detected during Jira connection)
        sprint_field_id = jira_integration.get("sprint_field_id")
        if sprint_field_id:
            print(f"[Sync] Using sprint field: {sprint_field_id}")

        # Fetch tasks from Jira
        try:
            from app.integrations.jira.normalizer import JiraNormalizer

            # Fetch tasks with JQL filter for default project
            jql = f"project = {default_project_key}"
            tasks = await jira_oauth_service.search_issues(
                access_token,
                cloud_id,
                jql,
                sprint_field_id=sprint_field_id
            )

            # Initialize Jira normalizer
            normalizer = JiraNormalizer(cloud_id=cloud_id, site_url=site_url)

            # Refresh Jira create-metadata cache (allowed create types + parent rules)
            # as part of explicit sync-now flow. This is fail-open for task sync.
            metadata_refresh_ok = False
            try:
                adapter = JiraAdapter(project=project, access_token_override=access_token)
                await adapter._fetch_and_cache_allowed_issue_types(  # noqa: SLF001
                    jira_project_key=str(default_project_key or "").strip()
                )
                metadata_refresh_ok = True
            except Exception as metadata_exc:
                print(
                    "[Sync] Jira create-metadata refresh failed (continuing task sync): "
                    f"project={project_id} key={default_project_key} error={metadata_exc}"
                )

            # Sync tasks to brain collection (upsert prevents duplicates) and mark dirty for recompute.
            brain_collection = get_brain_collection(project_id)
            synced_count = await _upsert_jira_tasks_to_brain_and_mark_dirty(
                brain_collection=brain_collection,
                tasks=tasks,
                normalizer=normalizer,
                custom_project_id=project_id,
            )

            print(f"[Sync] Synced {synced_count} tasks from Jira project {default_project_key}")

            # Detect workflow capabilities from synced tasks
            from app.projects.capability_detector import WorkflowCapabilityDetector

            # Fetch all tasks from brain collection for analysis
            all_tasks = await brain_collection.find({"entity_type": "work_item"}).to_list(length=None)

            # Detect capabilities (sprints, epics, etc.)
            capabilities = WorkflowCapabilityDetector.detect_capabilities(all_tasks)

            print(f"[Sync] Detected capabilities: Sprints={capabilities['has_sprints']}, Epics={capabilities['has_epics']}")
            print(f"[Sync] Grouping hierarchy: {capabilities['grouping_hierarchy']}")

            # Get sprint statistics if sprints detected
            sprint_stats = {}
            if capabilities['has_sprints']:
                sprint_stats = WorkflowCapabilityDetector.get_sprint_statistics(all_tasks)
                print(f"[Sync] Sprint stats: {sprint_stats.get('total_sprint_tasks', 0)} tasks in sprints, "
                      f"{sprint_stats.get('total_backlog_tasks', 0)} in backlog")

            # Get epic statistics if epics detected
            epic_stats = {}
            if capabilities['has_epics']:
                epic_stats = WorkflowCapabilityDetector.get_epic_statistics(all_tasks)
                print(f"[Sync] Epic stats: {epic_stats.get('total_epics', 0)} epics, "
                      f"{epic_stats.get('total_epic_children', 0)} child tasks")

            # Update project with detected capabilities and last sync timestamp
            existing_status_map = project.get("recommendation_status_category_map")
            if not isinstance(existing_status_map, dict):
                existing_status_map = {}
            encountered_statuses = _extract_status_names_from_jira_issues(tasks)
            unknown_statuses = [
                status_name
                for status_name in encountered_statuses
                if not _map_contains_status(existing_status_map, status_name)
            ]

            refreshed_status_map: Dict[str, str] = dict(existing_status_map)
            status_sync_error: Optional[str] = None
            status_sync_attempted = False
            unresolved_statuses = list(unknown_statuses)
            last_synced_at = project.get("status_map_last_synced_at")
            can_refresh = True
            if isinstance(last_synced_at, datetime):
                can_refresh = datetime.utcnow() - last_synced_at >= _STATUS_MAP_REFRESH_COOLDOWN
            if unknown_statuses and can_refresh:
                status_sync_attempted = True
                try:
                    fetched_map = await _fetch_jira_project_status_category_map(
                        access_token=access_token,
                        cloud_id=cloud_id,
                        project_key=default_project_key,
                    )
                    if isinstance(fetched_map, dict) and fetched_map:
                        refreshed_status_map.update(fetched_map)
                    unresolved_statuses = [
                        status_name
                        for status_name in unknown_statuses
                        if not _map_contains_status(refreshed_status_map, status_name)
                    ]
                except Exception as status_exc:
                    status_sync_error = str(status_exc)
                    print(
                        "[Jira StatusMap] Status refresh failed during sync "
                        f"project={project_id} error={status_exc}"
                    )
            elif unknown_statuses and not can_refresh:
                print(
                    "[Jira StatusMap] Refresh skipped by cooldown "
                    f"project={project_id} unknown_count={len(unknown_statuses)}"
                )

            await db.projects.update_one(
                {"project_id": project_id},
                {
                    "$set": {
                        "integrations.jira.last_sync_at": datetime.utcnow(),
                        "workflow_capabilities": capabilities,  # Store detected capabilities
                        "recommendation_status_category_map": refreshed_status_map,
                        "status_map_pending_unknowns": unresolved_statuses,
                        "status_map_refresh_in_progress": False,
                        "status_map_last_error": status_sync_error,
                        "status_map_last_synced_at": datetime.utcnow() if status_sync_attempted else project.get("status_map_last_synced_at") or datetime.utcnow(),
                        "updated_at": datetime.utcnow()
                    }
                }
            )

            # ------------------------------------------------------------------
            # Gap-fill: backfill jira_account_id for active members missing it.
            # Only touch members who don't have the ID yet — skip the rest.
            # ------------------------------------------------------------------
            members_without_jira_id = [
                m for m in project.get("members", [])
                if m.get("status") == "active"
                and not (m.get("integration_ids") or {}).get("jira_account_id")
                and m.get("email")
            ]

            filled_count = 0
            if members_without_jira_id:
                print(
                    f"[Sync] {len(members_without_jira_id)} active member(s) missing "
                    f"jira_account_id — attempting backfill"
                )
                for m in members_without_jira_id:
                    try:
                        await jira_oauth_service.sync_single_member_jira_id(
                            db=db,
                            project_id=project_id,
                            member_email=m["email"],
                            access_token=access_token,
                            cloud_id=cloud_id,
                        )
                        filled_count += 1
                        print(
                            f"[Sync] jira_account_id backfilled for: {m['email']}"
                        )
                    except Exception as fill_err:
                        print(
                            f"[Sync] jira_account_id backfill failed for "
                            f"{m['email']}: {fill_err}"
                        )

            await _force_pm_board_rebuild_after_jira_sync(
                project_object_id=project.get("_id"),
                custom_project_id=project_id,
            )
            return {
                "message": "Jira tasks synced successfully",
                "project_key": default_project_key,
                "tasks_synced": synced_count,
                "jira_create_metadata_refreshed": metadata_refresh_ok,
                "members_backfilled": filled_count,
                "workflow_capabilities": capabilities,
                "sprint_statistics": sprint_stats if capabilities['has_sprints'] else None,
                "epic_statistics": epic_stats if capabilities['has_epics'] else None
            }

        except Exception as e:
            print(f"[Sync] Task sync failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to sync tasks from Jira: {str(e)}"
            )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to sync Jira tasks: {str(e)}"
        )


@router.delete("/jira/disconnect/{project_id}")
async def disconnect_jira(
    project_id: str,
    purge_data: bool = Query(False, description="When true, also remove imported Jira work items from Brain."),
    current_user: dict = Depends(get_current_user),
):
    """Disconnect Jira integration from a project"""
    try:
        db = get_database()
        user_id = str(current_user.get("user_id") or "")
        object_id = ObjectId(project_id)

        project = await db.projects.find_one(
            {"_id": object_id, "user_id": user_id},
            {"project_id": 1, "integrations": 1, "active_provider": 1},
        )
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found or already disconnected"
            )

        custom_project_id = str(project.get("project_id") or str(project.get("_id") or "")).strip()

        # Remove from new structure (integrations.jira) and old structure (tools_summary.jira)
        result = await db.projects.update_one(
            {"_id": object_id, "user_id": user_id},
            {
                "$unset": {
                    "integrations.jira": "",
                    "tools_summary.jira": "",
                    "tools.jira": ""
                },
                "$set": {"updated_at": datetime.utcnow()}
            }
        )

        if result.modified_count == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found or already disconnected"
            )

        deleted_count = 0
        if purge_data and custom_project_id:
            try:
                brain_collection = get_brain_collection(custom_project_id)
                delete_result = await brain_collection.delete_many(_work_item_delete_filter())
                deleted_count = int(delete_result.deleted_count or 0)
            except Exception as purge_exc:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to purge project work items: {purge_exc}",
                ) from purge_exc

        await _invalidate_work_item_read_caches(
            db=db,
            project_object_id=project_id,
            custom_project_id=custom_project_id,
            trigger="jira_disconnect",
        )

        updated_project = await db.projects.find_one(
            {"_id": object_id, "user_id": user_id},
            {"integrations": 1, "active_provider": 1},
        )
        if isinstance(updated_project, dict):
            remaining_pm_providers = connected_pm_providers_from_project_context(updated_project)
            current_active = str(updated_project.get("active_provider") or "").strip().lower() or None
            next_active = resolve_active_provider_after_disconnect(
                disconnected_provider="jira",
                current_active_provider=current_active,
                remaining_connected_pm_providers=remaining_pm_providers,
            )
            if next_active != current_active:
                await db.projects.update_one(
                    {"_id": object_id, "user_id": user_id},
                    {"$set": {"active_provider": next_active, "updated_at": datetime.utcnow()}},
                )

        return {
            "message": "Jira disconnected successfully",
            "purged": bool(purge_data),
            "deleted_count": deleted_count,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to disconnect Jira: {str(e)}"
        )


@router.get("/workitems/data-status/{project_id}")
async def get_workitem_data_status(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Generic work-item data presence check for integration connect guardrails.
    Policy: if any work-item data already exists in project Brain, require explicit cleanup confirmation.
    """
    try:
        db = get_database()
        user_id = str(current_user.get("user_id") or "")
        project_query: Dict[str, Any] = {"user_id": user_id, "$or": [{"project_id": project_id}]}
        try:
            project_query["$or"].append({"_id": ObjectId(project_id)})
        except Exception:
            pass

        project = await db.projects.find_one(
            project_query,
            {"project_id": 1, "integrations": 1},
        )
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found or access denied",
            )

        custom_project_id = str(project.get("project_id") or str(project.get("_id") or "")).strip()
        brain_collection = get_brain_collection(custom_project_id)
        work_items_count = int(await brain_collection.count_documents(_work_item_delete_filter()))

        integrations = project.get("integrations") if isinstance(project, dict) else {}
        connected_workitem_integrations: List[str] = []
        if isinstance(integrations, dict):
            for integration_name, integration_value in integrations.items():
                if not _is_workitem_integration_record(str(integration_name or ""), integration_value):
                    continue
                status_value = str((integration_value or {}).get("status") or "").strip().lower()
                if _is_connected_integration_status(status_value):
                    connected_workitem_integrations.append(str(integration_name or "").strip().lower())

        has_work_items = work_items_count > 0
        requires_cleanup_before_connect = has_work_items

        return {
            "project_id": custom_project_id,
            "has_work_items": has_work_items,
            "work_items_count": work_items_count,
            "requires_cleanup_before_connect": requires_cleanup_before_connect,
            "connected_workitem_integrations": connected_workitem_integrations,
            # Compatibility aliases for existing Jira-specific UI callers.
            "has_stale_data": has_work_items,
            "stale_work_items_count": work_items_count,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to inspect work-item data status: {str(e)}",
        )


@router.post("/workitems/cleanup/{project_id}")
async def cleanup_workitems(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """
    Hard-delete all project work-item docs after explicit owner confirmation.
    Safety: blocked while any work-item integration remains connected/pending.
    """
    try:
        db = get_database()
        user_id = str(current_user.get("user_id") or "")
        project_query: Dict[str, Any] = {"user_id": user_id, "$or": [{"project_id": project_id}]}
        try:
            project_query["$or"].append({"_id": ObjectId(project_id)})
        except Exception:
            pass

        project = await db.projects.find_one(
            project_query,
            {"project_id": 1, "integrations": 1},
        )
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found or access denied",
            )

        integrations = project.get("integrations") if isinstance(project, dict) else {}
        connected_workitem_integrations: List[str] = []
        if isinstance(integrations, dict):
            for integration_name, integration_value in integrations.items():
                if not _is_workitem_integration_record(str(integration_name or ""), integration_value):
                    continue
                status_value = str((integration_value or {}).get("status") or "").strip().lower()
                if _is_connected_integration_status(status_value):
                    connected_workitem_integrations.append(str(integration_name or "").strip().lower())

        if connected_workitem_integrations:
            integrations_str = ", ".join(sorted(set(connected_workitem_integrations)))
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Disconnect work-item integration(s) before clearing data: {integrations_str}.",
            )

        custom_project_id = str(project.get("project_id") or str(project.get("_id") or "")).strip()
        brain_collection = get_brain_collection(custom_project_id)

        before_count = int(await brain_collection.count_documents(_work_item_delete_filter()))
        delete_result = await brain_collection.delete_many(_work_item_delete_filter())
        deleted_count = int(delete_result.deleted_count or 0)
        after_count = max(0, before_count - deleted_count)

        await _invalidate_work_item_read_caches(
            db=db,
            project_object_id=str(project.get("_id") or project_id),
            custom_project_id=custom_project_id,
            trigger="workitems_cleanup",
        )

        return {
            "message": "Project work items cleared successfully",
            "project_id": custom_project_id,
            "deleted_count": deleted_count,
            "remaining_count": after_count,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to clear project work items: {str(e)}",
        )


# Backward-compatible Jira aliases (now routed to generic work-item policy).
@router.get("/jira/data-status/{project_id}")
async def get_jira_data_status(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    return await get_workitem_data_status(project_id=project_id, current_user=current_user)


@router.post("/jira/cleanup-work-items/{project_id}")
async def cleanup_jira_work_items(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    return await cleanup_workitems(project_id=project_id, current_user=current_user)
