"""Jira-backed Cadence action adapters."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Mapping, MutableMapping, Optional

from app.core.security import encryption_service
from app.cortex.tools.schemas import normalize_external_id
from app.integrations.jira.normalizer import JiraNormalizer
from app.integrations.jira.oauth import jira_oauth_service
from app.tasks.brain_sync import BrainSync
from app.tasks.comment_composer import compose_comment_text
from app.tasks.schemas import UpdateTaskDTO
from app.tasks.service import TaskService


logger = logging.getLogger("app.cadence.action_adapters.providers.jira")
JIRA_API_DELAY_DEFAULT = 0.5


def _normalize_identity_token(value: Any) -> str:
    return str(value or "").strip().lower()


def _session_actor_identity(session: Mapping[str, Any]) -> str:
    member_email = str(session.get("member_email") or "").strip().lower()
    if member_email:
        return member_email
    member_name = str(session.get("member_name") or "").strip()
    if member_name:
        return member_name
    return str(session.get("user_id") or "Unknown User").strip() or "Unknown User"


def _member_identity_tokens(member: Mapping[str, Any]) -> set[str]:
    tokens: set[str] = set()
    email = _normalize_identity_token(member.get("email"))
    name = _normalize_identity_token(member.get("name"))
    user_id = _normalize_identity_token(member.get("user_id"))
    if email:
        tokens.add(email)
        local = email.split("@", 1)[0].strip()
        if local:
            tokens.add(local)
    if name:
        tokens.add(name)
        compact = name.replace(" ", "")
        if compact:
            tokens.add(compact)
        first = name.split(" ", 1)[0].strip()
        if first:
            tokens.add(first)
    if user_id:
        tokens.add(user_id)
    return tokens


def _workspace_identity_tokens(user: Mapping[str, Any]) -> set[str]:
    tokens: set[str] = set()
    email = _normalize_identity_token(user.get("emailAddress"))
    display_name = _normalize_identity_token(user.get("displayName"))
    account_id = _normalize_identity_token(user.get("accountId"))
    if email:
        tokens.add(email)
        local = email.split("@", 1)[0].strip()
        if local:
            tokens.add(local)
    if display_name:
        tokens.add(display_name)
        compact = display_name.replace(" ", "")
        if compact:
            tokens.add(compact)
        first = display_name.split(" ", 1)[0].strip()
        if first:
            tokens.add(first)
    if account_id:
        tokens.add(account_id)
    return tokens


async def _resolve_assignee_account_id(
    *,
    access_token: str,
    cloud_id: str,
    project: Mapping[str, Any],
    session: Mapping[str, Any],
    assignee_hint: str,
) -> Optional[str]:
    raw_hint = str(assignee_hint or "").strip()
    if not raw_hint:
        return None
    normalized_hint = _normalize_identity_token(raw_hint).lstrip("@")
    if not normalized_hint:
        return None

    me_aliases = {"me", "myself", "self", "i", "mine"}
    if normalized_hint in me_aliases:
        session_email = _normalize_identity_token(session.get("member_email"))
        session_name = _normalize_identity_token(session.get("member_name"))
        session_user_id = _normalize_identity_token(session.get("user_id"))
        if session_email:
            normalized_hint = session_email
        elif session_name:
            normalized_hint = session_name
        elif session_user_id:
            normalized_hint = session_user_id

    members = [item for item in (project.get("members") or []) if isinstance(item, dict)]
    active_members = [
        item
        for item in members
        if str(item.get("status") or "active").strip().lower() in {"", "active"}
    ]

    member_matches = [
        member
        for member in active_members
        if normalized_hint in _member_identity_tokens(member)
    ]
    if len(member_matches) == 1:
        integration_ids = (
            member_matches[0].get("integration_ids")
            if isinstance(member_matches[0].get("integration_ids"), dict)
            else {}
        )
        account_id = str(integration_ids.get("jira_account_id") or "").strip()
        if account_id:
            return account_id
        member_email = str(member_matches[0].get("email") or "").strip().lower()
        if member_email:
            resolved = await jira_oauth_service.search_user_by_email(access_token, cloud_id, member_email)
            if resolved:
                return str(resolved).strip()
    if len(member_matches) > 1:
        return None

    if "@" in normalized_hint:
        resolved = await jira_oauth_service.search_user_by_email(access_token, cloud_id, normalized_hint)
        if resolved:
            return str(resolved).strip()

    workspace_users = await jira_oauth_service.get_workspace_users(
        access_token=access_token,
        cloud_id=cloud_id,
    )
    workspace_matches = [
        user
        for user in (workspace_users or [])
        if normalized_hint in _workspace_identity_tokens(user)
    ]
    if len(workspace_matches) != 1:
        return None
    account_id = str(workspace_matches[0].get("accountId") or "").strip()
    return account_id or None


async def _refresh_token_with_retry(refresh_token: str, max_retries: int = 3) -> Optional[Dict[str, Any]]:
    """Refresh Jira access token with exponential backoff retry."""
    for attempt in range(max_retries):
        try:
            token_data = await jira_oauth_service.refresh_access_token(refresh_token)
            if attempt > 0:
                logger.info("jira_token_refresh_retry_success attempt=%s", attempt + 1)
            return token_data
        except ValueError as exc:
            if attempt < max_retries - 1:
                delay = 2 ** attempt
                logger.warning(
                    "jira_token_refresh_retry attempt=%s max=%s delay_s=%s error=%s",
                    attempt + 1,
                    max_retries,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    "jira_token_refresh_failed max=%s error=%s",
                    max_retries,
                    exc,
                )
                raise
    return None


async def _resolve_jira_access_token(
    db: Any,
    *,
    project_id: str,
    jira_integration: Dict[str, Any],
) -> str:
    """Resolve Jira access token and persist refresh result when near expiry."""
    access_token = encryption_service.decrypt(jira_integration["access_token"])
    refresh_token = encryption_service.decrypt(jira_integration.get("refresh_token", ""))
    expires_at = jira_integration.get("expires_at")

    needs_refresh = False
    if expires_at:
        now = datetime.utcnow()
        if expires_at < now + timedelta(minutes=5):
            needs_refresh = True
            logger.info("jira_access_token_refresh_needed project_id=%s", project_id)

    if needs_refresh and refresh_token:
        try:
            token_data = await _refresh_token_with_retry(refresh_token, max_retries=3)
            if token_data:
                new_access_token = token_data.get("access_token")
                new_refresh_token = token_data.get("refresh_token")
                expires_in = token_data.get("expires_in", 3600)

                encrypted_access = encryption_service.encrypt(new_access_token)
                encrypted_refresh = (
                    encryption_service.encrypt(new_refresh_token)
                    if new_refresh_token
                    else jira_integration.get("refresh_token")
                )
                await db.projects.update_one(
                    {"project_id": project_id},
                    {
                        "$set": {
                            "integrations.jira.access_token": encrypted_access,
                            "integrations.jira.refresh_token": encrypted_refresh,
                            "integrations.jira.expires_at": datetime.utcnow() + timedelta(seconds=expires_in),
                        }
                    },
                )
                access_token = new_access_token
                logger.info("jira_access_token_refresh_success project_id=%s", project_id)
            else:
                logger.warning("jira_access_token_refresh_empty_response project_id=%s", project_id)
        except ValueError as exc:
            logger.warning(
                "jira_access_token_refresh_failed_using_existing project_id=%s error=%s",
                project_id,
                exc,
            )
    return access_token


class _JiraTaskMutationAdapter:
    provider = "jira"
    capability_id = ""

    async def _execute_mutation(
        self,
        *,
        db: Any,
        session: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> MutableMapping[str, Any]:
        project_id = str(payload.get("project_id") or session.get("project_id") or "").strip()
        raw_task_id = str(
            payload.get("task_id") or payload.get("external_id") or payload.get("task_key") or ""
        ).strip()
        if not raw_task_id:
            # Fall back to the session's current task when no task ID is supplied in payload.
            # Cadence agent tools omit task_id when the LLM hasn't been given an explicit schema.
            _task_idx = int(session.get("current_task_index") or 0)
            _tasks = list(session.get("tasks") or [])
            if _task_idx < len(_tasks):
                raw_task_id = str(_tasks[_task_idx].get("external_id") or "").strip()
        updated_by = str(payload.get("updated_by") or _session_actor_identity(session)).strip()
        source_label = str(payload.get("source_label") or "Slack").strip() or "Slack"
        status = payload.get("status")
        assignee_account_id = str(payload.get("assignee_account_id") or "").strip()
        comment = compose_comment_text(str(payload.get("comment") or ""))
        add_status_auto_comment = bool(payload.get("add_status_auto_comment", True))
        api_delay_seconds = float(payload.get("api_delay_seconds") or JIRA_API_DELAY_DEFAULT)

        if not project_id:
            return {"ok": False, "error": "missing_project_id"}
        if not raw_task_id:
            return {"ok": False, "error": "missing_task_id"}

        try:
            task_id = normalize_external_id(raw_task_id)
        except Exception:
            task_id = raw_task_id.upper()

        project = await db.projects.find_one({"project_id": project_id})
        if not project:
            return {"ok": False, "error": "project_not_found", "project_id": project_id, "task_id": task_id}

        jira_integration = ((project.get("integrations") or {}).get("jira") or {})
        if not isinstance(jira_integration, dict) or not jira_integration.get("access_token"):
            return {"ok": False, "error": "jira_integration_missing", "project_id": project_id, "task_id": task_id}

        access_token = await _resolve_jira_access_token(
            db=db,
            project_id=project_id,
            jira_integration=jira_integration,
        )

        # Fast path: when session task carries pre-fetched transitions, apply by ID directly.
        # This bypasses the hardcoded _status_mapping_for_target() lookup and works for
        # any Jira project regardless of workflow configuration.
        _task_idx = int(session.get("current_task_index") or 0)
        _tasks = list(session.get("tasks") or [])
        _session_task = _tasks[_task_idx] if _task_idx < len(_tasks) else {}
        _available_transitions = list(_session_task.get("available_transitions") or [])

        status_updated = False
        comment_added = False
        brain_sync_succeeded = True

        if status and _available_transitions and not assignee_account_id:
            cloud_id = str(jira_integration.get("cloud_id") or "").strip()
            matched = next(
                (t for t in _available_transitions if t.get("name", "").lower() == str(status).lower()),
                None,
            )
            if matched:
                transition_result = await jira_oauth_service.apply_transition_by_id(
                    access_token=access_token,
                    cloud_id=cloud_id,
                    task_key=task_id,
                    transition_id=matched["id"],
                )
                status_updated = bool(transition_result.get("status_updated"))
                if status_updated:
                    logger.info(
                        "jira_transition_applied_by_id task=%s transition=%s id=%s",
                        task_id,
                        matched["name"],
                        matched["id"],
                    )
                else:
                    logger.warning(
                        "jira_transition_by_id_failed task=%s transition=%s id=%s",
                        task_id,
                        matched["name"],
                        matched["id"],
                    )
            else:
                # Transition name from LLM not in available list — treat as comment only
                logger.info(
                    "jira_transition_name_not_matched task=%s status=%s available=%s",
                    task_id,
                    status,
                    [t.get("name") for t in _available_transitions],
                )
                status = None  # clear so only comment path runs below

            if comment:
                from app.integrations.jira.oauth import jira_oauth_service as _jira
                comment_added = await _jira.add_comment(
                    access_token=access_token,
                    cloud_id=cloud_id,
                    task_key=task_id,
                    comment_text=comment,
                )

            # Keep Brain doc consistent with Jira mutation in fast transition path.
            # Previously this branch returned without a Brain sync, causing stale task status.
            try:
                site_url = str(jira_integration.get("site_url") or "").strip()
                raw_issue = await jira_oauth_service.get_issue(
                    access_token=access_token,
                    cloud_id=cloud_id,
                    issue_key=task_id,
                )
                normalized_task = JiraNormalizer(
                    cloud_id=cloud_id,
                    site_url=site_url,
                ).normalize_task(raw_issue)
                await BrainSync.sync_task_to_brain(
                    project_id=project_id,
                    jira_task=normalized_task,
                )
                brain_sync_succeeded = True
            except Exception as sync_error:
                brain_sync_succeeded = False
                logger.warning(
                    "jira_fast_path_brain_sync_failed project_id=%s task_id=%s error=%s",
                    project_id,
                    task_id,
                    sync_error,
                )

            return {
                "ok": True,
                "project_id": project_id,
                "task_id": task_id,
                "status": status,
                "comment": comment,
                "status_updated": status_updated,
                "comment_added": comment_added,
                "assignee_updated": False,
                "brain_sync_succeeded": brain_sync_succeeded,
                "task_status": "unknown",
                "already_progressed": False,
            }

        # Fallback path: no pre-fetched transitions — use existing canonical mapping pipeline.
        mutation = UpdateTaskDTO(
            task_id=task_id,
            status=status,
            comment=comment,
            assignee_account_id=assignee_account_id or None,
            project_id=project_id,
        )
        mutation_result = await TaskService.mutate_task(
            task_data=mutation,
            project=project,
            updated_by=updated_by,
            source_label=source_label,
            access_token_override=access_token,
            add_status_auto_comment=add_status_auto_comment,
            api_delay_seconds=api_delay_seconds,
        )

        final_status = str((mutation_result.task.status or "")).strip().lower()
        already_progressed = final_status in {"in_progress", "done", "in review"}
        return {
            "ok": True,
            "project_id": project_id,
            "task_id": task_id,
            "status": status,
            "comment": comment,
            "status_updated": bool(mutation_result.status_updated),
            "comment_added": bool(mutation_result.comment_added),
            "assignee_updated": bool(mutation_result.assignee_updated),
            "brain_sync_succeeded": bool(mutation_result.brain_sync_succeeded),
            "task_status": final_status or "unknown",
            "already_progressed": bool(already_progressed and not mutation_result.status_updated),
        }


class JiraTaskUpdateStatusAdapter(_JiraTaskMutationAdapter):
    provider = "jira"
    capability_id = "workitem_update_status"

    async def execute(
        self,
        *,
        db: Any,
        session: Mapping[str, Any],
        payload: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> MutableMapping[str, Any]:
        _ = context
        if str(payload.get("status") or "").strip() == "":
            return {"ok": False, "error": "missing_status"}
        return await self._execute_mutation(db=db, session=session, payload=payload)


class JiraTaskAddCommentAdapter(_JiraTaskMutationAdapter):
    provider = "jira"
    capability_id = "workitem_add_comment"

    async def execute(
        self,
        *,
        db: Any,
        session: Mapping[str, Any],
        payload: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> MutableMapping[str, Any]:
        _ = context
        comment = str(payload.get("comment") or "").strip()
        if not comment:
            return {"ok": False, "error": "missing_comment"}
        return await self._execute_mutation(db=db, session=session, payload=payload)


class JiraTaskAssignAdapter(_JiraTaskMutationAdapter):
    provider = "jira"
    capability_id = "workitem_assign"

    async def execute(
        self,
        *,
        db: Any,
        session: Mapping[str, Any],
        payload: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> MutableMapping[str, Any]:
        _ = context
        assignee_hint = str(
            payload.get("assignee")
            or payload.get("assignee_value")
            or payload.get("assignee_account_id")
            or ""
        ).strip()
        if not assignee_hint:
            return {"ok": False, "error": "missing_assignee"}

        project_id = str(payload.get("project_id") or session.get("project_id") or "").strip()
        if not project_id:
            return {"ok": False, "error": "missing_project_id"}
        project = await db.projects.find_one({"project_id": project_id})
        if not project:
            return {"ok": False, "error": "project_not_found", "project_id": project_id}

        jira_integration = ((project.get("integrations") or {}).get("jira") or {})
        if not isinstance(jira_integration, dict) or not jira_integration.get("access_token"):
            return {"ok": False, "error": "jira_integration_missing", "project_id": project_id}

        access_token = await _resolve_jira_access_token(
            db=db,
            project_id=project_id,
            jira_integration=jira_integration,
        )
        cloud_id = str(jira_integration.get("cloud_id") or "").strip()
        assignee_account_id = await _resolve_assignee_account_id(
            access_token=access_token,
            cloud_id=cloud_id,
            project=project,
            session=session,
            assignee_hint=assignee_hint,
        )
        if not assignee_account_id:
            return {"ok": False, "error": "assignee_not_found", "assignee": assignee_hint}

        normalized_payload: Dict[str, Any] = dict(payload or {})
        normalized_payload["project_id"] = project_id
        normalized_payload["assignee_account_id"] = assignee_account_id
        return await self._execute_mutation(db=db, session=session, payload=normalized_payload)


def get_jira_cadence_adapters() -> list[Any]:
    """Return Jira-built-in Cadence adapters."""
    return [
        JiraTaskUpdateStatusAdapter(),
        JiraTaskAddCommentAdapter(),
        JiraTaskAssignAdapter(),
    ]
