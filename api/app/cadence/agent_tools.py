"""Cadence tool wrappers for SharedAgentRuntime.

Five CortexTool implementations that wrap existing CadenceActionAdapters.
Each tool reads fresh session state from DB before executing to prevent
stale-state issues in a multi-turn LLM loop.

These tools are registered in a Cadence-specific CortexToolRegistry and
used exclusively by the Cadence SharedAgentRuntime instance.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel

from app.cadence.action_adapters.registry import cadence_action_adapter_registry
from app.cadence.session_store import (
    SOURCE,
    ENTITY_TYPE,
    move_to_next_task,
    complete_session,
)
from app.core.config import settings
from app.core.database import get_database
from app.projects.recommendations.recommendation_orchestrator import mark_project_task_dirty
from app.cortex.tools.base import CortexTool, IdempotencyClass, ToolSpec
from app.sessions.repository import SessionRepository

logger = logging.getLogger("app.cadence.agent_tools")


async def _load_fresh_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Load the latest session document from project-scoped Brain DB.

    Sessions are stored in Brain DB project-scoped collections via SessionRepository,
    not in the main DB cadence_sessions collection.
    """
    db = get_database()
    if db is None:
        return None
    repo = SessionRepository(db=db)
    return await repo.get_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
    )


def _extract_session_id(execution_context: Optional[Dict[str, Any]]) -> str:
    return str((execution_context or {}).get("session_id") or "").strip()


def _extract_project_id(execution_context: Optional[Dict[str, Any]]) -> str:
    return str((execution_context or {}).get("project_id") or "").strip()


def _extract_lease_token(execution_context: Optional[Dict[str, Any]]) -> Optional[int]:
    raw = (execution_context or {}).get("lease_token")
    try:
        token = int(raw)
    except Exception:
        return None
    return token if token > 0 else None


def _mark_recommendation_dirty_best_effort(*, project_id: str, task_key: str) -> None:
    clean_project = str(project_id or "").strip()
    clean_task = str(task_key or "").strip()
    if not clean_project or not clean_task:
        return
    try:
        mark_project_task_dirty(clean_project, clean_task)
    except Exception as exc:
        logger.warning(
            "cadence_recommendation_dirty_mark_failed project_id=%s task_key=%s error=%s",
            clean_project,
            clean_task,
            exc,
        )


def _normalize_status(raw: str) -> str:
    """Lowercase, collapse whitespace/hyphens/underscores for status comparison."""
    return raw.strip().lower().replace(" ", "_").replace("-", "_")


async def _persist_task_field(
    session_id: str,
    task_index: int,
    field: str,
    value: str,
    *,
    lease_token: Optional[int] = None,
) -> bool:
    """Direct `$set` on a single task-level field without touching session status.

    Uses the same SessionRepository path as ``_load_fresh_session`` so reads
    and writes go through the same DB/collection resolution.
    """
    db = get_database()
    if db is None:
        return False
    enforce = bool(getattr(settings, "cadence_lease_enforce", False))
    token_value = int(lease_token or 0)
    if token_value <= 0 and enforce:
        logger.warning(
            "cadence_persist_task_field_missing_lease session=%s field=%s",
            session_id,
            field,
        )
        return False
    repo = SessionRepository(db=db)
    filter_extension = {"lease_version": token_value} if token_value > 0 else None
    return await repo.update_session_by_id(
        entity_type=ENTITY_TYPE,
        source=SOURCE,
        session_id=session_id,
        filter_extension=filter_extension,
        update_doc={"$set": {f"tasks.{task_index}.{field}": value}},
    )


# ---------------------------------------------------------------------------
# Input models — give the LLM an explicit parameter schema for write tools
# ---------------------------------------------------------------------------

class CadenceUpdateStatusInput(BaseModel):
    status: str
    user_phrase: str = ""
    task_key: str = ""


class CadenceAddCommentInput(BaseModel):
    comment: str
    task_key: str = ""


class CadenceAssignTaskInput(BaseModel):
    assignee: str
    task_key: str = ""


# ---------------------------------------------------------------------------
# Tool 1 — Update task status
# ---------------------------------------------------------------------------

class CadenceUpdateTaskStatusTool(CortexTool):
    """Update the status of the current check-in task via the Jira adapter."""

    input_model = CadenceUpdateStatusInput

    spec = ToolSpec(
        name="cadence_update_task_status",
        description=(
            "Update the status of the current Cadence check-in task. "
            "Pass 'status' as the resolved canonical value (e.g., done, in_progress, blocked, in_review, todo) "
            "and 'user_phrase' as the user's original words describing the status."
        ),
        provider="cadence",
        capabilities=["cadence_task_update"],
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute_with_context(
        self,
        args: Dict[str, Any],
        execution_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        session_id = _extract_session_id(execution_context)
        if not session_id:
            return {"ok": False, "error": "missing_session_id"}

        session = await _load_fresh_session(session_id)
        if not session:
            return {"ok": False, "error": "session_not_found"}

        db = get_database()
        status = str(args.get("status") or "").strip()
        task_key = str(args.get("task_key") or "").strip()
        user_phrase = str(args.get("user_phrase") or "").strip()
        lease_token = _extract_lease_token(execution_context)
        task_index = int(session.get("current_task_index") or 0)
        tasks = list(session.get("tasks") or [])
        current_task = tasks[task_index] if task_index < len(tasks) else {}
        resolved_task_key = task_key or str(current_task.get("external_id") or "").strip()
        project_id = _extract_project_id(execution_context) or str(session.get("project_id") or "").strip()
        current_status = str(current_task.get("status") or "").strip()

        # ── Same-status guard ──────────────────────────────────────────
        # If the requested status matches the task's existing status,
        # treat the user's input as a comment instead of a redundant
        # status transition (avoids "updated to in_progress" when
        # already In Progress).
        if _normalize_status(status) == _normalize_status(current_status):
            comment_text = user_phrase or f"Status unchanged: {status}"
            logger.info(
                "cadence_status_same_as_current session=%s task_index=%s status=%s treating_as_comment=true",
                session_id, task_index, status,
            )
            try:
                comment_result = await cadence_action_adapter_registry.dispatch(
                    provider="jira",
                    capability_id="workitem_add_comment",
                    db=db,
                    session=session,
                    payload={"comment": comment_text, "task_key": task_key},
                    context=execution_context or {},
                )
            except Exception as exc:
                logger.error("cadence_update_task_status same-status comment failed: %s", exc)
                comment_result = {"ok": False, "error": str(exc)}

            if comment_result.get("ok") and comment_text:
                await _persist_task_field(
                    session_id,
                    task_index,
                    "comment",
                    comment_text,
                    lease_token=lease_token,
                )
                _mark_recommendation_dirty_best_effort(
                    project_id=project_id,
                    task_key=resolved_task_key,
                )

            return {
                "ok": True,
                "status_updated": False,
                "same_status": True,
                "comment_ok": comment_result.get("ok", False),
            }

        # ── Normal status transition ───────────────────────────────────
        try:
            result = await cadence_action_adapter_registry.dispatch(
                provider="jira",
                capability_id="workitem_update_status",
                db=db,
                session=session,
                payload={"status": status, "task_key": task_key},
                context=execution_context or {},
            )
        except Exception as exc:
            logger.error("cadence_update_task_status failed: %s", exc)
            result = {"ok": False, "error": str(exc)}

        # If the status transition was rejected or not applied by Jira, fall back to
        # adding a comment so the user's intent is still captured on the task.
        if not (result.get("ok") and result.get("status_updated", True)):
            fallback_text = user_phrase or status
            comment = f"Status note: {fallback_text} (transition not available in Jira)"
            try:
                fresh_session = await _load_fresh_session(session_id)
                comment_result = await cadence_action_adapter_registry.dispatch(
                    provider="jira",
                    capability_id="workitem_add_comment",
                    db=db,
                    session=fresh_session,
                    payload={"comment": comment, "task_key": task_key},
                    context=execution_context or {},
                )
                # Persist fallback comment to session document.
                await _persist_task_field(
                    session_id,
                    task_index,
                    "comment",
                    comment,
                    lease_token=lease_token,
                )
                if comment_result.get("ok"):
                    _mark_recommendation_dirty_best_effort(
                        project_id=project_id,
                        task_key=resolved_task_key,
                    )
                return {
                    "ok": True,
                    "status_updated": False,
                    "comment_fallback": True,
                    "comment_ok": comment_result.get("ok", False),
                    "original_error": result.get("error"),
                }
            except Exception as fallback_exc:
                logger.error("cadence_update_task_status comment fallback failed: %s", fallback_exc)
                return {
                    "ok": True,
                    "status_updated": False,
                    "comment_fallback": False,
                    "original_error": result.get("error"),
                }

        # Persist status change to session task (task-level only, no session status change).
        await _persist_task_field(
            session_id,
            task_index,
            "updated_status",
            status,
            lease_token=lease_token,
        )
        if result.get("ok"):
            _mark_recommendation_dirty_best_effort(
                project_id=project_id,
                task_key=resolved_task_key,
            )
        logger.info(
            "cadence_session_status_persisted session=%s task_index=%s status=%s",
            session_id, task_index, status,
        )

        return result


# ---------------------------------------------------------------------------
# Tool 2 — Add comment
# ---------------------------------------------------------------------------

class CadenceAddCommentTool(CortexTool):
    """Add a comment to the current check-in task via the Jira adapter."""

    input_model = CadenceAddCommentInput

    spec = ToolSpec(
        name="cadence_add_comment",
        description=(
            "Add a comment to the current Cadence check-in task. "
            "Use this when the user provides notes or context about their progress."
        ),
        provider="cadence",
        capabilities=["cadence_task_comment"],
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute_with_context(
        self,
        args: Dict[str, Any],
        execution_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        session_id = _extract_session_id(execution_context)
        if not session_id:
            return {"ok": False, "error": "missing_session_id"}

        session = await _load_fresh_session(session_id)
        if not session:
            return {"ok": False, "error": "session_not_found"}

        db = get_database()
        comment_text = str(args.get("comment") or "").strip()
        lease_token = _extract_lease_token(execution_context)
        task_index = int(session.get("current_task_index") or 0)
        tasks = list(session.get("tasks") or [])
        current_task = tasks[task_index] if task_index < len(tasks) else {}
        task_key = str(args.get("task_key") or "").strip() or str(current_task.get("external_id") or "").strip()
        project_id = _extract_project_id(execution_context) or str(session.get("project_id") or "").strip()

        try:
            result = await cadence_action_adapter_registry.dispatch(
                provider="jira",
                capability_id="workitem_add_comment",
                db=db,
                session=session,
                payload={
                    "comment": comment_text,
                    "task_key": task_key,
                },
                context=execution_context or {},
            )
        except Exception as exc:
            logger.error("cadence_add_comment failed: %s", exc)
            return {"ok": False, "error": str(exc)}

        # Persist comment to session task (task-level only, no session status change).
        if result.get("ok") and comment_text:
            await _persist_task_field(
                session_id,
                task_index,
                "comment",
                comment_text,
                lease_token=lease_token,
            )
            _mark_recommendation_dirty_best_effort(
                project_id=project_id,
                task_key=task_key,
            )
            logger.info(
                "cadence_session_comment_persisted session=%s task_index=%s",
                session_id, task_index,
            )

        return result


# ---------------------------------------------------------------------------
# Tool 3 — Advance to next task
# ---------------------------------------------------------------------------

class CadenceAssignTaskTool(CortexTool):
    """Assign the current check-in task via the Jira adapter."""

    input_model = CadenceAssignTaskInput

    spec = ToolSpec(
        name="cadence_assign_task",
        description=(
            "Assign the current Cadence check-in task. "
            "Use this when the user asks to assign or reassign the task."
        ),
        provider="cadence",
        capabilities=["cadence_task_assign"],
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute_with_context(
        self,
        args: Dict[str, Any],
        execution_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        session_id = _extract_session_id(execution_context)
        if not session_id:
            return {"ok": False, "error": "missing_session_id"}

        session = await _load_fresh_session(session_id)
        if not session:
            return {"ok": False, "error": "session_not_found"}

        db = get_database()
        assignee = str(args.get("assignee") or "").strip()
        task_index = int(session.get("current_task_index") or 0)
        tasks = list(session.get("tasks") or [])
        current_task = tasks[task_index] if task_index < len(tasks) else {}
        task_key = str(args.get("task_key") or "").strip() or str(current_task.get("external_id") or "").strip()
        project_id = _extract_project_id(execution_context) or str(session.get("project_id") or "").strip()

        if not assignee:
            return {"ok": False, "error": "missing_assignee"}

        try:
            result = await cadence_action_adapter_registry.dispatch(
                provider="jira",
                capability_id="workitem_assign",
                db=db,
                session=session,
                payload={"assignee": assignee, "task_key": task_key},
                context=execution_context or {},
            )
        except Exception as exc:
            logger.error("cadence_assign_task failed: %s", exc)
            return {"ok": False, "error": str(exc)}

        if result.get("ok"):
            _mark_recommendation_dirty_best_effort(
                project_id=project_id,
                task_key=task_key,
            )
        return result


class CadenceAdvanceTaskTool(CortexTool):
    """Advance the check-in session to the next task."""

    spec = ToolSpec(
        name="cadence_advance_task",
        description=(
            "Advance the Cadence check-in session to the next task. "
            "Call this after status (and optionally comment) for the current task are recorded."
        ),
        provider="cadence",
        capabilities=["cadence_session_advance"],
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute_with_context(
        self,
        args: Dict[str, Any],
        execution_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        session_id = _extract_session_id(execution_context)
        if not session_id:
            return {"ok": False, "error": "missing_session_id"}

        session = await _load_fresh_session(session_id)
        if not session:
            return {"ok": False, "error": "session_not_found"}

        db = get_database()
        try:
            has_more = await move_to_next_task(db, session_id)
            return {"ok": True, "advanced": True, "has_more": has_more}
        except Exception as exc:
            logger.error("cadence_advance_task failed: %s", exc)
            return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Tool 4 — Ask comment follow-up
# ---------------------------------------------------------------------------

class CadenceAskCommentFollowupTool(CortexTool):
    """
    Signal that the LLM should ask the user for a comment on the current task.

    This replaces the old 'awaiting_comment' session state. The LLM calls this
    tool to indicate it needs a follow-up comment before advancing the task.
    Returns a cue text that the LLM should include in its response.
    """

    spec = ToolSpec(
        name="cadence_ask_comment_followup",
        description=(
            "Ask the user to provide a comment or notes for the current task. "
            "Use this when the user gave a status update but no comment, and a comment would be valuable."
        ),
        provider="cadence",
        capabilities=["cadence_comment_prompt"],
        idempotency=IdempotencyClass.IDEMPOTENT.value,
    )

    async def execute_with_context(
        self,
        args: Dict[str, Any],
        execution_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return {
            "ok": True,
            "prompt_text": "Any specific notes to add to this task?",
        }


# ---------------------------------------------------------------------------
# Tool 5 — Complete session
# ---------------------------------------------------------------------------

class CadenceCompleteSessionTool(CortexTool):
    """Complete the Cadence check-in session after all tasks are reviewed."""

    spec = ToolSpec(
        name="cadence_complete_session",
        description=(
            "Mark the Cadence check-in session as complete. "
            "Call this only after all tasks have been reviewed and updated."
        ),
        provider="cadence",
        capabilities=["cadence_session_complete"],
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute_with_context(
        self,
        args: Dict[str, Any],
        execution_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        session_id = _extract_session_id(execution_context)
        if not session_id:
            return {"ok": False, "error": "missing_session_id"}

        db = get_database()
        try:
            await complete_session(db, session_id, reason="agent_complete")
            return {"ok": True, "completed": True}
        except Exception as exc:
            logger.error("cadence_complete_session failed: %s", exc)
            return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Cadence-specific tool registry and runtime
# ---------------------------------------------------------------------------

def build_cadence_tool_registry():
    """Build and return a CortexToolRegistry with only Cadence tools registered."""
    from app.cortex.tools.registry import CortexToolRegistry

    registry = CortexToolRegistry()
    registry.register(CadenceUpdateTaskStatusTool())
    registry.register(CadenceAddCommentTool())
    registry.register(CadenceAssignTaskTool())
    registry.register(CadenceAdvanceTaskTool())
    registry.register(CadenceAskCommentFollowupTool())
    registry.register(CadenceCompleteSessionTool())
    return registry


def _build_cadence_tool_descriptors() -> list:
    """Return prompt-friendly descriptors for all Cadence tools."""
    registry = build_cadence_tool_registry()
    return registry.visible_tool_descriptors(role=None, connected_integrations=[])


def _build_cadence_executor():
    """Build an AgentExecutor wired to the Cadence-only tool registry."""
    from app.agent_runtime.executor import AgentExecutor

    return AgentExecutor(tool_registry=build_cadence_tool_registry())


# Cadence-specific runtime — separate from the Cortex shared_agent_runtime singleton.
# Uses an AgentExecutor wired to cadence tools only; Cortex tools are never visible here.
from app.agent_runtime.runtime import SharedAgentRuntime  # noqa: E402

cadence_agent_runtime = SharedAgentRuntime(executor=_build_cadence_executor())
