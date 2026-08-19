"""Cortex orchestrator baseline flow for Phase 4."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import json
import logging
import re
import traceback
from time import perf_counter
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import uuid4

from app.core.config import settings
from app.core.pending_interactions import (
    create_pending_interaction,
    find_pending_interaction,
    resolve_pending_interaction,
)
from app.cortex.domain.normalization import resolve_enum_alias
from app.core.database import get_database
from app.cortex.domain.tasks.query_intent import (
    is_task_read_self_scope_request,
    is_task_write_intent,
    parse_task_read_intent,
)
from app.agent_runtime.executor import AgentExecutor, ExecutorResult
from app.cortex.plans.executor import ActionPlanExecutor
from app.cortex.plans.graph_builder import build_execution_graph
from app.cortex.plans.graph_models import GraphNodeKind
from app.cortex.plans.runtime_gates import dependency_gate, predicate_gate
from app.cortex.clarification_formatting import build_missing_fields_response
from app.cortex.plans.models import (
    ActionPlan,
    ActionPlanExecutionResult,
    PlanExecutionStatus,
    action_plan_from_dict,
    action_plan_to_dict,
)
from app.cortex.plans.planner import ActionPlanBuilder
from app.cortex.prompt_builder import CortexPromptBuilder, PromptBuildInput
from app.cortex.proposals.models import ProposedToolCall, ToolProposal
from app.cortex.proposals.service import CortexProposalService
from app.cortex.response_guard import CortexResponseGuard
from app.cortex.routing.capability_resolver import CapabilityResolver
from app.cortex.response import CortexResponseService, ResponsePayload
from app.cortex.sessions import CortexSession, CortexSessionManager, build_session_key
from app.cortex.tools.base import IdempotencyClass
from app.cortex.tools.contracts import (
    canonical_aliases_for_tool,
    canonicalize_tool_args,
)
from app.cortex.tools.registry import CortexToolRegistry
from app.domain.capabilities.materialization import (
    build_capability_context,
    materialize_visible_tool_descriptors,
)
from app.domain.parsing.registry import message_for_reason_code
from app.domain.parsing.service import (
    build_clarification_payload,
    enforce_mutation_gate,
    parse_with_runtime_adapter,
)
from app.domain.scope import (
    SCOPE_GATE_GREETING,
    SCOPE_GATE_OUT_OF_SCOPE,
    SCOPE_GATE_PROJECT_RELATED,
    ScopeGateDecision,
    ScopeLlmClassifier,
    build_scope_refusal_response,
    classify_project_scope_gate,
)
from app.domain.scope import registry as scope_registry
from app.sessions.repository import SessionRepository
from app.domain.capabilities.deterministic.service import deterministic_interpretation_service
from app.domain.capabilities.deterministic.pattern_registry import list_action_patterns
from app.agent_runtime.context import AgentContext
from app.agent_runtime.runtime import shared_agent_runtime
from app.integrations.slack.context_footer import (
    append_project_context_footer,
    build_project_context_footer,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrchestratorResult:
    """High-level result emitted by Cortex orchestrator."""

    ok: bool
    response_text: Optional[str] = None
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class CortexOrchestrator:
    """Coordinates ingress, session, prompt, executor, and response stages."""

    CONFIRMATION_TIMEOUT_SECONDS = 5 * 60
    QUESTION_TIMEOUT_SECONDS = 10 * 60
    SYSTEM_MANAGED_TOOL_FIELDS = {"operation_id"}
    SAFE_EXPLICIT_ID_WRITE_TOOLS = {
        "jira_update_status",
        "jira_add_comment",
        "jira_update_description",
    }
    TARGET_REF_ARG_FIELDS = ("external_id", "task_id", "issue_id", "action_item_id", "entity_id", "id")
    FASTPATH_ANALYSIS_QUERY_MARKERS = (
        "for each",
        "per member",
        "by member",
        "breakdown",
        "distribution",
        "across team",
        "who has",
    )
    FASTPATH_TRUNCATION_HINT = (
        "This result is long and currently summarized. "
        "Ask a more specific query (for example, by member, status, priority, or due window) for a focused view."
    )
    WORK_ITEM_LIST_FOLLOWUP_PROMPT = (
        "Would you like me to take any action on these work items, or run another query?"
    )
    WORK_ITEM_LIST_DEFAULT_INTRO = "Here's what I found."

    def __init__(
        self,
        *,
        session_manager: Optional[CortexSessionManager] = None,
        prompt_builder: Optional[CortexPromptBuilder] = None,
        executor: Optional[AgentExecutor] = None,
        response_service: Optional[CortexResponseService] = None,
        tool_registry: Optional[CortexToolRegistry] = None,
        plan_builder: Optional[ActionPlanBuilder] = None,
        plan_executor: Optional[ActionPlanExecutor] = None,
        proposal_service: Optional[CortexProposalService] = None,
        scope_classifier: Optional[ScopeLlmClassifier] = None,
    ) -> None:
        self.session_manager = session_manager or CortexSessionManager()
        self.prompt_builder = prompt_builder or CortexPromptBuilder()
        self.executor = executor or AgentExecutor()
        self.response_service = response_service or CortexResponseService()
        self.tool_registry = tool_registry or CortexToolRegistry()
        self.capability_resolver = CapabilityResolver(tool_registry=self.tool_registry)
        self.plan_builder = plan_builder or ActionPlanBuilder()
        self.plan_executor = plan_executor or ActionPlanExecutor(executor=self.executor)
        self.proposal_service = proposal_service or CortexProposalService()
        self.scope_classifier = scope_classifier or ScopeLlmClassifier()
        if not self.tool_registry.list_names():
            self.tool_registry.register_launch_toolset()

    async def handle_turn(self, payload: Dict[str, Any]) -> OrchestratorResult:
        """
        Run one Cortex turn through the Phase 4 baseline pipeline:
        ingress -> session -> prompt -> executor handoff -> respond -> persist.
        """
        ingress = payload.get("ingress") or {}
        raw_event = payload.get("raw_event") or {}
        missing = self._validate_ingress(ingress)
        if missing:
            return OrchestratorResult(ok=False, error=f"missing_ingress_fields:{','.join(missing)}")

        source = str(ingress["source"])
        project_id = str(ingress["project_id"])
        user_id = str(ingress["user_id"])
        session_anchor = str(ingress["session_anchor"])
        channel_id = str(ingress["channel_id"])
        thread_ts = ingress.get("thread_ts")
        user_message = str(ingress["message_text"]).strip()
        ingress_metadata = ingress.get("metadata") if isinstance(ingress.get("metadata"), dict) else {}
        actor_email = str(ingress_metadata.get("actor_email") or "").strip().lower() or None
        member_role = str(ingress_metadata.get("member_role") or "").strip().lower() or None
        ingress_runtime_target_refs = [
            str(item or "").strip().upper()
            for item in (ingress_metadata.get("resume_target_refs") or [])
            if str(item or "").strip()
        ]
        ingress_member_candidates = self._normalize_member_candidates(
            ingress_metadata.get("member_candidates")
        )
        message_context_source = (
            str(ingress_metadata.get("message_context_source") or "").strip().lower()
            or "message_text"
        )
        run_id = str(uuid4())
        started_at = datetime.utcnow()

        try:
            session_key = build_session_key(
                project_id=project_id,
                user_id=user_id,
                source=source,
                anchor=session_anchor,
            )
        except Exception as exc:
            return OrchestratorResult(ok=False, error=f"invalid_session_key:{exc}")

        try:
            session = await self.session_manager.load_or_create_session(
                session_key=session_key,
                project_id=project_id,
                user_id=user_id,
                source=source,
                anchor=session_anchor,
            )
        except Exception as exc:
            logger.exception(
                "Cortex session bootstrap failed | project_id=%s user_id=%s source=%s session_anchor=%s",
                project_id,
                user_id,
                source,
                session_anchor,
            )
            traceback.print_exc()
            return OrchestratorResult(
                ok=False,
                error=f"session_bootstrap_failed:{type(exc).__name__}:{exc}",
            )

        pending_resolution = await self._handle_pending_confirmation_turn(
            session_key=session_key,
            pending_confirmation=session.pending_confirmation,
            user_message=user_message,
        )
        if pending_resolution is not None:
            delivery: Dict[str, Any] = {}
            if pending_resolution.response_text:
                delivery = await self._deliver_response(
                    text=pending_resolution.response_text,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    metadata={
                        "source": source,
                        "project_id": project_id,
                        "team_id": ingress.get("team_id"),
                        "session_key": session_key,
                    },
                )
            metadata = dict(pending_resolution.metadata or {})
            metadata.update(
                {
                    "run_id": run_id,
                    "session_key": session_key,
                    "delivered": bool(delivery.get("ok", False)),
                    "delivery": delivery,
                }
            )
            return OrchestratorResult(
                ok=pending_resolution.ok,
                response_text=None if metadata.get("delivered") else pending_resolution.response_text,
                error=pending_resolution.error,
                metadata=metadata,
            )

        pending_question_resolution = await self._handle_pending_question_turn(
            session_key=session_key,
            pending_question=session.pending_question,
            user_message=user_message,
        )
        if pending_question_resolution is not None:
            delivery: Dict[str, Any] = {}
            if pending_question_resolution.response_text:
                delivery = await self._deliver_response(
                    text=pending_question_resolution.response_text,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    metadata={
                        "source": source,
                        "project_id": project_id,
                        "team_id": ingress.get("team_id"),
                        "session_key": session_key,
                    },
                )
            metadata = dict(pending_question_resolution.metadata or {})
            metadata.update(
                {
                    "run_id": run_id,
                    "session_key": session_key,
                    "delivered": bool(delivery.get("ok", False)),
                    "delivery": delivery,
                }
            )
            return OrchestratorResult(
                ok=pending_question_resolution.ok,
                response_text=None if metadata.get("delivered") else pending_question_resolution.response_text,
                error=pending_question_resolution.error,
                metadata=metadata,
            )

        if bool(getattr(settings, "promarshal_global_scope_policy_enabled", False)):
            ingress_scope_classification = str(
                ingress_metadata.get("scope_gate_classification") or ""
            ).strip().lower()
            ingress_scope_reason_code = str(
                ingress_metadata.get("scope_gate_reason_code") or ""
            ).strip().lower()
            ingress_scope_signals = ingress_metadata.get("scope_gate_signals")
            gate_decision = classify_project_scope_gate(user_message)
            if ingress_scope_classification:
                gate_signals = (
                    [str(item).strip() for item in ingress_scope_signals if str(item).strip()]
                    if isinstance(ingress_scope_signals, list)
                    else []
                )
                gate_decision = ScopeGateDecision(
                    classification=ingress_scope_classification,
                    reason_code=ingress_scope_reason_code or gate_decision.reason_code,
                    signals=gate_signals or gate_decision.signals,
                )

            stage1_strength = self._scope_stage1_strength(gate_decision)
            if gate_decision.classification == SCOPE_GATE_GREETING:
                await self._append_event(
                    session_key=session_key,
                    event_type="scope_stage2_evaluation",
                    payload={
                        "scope_stage2_applied": False,
                        "scope_stage2_downgrade_blocked": False,
                        "scope_stage1_strength": stage1_strength,
                        "scope_stage1_classification": gate_decision.classification,
                        "scope_stage1_reason_code": gate_decision.reason_code,
                        "stage2_skip_reason": "greeting_pass_through",
                    },
                )

            if gate_decision.classification == SCOPE_GATE_OUT_OF_SCOPE and not is_task_write_intent(user_message):
                refusal_text = build_scope_refusal_response()
                delivery = await self._deliver_response(
                    text=refusal_text,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    metadata={
                        "source": source,
                        "project_id": project_id,
                        "team_id": ingress.get("team_id"),
                        "session_key": session_key,
                    },
                )
                await self._append_event(
                    session_key=session_key,
                    event_type="scope_policy_block",
                    payload={
                        "reason_code": gate_decision.reason_code,
                        "user_message": user_message,
                        "stage": "deterministic_gate",
                    },
                )
                metadata = {
                    "run_id": run_id,
                    "session_key": session_key,
                    "scope_policy": {
                        "classification": gate_decision.classification,
                        "reason_code": gate_decision.reason_code,
                        "stage": "deterministic_gate",
                    },
                    "delivered": bool(delivery.get("ok", False)),
                    "delivery": delivery,
                }
                return OrchestratorResult(
                    ok=False,
                    response_text=None if metadata.get("delivered") else refusal_text,
                    error="out_of_scope",
                    metadata=metadata,
                )

            if bool(getattr(settings, "scope_two_stage_enabled", True)) and bool(
                getattr(settings, "scope_llm_classifier_enabled", True)
            ) and gate_decision.classification != SCOPE_GATE_GREETING:
                llm_scope = await self.scope_classifier.classify(
                    executor=self.executor,
                    user_message=user_message,
                    gate_decision={
                        "classification": gate_decision.classification,
                        "reason_code": gate_decision.reason_code,
                        "signals": gate_decision.signals,
                    },
                    context={
                        "project_id": project_id,
                        "user_id": user_id,
                        "session_key": session_key,
                        "source": source,
                        "run_id": run_id,
                        "role": member_role or "",
                    },
                )
                if llm_scope is not None:
                    downgrade_attempted = llm_scope.scope_classification == "out_of_scope"
                    write_intent = is_task_write_intent(user_message)
                    strong_stage1_match = self._is_strong_project_related_scope_gate_decision(gate_decision)
                    downgrade_blocked = bool(downgrade_attempted and strong_stage1_match and not write_intent)
                    await self._append_event(
                        session_key=session_key,
                        event_type="scope_stage2_evaluation",
                        payload={
                            "scope_stage2_applied": True,
                            "scope_stage2_downgrade_blocked": downgrade_blocked,
                            "scope_stage1_strength": stage1_strength,
                            "scope_stage1_classification": gate_decision.classification,
                            "scope_stage1_reason_code": gate_decision.reason_code,
                            "scope_stage2_classification": llm_scope.scope_classification,
                            "scope_stage2_reason_code": llm_scope.reason_code,
                            "scope_stage2_confidence": llm_scope.confidence,
                        },
                    )
                    if (
                        llm_scope.scope_classification == "out_of_scope"
                        and not write_intent
                        and not strong_stage1_match
                    ):
                        refusal_text = build_scope_refusal_response()
                        delivery = await self._deliver_response(
                            text=refusal_text,
                            channel_id=channel_id,
                            thread_ts=thread_ts,
                            metadata={
                                "source": source,
                                "project_id": project_id,
                                "team_id": ingress.get("team_id"),
                                "session_key": session_key,
                            },
                        )
                        await self._append_event(
                            session_key=session_key,
                            event_type="scope_policy_block",
                            payload={
                                "reason_code": llm_scope.reason_code,
                                "user_message": user_message,
                                "stage": "llm_classifier",
                                "detail_classification": llm_scope.detail_classification,
                                "confidence": llm_scope.confidence,
                            },
                        )
                        metadata = {
                            "run_id": run_id,
                            "session_key": session_key,
                            "scope_policy": {
                                "classification": llm_scope.scope_classification,
                                "reason_code": llm_scope.reason_code,
                                "detail_classification": llm_scope.detail_classification,
                                "confidence": llm_scope.confidence,
                                "stage": "llm_classifier",
                            },
                            "delivered": bool(delivery.get("ok", False)),
                            "delivery": delivery,
                        }
                        return OrchestratorResult(
                            ok=False,
                            response_text=None if metadata.get("delivered") else refusal_text,
                            error="out_of_scope",
                            metadata=metadata,
                        )

        await self._append_event(
            session_key=session_key,
            event_type="user_message",
            payload={
                "text": user_message,
                "source": source,
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "raw_event_type": raw_event.get("type"),
            },
        )

        project_context = await self._load_project_context(project_id=project_id)
        role = self._resolve_caller_role(
            project_context=project_context,
            ingress_user_id=user_id,
            ingress_actor_email=actor_email,
            ingress_role_hint=member_role,
        )
        actor_member = self._resolve_actor_member(
            project_context=project_context,
            ingress_user_id=user_id,
            ingress_actor_email=actor_email,
        )
        actor_member_integration_ids = (
            actor_member.get("integration_ids")
            if isinstance(actor_member, dict)
            and isinstance(actor_member.get("integration_ids"), dict)
            else {}
        )
        actor_jira_account_id = str(actor_member_integration_ids.get("jira_account_id") or "").strip() or None
        actor_email_resolved = str((actor_member or {}).get("email") or "").strip().lower() or actor_email
        self_scope_requested = is_task_read_self_scope_request(user_message)
        connected_integrations = self._connected_integration_names(project_context)
        capability_context = build_capability_context(project_context=project_context)
        project_context = {
            **project_context,
            "active_provider": capability_context.active_provider,
            "capability_context": capability_context.as_dict(),
        }
        resolved_member_candidates = self._resolve_member_candidates_for_turn(
            project_context=project_context,
            user_message=user_message,
            actor_email=actor_email,
            actor_user_id=user_id,
            ingress_candidates=ingress_member_candidates,
        )
        visible_tool_descriptors = materialize_visible_tool_descriptors(
            visible_tools=self.tool_registry.visible_tool_descriptors(
                role=role,
                connected_integrations=connected_integrations,
            ),
            capability_context=capability_context,
        )
        launch_tool_descriptors = list(visible_tool_descriptors)
        force_tool_choice = self._should_force_required_tool_choice(
            user_message=user_message,
            connected_integrations=connected_integrations,
        )
        session_snapshot = self._build_session_snapshot(session=session)

        fastpath_intent = None
        if bool(getattr(settings, "cortex_task_read_fastpath_enabled", True)):
            fastpath_intent = self._resolve_task_read_fastpath_intent(
                user_message=user_message,
                connected_integrations=connected_integrations,
                launch_tool_descriptors=launch_tool_descriptors,
            )
        if fastpath_intent is not None:
            request_meta = {
                "deterministic_fastpath": True,
                "fastpath_tool_name": "jira_search_work_items",
                "registered_tool_count": len(launch_tool_descriptors),
                "tool_count": 1,
                "prompt_chars": 0,
                "prompt_meta": {"source": "deterministic_fastpath"},
            }
            await self._persist_turn_metadata(
                session_key=session_key,
                run_id=run_id,
                status="started",
                source=source,
                project_id=project_id,
                user_id=user_id,
                user_message=user_message,
                request_meta=request_meta,
                executor_meta={},
                delivery_meta={},
                response_text=None,
                started_at=started_at,
                finished_at=None,
                error=None,
            )
            await self._append_event(
                session_key=session_key,
                event_type="deterministic_fastpath_request",
                payload={
                    "run_id": run_id,
                    "tool_name": "jira_search_work_items",
                    "intent_args": dict(fastpath_intent.args or {}),
                },
            )
            fastpath_execution_context: Dict[str, Any] = {
                "project_id": project_id,
                "user_id": user_id,
                "session_key": session_key,
                "source": source,
                "run_id": run_id,
                "latest_user_message": user_message,
                "user_message": user_message,
                "actor_email": actor_email_resolved,
                "actor_identity": {
                    "external_user_id": user_id,
                    "canonical_email": actor_email_resolved,
                    "jira_account_id": actor_jira_account_id,
                },
                "self_scope_requested": self_scope_requested,
                "role": role,
                "connected_integrations": connected_integrations,
                "member_candidates": resolved_member_candidates,
                "session_snapshot": session_snapshot,
                "session_snapshot_target_refs": list(session_snapshot.get("target_refs") or []),
            }
            tool_started = perf_counter()
            fastpath_result = await self.executor.execute_tool_call(
                tool_name="jira_search_work_items",
                args=dict(fastpath_intent.args or {}),
                execution_context=fastpath_execution_context,
            )
            tool_ms = int((perf_counter() - tool_started) * 1000)
            fastpath_ok = bool((fastpath_result or {}).get("ok"))
            tool_errors = []
            if not fastpath_ok:
                tool_errors = [
                    {
                        "tool_name": "jira_search_work_items",
                        "error_code": str((fastpath_result or {}).get("error_code") or "").strip(),
                        "error_class": str((fastpath_result or {}).get("error_class") or "").strip(),
                        "user_message": str((fastpath_result or {}).get("user_message") or "").strip(),
                    }
                ]
            fastpath_executor_meta: Dict[str, Any] = {
                "provider": "deterministic",
                "model": "deterministic_fastpath",
                "attempt": 1,
                "latency_ms": tool_ms,
                "llm_path": "deterministic_fastpath",
                "raw_responses": 0,
                "interruptions": 0,
                "approval_interruptions": [],
                "last_response_id": None,
                "runtime_tool_count": 1,
                "tool_choice": "deterministic_fastpath",
                "tool_calls_count": 1,
                "tool_names_called": ["jira_search_work_items"],
                "tool_success_count": 1 if fastpath_ok else 0,
                "tool_error_count": 0 if fastpath_ok else 1,
                "tool_errors": tool_errors,
                "committed_actions": [],
                "target_refs": [],
                "tool_ms_total": tool_ms,
                "tool_ms_by_name": {"jira_search_work_items": tool_ms},
            }
            if fastpath_ok:
                draft_response_text = self._render_task_read_fastpath_response(
                    tool_result=fastpath_result,
                    intent_args=dict(fastpath_intent.args or {}),
                    is_count_request=bool(fastpath_intent.is_count_request),
                )
                response_text, compose_meta = await self._compose_fastpath_response_with_shared_runtime(
                    user_message=user_message,
                    draft_response_text=draft_response_text,
                    tool_result=fastpath_result,
                    source=source,
                    project_id=project_id,
                    user_id=user_id,
                    session_key=session_key,
                    role=role,
                    project_context=project_context,
                    session=session,
                    run_id=run_id,
                    actor_email_resolved=actor_email_resolved,
                    actor_jira_account_id=actor_jira_account_id,
                    self_scope_requested=self_scope_requested,
                    connected_integrations=connected_integrations,
                    message_context_source=message_context_source,
                    resolved_member_candidates=resolved_member_candidates,
                    session_snapshot=session_snapshot,
                )
                fastpath_executor_meta["latency_ms"] = tool_ms + int(compose_meta.get("latency_ms") or 0)
                fastpath_executor_meta["compose_runtime_used"] = bool(compose_meta.get("used"))
                fastpath_executor_meta["compose_runtime_latency_ms"] = int(
                    compose_meta.get("latency_ms") or 0
                )
                fastpath_executor_meta["compose_runtime_reason"] = str(
                    compose_meta.get("reason") or "none"
                ).strip()
                fastpath_items = (
                    fastpath_result.get("items")
                    if isinstance(fastpath_result.get("items"), list)
                    else []
                )
                fastpath_executor_meta["read_tool_name"] = "jira_search_work_items"
                fastpath_executor_meta["read_items_count"] = int(
                    fastpath_result.get("count") or len(fastpath_items)
                )
                fastpath_executor_meta["read_items"] = [
                    {
                        "external_id": str(item.get("external_id") or "").strip(),
                        "title": str(item.get("title") or "").strip()[:240],
                        "provider_type": str(
                            item.get("provider_type") or item.get("issue_type") or ""
                        ).strip(),
                        "status": str(item.get("status") or "").strip(),
                        "status_raw": str(item.get("status_raw") or "").strip(),
                        "status_display": str(
                            item.get("status_display") or item.get("status_raw") or ""
                        ).strip(),
                        "assignee_name": str(item.get("assignee_name") or "").strip(),
                        "due_date": str(item.get("due_date") or "").strip(),
                    }
                    for item in fastpath_items[:20]
                    if isinstance(item, dict)
                ]
                fastpath_execute_result = ExecutorResult(
                    ok=True,
                    output_text=response_text,
                    raw_response=fastpath_executor_meta,
                    error=None,
                )
                response_text = await self._compose_response_text(
                    execute_result=fastpath_execute_result,
                    response_text=response_text,
                    user_message=user_message,
                    project_id=project_id,
                    user_id=user_id,
                    tool_descriptors=[{"name": "jira_search_work_items", "idempotency": "read_only"}],
                )
                response_text = self._append_task_read_truncation_hint_if_needed(
                    response_text=response_text,
                    tool_result=fastpath_result,
                )
                delivery_started_at = perf_counter()
                delivery = await self._deliver_response(
                    text=response_text,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    metadata={
                        "source": source,
                        "project_id": project_id,
                        "team_id": ingress.get("team_id"),
                        "session_key": session_key,
                        "user_id": user_id,
                        "tool_calls_count": int(fastpath_executor_meta.get("tool_calls_count") or 0),
                    },
                )
                delivery_ms = int((perf_counter() - delivery_started_at) * 1000)
                self._log_tool_outcomes_for_debug(
                    run_id=run_id,
                    session_key=session_key,
                    execute_result=fastpath_execute_result,
                    started_at=started_at,
                    delivery_ms=delivery_ms,
                )
                await self._append_event(
                    session_key=session_key,
                    event_type="deterministic_fastpath_response",
                    payload={
                        "run_id": run_id,
                        "tool_name": "jira_search_work_items",
                        "text": response_text,
                        "executor": fastpath_executor_meta,
                        "delivery": delivery,
                    },
                )
                finished_at = datetime.utcnow()
                await self._persist_turn_metadata(
                    session_key=session_key,
                    run_id=run_id,
                    status="completed",
                    source=source,
                    project_id=project_id,
                    user_id=user_id,
                    user_message=user_message,
                    request_meta=request_meta,
                    executor_meta=fastpath_executor_meta,
                    delivery_meta=delivery,
                    response_text=response_text,
                    started_at=started_at,
                    finished_at=finished_at,
                    error=None,
                )
                return OrchestratorResult(
                    ok=True,
                    response_text=response_text,
                    metadata={
                        "run_id": run_id,
                        "session_key": session_key,
                        "deterministic_fastpath": True,
                        "delivered": bool(delivery.get("ok", False)),
                        "executor": fastpath_executor_meta,
                        "delivery": delivery,
                    },
                )

            failure_message = str((fastpath_result or {}).get("user_message") or "").strip()
            if not failure_message:
                failure_message = "I couldn't complete that task-read request right now."
            delivery = await self._deliver_response(
                text=failure_message,
                channel_id=channel_id,
                thread_ts=thread_ts,
                metadata={
                    "source": source,
                    "project_id": project_id,
                    "team_id": ingress.get("team_id"),
                    "session_key": session_key,
                    "user_id": user_id,
                    "tool_calls_count": int((execute_result.raw_response or {}).get("tool_calls_count") or 0),
                },
            )
            await self._append_event(
                session_key=session_key,
                event_type="error",
                payload={
                    "error_class": "runtime",
                    "user_message": failure_message,
                    "run_id": run_id,
                    "details": str((fastpath_result or {}).get("error_code") or "fastpath_tool_failed"),
                    "committed_actions": [],
                    "pending_actions": [],
                    "delivery": delivery,
                    "deterministic_fastpath": True,
                },
            )
            await self._emit_cortex_parse_telemetry(
                session_key=session_key,
                run_id=run_id,
                event="fastpath_executor_failure",
                reason_codes=[
                    str((fastpath_result or {}).get("error_code") or "fastpath_tool_failed").strip().lower()
                ],
                context={"error": str((fastpath_result or {}).get("error_code") or "fastpath_tool_failed")},
            )
            await self._persist_turn_metadata(
                session_key=session_key,
                run_id=run_id,
                status="failed",
                source=source,
                project_id=project_id,
                user_id=user_id,
                user_message=user_message,
                request_meta=request_meta,
                executor_meta=fastpath_executor_meta,
                delivery_meta=delivery,
                response_text=failure_message,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                error=str((fastpath_result or {}).get("error_code") or "fastpath_tool_failed"),
            )
            return OrchestratorResult(
                ok=False,
                response_text=None if delivery.get("ok") else failure_message,
                error=str((fastpath_result or {}).get("error_code") or "fastpath_tool_failed"),
                metadata={
                    "run_id": run_id,
                    "session_key": session_key,
                    "deterministic_fastpath": True,
                    "delivered": bool(delivery.get("ok", False)),
                    "delivery": delivery,
                    "executor": fastpath_executor_meta,
                },
            )

        # Stage 1 (deterministic action plan) and Stage 2 (implicit tool proposal)
        # are removed. All requests now go directly through SharedAgentRuntime (LLM loop).
        # The LLM receives pre-processed intent hints and decides tool selection itself.
        # Removed stages:
        #   Stage 1: _maybe_handle_action_plan_turn — ActionPlanBuilder, 2+ actions, no LLM response
        #   Stage 2: _maybe_handle_implicit_tool_proposal_turn — CortexProposalService, response broken

        # Pre-process: normalize + pattern match + score (~5ms, no LLM).
        # Output is injected into the system prompt as a structured hint block.
        pre_processed_interpretation = deterministic_interpretation_service.interpret(
            text=user_message,
            pattern_specs=list_action_patterns(),
        )
        pre_processed_dict = {
            "normalized_text": pre_processed_interpretation.normalized_text,
            "confidence": pre_processed_interpretation.confidence,
            "slot_coverage": pre_processed_interpretation.slot_coverage,
            "signal_strength": pre_processed_interpretation.signal_strength,
            "matches": [
                {
                    "action_type": m.action_type,
                    "capability_id": m.capability_id,
                    "tool_name": m.tool_name,
                    "extracted_slots": dict(m.extracted_slots or {}),
                    "missing_required_fields": list(m.missing_required_fields or []),
                }
                for m in (pre_processed_interpretation.matches or [])
            ],
            "missing_slots": dict(pre_processed_interpretation.missing_slots or {}),
        }

        prompt_payload = self.prompt_builder.build(
            PromptBuildInput(
                project_context=project_context,
                session_context={
                    "summary_history": session.summary_history,
                    "recent_turns": session.recent_turns,
                    "transcript": session.transcript,
                    "session_snapshot": session_snapshot,
                },
                user_message=user_message,
                visible_tools=launch_tool_descriptors,
                role=role,
                metadata={
                    "source": source,
                    "project_id": project_id,
                    "session_key": session_key,
                    "message_context_source": message_context_source,
                    "member_candidates": resolved_member_candidates,
                    "session_snapshot": session_snapshot,
                },
                pre_processed=pre_processed_dict,
            )
        )

        await self._append_event(
            session_key=session_key,
            event_type="llm_request",
            payload={
                "run_id": run_id,
                "provider": "pending",
                "model": "pending",
                "registered_tool_count": len(launch_tool_descriptors),
                "prompt_meta": prompt_payload.get("meta", {}),
                "tool_count": len(prompt_payload.get("tools", [])),
            },
        )
        await self._persist_turn_metadata(
            session_key=session_key,
            run_id=run_id,
            status="started",
            source=source,
            project_id=project_id,
            user_id=user_id,
            user_message=user_message,
            request_meta={
                "registered_tool_count": len(launch_tool_descriptors),
                "prompt_meta": prompt_payload.get("meta", {}),
                "tool_count": len(prompt_payload.get("tools", [])),
                "prompt_chars": len(str(prompt_payload.get("instructions") or "")),
            },
            executor_meta={},
            delivery_meta={},
            response_text=None,
            started_at=started_at,
            finished_at=None,
            error=None,
        )

        agent_context = AgentContext(
            user_message=user_message,
            project_id=project_id,
            user_id=user_id,
            session_key=session_key,
            source=source,
            pre_processed=pre_processed_dict,
            session_history={
                "summary_history": session.summary_history,
                "recent_turns": session.recent_turns,
                "transcript": session.transcript,
            },
            project_context=project_context,
            feature_context={
                "actor_email": actor_email_resolved,
                "actor_identity": {
                    "external_user_id": user_id,
                    "canonical_email": actor_email_resolved,
                    "jira_account_id": actor_jira_account_id,
                },
                "channel_id": channel_id,
                "thread_ts": thread_ts,
                "self_scope_requested": self_scope_requested,
                "role": role,
                "connected_integrations": connected_integrations,
                "force_tool_choice": force_tool_choice,
                "member_candidates": resolved_member_candidates,
                "message_context_source": message_context_source,
                "latest_user_message": user_message,
                "session_snapshot": session_snapshot,
                "session_snapshot_target_refs": list(session_snapshot.get("target_refs") or []),
                "runtime_target_refs": ingress_runtime_target_refs,
            },
            run_id=run_id,
        )
        execute_result = await shared_agent_runtime.run(
            context=agent_context,
            system_prompt=str(prompt_payload.get("instructions") or ""),
            tools=list(prompt_payload.get("tools") or []),
        )

        pending_confirmation = self._build_pending_confirmation_from_executor(
            execute_result=execute_result,
            run_id=run_id,
        )
        if pending_confirmation:
            await self.session_manager.set_pending_confirmation(
                session_key=session_key,
                pending_confirmation=pending_confirmation,
            )
            await self._append_event(
                session_key=session_key,
                event_type="confirmation_request",
                payload={
                    "run_id": run_id,
                    **pending_confirmation,
                },
            )
            response_text = self._build_confirmation_prompt(pending_confirmation)
            delivery = await self._deliver_response(
                text=response_text,
                channel_id=channel_id,
                thread_ts=thread_ts,
                metadata={
                    "source": source,
                    "project_id": project_id,
                    "team_id": ingress.get("team_id"),
                    "session_key": session_key,
                },
            )
            await self._persist_turn_metadata(
                session_key=session_key,
                run_id=run_id,
                status="awaiting_confirmation",
                source=source,
                project_id=project_id,
                user_id=user_id,
                user_message=user_message,
                request_meta={
                    "registered_tool_count": len(launch_tool_descriptors),
                    "prompt_meta": prompt_payload.get("meta", {}),
                    "tool_count": len(prompt_payload.get("tools", [])),
                    "prompt_chars": len(str(prompt_payload.get("instructions") or "")),
                },
                executor_meta=execute_result.raw_response or {},
                delivery_meta=delivery,
                response_text=response_text,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                error=None,
            )
            return OrchestratorResult(
                ok=True,
                response_text=response_text,
                metadata={
                    "run_id": run_id,
                    "session_key": session_key,
                    "awaiting_confirmation": True,
                    "pending_confirmation": pending_confirmation,
                    "delivery": delivery,
                },
            )

        if not execute_result.ok:
            executor_committed_actions = self._extract_committed_action_texts(
                execute_result.raw_response or {},
            )
            if executor_committed_actions:
                await self._record_committed_actions(
                    session_key=session_key,
                    action_texts=executor_committed_actions,
                )
            committed_action_texts = await self._load_committed_action_texts(
                session_key=session_key,
                baseline_actions=session.committed_actions,
            )
            pending_action_texts = self._extract_pending_action_texts(
                execute_result.raw_response or {},
            )
            failure_message = self._build_failed_turn_user_message(
                execute_result=execute_result,
                committed_action_texts=committed_action_texts,
                pending_action_texts=pending_action_texts,
            )
            provider_type_pending = await self._queue_provider_type_pending_interaction_from_execute_error(
                execute_result=execute_result,
                project_id=project_id,
                user_id=user_id,
                team_id=str(ingress.get("team_id") or "").strip() or None,
                actor_user_id=str((actor_member or {}).get("user_id") or "").strip() or user_id,
            )
            if provider_type_pending is not None:
                response_text = str(provider_type_pending.get("response_text") or failure_message).strip()
                await self._append_event(
                    session_key=session_key,
                    event_type="pending_interaction_requested",
                    payload={
                        "run_id": run_id,
                        "interaction_type": "provider_type_clarification",
                        "missing_fields": provider_type_pending.get("missing_fields") or [],
                    },
                )
                delivery = await self._deliver_response(
                    text=response_text,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    metadata={
                        "source": source,
                        "project_id": project_id,
                        "team_id": ingress.get("team_id"),
                        "session_key": session_key,
                    },
                )
                await self._persist_turn_metadata(
                    session_key=session_key,
                    run_id=run_id,
                    status="awaiting_question",
                    source=source,
                    project_id=project_id,
                    user_id=user_id,
                    user_message=user_message,
                    request_meta={
                        "registered_tool_count": len(launch_tool_descriptors),
                        "prompt_meta": prompt_payload.get("meta", {}),
                        "tool_count": len(prompt_payload.get("tools", [])),
                        "prompt_chars": len(str(prompt_payload.get("instructions") or "")),
                    },
                    executor_meta=execute_result.raw_response or {},
                    delivery_meta=delivery,
                    response_text=response_text,
                    started_at=started_at,
                    finished_at=datetime.utcnow(),
                    error=None,
                )
                return OrchestratorResult(
                    ok=True,
                    response_text=None if delivery.get("ok") else response_text,
                    metadata={
                        "run_id": run_id,
                        "session_key": session_key,
                        "awaiting_question": True,
                        "delivery": delivery,
                    },
                )
            raw_error_code = str((execute_result.raw_response or {}).get("error_code") or "").strip().lower()
            reason_code = raw_error_code or str(execute_result.error or "").strip().lower() or "invalid_input"
            delivery = await self._deliver_response(
                text=failure_message,
                channel_id=channel_id,
                thread_ts=thread_ts,
                metadata={
                    "source": source,
                    "project_id": project_id,
                    "team_id": ingress.get("team_id"),
                    "session_key": session_key,
                },
            )
            await self._append_event(
                session_key=session_key,
                event_type="error",
                payload={
                    "error_class": "runtime",
                    "user_message": failure_message,
                    "run_id": run_id,
                    "details": execute_result.error or "executor_failed",
                    "committed_actions": committed_action_texts,
                    "pending_actions": pending_action_texts,
                    "delivery": delivery,
                },
            )
            await self._emit_cortex_parse_telemetry(
                session_key=session_key,
                run_id=run_id,
                event="executor_failure",
                reason_codes=[reason_code],
                context={
                    "error": str(execute_result.error or "").strip().lower() or "executor_failed",
                    "committed_actions": len(committed_action_texts),
                    "pending_actions": len(pending_action_texts),
                },
            )
            await self._persist_turn_metadata(
                session_key=session_key,
                run_id=run_id,
                status="failed",
                source=source,
                project_id=project_id,
                user_id=user_id,
                user_message=user_message,
                request_meta={
                    "registered_tool_count": len(launch_tool_descriptors),
                    "prompt_meta": prompt_payload.get("meta", {}),
                    "tool_count": len(prompt_payload.get("tools", [])),
                    "prompt_chars": len(str(prompt_payload.get("instructions") or "")),
                },
                executor_meta=execute_result.raw_response or {},
                delivery_meta=delivery,
                response_text=failure_message,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                error=execute_result.error or "executor_failed",
            )
            return OrchestratorResult(
                ok=False,
                response_text=None if delivery.get("ok") else failure_message,
                error=execute_result.error or "executor_failed",
                metadata={
                    "run_id": run_id,
                    "session_key": session_key,
                    "delivered": bool(delivery.get("ok", False)),
                    "delivery": delivery,
                    "committed_actions": committed_action_texts,
                    "pending_actions": pending_action_texts,
                },
            )

        provider_type_pending_success = await self._queue_provider_type_pending_interaction_from_execute_error(
            execute_result=execute_result,
            project_id=project_id,
            user_id=user_id,
            team_id=str(ingress.get("team_id") or "").strip() or None,
            actor_user_id=str((actor_member or {}).get("user_id") or "").strip() or user_id,
        )
        if provider_type_pending_success is not None:
            response_text = str(
                provider_type_pending_success.get("response_text")
                or "I need one clarification before I continue."
            ).strip()
            delivery_started_at = perf_counter()
            delivery = await self._deliver_response(
                text=response_text,
                channel_id=channel_id,
                thread_ts=thread_ts,
                metadata={
                    "source": source,
                    "project_id": project_id,
                    "team_id": ingress.get("team_id"),
                    "session_key": session_key,
                },
            )
            delivery_ms = int((perf_counter() - delivery_started_at) * 1000)
            await self._append_event(
                session_key=session_key,
                event_type="pending_interaction_requested",
                payload={
                    "run_id": run_id,
                    "interaction_type": "provider_type_clarification",
                    "missing_fields": provider_type_pending_success.get("missing_fields") or [],
                },
            )
            await self._persist_turn_metadata(
                session_key=session_key,
                run_id=run_id,
                status="awaiting_question",
                source=source,
                project_id=project_id,
                user_id=user_id,
                user_message=user_message,
                request_meta={
                    "registered_tool_count": len(launch_tool_descriptors),
                    "prompt_meta": prompt_payload.get("meta", {}),
                    "tool_count": len(prompt_payload.get("tools", [])),
                    "prompt_chars": len(str(prompt_payload.get("instructions") or "")),
                },
                executor_meta=execute_result.raw_response or {},
                delivery_meta=delivery,
                response_text=response_text,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                error=None,
            )
            self._log_tool_outcomes_for_debug(
                run_id=run_id,
                session_key=session_key,
                execute_result=execute_result,
                started_at=started_at,
                delivery_ms=delivery_ms,
            )
            return OrchestratorResult(
                ok=True,
                response_text=None if delivery.get("ok") else response_text,
                metadata={
                    "run_id": run_id,
                    "session_key": session_key,
                    "awaiting_question": True,
                    "delivery": delivery,
                },
            )

        response_text = await self._compose_response_text(
            execute_result=execute_result,
            response_text=execute_result.output_text,
            user_message=user_message,
            project_id=project_id,
            user_id=user_id,
            tool_descriptors=list(prompt_payload.get("tools") or []),
        )
        delivery_started_at = perf_counter()
        delivery = await self._deliver_response(
            text=response_text,
            channel_id=channel_id,
            thread_ts=thread_ts,
            metadata={
                "source": source,
                "project_id": project_id,
                "team_id": ingress.get("team_id"),
                "session_key": session_key,
                "user_id": user_id,
                "tool_calls_count": int((execute_result.raw_response or {}).get("tool_calls_count") or 0),
            },
        )
        delivery_ms = int((perf_counter() - delivery_started_at) * 1000)
        self._log_tool_outcomes_for_debug(
            run_id=run_id,
            session_key=session_key,
            execute_result=execute_result,
            started_at=started_at,
            delivery_ms=delivery_ms,
        )

        await self._append_event(
            session_key=session_key,
            event_type="llm_response",
            payload={
                "text": response_text,
                "run_id": run_id,
                "executor": execute_result.raw_response or {},
                "delivery": delivery,
            },
        )
        finished_at = datetime.utcnow()
        await self._persist_turn_metadata(
            session_key=session_key,
            run_id=run_id,
            status="completed",
            source=source,
            project_id=project_id,
            user_id=user_id,
            user_message=user_message,
            request_meta={
                "registered_tool_count": len(launch_tool_descriptors),
                "prompt_meta": prompt_payload.get("meta", {}),
                "tool_count": len(prompt_payload.get("tools", [])),
                "prompt_chars": len(str(prompt_payload.get("instructions") or "")),
            },
            executor_meta=execute_result.raw_response or {},
            delivery_meta=delivery,
            response_text=response_text,
            started_at=started_at,
            finished_at=finished_at,
            error=None,
        )

        return OrchestratorResult(
            ok=True,
            response_text=response_text,
            metadata={
                "run_id": run_id,
                "session_key": session_key,
                "delivered": bool(delivery.get("ok", False)),
                "executor": execute_result.raw_response or {},
                "delivery": delivery,
            },
        )

    async def _compose_response_text(
        self,
        *,
        execute_result: ExecutorResult,
        response_text: Optional[str],
        user_message: Optional[str] = None,
        project_id: str,
        user_id: str,
        tool_descriptors: List[Dict[str, Any]],
    ) -> str:
        text = str(response_text or "").strip()
        if not text:
            text = "I could not generate a response for that request."
        text = self._prefer_deterministic_clarification_response(
            response_text=text,
            execute_result=execute_result,
        )
        text = self._prefer_grounded_tool_error_message(
            response_text=text,
            execute_result=execute_result,
        )
        text = self._prefer_deterministic_action_item_create_summary(
            response_text=text,
            execute_result=execute_result,
        )
        text = self._prefer_standardized_work_item_read_response(
            response_text=text,
            execute_result=execute_result,
            user_message=user_message,
        )
        text = self._normalize_partial_action_item_outcome_response(
            response_text=text,
            execute_result=execute_result,
        )
        text = self._normalize_partial_jira_create_assign_outcome_response(
            response_text=text,
            execute_result=execute_result,
        )
        text = self._ensure_parent_line_for_jira_create_response(
            response_text=text,
            execute_result=execute_result,
        )
        text = self._sanitize_structured_status_placeholders(text)
        text = self._bold_section_headings(text)
        text = await self._append_cadence_guard_hint_if_needed(
            response_text=text,
            project_id=project_id,
            user_id=user_id,
        )
        return CortexResponseGuard.apply(
            response_text=text,
            raw_response=execute_result.raw_response or {},
            tool_descriptors=tool_descriptors or [],
        )

    async def _compose_fastpath_response_with_shared_runtime(
        self,
        *,
        user_message: str,
        draft_response_text: str,
        tool_result: Dict[str, Any],
        source: str,
        project_id: str,
        user_id: str,
        session_key: str,
        role: str,
        project_context: Dict[str, Any],
        session: CortexSession,
        run_id: str,
        actor_email_resolved: Optional[str],
        actor_jira_account_id: Optional[str],
        self_scope_requested: bool,
        connected_integrations: List[str],
        message_context_source: str,
        resolved_member_candidates: List[Dict[str, Any]],
        session_snapshot: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Compose fast-path read output via shared runtime response path (tools disabled).
        Fall back to deterministic draft on any compose failure.
        """
        started = perf_counter()
        draft_text = str(draft_response_text or "").strip()
        compose_enabled = bool(getattr(settings, "cortex_fastpath_compose_enabled", True))
        items = tool_result.get("items") if isinstance(tool_result, dict) else []
        if not isinstance(items, list):
            items = []
        task_sample: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            task_sample.append(
                {
                    "external_id": str(item.get("external_id") or "").strip(),
                    "title": str(item.get("title") or "").strip()[:180],
                    "provider_type": str(item.get("provider_type") or item.get("issue_type") or "").strip(),
                    "status": str(item.get("status") or "").strip(),
                    "assignee_name": str(item.get("assignee_name") or "").strip(),
                    "due_date": str(item.get("due_date") or "").strip(),
                }
            )
        compose_outcome_payload = {
            "ok": bool((tool_result or {}).get("ok")) if isinstance(tool_result, dict) else False,
            "count": int((tool_result or {}).get("count") or len(items))
            if isinstance(tool_result, dict)
            else len(items),
            "items": task_sample,
        }

        if not compose_enabled:
            return draft_text, {
                "used": False,
                "latency_ms": int((perf_counter() - started) * 1000),
                "reason": "disabled",
            }
        compose_call_started = perf_counter()
        print(
            "Cortex fastpath compose start: "
            f"enabled={compose_enabled} count={compose_outcome_payload.get('count')} "
            f"sample_items={len(compose_outcome_payload.get('items') or [])}"
        )
        compose_input = (
            "Compose the final user-facing response from grounded task-read outcome.\n"
            "Do not invent tasks, counts, assignees, or due dates.\n"
            "Keep the response natural and concise for Slack.\n\n"
            "When listing tasks, include provider-native type when available "
            "(from `provider_type`, for example Story, Bug, Epic, Sub-task, Issue).\n\n"
            f"Original user request:\n{str(user_message or '').strip()}\n\n"
            f"Grounded task-read outcome (JSON):\n{json.dumps(compose_outcome_payload, ensure_ascii=True)}\n"
        )
        session_summary_history = list(getattr(session, "summary_history", []) or [])
        session_recent_turns = list(getattr(session, "recent_turns", []) or [])
        session_transcript = list(getattr(session, "transcript", []) or [])
        pre_processed_for_compose = {
            "normalized_text": "",
            "confidence": 1.0,
            "slot_coverage": 1.0,
            "signal_strength": 1.0,
            "matches": [],
            "missing_slots": {},
        }

        prompt_payload = self.prompt_builder.build(
            PromptBuildInput(
                project_context=project_context,
                session_context={
                    "summary_history": session_summary_history,
                    "recent_turns": session_recent_turns,
                    "transcript": session_transcript,
                    "session_snapshot": session_snapshot,
                },
                user_message=compose_input,
                visible_tools=[],
                role=role,
                metadata={
                    "source": source,
                    "project_id": project_id,
                    "session_key": session_key,
                    "message_context_source": message_context_source,
                    "member_candidates": resolved_member_candidates,
                    "session_snapshot": session_snapshot,
                    "fastpath_compose_mode": True,
                },
                pre_processed=pre_processed_for_compose,
            )
        )
        compose_context = AgentContext(
            user_message=compose_input,
            project_id=project_id,
            user_id=user_id,
            session_key=session_key,
            source=source,
            pre_processed=pre_processed_for_compose,
            session_history={
                "summary_history": session_summary_history,
                "recent_turns": session_recent_turns,
                "transcript": session_transcript,
            },
            project_context=project_context,
            feature_context={
                "actor_email": actor_email_resolved,
                "actor_identity": {
                    "external_user_id": user_id,
                    "canonical_email": actor_email_resolved,
                    "jira_account_id": actor_jira_account_id,
                },
                "self_scope_requested": self_scope_requested,
                "role": role,
                "connected_integrations": connected_integrations,
                "member_candidates": resolved_member_candidates,
                "message_context_source": message_context_source,
                "latest_user_message": user_message,
                "session_snapshot": session_snapshot,
                "session_snapshot_target_refs": list(session_snapshot.get("target_refs") or []),
                "deterministic_fastpath": True,
                "fastpath_compose_mode": True,
            },
            run_id=f"{run_id}:fastpath-compose",
        )
        try:
            compose_result = await asyncio.wait_for(
                shared_agent_runtime.run(
                    context=compose_context,
                    system_prompt=str(prompt_payload.get("instructions") or ""),
                    tools=[],
                ),
                timeout=8.0,
            )
            composed_text = str(getattr(compose_result, "output_text", "") or "").strip()
            compose_ok = bool(getattr(compose_result, "ok", False))
            compose_error = str(getattr(compose_result, "error", "") or "").strip()
            if not compose_ok:
                compose_elapsed_ms = int((perf_counter() - compose_call_started) * 1000)
                print(
                    "Cortex fastpath compose failed: "
                    f"latency_ms={compose_elapsed_ms} error={compose_error}"
                )
                return draft_text, {
                    "used": False,
                    "latency_ms": int((perf_counter() - started) * 1000),
                    "reason": "runtime_failed",
                }
        except asyncio.TimeoutError:
            compose_elapsed_ms = int((perf_counter() - compose_call_started) * 1000)
            print(
                "Cortex fastpath compose timeout: "
                f"latency_ms={compose_elapsed_ms}"
            )
            return draft_text, {
                "used": False,
                "latency_ms": int((perf_counter() - started) * 1000),
                "reason": "timeout",
            }
        except Exception:
            compose_elapsed_ms = int((perf_counter() - compose_call_started) * 1000)
            print(
                "Cortex fastpath compose exception: "
                f"latency_ms={compose_elapsed_ms}"
            )
            return draft_text, {
                "used": False,
                "latency_ms": int((perf_counter() - started) * 1000),
                "reason": "exception",
            }

        latency_ms = int((perf_counter() - started) * 1000)
        compose_elapsed_ms = int((perf_counter() - compose_call_started) * 1000)
        composed_text = str(composed_text or "").strip()
        print(
            "Cortex fastpath compose done: "
            f"latency_ms={compose_elapsed_ms} "
            f"empty={not bool(composed_text)}"
        )
        if not composed_text:
            return draft_text, {
                "used": False,
                "latency_ms": latency_ms,
                "reason": "empty",
            }
        if composed_text == draft_text:
            return composed_text, {
                "used": False,
                "latency_ms": latency_ms,
                "reason": "fallback_same_text",
            }
        return composed_text, {"used": True, "latency_ms": latency_ms, "reason": "ok"}

    async def _run_executor(
        self,
        *,
        prompt_payload: Dict[str, Any],
        context: Dict[str, Any],
    ) -> ExecutorResult:
        try:
            return await self.executor.run(
                {
                    "prompt": prompt_payload.get("instructions", ""),
                    "sections": prompt_payload.get("sections", []),
                    "tools": prompt_payload.get("tools", []),
                    "meta": prompt_payload.get("meta", {}),
                    "context": context,
                }
            )
        except NotImplementedError:
            return ExecutorResult(ok=False, error="executor_not_available")
        except Exception as exc:
            print(f"Cortex executor failed: {exc}")
            return ExecutorResult(ok=False, error="executor_exception")

    async def _deliver_response(
        self,
        *,
        text: str,
        channel_id: str,
        thread_ts: Optional[str],
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        payload_text = str(text or "").strip()
        if payload_text:
            payload_text = await self._maybe_append_project_context_footer(
                text=payload_text,
                metadata=metadata,
            )
        try:
            return await self.response_service.send(
                ResponsePayload(
                    text=payload_text,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    metadata=metadata,
                )
            )
        except NotImplementedError:
            return {"ok": False, "reason": "response_service_not_ready"}
        except Exception as exc:
            print(f"Cortex response delivery failed: {exc}")
            return {"ok": False, "reason": "response_delivery_exception"}

    async def _maybe_append_project_context_footer(
        self,
        *,
        text: str,
        metadata: Dict[str, Any],
    ) -> str:
        payload_text = str(text or "").strip()
        if not payload_text:
            return payload_text

        source = str((metadata or {}).get("source") or "").strip().lower()
        if source != "slack_dm":
            return payload_text
        if int((metadata or {}).get("tool_calls_count") or 0) <= 0:
            return payload_text

        team_id = str((metadata or {}).get("team_id") or "").strip()
        slack_user_id = str((metadata or {}).get("user_id") or "").strip()
        project_id = str((metadata or {}).get("project_id") or "").strip()
        if not team_id or not slack_user_id or not project_id:
            return payload_text

        db = get_database()
        if db is None:
            return payload_text

        project = (
            (metadata or {}).get("project")
            if isinstance((metadata or {}).get("project"), dict)
            else None
        )
        if not isinstance(project, dict):
            project = await self._load_project_context(project_id=project_id)

        try:
            footer = await build_project_context_footer(
                db=db,
                team_id=team_id,
                slack_user_id=slack_user_id,
                project=project,
            )
        except Exception as footer_exc:
            print(
                "cortex_context_footer_build_failed_in_orchestrator: "
                f"team_id={team_id} user={slack_user_id} project_id={project_id} error={footer_exc}"
            )
            return payload_text
        return append_project_context_footer(payload_text, footer)

    async def _append_event(
        self,
        *,
        session_key: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> None:
        try:
            await self.session_manager.append_transcript_event(
                session_key=session_key,
                event={
                    "event_id": str(uuid4()),
                    "event_type": event_type,
                    "timestamp": datetime.utcnow(),
                    "payload": payload,
                },
            )
        except Exception as exc:
            print(f"Cortex transcript append failed ({event_type}): {exc}")

    async def _persist_turn_metadata(
        self,
        *,
        session_key: str,
        run_id: str,
        status: str,
        source: str,
        project_id: str,
        user_id: str,
        user_message: str,
        request_meta: Dict[str, Any],
        executor_meta: Dict[str, Any],
        delivery_meta: Dict[str, Any],
        response_text: Optional[str],
        started_at: datetime,
        finished_at: Optional[datetime],
        error: Optional[str],
    ) -> None:
        now = datetime.utcnow()
        turn_metadata = {
            "run_id": run_id,
            "status": status,
            "source": source,
            "project_id": project_id,
            "user_id": user_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "request": request_meta,
            "executor": executor_meta,
            "delivery": delivery_meta,
            "error": error,
            "updated_at": now,
        }

        response_preview = None
        if response_text is not None:
            response_preview = response_text[:1500]

        try:
            await self.session_manager.update_session_fields(
                session_key=session_key,
                fields={
                    "metadata.last_run_id": run_id,
                    "metadata.last_turn_status": status,
                    "metadata.last_turn": turn_metadata,
                    "metadata.last_user_message": user_message[:1000],
                    "metadata.last_response_preview": response_preview,
                    "metadata.last_delivery_ok": bool(delivery_meta.get("ok", False)),
                    "metadata.last_error": error,
                    "metadata.last_updated_at": now,
                },
            )
        except Exception as exc:
            print(f"Cortex turn metadata persistence failed: {exc}")

    async def _load_project_context(self, *, project_id: str) -> Dict[str, Any]:
        db = get_database()
        if db is None:
            return {"project_id": project_id}
        project = await db.projects.find_one(
            {"project_id": project_id},
            {"project_id": 1, "project_name": 1, "timezone": 1, "members": 1, "integrations": 1, "active_provider": 1},
        )
        if not project:
            return {"project_id": project_id}
        return {
            "project_id": project.get("project_id") or project_id,
            "project_name": project.get("project_name") or "Unknown Project",
            "timezone": project.get("timezone") or "UTC",
            "members": project.get("members") or [],
            "integrations": project.get("integrations") or {},
            "active_provider": str(project.get("active_provider") or "").strip().lower() or None,
        }

    def _resolve_caller_role(
        self,
        *,
        project_context: Dict[str, Any],
        ingress_user_id: str,
        ingress_actor_email: Optional[str] = None,
        ingress_role_hint: Optional[str] = None,
    ) -> str:
        normalized_hint = str(ingress_role_hint or "").strip().lower()
        if normalized_hint in {"owner", "admin", "member"}:
            return normalized_hint

        normalized_actor_email = str(ingress_actor_email or "").strip().lower()
        normalized_ingress_user_id = str(ingress_user_id or "").strip()
        members = project_context.get("members") or []
        for member in members:
            if not isinstance(member, dict):
                continue
            if str(member.get("status") or "").strip().lower() != "active":
                continue
            integration_ids = member.get("integration_ids") or {}
            if str(integration_ids.get("slack_user_id") or "").strip() == normalized_ingress_user_id:
                role = str(member.get("role") or "member").strip().lower()
                return role or "member"
        for member in members:
            if not isinstance(member, dict):
                continue
            if str(member.get("status") or "").strip().lower() != "active":
                continue
            member_email = str(member.get("email") or "").strip().lower()
            if normalized_actor_email and member_email == normalized_actor_email:
                role = str(member.get("role") or "member").strip().lower()
                return role or "member"
            if "@" in normalized_ingress_user_id and member_email == normalized_ingress_user_id.lower():
                role = str(member.get("role") or "member").strip().lower()
                return role or "member"
        return "member"

    def _resolve_actor_member(
        self,
        *,
        project_context: Dict[str, Any],
        ingress_user_id: str,
        ingress_actor_email: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        normalized_actor_email = str(ingress_actor_email or "").strip().lower()
        normalized_ingress_user_id = str(ingress_user_id or "").strip()
        members = project_context.get("members") or []
        for member in members:
            if not isinstance(member, dict):
                continue
            if str(member.get("status") or "").strip().lower() != "active":
                continue
            integration_ids = member.get("integration_ids") or {}
            if str(integration_ids.get("slack_user_id") or "").strip() == normalized_ingress_user_id:
                return member
        for member in members:
            if not isinstance(member, dict):
                continue
            if str(member.get("status") or "").strip().lower() != "active":
                continue
            member_email = str(member.get("email") or "").strip().lower()
            if normalized_actor_email and member_email == normalized_actor_email:
                return member
            if "@" in normalized_ingress_user_id and member_email == normalized_ingress_user_id.lower():
                return member
        return None

    def _validate_ingress(self, ingress: Dict[str, Any]) -> list[str]:
        required = [
            "source",
            "project_id",
            "user_id",
            "session_anchor",
            "channel_id",
            "message_text",
        ]
        return [key for key in required if not ingress.get(key)]

    @staticmethod
    def _is_strong_project_related_scope_gate_decision(gate_decision: ScopeGateDecision) -> bool:
        if not isinstance(gate_decision, ScopeGateDecision):
            return False
        if gate_decision.classification != SCOPE_GATE_PROJECT_RELATED:
            return False
        reason_code = str(gate_decision.reason_code or "").strip().lower()
        signals = {str(item or "").strip().lower() for item in (gate_decision.signals or [])}
        if scope_registry.SIGNAL_WORK_ITEM_KEY in signals:
            return True
        if reason_code == scope_registry.REASON_PM_ANALYTICS_QUERY:
            return True
        if reason_code == scope_registry.REASON_PROJECT_SIGNAL_DETECTED and (
            scope_registry.SIGNAL_PROJECT_ENTITY_HINT in signals
            or scope_registry.SIGNAL_PROJECT_ACTION_HINT in signals
        ):
            return True
        return False

    @staticmethod
    def _scope_stage1_strength(gate_decision: ScopeGateDecision) -> str:
        if gate_decision.classification == SCOPE_GATE_GREETING:
            return "greeting"
        if CortexOrchestrator._is_strong_project_related_scope_gate_decision(gate_decision):
            return "strong_project_related"
        if gate_decision.classification == SCOPE_GATE_PROJECT_RELATED:
            return "weak_project_related"
        if gate_decision.classification == SCOPE_GATE_OUT_OF_SCOPE:
            return "out_of_scope"
        return "uncertain"

    def _resolve_task_read_fastpath_intent(
        self,
        *,
        user_message: str,
        connected_integrations: List[str],
        launch_tool_descriptors: List[Dict[str, Any]],
    ) -> Optional[Any]:
        if "jira" not in {str(item or "").strip().lower() for item in (connected_integrations or [])}:
            return None
        normalized_message = " ".join(str(user_message or "").strip().lower().split())
        if any(marker in normalized_message for marker in self.FASTPATH_ANALYSIS_QUERY_MARKERS):
            return None
        tool_names = {
            str(item.get("name") or "").strip()
            for item in (launch_tool_descriptors or [])
            if isinstance(item, dict)
        }
        if "jira_search_work_items" not in tool_names:
            return None
        if is_task_write_intent(user_message):
            return None
        intent = parse_task_read_intent(user_message)
        if intent is None:
            return None
        return intent

    def _render_task_read_fastpath_response(
        self,
        *,
        tool_result: Dict[str, Any],
        intent_args: Dict[str, Any],
        is_count_request: bool,
    ) -> str:
        items = tool_result.get("items") if isinstance(tool_result.get("items"), list) else []
        total = int(tool_result.get("count") or len(items or []))
        status_token = str((intent_args or {}).get("status") or "").strip().lower()
        if is_count_request:
            # Keep phrasing natural and avoid fixed status bucket wording.
            if status_token == "pending":
                split_text = self._render_dynamic_status_split(
                    items=items,
                    total=total,
                    include_partial_note=True,
                )
                if split_text:
                    return f"You currently have {total} open work items.\n\n{split_text}"
                return f"You currently have {total} open work items."
            return f"You currently have {total} matching work items."

        if total <= 0:
            return "I couldn't find any matching work items."
        return self._render_standardized_work_item_list_response(
            items=items,
            total=total,
        )

    def _render_dynamic_status_split(
        self,
        *,
        items: List[Any],
        total: int,
        include_partial_note: bool,
    ) -> str:
        if not isinstance(items, list) or not items:
            return ""
        buckets: Dict[str, int] = {}
        for raw in items:
            if not isinstance(raw, dict):
                continue
            status_label = (
                str(raw.get("status_display") or "").strip()
                or str(raw.get("status_raw") or "").strip()
                or str(raw.get("status") or "").strip()
                or "Unknown"
            )
            buckets[status_label] = int(buckets.get(status_label) or 0) + 1
        if not buckets:
            return ""

        lines: List[str] = ["Status breakdown:"]
        for label, count in sorted(buckets.items(), key=lambda item: (-int(item[1]), str(item[0]).lower())):
            lines.append(f"- {label}: {count}")
        if include_partial_note and int(total) > len(items):
            lines.append("Note: breakdown is based on currently returned items.")
        return "\n".join(lines)

    def _prefer_standardized_work_item_read_response(
        self,
        *,
        response_text: str,
        execute_result: ExecutorResult,
        user_message: Optional[str] = None,
    ) -> str:
        text = str(response_text or "").strip()
        raw = execute_result.raw_response if isinstance(execute_result.raw_response, dict) else {}
        read_tool_name = str(raw.get("read_tool_name") or "").strip().lower()
        if not self._is_standardized_work_item_read_tool(read_tool_name):
            return text
        # Keep list formatting only for explicit browse-style read intent.
        # Field/detail follow-ups (for example "who created it", "is there a description?")
        # should remain free-form so the assistant can answer from available read fields.
        parsed_read_intent = parse_task_read_intent(str(user_message or ""))
        if parsed_read_intent is None:
            return text
        if bool(getattr(parsed_read_intent, "is_count_request", False)):
            return text
        read_items = raw.get("read_items")
        if not isinstance(read_items, list) or not read_items:
            return text
        total = int(raw.get("read_items_count") or len(read_items))
        rendered = self._render_standardized_work_item_list_response(
            items=read_items,
            total=total,
        )
        intro = self._extract_work_item_read_intro_line(
            response_text=text,
        )
        if intro:
            return f"{intro}\n\n{rendered}"
        return rendered

    @staticmethod
    def _is_standardized_work_item_read_tool(tool_name: str) -> bool:
        normalized = str(tool_name or "").strip().lower()
        return normalized.endswith("_search_work_items") or normalized.endswith(
            "_get_work_item_details"
        )

    def _render_standardized_work_item_list_response(
        self,
        *,
        items: List[Any],
        total: int,
    ) -> str:
        display_items = [item for item in items if isinstance(item, dict)][:10]
        if not display_items:
            return "I couldn't find any matching work items."
        lines: List[str] = []
        for index, item in enumerate(display_items):
            external_id = str(item.get("external_id") or "").strip() or "Unknown"
            title = str(item.get("title") or "").strip() or "Untitled work item"
            provider_type = str(item.get("provider_type") or item.get("issue_type") or "").strip()
            status_display = str(
                item.get("status_display")
                or item.get("status_raw")
                or ""
            ).strip()
            status_value = (
                status_display
                if status_display
                else "Unknown"
            )
            if not status_display:
                print(
                    "cortex_work_item_status_display_missing: "
                    f"external_id={external_id} source_status={str(item.get('status') or '').strip() or 'n/a'}"
                )
            assignee_value = str(item.get("assignee_name") or "").strip() or "Unassigned"
            due_value = str(item.get("due_date") or "").strip() or "Not set"
            type_value = provider_type or "Work item"
            lines.append(f"- *{external_id} ({type_value})*: {title}")
            lines.append(
                f"  Status: {status_value} | Assignee: {assignee_value} | Due: {due_value}"
            )
            if index < len(display_items) - 1:
                lines.append("")
        if int(total) > len(display_items):
            lines.append(f"...and {int(total) - len(display_items)} more.")
        lines.append("")
        lines.append(self.WORK_ITEM_LIST_FOLLOWUP_PROMPT)
        return "\n".join(lines)

    def _extract_work_item_read_intro_line(
        self,
        *,
        response_text: str,
    ) -> str:
        raw = str(response_text or "").strip()
        if not raw:
            return self.WORK_ITEM_LIST_DEFAULT_INTRO
        first_line = next((line.strip() for line in raw.splitlines() if str(line).strip()), "")
        candidate = re.sub(r"\*+", "", first_line).strip()
        if not candidate:
            return self.WORK_ITEM_LIST_DEFAULT_INTRO
        normalized = candidate.lower()
        if normalized.startswith("here are the matching work items"):
            return self.WORK_ITEM_LIST_DEFAULT_INTRO
        if normalized.startswith("here are the work item details"):
            return self.WORK_ITEM_LIST_DEFAULT_INTRO
        if normalized.startswith(("-", "*", "status:", "assignee:", "due:", "type:")):
            return self.WORK_ITEM_LIST_DEFAULT_INTRO
        if "|" in candidate:
            return self.WORK_ITEM_LIST_DEFAULT_INTRO
        if re.search(r"\b[A-Z][A-Z0-9_]*-\d+\b", candidate):
            return self.WORK_ITEM_LIST_DEFAULT_INTRO
        if len(candidate) > 120:
            candidate = candidate[:117].rstrip() + "..."
        if not re.search(r"[.!?]$", candidate):
            candidate = f"{candidate}."
        return candidate

    def _append_task_read_truncation_hint_if_needed(
        self,
        *,
        response_text: str,
        tool_result: Dict[str, Any],
    ) -> str:
        text = str(response_text or "").strip()
        if not text:
            return text
        if self.FASTPATH_TRUNCATION_HINT in text:
            return text
        items = tool_result.get("items") if isinstance(tool_result.get("items"), list) else []
        total = int(tool_result.get("count") or len(items or []))
        returned = len(items or [])
        if total > returned and returned > 0:
            return f"{text}\n\n{self.FASTPATH_TRUNCATION_HINT}"
        return text

    def _sanitize_structured_status_placeholders(self, response_text: str) -> str:
        text = str(response_text or "").strip()
        if not text:
            return text

        cleaned_lines: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            normalized = line.strip().lower()

            if self._is_empty_status_placeholder_line(normalized, "not completed:"):
                continue
            if self._is_empty_status_placeholder_line(normalized, "next step:"):
                continue

            cleaned_lines.append(line)

        cleaned = "\n".join(cleaned_lines).strip()
        cleaned = re.sub(
            r"(?i)\s*not completed:\s*(none|n/?a|null|-)\.?",
            "",
            cleaned,
        )
        cleaned = re.sub(
            r"(?i)\s*next step:\s*(none|n/?a|null|-)\.?",
            "",
            cleaned,
        )
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        if not cleaned:
            return text
        return cleaned

    def _bold_section_headings(self, response_text: str) -> str:
        text = str(response_text or "").strip()
        if not text:
            return text

        lines = text.splitlines()
        heading_patterns = (
            "in progress:",
            "to do:",
            "todo:",
            "done:",
            "blocked:",
            "pending:",
            "your tasks:",
            "your work items:",
            "work items:",
            "action items:",
            "summary:",
            "key insights:",
            "next steps:",
            "not completed:",
        )

        formatted: List[str] = []
        for line in lines:
            stripped = line.strip()
            normalized = stripped.lower()
            is_heading = (
                stripped
                and stripped.endswith(":")
                and not stripped.startswith(("-", "*", "•"))
                and len(stripped) <= 80
                and not (stripped.startswith("*") and stripped.endswith("*"))
            )
            if is_heading or normalized in heading_patterns:
                formatted.append(f"*{stripped}*")
            else:
                formatted.append(line)
        return "\n".join(formatted).strip()

    @staticmethod
    def _is_empty_status_placeholder_line(normalized_line: str, prefix: str) -> bool:
        if not normalized_line.startswith(prefix):
            return False
        remainder = normalized_line[len(prefix) :].strip().strip(".")
        return remainder in {"none", "n/a", "na", "null", "-", ""}

    async def _append_cadence_guard_hint_if_needed(
        self,
        *,
        response_text: str,
        project_id: str,
        user_id: str,
    ) -> str:
        hint = "You also have a pending Cadence check-in — use the buttons above or reply here to continue."
        if not response_text or hint in response_text:
            return response_text

        try:
            has_active = await self._has_active_cadence_session(
                project_id=project_id,
                user_id=user_id,
            )
            if has_active:
                return f"{response_text}\n\n{hint}"
        except Exception as exc:
            # UX hint is non-blocking; never fail the response path on this check.
            print(f"Cortex cadence guard check failed: {exc}")

        return response_text

    async def _has_active_cadence_session(
        self,
        *,
        project_id: str,
        user_id: str,
    ) -> bool:
        db = get_database()
        if db is None:
            return False

        repo = SessionRepository(db=db)
        active_session = await repo.find_active_session(
            entity_type="cadence_session",
            source="cadence",
            user_id=user_id,
            project_id=project_id,
        )
        return bool(active_session)

    async def _handle_pending_confirmation_turn(
        self,
        *,
        session_key: str,
        pending_confirmation: Optional[Dict[str, Any]],
        user_message: str,
    ) -> Optional[OrchestratorResult]:
        if not pending_confirmation:
            return None

        tool_name = str(pending_confirmation.get("pending_tool_name") or "this action")
        call_id = str(pending_confirmation.get("pending_tool_call_id") or "")
        now = datetime.utcnow()

        if self._is_pending_confirmation_expired(pending_confirmation, now=now):
            await self.session_manager.set_pending_confirmation(
                session_key=session_key,
                pending_confirmation=None,
            )
            await self._append_event(
                session_key=session_key,
                event_type="confirmation_timeout",
                payload={
                    "pending_tool_call_id": call_id,
                    "pending_tool_name": tool_name,
                    "expired_at": now,
                },
            )
            return OrchestratorResult(
                ok=True,
                response_text=(
                    "The previous approval request has timed out. "
                    "Please ask again if you still want to run that action."
                ),
                metadata={"pending_confirmation_expired": True, "session_key": session_key},
            )

        decision = self._parse_confirmation_decision(user_message)
        if not decision:
            if str(pending_confirmation.get("type") or "").strip().lower() == "proposed_tool_calls":
                return OrchestratorResult(
                    ok=True,
                    response_text=self._build_confirmation_prompt(pending_confirmation),
                    metadata={"pending_confirmation": pending_confirmation, "session_key": session_key},
                )
            return OrchestratorResult(
                ok=True,
                response_text=(
                    f"You have a pending approval for `{tool_name}`. "
                    "Reply with `approve` or `decline`."
                ),
                metadata={"pending_confirmation": pending_confirmation, "session_key": session_key},
            )

        await self.session_manager.set_pending_confirmation(
            session_key=session_key,
            pending_confirmation=None,
        )
        await self._append_event(
            session_key=session_key,
            event_type="confirmation_response",
            payload={
                "pending_tool_call_id": call_id,
                "pending_tool_name": tool_name,
                "decision": decision,
                "responded_at": now,
            },
        )
        await self.session_manager.update_session_fields(
            session_key=session_key,
            fields={
                "metadata.last_confirmation_decision": decision,
                "metadata.last_confirmation_tool": tool_name,
                "metadata.last_confirmation_at": now,
            },
        )

        if str(pending_confirmation.get("type") or "").strip().lower() == "proposed_tool_calls":
            if decision == "approve":
                execution_context = pending_confirmation.get("execution_context")
                proposed_calls = pending_confirmation.get("proposed_tool_calls")
                if isinstance(execution_context, dict) and isinstance(proposed_calls, list):
                    execution_context["latest_user_message"] = str(user_message or "").strip()
                    existing_tokens = [
                        str(item or "").strip()
                        for item in (execution_context.get("quality_user_confirmed_fields") or [])
                        if str(item or "").strip()
                    ]
                    merged_tokens: List[str] = []
                    seen_tokens: set[str] = set()
                    for token in existing_tokens:
                        lowered = token.lower()
                        if not lowered or lowered in seen_tokens:
                            continue
                        seen_tokens.add(lowered)
                        merged_tokens.append(token)

                    for call in proposed_calls:
                        if not isinstance(call, dict):
                            continue
                        tool_name = str(call.get("tool_name") or "").strip()
                        args = call.get("args")
                        if not tool_name or not isinstance(args, dict):
                            continue
                        for field_name, value in args.items():
                            key = str(field_name or "").strip()
                            if not key:
                                continue
                            if value is None:
                                continue
                            if isinstance(value, str):
                                if not value.strip():
                                    continue
                            plain_token = key
                            scoped_token = f"{tool_name}.{key}"
                            for token in (plain_token, scoped_token):
                                lowered = token.lower()
                                if not lowered or lowered in seen_tokens:
                                    continue
                                seen_tokens.add(lowered)
                                merged_tokens.append(token)

                    execution_context["quality_user_confirmed_fields"] = merged_tokens
                return await self._execute_confirmed_proposal_tool_calls(
                    pending_confirmation=pending_confirmation,
                    session_key=session_key,
                )
            return OrchestratorResult(
                ok=True,
                response_text="Understood. I won't run that proposed action.",
                metadata={"confirmation_decision": decision, "session_key": session_key},
            )

        if decision == "approve":
            return OrchestratorResult(
                ok=True,
                response_text=(
                    f"Approved `{tool_name}`. "
                    "Please resend the request so I can execute it with the approved action."
                ),
                metadata={"confirmation_decision": decision, "session_key": session_key},
            )
        return OrchestratorResult(
            ok=True,
            response_text=f"Declined `{tool_name}`. I will not run that action.",
            metadata={"confirmation_decision": decision, "session_key": session_key},
        )

    async def _handle_pending_question_turn(
        self,
        *,
        session_key: str,
        pending_question: Optional[Dict[str, Any]],
        user_message: str,
    ) -> Optional[OrchestratorResult]:
        if not pending_question:
            return None

        now = datetime.utcnow()
        question_type = str(pending_question.get("type") or "").strip().lower()
        calls = pending_question.get("proposed_tool_calls")
        execution_context = pending_question.get("execution_context")
        if not isinstance(calls, list) or not isinstance(execution_context, dict):
            await self.session_manager.set_pending_question(
                session_key=session_key,
                pending_question=None,
            )
            return OrchestratorResult(
                ok=True,
                response_text=(
                    "I couldn't continue the previous clarification because context was lost. "
                    "Please restate the request and I will proceed."
                ),
                metadata={"pending_question_invalid": True, "session_key": session_key},
            )

        if self._is_pending_question_expired(pending_question, now=now):
            await self.session_manager.set_pending_question(
                session_key=session_key,
                pending_question=None,
            )
            await self._append_event(
                session_key=session_key,
                event_type="pending_question_timeout",
                payload={
                    "type": question_type or "proposal_clarification",
                    "expired_at": now,
                },
            )
            return OrchestratorResult(
                ok=True,
                response_text=(
                    "That earlier clarification has timed out. "
                    "Share the request again and I will continue."
                ),
                metadata={"pending_question_expired": True, "session_key": session_key},
            )

        decision = (user_message or "").strip().lower()
        if decision in {"cancel", "skip", "later", "stop", "never mind", "nevermind"}:
            await self.session_manager.set_pending_question(
                session_key=session_key,
                pending_question=None,
            )
            await self._append_event(
                session_key=session_key,
                event_type="pending_question_cancelled",
                payload={"type": question_type or "proposal_clarification", "cancelled_at": now},
            )
            return OrchestratorResult(
                ok=True,
                response_text="Understood. I cancelled the pending clarification.",
                metadata={"pending_question_cancelled": True, "session_key": session_key},
            )
        if (
            question_type == "proposal_missing_fields"
            and self._should_supersede_pending_missing_fields(
                pending_question=pending_question,
                user_message=user_message,
            )
        ):
            await self.session_manager.set_pending_question(
                session_key=session_key,
                pending_question=None,
            )
            await self._append_event(
                session_key=session_key,
                event_type="pending_question_superseded",
                payload={"type": question_type or "proposal_clarification", "superseded_at": now},
            )
            return None

        prepared_calls, guidance = self._prepare_tool_calls_from_pending_question(
            pending_question=pending_question,
            user_message=user_message,
        )
        if not prepared_calls:
            return OrchestratorResult(
                ok=True,
                response_text=guidance
                or "Please share the missing detail so I can continue.",
                metadata={"pending_question": pending_question, "session_key": session_key},
            )

        execution_context["latest_user_message"] = user_message
        if question_type == "proposal_scope_confirmation":
            origin = str(pending_question.get("origin") or "").strip().lower()
            if origin == "provider_selection":
                provider_decision = self._parse_provider_selection_decision(
                    user_message=user_message,
                    candidate_providers=[
                        str(item).strip().lower()
                        for item in (pending_question.get("candidate_providers") or [])
                        if str(item).strip()
                    ],
                )
                if provider_decision:
                    execution_context["provider_selection_override"] = provider_decision
                    execution_context["provider_hint"] = provider_decision
            else:
                scope_decision = self._parse_scope_confirmation_decision(user_message)
                if scope_decision:
                    execution_context["bulk_scope_decision"] = scope_decision
        if question_type == "proposal_missing_fields":
            existing_tokens = [
                str(item or "").strip()
                for item in (execution_context.get("quality_user_confirmed_fields") or [])
                if str(item or "").strip()
            ]
            missing_tokens = self._coalesce_missing_field_tokens(
                [str(item or "").strip() for item in (pending_question.get("missing_fields") or [])]
            )
            merged_tokens: List[str] = []
            seen: set[str] = set()
            for token in existing_tokens + missing_tokens:
                lowered = token.lower()
                if not lowered or lowered in seen:
                    continue
                seen.add(lowered)
                merged_tokens.append(token)
            execution_context["quality_user_confirmed_fields"] = merged_tokens
            self._inject_target_refs_from_calls(
                execution_context=execution_context,
                calls=prepared_calls,
            )

        await self.session_manager.set_pending_question(
            session_key=session_key,
            pending_question=None,
        )
        await self._append_event(
            session_key=session_key,
            event_type="pending_question_resolved",
            payload={
                "type": question_type or "proposal_clarification",
                "resolved_at": now,
            },
        )
        execution = await self._execute_confirmed_proposal_tool_calls(
            pending_confirmation={
                "proposed_tool_calls": prepared_calls,
                "execution_context": execution_context,
            },
            session_key=session_key,
        )
        metadata = dict(execution.metadata or {})
        metadata.update({"pending_question_resolved": True, "session_key": session_key})
        return OrchestratorResult(
            ok=execution.ok,
            response_text=execution.response_text,
            error=execution.error,
            metadata=metadata,
        )

    def _is_pending_question_expired(
        self,
        pending_question: Dict[str, Any],
        *,
        now: datetime,
    ) -> bool:
        expires_at = pending_question.get("pending_question_expires_at")
        if not expires_at:
            return False
        if isinstance(expires_at, datetime):
            return expires_at <= now
        if isinstance(expires_at, str):
            try:
                parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if parsed.tzinfo is not None:
                    parsed = parsed.replace(tzinfo=None)
                return parsed <= now
            except Exception:
                return False
        return False

    def _prepare_tool_calls_from_pending_question(
        self,
        *,
        pending_question: Dict[str, Any],
        user_message: str,
    ) -> tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        question_type = str(pending_question.get("type") or "").strip().lower()
        raw_calls = pending_question.get("proposed_tool_calls")
        calls: List[Dict[str, Any]] = []
        if isinstance(raw_calls, list):
            for item in raw_calls:
                if not isinstance(item, dict):
                    continue
                tool_name = str(item.get("tool_name") or "").strip()
                args = item.get("args")
                if not tool_name or not isinstance(args, dict):
                    continue
                calls.append({"tool_name": tool_name, "args": dict(args)})
        if not calls:
            return None, "I couldn't continue because the previous action context is incomplete."

        text = str(user_message or "").strip()
        if not text:
            return None, "Please share the missing detail so I can continue."

        if question_type == "proposal_unresolved_delegate":
            if self._contains_unassigned_intent(text):
                for call in calls:
                    args = call.get("args") or {}
                    if isinstance(args, dict):
                        args.pop("assignee", None)
                        args.pop("assignee_email", None)
                        args.pop("assignee_value", None)
                return self._fill_missing_fields_from_followup(
                    calls=calls,
                    pending_question=pending_question,
                    user_message=text,
                )
            email = self._extract_first_email(text)
            if email:
                for call in calls:
                    args = call.get("args") or {}
                    if not isinstance(args, dict):
                        continue
                    if "assignee" in args or str(call.get("tool_name") or "").strip() in {
                        "jira_create_work_item",
                        "jira_assign_work_item",
                    }:
                        args["assignee"] = email
                return self._fill_missing_fields_from_followup(
                    calls=calls,
                    pending_question=pending_question,
                    user_message=text,
                )
            return None, (
                "I can continue this now. Share the assignee email, or say `unassigned` and I'll create it unassigned."
            )

        if question_type == "proposal_scope_confirmation":
            origin = str(pending_question.get("origin") or "").strip().lower()
            if origin == "provider_selection":
                return self._prepare_tool_calls_for_provider_selection_pending(
                    pending_question=pending_question,
                    user_message=text,
                )
            scope_decision = self._parse_scope_confirmation_decision(text)
            if not scope_decision:
                return None, (
                    "Please confirm scope: reply `include all` to apply to all matching targets, "
                    "or `only selected` to continue with the current subset."
                )
            if scope_decision == "include_all":
                templates = pending_question.get("write_target_templates")
                missing_targets = pending_question.get("missing_targets")
                expanded = self._expand_calls_with_targets(
                    calls=calls,
                    templates=templates if isinstance(templates, list) else [],
                    targets=missing_targets if isinstance(missing_targets, list) else [],
                )
                if len(expanded) <= len(calls) and isinstance(missing_targets, list) and missing_targets:
                    return None, (
                        "I need explicit target identifiers to include the omitted items for this tool set. "
                        "Please provide the IDs to include."
                    )
                return expanded, None
            return calls, None

        if question_type == "proposal_missing_fields":
            missing_fields = pending_question.get("missing_fields")
            unresolved: List[str] = []
            fields = [str(item or "").strip() for item in (missing_fields or []) if str(item or "").strip()]
            for field_ref in fields:
                tool_name, _, field_name = field_ref.partition(".")
                if not tool_name or not field_name:
                    continue
                if field_name in self.SYSTEM_MANAGED_TOOL_FIELDS:
                    continue
                matched = False
                for call in calls:
                    if str(call.get("tool_name") or "").strip() != tool_name:
                        continue
                    value = self._resolve_missing_field_value(field_name=field_name, user_message=text)
                    if value is None:
                        unresolved.append(field_ref)
                        matched = True
                        break
                    args = call.get("args") or {}
                    if isinstance(args, dict):
                        args[field_name] = value
                    matched = True
                    break
                if not matched:
                    unresolved.append(field_ref)
            if unresolved:
                return None, self._build_missing_fields_response(unresolved)
            return calls, None

        return None, "Please restate the request and I will continue."

    def _extract_provider_type_clarification_tool_error(
        self,
        *,
        raw_response: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        errors = raw_response.get("tool_errors")
        if not isinstance(errors, list):
            return None
        for item in errors:
            if not isinstance(item, dict):
                continue
            error_code = str(item.get("error_code") or "").strip().lower()
            if error_code != "provider_type_clarification_required":
                continue
            details = item.get("details")
            if not isinstance(details, dict):
                continue
            proposed = details.get("proposed_tool_call")
            if not isinstance(proposed, dict):
                continue
            args = proposed.get("args")
            if not isinstance(args, dict):
                continue
            tool_name = str(proposed.get("tool_name") or "").strip()
            if not tool_name:
                continue
            requested_value = str(details.get("requested_value") or "").strip() or None
            suggested_value = str(details.get("suggested_value") or "").strip() or None
            allowed_values = [
                str(value or "").strip()
                for value in (details.get("allowed_values") or [])
                if str(value or "").strip()
            ]
            user_message = str(item.get("user_message") or "").strip()
            if not user_message:
                requested_label = requested_value or "That type"
                if suggested_value:
                    user_message = (
                        f"`{requested_label}` isn't allowed. Use `{suggested_value}` instead? "
                        "Reply `yes`, the type name, or `cancel`."
                    )
                else:
                    allowed_line = ", ".join(allowed_values) or "an allowed type"
                    user_message = (
                        f"`{requested_label}` isn't allowed. "
                        f"Reply with one of: {allowed_line}, or `cancel`."
                    )
            return {
                "tool_name": tool_name,
                "args": dict(args),
                "user_message": user_message,
                "requested_value": requested_value,
                "suggested_value": suggested_value,
                "allowed_values": allowed_values,
                "ranked_suggestions": list(details.get("ranked_suggestions") or []),
            }
        return None

    async def _queue_provider_type_pending_interaction_from_execute_error(
        self,
        *,
        execute_result: ExecutorResult,
        project_id: str,
        user_id: str,
        team_id: Optional[str],
        actor_user_id: str,
    ) -> Optional[Dict[str, Any]]:
        raw = execute_result.raw_response or {}
        provider_type_error = self._extract_provider_type_clarification_tool_error(
            raw_response=raw if isinstance(raw, dict) else {},
        )
        if provider_type_error is None:
            return None

        tool_name = str(provider_type_error.get("tool_name") or "").strip()
        args = provider_type_error.get("args")
        if not tool_name or not isinstance(args, dict):
            return None
        response_text = str(provider_type_error.get("user_message") or "").strip()
        if not response_text:
            response_text = "I need a quick type confirmation before I continue."

        normalized_project_id = str(project_id or "").strip()
        normalized_target_user = str(actor_user_id or "").strip() or str(user_id or "").strip()
        if not normalized_project_id or not normalized_target_user:
            return None

        existing = await find_pending_interaction(
            project_id=normalized_project_id,
            target_user_id=normalized_target_user,
            interaction_type="provider_type_clarification",
        )
        if isinstance(existing, dict):
            existing_id = str(existing.get("interaction_id") or "").strip()
            if existing_id:
                await resolve_pending_interaction(
                    interaction_id=existing_id,
                    terminal_status="stale_target",
                )

        now = datetime.utcnow()
        await create_pending_interaction(
            interaction_type="provider_type_clarification",
            project_id=normalized_project_id,
            target_user_id=normalized_target_user,
            source=f"cortex_{tool_name}",
            provider="slack",
            workspace_id=str(team_id or "").strip() or None,
            external_user_id=str(user_id or "").strip() or None,
            expires_at=now + timedelta(seconds=self.QUESTION_TIMEOUT_SECONDS),
            context_payload={
                "tool_name": tool_name,
                "args": self._canonicalize_tool_args_for_execution(
                    tool_name=tool_name,
                    args=dict(args),
                ),
                "requested_value": provider_type_error.get("requested_value"),
                "suggested_value": provider_type_error.get("suggested_value"),
                "allowed_values": provider_type_error.get("allowed_values") or [],
                "ranked_suggestions": provider_type_error.get("ranked_suggestions") or [],
                "guidance_text": response_text,
            },
        )
        return {
            "interaction_type": "provider_type_clarification",
            "missing_fields": [f"{tool_name}.provider_type"],
            "response_text": response_text,
        }

    async def _set_pending_missing_fields_question(
        self,
        *,
        session_key: str,
        missing_fields: List[str],
        unresolved_calls: List[Dict[str, Any]],
        execution_context: Dict[str, Any],
    ) -> None:
        now = datetime.utcnow()
        missing = [str(item) for item in missing_fields if str(item).strip()]
        parser_understanding = {
            "missing_fields": list(missing),
            "proposed_tool_calls": list(unresolved_calls or []),
        }
        clarification_payload = build_clarification_payload(
            reason_code="clarification_required",
            original_user_text=(
                str(execution_context.get("latest_user_message") or "").strip()
                or str(execution_context.get("user_message") or "").strip()
            ),
            parser_understanding=parser_understanding,
            clarification_kind="tool_args",
            retry_count=0,
            max_retries=1,
            extra={
                "session_key": str(session_key or "").strip(),
                "run_id": str(execution_context.get("run_id") or "").strip(),
                "missing_fields": list(missing),
            },
        )
        pending_question = {
            "type": "proposal_missing_fields",
            "run_id": str(execution_context.get("run_id") or "").strip(),
            "pending_question_expires_at": now + timedelta(seconds=self.QUESTION_TIMEOUT_SECONDS),
            "requested_at": now,
            "missing_fields": missing,
            "proposed_tool_calls": unresolved_calls,
            "execution_context": execution_context,
            "clarification_payload": clarification_payload,
        }
        await self.session_manager.set_pending_question(
            session_key=session_key,
            pending_question=pending_question,
        )
        await self._append_event(
            session_key=session_key,
            event_type="implicit_proposal_question_requested",
            payload={
                "run_id": str(execution_context.get("run_id") or "").strip(),
                "question_type": "proposal_missing_fields",
                "missing_fields": pending_question.get("missing_fields") or [],
            },
        )

    async def _set_pending_scope_confirmation_question(
        self,
        *,
        session_key: str,
        candidate_targets: List[str],
        selected_targets: List[str],
        missing_targets: List[str],
        unresolved_calls: List[Dict[str, Any]],
        execution_context: Dict[str, Any],
    ) -> None:
        now = datetime.utcnow()
        normalized_candidates = [str(item).strip() for item in candidate_targets if str(item).strip()]
        normalized_selected = [str(item).strip() for item in selected_targets if str(item).strip()]
        normalized_missing = [str(item).strip() for item in missing_targets if str(item).strip()]
        templates = self._build_write_target_templates(unresolved_calls)
        parser_understanding = {
            "candidate_targets": list(normalized_candidates),
            "selected_targets": list(normalized_selected),
            "missing_targets": list(normalized_missing),
            "proposed_tool_calls": list(unresolved_calls or []),
        }
        clarification_payload = build_clarification_payload(
            reason_code="ambiguous_target",
            original_user_text=(
                str(execution_context.get("latest_user_message") or "").strip()
                or str(execution_context.get("user_message") or "").strip()
            ),
            parser_understanding=parser_understanding,
            clarification_kind="scope",
            retry_count=0,
            max_retries=1,
            extra={
                "session_key": str(session_key or "").strip(),
                "run_id": str(execution_context.get("run_id") or "").strip(),
                "candidate_targets": list(normalized_candidates),
                "selected_targets": list(normalized_selected),
                "missing_targets": list(normalized_missing),
            },
        )
        pending_question = {
            "type": "proposal_scope_confirmation",
            "run_id": str(execution_context.get("run_id") or "").strip(),
            "pending_question_expires_at": now + timedelta(seconds=self.QUESTION_TIMEOUT_SECONDS),
            "requested_at": now,
            "candidate_targets": normalized_candidates,
            "selected_targets": normalized_selected,
            "missing_targets": normalized_missing,
            "write_target_templates": templates,
            "proposed_tool_calls": unresolved_calls,
            "execution_context": execution_context,
            "clarification_payload": clarification_payload,
        }
        await self.session_manager.set_pending_question(
            session_key=session_key,
            pending_question=pending_question,
        )
        await self._append_event(
            session_key=session_key,
            event_type="implicit_proposal_question_requested",
            payload={
                "run_id": str(execution_context.get("run_id") or "").strip(),
                "question_type": "proposal_scope_confirmation",
                "candidate_target_count": len(normalized_candidates),
                "selected_target_count": len(normalized_selected),
                "missing_target_count": len(normalized_missing),
            },
        )

    async def _repair_calls_with_context_if_needed(
        self,
        *,
        calls: List[Dict[str, Any]],
        execution_context: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        if not isinstance(calls, list) or not calls:
            return []

        normalized_calls: List[Dict[str, Any]] = []
        for item in calls:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool_name") or "").strip()
            args = item.get("args")
            if not tool_name or not isinstance(args, dict):
                continue
            normalized_calls.append(
                {
                    "tool_name": tool_name,
                    "args": self._canonicalize_tool_args_for_execution(
                        tool_name=tool_name,
                        args=args,
                    ),
                }
            )
        if not normalized_calls:
            return []

        still_missing = False
        for item in normalized_calls:
            tool_name = str(item.get("tool_name") or "").strip()
            args = item.get("args") or {}
            for field in self._proposal_required_fields_for_tool_call(tool_name):
                if not str(args.get(field) or "").strip():
                    still_missing = True
                    break
            if still_missing:
                break

        if not still_missing:
            return normalized_calls

        contracts = self._proposal_tool_contracts(
            [
                {"name": str(item.get("tool_name") or "").strip()}
                for item in normalized_calls
                if str(item.get("tool_name") or "").strip()
            ]
        )
        if not self._consume_repair_budget(execution_context):
            return normalized_calls
        original_request = self._compact_contextual_request_text(
            str(execution_context.get("user_message") or "").strip()
        )
        followup_message = self._compact_contextual_request_text(
            str(execution_context.get("latest_user_message") or "").strip()
        )
        message_context_source = str(execution_context.get("message_context_source") or "").strip()
        repaired = await self.proposal_service.repair_tool_args(
            executor=self.executor,
            tool_calls=normalized_calls,
            tool_contracts=contracts,
            original_request=original_request,
            followup_message=followup_message,
            message_context_source=message_context_source,
            context=execution_context,
        )
        merged: List[Dict[str, Any]] = []
        repaired_by_tool = {
            str(item.get("tool_name") or "").strip(): item
            for item in (repaired or [])
            if isinstance(item, dict)
        }
        for item in normalized_calls:
            tool_name = str(item.get("tool_name") or "").strip()
            base_args = item.get("args") or {}
            repaired_item = repaired_by_tool.get(tool_name) or {}
            repaired_args = repaired_item.get("args") if isinstance(repaired_item, dict) else {}
            if not isinstance(base_args, dict):
                base_args = {}
            if not isinstance(repaired_args, dict):
                repaired_args = {}
            merged_args = dict(base_args)
            for key, value in repaired_args.items():
                if value is None:
                    continue
                text_value = str(value).strip().lower() if isinstance(value, str) else ""
                if text_value in {"", "none", "null"}:
                    continue
                merged_args[key] = value
            merged.append(
                {
                    "tool_name": tool_name,
                    "args": self._canonicalize_tool_args_for_execution(
                        tool_name=tool_name,
                        args=merged_args,
                    ),
                }
            )

        # Deterministic final backfill for still-missing required fields from compacted context text.
        context_candidates = [followup_message, original_request]
        finalized: List[Dict[str, Any]] = []
        for item in merged:
            tool_name = str(item.get("tool_name") or "").strip()
            args = item.get("args") if isinstance(item.get("args"), dict) else {}
            if not tool_name:
                continue
            next_args = dict(args or {})
            for field_name in self._proposal_required_fields_for_tool_call(tool_name):
                if str(next_args.get(field_name) or "").strip():
                    continue
                resolved_value: Optional[str] = None
                for candidate_text in context_candidates:
                    if not str(candidate_text or "").strip():
                        continue
                    resolved_value = self._resolve_missing_field_value(
                        field_name=field_name,
                        user_message=str(candidate_text),
                    )
                    if str(resolved_value or "").strip():
                        break
                if str(resolved_value or "").strip():
                    next_args[field_name] = resolved_value
            finalized.append(
                {
                    "tool_name": tool_name,
                    "args": self._canonicalize_tool_args_for_execution(
                        tool_name=tool_name,
                        args=next_args,
                    ),
                }
            )
        return finalized

    async def _refine_proposal_calls_with_quality(
        self,
        *,
        calls: List[Dict[str, Any]],
        visible_tool_descriptors: List[Dict[str, Any]],
        execution_context: Dict[str, Any],
    ) -> tuple[List[Dict[str, Any]], List[str], List[str]]:
        _ = visible_tool_descriptors
        normalized_calls: List[Dict[str, Any]] = []
        for item in calls:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool_name") or "").strip()
            args = item.get("args")
            if not tool_name or not isinstance(args, dict):
                continue
            normalized_calls.append(
                {
                    "tool_name": tool_name,
                    "args": self._canonicalize_tool_args_for_execution(
                        tool_name=tool_name,
                        args=args,
                    ),
                    "field_meta": (
                        dict(item.get("field_meta"))
                        if isinstance(item.get("field_meta"), dict)
                        else {}
                    ),
                }
            )
        if not normalized_calls:
            return [], [], []

        refined_calls: List[Dict[str, Any]] = []
        missing_fields: List[str] = []
        prevalidated: set[str] = set()

        for item in normalized_calls:
            tool_name = str(item.get("tool_name") or "").strip()
            args = item.get("args") if isinstance(item.get("args"), dict) else {}
            if not tool_name or not isinstance(args, dict):
                continue

            prepared = await self.executor.prepare_tool_args_for_execution(
                tool_name=tool_name,
                args=args,
                execution_context=execution_context,
            )
            if isinstance(prepared, dict) and prepared.get("_tool_error"):
                tool_error = prepared.get("_tool_error") if isinstance(prepared.get("_tool_error"), dict) else {}
                extracted = self._extract_insufficient_fields_from_tool_error(
                    tool_name=tool_name,
                    result=tool_error,
                )
                for token in extracted:
                    if token not in missing_fields:
                        missing_fields.append(token)
                refined_calls.append(
                    {
                        "tool_name": tool_name,
                        "args": dict(args),
                        "field_meta": (
                            dict(item.get("field_meta"))
                            if isinstance(item.get("field_meta"), dict)
                            else {}
                        ),
                    }
                )
                continue

            prepared_args = prepared if isinstance(prepared, dict) else {}
            canonical_prepared = self._canonicalize_tool_args_for_execution(
                tool_name=tool_name,
                args=prepared_args,
            )
            refined_calls.append(
                {
                    "tool_name": tool_name,
                    "args": canonical_prepared,
                    "field_meta": (
                        dict(item.get("field_meta"))
                        if isinstance(item.get("field_meta"), dict)
                        else {}
                    ),
                }
            )
            for field_name in canonical_prepared.keys():
                token = str(field_name).strip().lower()
                if not token:
                    continue
                prevalidated.add(token)
                prevalidated.add(f"{tool_name}.{token}".lower())

        return (
            refined_calls,
            self._coalesce_missing_field_tokens(missing_fields),
            sorted(prevalidated),
        )

    def _fill_missing_fields_from_followup(
        self,
        *,
        calls: List[Dict[str, Any]],
        pending_question: Dict[str, Any],
        user_message: str,
    ) -> tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        missing_fields = pending_question.get("missing_fields")
        if not isinstance(missing_fields, list) or not missing_fields:
            return calls, None

        unresolved: List[str] = []
        for field_ref in [str(item or "").strip() for item in missing_fields if str(item or "").strip()]:
            tool_name, _, field_name = field_ref.partition(".")
            if not tool_name or not field_name:
                continue
            if field_name in self.SYSTEM_MANAGED_TOOL_FIELDS:
                continue
            matched = False
            for call in calls:
                if str(call.get("tool_name") or "").strip() != tool_name:
                    continue
                args = call.get("args") or {}
                if not isinstance(args, dict):
                    continue
                if str(args.get(field_name) or "").strip():
                    matched = True
                    break
                value = self._resolve_missing_field_value(field_name=field_name, user_message=user_message)
                if value is None:
                    unresolved.append(field_ref)
                    matched = True
                    break
                args[field_name] = value
                matched = True
                break
            if not matched:
                unresolved.append(field_ref)
        if unresolved:
            return None, self._build_missing_fields_response(unresolved)
        return calls, None

    def _should_supersede_pending_missing_fields(
        self,
        *,
        pending_question: Dict[str, Any],
        user_message: str,
    ) -> bool:
        text = str(user_message or "").strip()
        if not text:
            return False
        lowered = text.lower()
        if lowered in {"continue previous", "continue", "use previous task", "resume"}:
            return False
        if any(
            marker in lowered
            for marker in (
                "new question",
                "another question",
                "different request",
                "ignore that",
                "leave that",
            )
        ):
            return True
        if not self._looks_like_distinct_new_intent(lowered):
            return False
        # If message can still satisfy all missing fields, keep continuation behavior.
        missing_fields = [
            str(item or "").strip()
            for item in (pending_question.get("missing_fields") or [])
            if str(item or "").strip()
        ]
        if not missing_fields:
            return False
        if any(
            str(field_ref).partition(".")[2].strip().lower() == "comment"
            for field_ref in missing_fields
        ):
            return True
        for field_ref in missing_fields:
            _tool_name, _, field_name = field_ref.partition(".")
            if not field_name:
                continue
            if self._resolve_missing_field_value(field_name=field_name, user_message=text) is None:
                return True
            # comment-like pending fields must contain content, not just target references.
            if field_name.strip().lower() == "comment":
                if self._looks_like_comment_target_reference_only(text):
                    return True
        return False

    def _looks_like_distinct_new_intent(self, lowered: str) -> bool:
        cues = (
            "list ",
            "show ",
            "what ",
            "who ",
            "how many",
            "my tasks",
            "status of",
            "find ",
            "search ",
            "summarize ",
            "analyze ",
            "help",
        )
        return any(cue in lowered for cue in cues)

    def _looks_like_comment_target_reference_only(self, text: str) -> bool:
        compact = " ".join(str(text or "").strip().split())
        if not compact:
            return False
        issue_key = r"[a-z][a-z0-9_]*[-\s]?\d+"
        patterns = [
            rf"^(?:for|to|on)\s+(?:task|issue\s+)?{issue_key}\s*$",
            rf"^(?:(?:task|issue)\s+)?{issue_key}\s*$",
            rf"^(?:add|post)\s+(?:a\s+)?comments?\s+(?:for|to|on)\s+(?:task|issue\s+)?{issue_key}\s*$",
            rf"^(?:comment|comments)\s+(?:for|to|on)\s+(?:task|issue\s+)?{issue_key}\s*$",
        ]
        return any(re.match(pattern, compact, flags=re.IGNORECASE) for pattern in patterns)

    def _inject_target_refs_from_calls(
        self,
        *,
        execution_context: Dict[str, Any],
        calls: List[Dict[str, Any]],
    ) -> None:
        if not isinstance(execution_context, dict):
            return
        target_tokens: List[str] = []
        for call in calls or []:
            if not isinstance(call, dict):
                continue
            args = call.get("args")
            if not isinstance(args, dict):
                continue
            for key in ("external_id", "task_id", "issue_id", "id", "entity_id"):
                raw = str(args.get(key) or "").strip()
                if not raw:
                    continue
                token = raw.upper() if "-" in raw else raw
                if token not in target_tokens:
                    target_tokens.append(token)
        if not target_tokens:
            return
        for context_key in ("runtime_target_refs", "session_snapshot_target_refs"):
            current = execution_context.get(context_key)
            merged = list(current) if isinstance(current, list) else []
            for token in target_tokens:
                if token not in merged:
                    merged.append(token)
            execution_context[context_key] = merged

    def _contains_unassigned_intent(self, text: str) -> bool:
        lowered = str(text or "").strip().lower()
        markers = [
            "unassigned",
            "without assignee",
            "without assigning",
            "without assignment",
            "no assignee",
            "don't assign",
            "do not assign",
            "leave unassigned",
        ]
        return any(marker in lowered for marker in markers)

    def _extract_first_email(self, text: str) -> Optional[str]:
        match = re.search(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", str(text or ""), flags=re.IGNORECASE)
        if not match:
            return None
        return str(match.group(0) or "").strip().lower() or None

    def _compact_contextual_request_text(self, text: str) -> str:
        """Collapse Slack thread wrappers into compact semantic request text."""
        raw = str(text or "").strip()
        if not raw:
            return ""

        extracted: List[str] = []
        for line in raw.splitlines():
            token = str(line or "").strip()
            if not token:
                continue
            lowered = token.lower()
            if lowered.startswith("thread root:"):
                token = token.split(":", 1)[1].strip() if ":" in token else ""
            elif lowered.startswith("current request:"):
                token = token.split(":", 1)[1].strip() if ":" in token else ""
            elif lowered.startswith("thread context:"):
                continue
            elif token.startswith("- "):
                token = token[2:].strip()
            if token:
                extracted.append(token)

        if not extracted:
            return raw

        deduped: List[str] = []
        seen: set[str] = set()
        for token in extracted:
            key = " ".join(str(token or "").lower().split())
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(token)
        return ". ".join(deduped).strip() or raw

    def _resolve_missing_field_value(self, *, field_name: str, user_message: str) -> Optional[str]:
        name = str(field_name or "").strip().lower()
        raw_text = str(user_message or "").strip()
        text = self._compact_contextual_request_text(raw_text)
        if not text:
            return None
        if name in {"title", "task_title", "summary"}:
            cleaned = re.sub(r"^(title|task title|summary)\s*(is|:)\s*", "", text, flags=re.IGNORECASE).strip()
            if cleaned and cleaned != text:
                return cleaned

            for pattern in (
                r"\b(?:can you|could you|would you|please)\s+(.+?)(?:[?.!]|$)",
                r"\b(?:work on|handle|take care of|follow up on|check on)\s+(.+?)(?:[?.!]|$)",
            ):
                match = re.search(pattern, text, flags=re.IGNORECASE)
                if not match:
                    continue
                candidate = str(match.group(1) or "").strip(" \t\r\n.,:;!?-")
                if not candidate:
                    continue
                return candidate[0].upper() + candidate[1:] if candidate else None

            # Ignore plain approval/ack confirmations for missing-field resolution.
            if self._parse_confirmation_decision(text) is not None:
                return None

            # For direct follow-ups, accept concise single-line text as candidate value.
            if "\n" not in raw_text and len(text) <= 140:
                return text
            return None
        if name in {"external_id", "task_id"}:
            match = re.search(r"\b[A-Z][A-Z0-9]+-\d+\b", text.upper())
            if not match:
                return None
            return str(match.group(0) or "").strip().upper() or None
        if name in {"assignee", "assignee_email", "assignee_value"}:
            if self._contains_unassigned_intent(text):
                return "unassigned"
            return self._extract_first_email(text)
        if name == "status":
            status_aliases = {
                "todo": "todo",
                "to do": "todo",
                "in progress": "in_progress",
                "done": "done",
                "completed": "done",
                "complete": "done",
                "closed": "done",
                "pending": "pending",
            }
            return resolve_enum_alias(text, status_aliases)
        return text

    def _is_pending_confirmation_expired(
        self,
        pending_confirmation: Dict[str, Any],
        *,
        now: datetime,
    ) -> bool:
        expires_at = pending_confirmation.get("pending_tool_expires_at")
        if not expires_at:
            return False
        if isinstance(expires_at, datetime):
            return expires_at <= now
        if isinstance(expires_at, str):
            try:
                parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if parsed.tzinfo is not None:
                    parsed = parsed.replace(tzinfo=None)
                return parsed <= now
            except Exception:
                return False
        return False

    def _parse_confirmation_decision(self, user_message: str) -> Optional[str]:
        normalized = (user_message or "").strip().lower()
        if normalized in {
            "approve",
            "approved",
            "yes",
            "y",
            "ok",
            "okay",
            "proceed",
            "go ahead",
            "continue",
            "sure",
            "please do",
            "do it",
            "go for it",
            "yes proceed",
        }:
            return "approve"
        if normalized in {"decline", "declined", "no", "n", "cancel"}:
            return "decline"
        return None

    def _build_pending_confirmation_from_executor(
        self,
        *,
        execute_result: ExecutorResult,
        run_id: str,
    ) -> Optional[Dict[str, Any]]:
        raw = execute_result.raw_response or {}
        interruptions = raw.get("approval_interruptions") or []
        if not interruptions:
            return None

        first = interruptions[0]
        if not isinstance(first, dict):
            return None

        tool_name = str(first.get("tool_name") or "").strip()
        call_id = str(first.get("call_id") or "").strip()
        args = first.get("arguments")
        if not tool_name or not call_id:
            return None

        now = datetime.utcnow()
        expires_at = now + timedelta(seconds=self.CONFIRMATION_TIMEOUT_SECONDS)
        return {
            "run_id": run_id,
            "pending_tool_call_id": call_id,
            "pending_tool_name": tool_name,
            "pending_tool_args_hash": self._hash_tool_args(args),
            "pending_tool_expires_at": expires_at,
            "requested_at": now,
        }

    def _hash_tool_args(self, args: Any) -> str:
        try:
            normalized = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
        except Exception:
            normalized = str(args)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    def _build_confirmation_prompt(self, pending_confirmation: Dict[str, Any]) -> str:
        if str(pending_confirmation.get("type") or "").strip().lower() == "proposed_tool_calls":
            summary = str(pending_confirmation.get("proposal_summary") or "").strip()
            if summary:
                return f"{summary} Should I proceed?"
            return "I inferred an actionable request. Should I proceed?"
        tool_name = str(pending_confirmation.get("pending_tool_name") or "this action")
        return (
            f"Approval needed for `{tool_name}`. "
            "Reply with `approve` to continue or `decline` to cancel."
        )

    async def _execute_confirmed_proposal_tool_calls(
        self,
        *,
        pending_confirmation: Dict[str, Any],
        session_key: str,
    ) -> OrchestratorResult:
        calls = pending_confirmation.get("proposed_tool_calls")
        execution_context = pending_confirmation.get("execution_context")
        if not isinstance(calls, list) or not isinstance(execution_context, dict):
            return OrchestratorResult(
                ok=False,
                response_text="I couldn't run that proposal because the stored context is invalid. Please try again.",
                error="invalid_pending_proposal_payload",
                metadata={"session_key": session_key},
            )

        if str(pending_confirmation.get("type") or "").strip().lower() == "proposed_tool_calls":
            existing_tokens = [
                str(item or "").strip()
                for item in (execution_context.get("quality_user_confirmed_fields") or [])
                if str(item or "").strip()
            ]
            merged_tokens: List[str] = []
            seen_tokens: set[str] = set()
            for token in existing_tokens:
                lowered = token.lower()
                if not lowered or lowered in seen_tokens:
                    continue
                seen_tokens.add(lowered)
                merged_tokens.append(token)

            for call in calls:
                if not isinstance(call, dict):
                    continue
                tool_name = str(call.get("tool_name") or "").strip()
                args = call.get("args")
                if not tool_name or not isinstance(args, dict):
                    continue
                for field_name, value in args.items():
                    key = str(field_name or "").strip()
                    if not key:
                        continue
                    if value is None:
                        continue
                    if isinstance(value, str) and not value.strip():
                        continue
                    for token in (key, f"{tool_name}.{key}"):
                        lowered = token.lower()
                        if not lowered or lowered in seen_tokens:
                            continue
                        seen_tokens.add(lowered)
                        merged_tokens.append(token)
            execution_context["quality_user_confirmed_fields"] = merged_tokens

        calls = await self._repair_calls_with_context_if_needed(
            calls=calls,
            execution_context=execution_context,
        )

        write_prepared_by_index: Dict[int, Dict[str, Any]] = {}
        if self._should_force_write_batch_preflight(calls):
            (
                write_prepared_by_index,
                preflight_missing_fields,
                preflight_unresolved_calls,
                preflight_failed,
            ) = await self._preflight_write_calls_for_proposal(
                calls=calls,
                execution_context=execution_context,
            )
            if preflight_missing_fields:
                missing_fields = self._coalesce_missing_field_tokens(preflight_missing_fields)
                if preflight_unresolved_calls:
                    await self._set_pending_missing_fields_question(
                        session_key=session_key,
                        missing_fields=missing_fields,
                        unresolved_calls=preflight_unresolved_calls,
                        execution_context=execution_context,
                    )
                clarification_text = self._build_missing_fields_response(missing_fields)
                return OrchestratorResult(
                    ok=True,
                    response_text=clarification_text,
                    metadata={
                        "session_key": session_key,
                        "proposal_executed": False,
                        "awaiting_question": True,
                        "write_preflight_blocked": True,
                    },
                )
            if preflight_failed:
                return OrchestratorResult(
                    ok=False,
                    response_text=(
                        "I couldn't run the approved actions because write preflight failed. "
                        f"{self._join_action_texts(preflight_failed)}."
                    ),
                    error="proposal_preflight_failed",
                    metadata={
                        "session_key": session_key,
                        "proposal_executed": False,
                        "write_preflight_blocked": True,
                    },
                )

        completed: List[str] = []
        failed: List[str] = []
        missing_fields: List[str] = []
        unresolved_calls: List[Dict[str, Any]] = []
        read_candidate_targets: List[str] = []
        for idx, item in enumerate(calls):
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool_name") or "").strip()
            args = item.get("args")
            if not tool_name or not isinstance(args, dict):
                continue

            canonical_args = self._canonicalize_tool_args_for_execution(
                tool_name=tool_name,
                args=args,
            )
            if idx in write_prepared_by_index:
                execution_args = dict(write_prepared_by_index.get(idx) or {})
            else:
                (
                    prepared_args,
                    prepare_missing,
                    prepare_failed,
                    prepared_canonical_args,
                ) = await self._prepare_tool_args_for_orchestrator_execution(
                    tool_name=tool_name,
                    args=canonical_args,
                    execution_context=execution_context,
                )
                if prepare_missing:
                    for token in prepare_missing:
                        if token not in missing_fields:
                            missing_fields.append(token)
                    unresolved_calls.append({"tool_name": tool_name, "args": prepared_canonical_args})
                    continue
                if prepare_failed:
                    failed.append(prepare_failed)
                    continue
                execution_args = dict(prepared_args or prepared_canonical_args)

            if (
                self._is_write_tool_call(tool_name)
                and read_candidate_targets
                and self._is_all_matches_scope_request(execution_context)
                and str(execution_context.get("bulk_scope_decision") or "").strip().lower()
                != "selected_only"
            ):
                remaining_calls = [entry for entry in calls[idx:] if isinstance(entry, dict)]
                mismatch = self._compute_scope_mismatch(
                    calls=remaining_calls,
                    candidate_targets=read_candidate_targets,
                )
                if mismatch is not None:
                    await self._set_pending_scope_confirmation_question(
                        session_key=session_key,
                        candidate_targets=mismatch.get("candidate_targets") or [],
                        selected_targets=mismatch.get("selected_targets") or [],
                        missing_targets=mismatch.get("missing_targets") or [],
                        unresolved_calls=remaining_calls,
                        execution_context=execution_context,
                    )
                    return OrchestratorResult(
                        ok=True,
                        response_text=self._build_scope_confirmation_response(
                            candidate_targets=mismatch.get("candidate_targets") or [],
                            selected_targets=mismatch.get("selected_targets") or [],
                            missing_targets=mismatch.get("missing_targets") or [],
                        ),
                        metadata={
                            "session_key": session_key,
                            "proposal_executed": False,
                            "awaiting_question": True,
                            "scope_confirmation_required": True,
                        },
                    )

            result = await self.executor.execute_tool_call(
                tool_name=tool_name,
                args=execution_args,
                execution_context=execution_context,
                enforce_approval=True,
            )
            if isinstance(result, dict) and bool(result.get("ok")):
                if not self._is_write_tool_call(tool_name):
                    for token in self._extract_target_refs_from_result(result):
                        if token not in read_candidate_targets:
                            read_candidate_targets.append(token)
                summary = self._extract_committed_action_summary_from_result(result, tool_name=tool_name)
                if summary:
                    completed.append(summary)
            else:
                tool_user_message = ""
                error_code = ""
                if isinstance(result, dict):
                    tool_user_message = str(result.get("user_message") or "").strip()
                    error_code = str(result.get("error_code") or "").strip().lower()
                if error_code == "insufficient_argument_quality":
                    extracted = self._extract_insufficient_fields_from_tool_error(
                        tool_name=tool_name,
                        result=result if isinstance(result, dict) else {},
                    )
                    for token in extracted:
                        if token not in missing_fields:
                            missing_fields.append(token)
                    unresolved_calls.append({"tool_name": tool_name, "args": execution_args})
                    continue
                failed.append(tool_user_message or f"{tool_name} failed")

        if missing_fields:
            missing_fields = self._coalesce_missing_field_tokens(missing_fields)
            if unresolved_calls:
                await self._set_pending_missing_fields_question(
                    session_key=session_key,
                    missing_fields=missing_fields,
                    unresolved_calls=unresolved_calls,
                    execution_context=execution_context,
                )
            clarification_text = self._build_missing_fields_response(missing_fields)
            if completed:
                await self._record_committed_actions(session_key=session_key, action_texts=completed)
                return OrchestratorResult(
                    ok=False,
                    response_text=(
                        f"Completed: {self._join_action_texts(completed)}. "
                        f"{clarification_text}"
                    ),
                    error="proposal_missing_fields",
                    metadata={"session_key": session_key, "proposal_executed": True},
                )
            return OrchestratorResult(
                ok=True,
                response_text=clarification_text,
                metadata={
                    "session_key": session_key,
                    "proposal_executed": False,
                    "awaiting_question": True,
                },
            )

        if completed and not failed:
            text = f"Completed: {self._join_action_texts(completed)}."
            await self._record_committed_actions(session_key=session_key, action_texts=completed)
            return OrchestratorResult(
                ok=True,
                response_text=text,
                metadata={"session_key": session_key, "proposal_executed": True},
            )
        if completed and failed:
            text = (
                f"I completed part of the approved actions. Completed: {self._join_action_texts(completed)}. "
                f"Not completed: {self._join_action_texts(failed)}."
            )
            await self._record_committed_actions(session_key=session_key, action_texts=completed)
            return OrchestratorResult(
                ok=False,
                response_text=text,
                error="proposal_partial_failure",
                metadata={"session_key": session_key, "proposal_executed": True},
            )
        return OrchestratorResult(
            ok=False,
            response_text=f"I couldn't complete the approved action. {self._join_action_texts(failed)}.",
            error="proposal_execution_failed",
            metadata={"session_key": session_key, "proposal_executed": True},
        )

    async def _maybe_handle_implicit_tool_proposal_turn(
        self,
        *,
        session_key: str,
        user_message: str,
        channel_id: str,
        thread_ts: Optional[str],
        source: str,
        project_id: str,
        user_id: str,
        role: str,
        actor_email: Optional[str],
        connected_integrations: list[str],
        visible_tool_descriptors: list[Dict[str, Any]],
        project_context: Dict[str, Any],
        run_id: str,
        started_at: datetime,
        team_id: Optional[str],
        member_candidates: List[Dict[str, Any]],
        message_context_source: str,
    ) -> Optional[OrchestratorResult]:
        if not self._should_try_implicit_tool_proposal(
            user_message=user_message,
            connected_integrations=connected_integrations,
            member_candidates=member_candidates,
        ):
            return None

        proposal_user_message = self._compact_contextual_request_text(user_message) or str(user_message or "").strip()

        proposal = await self.proposal_service.propose(
            executor=self.executor,
            user_message=proposal_user_message,
            visible_tools=visible_tool_descriptors,
            tool_contracts=self._proposal_tool_contracts(visible_tool_descriptors),
            member_candidates=member_candidates,
            message_context_source=message_context_source,
            context={
                "project_id": project_id,
                "user_id": user_id,
                "session_key": session_key,
                "source": source,
                "run_id": run_id,
                "role": role,
            },
        )
        if proposal is None:
            return None

        scope_classification = self._normalize_proposal_scope_classification(
            str(getattr(proposal, "scope_classification", "") or "")
        )
        policy_reason_code = str(getattr(proposal, "policy_reason_code", "") or "").strip().lower()

        if scope_classification == "out_of_scope":
            response_text = build_scope_refusal_response()
            delivery = await self._deliver_response(
                text=response_text,
                channel_id=channel_id,
                thread_ts=thread_ts,
                metadata={
                    "source": source,
                    "project_id": project_id,
                    "team_id": team_id,
                    "session_key": session_key,
                },
            )
            await self._append_event(
                session_key=session_key,
                event_type="proposal_scope_block",
                payload={
                    "run_id": run_id,
                    "scope_classification": scope_classification,
                    "policy_reason_code": policy_reason_code or "proposal_marked_out_of_scope",
                },
            )
            await self._persist_turn_metadata(
                session_key=session_key,
                run_id=run_id,
                status="completed",
                source=source,
                project_id=project_id,
                user_id=user_id,
                user_message=user_message,
                request_meta={
                    "implicit_proposal": True,
                    "scope_classification": scope_classification,
                    "policy_reason_code": policy_reason_code or "proposal_marked_out_of_scope",
                },
                executor_meta={"proposal_scope_enforced": True},
                delivery_meta=delivery,
                response_text=response_text,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                error="out_of_scope",
            )
            return OrchestratorResult(
                ok=False,
                response_text=None if delivery.get("ok") else response_text,
                error="out_of_scope",
                metadata={
                    "run_id": run_id,
                    "session_key": session_key,
                    "scope_policy": {
                        "classification": scope_classification,
                        "reason_code": policy_reason_code or "proposal_marked_out_of_scope",
                    },
                    "delivered": bool(delivery.get("ok", False)),
                    "delivery": delivery,
                },
            )

        if scope_classification == "ambiguous":
            response_text = (
                "I can help with project operations in ProMarshal. "
                "Do you want me to list work items, check work item status, create a work item, or update a work item?"
            )
            delivery = await self._deliver_response(
                text=response_text,
                channel_id=channel_id,
                thread_ts=thread_ts,
                metadata={
                    "source": source,
                    "project_id": project_id,
                    "team_id": team_id,
                    "session_key": session_key,
                },
            )
            await self._persist_turn_metadata(
                session_key=session_key,
                run_id=run_id,
                status="completed",
                source=source,
                project_id=project_id,
                user_id=user_id,
                user_message=user_message,
                request_meta={
                    "implicit_proposal": True,
                    "scope_classification": scope_classification,
                    "policy_reason_code": policy_reason_code or "proposal_scope_ambiguous",
                },
                executor_meta={"proposal_scope_enforced": True},
                delivery_meta=delivery,
                response_text=response_text,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                error=None,
            )
            return OrchestratorResult(
                ok=True,
                response_text=None if delivery.get("ok") else response_text,
                metadata={"session_key": session_key, "scope_clarification": True},
            )

        (
            resolved_proposal_calls,
            capability_routing_resolved,
            capability_routing_unresolved,
        ) = self._resolve_proposal_calls_to_tools(
            proposal=proposal,
            role=role,
            connected_integrations=connected_integrations,
            project_context=project_context,
        )
        proposal.proposed_tool_calls = resolved_proposal_calls
        normalized_unresolved = self._normalize_capability_unresolved_entries(
            unresolved=capability_routing_unresolved,
            path="proposal",
        )
        normalized_resolved = self._normalize_capability_resolved_entries(
            resolved=capability_routing_resolved,
            path="proposal",
        )
        if proposal.is_actionable and normalized_unresolved:
            ambiguous_payload = self._collect_ambiguous_provider_payload(
                unresolved=capability_routing_unresolved,
            )
            if ambiguous_payload:
                provider_execution_context = {
                    "project_id": project_id,
                    "user_id": user_id,
                    "actor_email": actor_email,
                    "user_message": user_message,
                    "latest_user_message": user_message,
                    "session_key": session_key,
                    "source": source,
                    "run_id": run_id,
                    "role": role,
                    "connected_integrations": connected_integrations,
                    "project_context": project_context,
                    "member_candidates": member_candidates,
                }
                await self._set_pending_provider_selection_question(
                    session_key=session_key,
                    candidate_providers=ambiguous_payload.get("providers") or [],
                    unresolved_calls=ambiguous_payload.get("unresolved_calls") or [],
                    execution_context=provider_execution_context,
                    capability=ambiguous_payload.get("capability"),
                )
                response_text = self._build_provider_selection_response(
                    providers=ambiguous_payload.get("providers") or [],
                    capability=ambiguous_payload.get("capability"),
                )
                delivery = await self._deliver_response(
                    text=response_text,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    metadata={
                        "source": source,
                        "project_id": project_id,
                        "team_id": team_id,
                        "session_key": session_key,
                    },
                )
                await self._emit_capability_unresolved_event(
                    session_key=session_key,
                    run_id=run_id,
                    unresolved=normalized_unresolved,
                    resolved=normalized_resolved,
                )
                await self._persist_turn_metadata(
                    session_key=session_key,
                    run_id=run_id,
                    status="awaiting_question",
                    source=source,
                    project_id=project_id,
                    user_id=user_id,
                    user_message=user_message,
                    request_meta={
                        "implicit_proposal": True,
                        "scope_classification": scope_classification,
                        "policy_reason_code": "ambiguous_provider",
                    },
                    executor_meta={
                        "capability_routing": {
                            "resolved": normalized_resolved,
                            "unresolved": normalized_unresolved,
                        },
                        "provider_selection_required": True,
                        "provider_candidates": ambiguous_payload.get("providers") or [],
                    },
                    delivery_meta=delivery,
                    response_text=response_text,
                    started_at=started_at,
                    finished_at=datetime.utcnow(),
                    error=None,
                )
                return OrchestratorResult(
                    ok=True,
                    response_text=None if delivery.get("ok") else response_text,
                    metadata={
                        "run_id": run_id,
                        "session_key": session_key,
                        "awaiting_question": True,
                        "provider_selection_required": True,
                        "capability_routing": {
                            "resolved": normalized_resolved,
                            "unresolved": normalized_unresolved,
                        },
                        "delivery": delivery,
                    },
                )

            response_text = self._build_unsupported_operation_response(normalized_unresolved)
            delivery = await self._deliver_response(
                text=response_text,
                channel_id=channel_id,
                thread_ts=thread_ts,
                metadata={
                    "source": source,
                    "project_id": project_id,
                    "team_id": team_id,
                    "session_key": session_key,
                },
            )
            await self._emit_capability_unresolved_event(
                session_key=session_key,
                run_id=run_id,
                unresolved=normalized_unresolved,
                resolved=normalized_resolved,
            )
            await self._persist_turn_metadata(
                session_key=session_key,
                run_id=run_id,
                status="completed",
                source=source,
                project_id=project_id,
                user_id=user_id,
                user_message=user_message,
                request_meta={
                    "implicit_proposal": True,
                    "scope_classification": scope_classification,
                    "policy_reason_code": "unsupported_capability",
                },
                executor_meta={
                    "capability_routing": {
                        "resolved": normalized_resolved,
                        "unresolved": normalized_unresolved,
                    }
                },
                delivery_meta=delivery,
                response_text=response_text,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                error="unsupported_capability",
            )
            return OrchestratorResult(
                ok=False,
                response_text=None if delivery.get("ok") else response_text,
                error="unsupported_capability",
                metadata={
                    "run_id": run_id,
                    "session_key": session_key,
                    "capability_routing": {
                        "resolved": normalized_resolved,
                        "unresolved": normalized_unresolved,
                    },
                    "delivery": delivery,
                },
            )

        needs_tool_call = bool(proposal.is_actionable and proposal.proposed_tool_calls)

        if scope_classification == "in_scope_action" and not needs_tool_call:
            response_text = (
                "I can help with that, but I need a concrete project action. "
                "For example: `list my work items`, `show SCRUM-10 status`, or `create a work item for release checklist`."
            )
            delivery = await self._deliver_response(
                text=response_text,
                channel_id=channel_id,
                thread_ts=thread_ts,
                metadata={
                    "source": source,
                    "project_id": project_id,
                    "team_id": team_id,
                    "session_key": session_key,
                },
            )
            await self._persist_turn_metadata(
                session_key=session_key,
                run_id=run_id,
                status="completed",
                source=source,
                project_id=project_id,
                user_id=user_id,
                user_message=user_message,
                request_meta={
                    "implicit_proposal": True,
                    "scope_classification": scope_classification,
                    "policy_reason_code": policy_reason_code or "actionable_without_tool_calls",
                },
                executor_meta={"proposal_scope_enforced": True, "needs_tool_call": True, "tool_calls_count": 0},
                delivery_meta=delivery,
                response_text=response_text,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                error=None,
            )
            return OrchestratorResult(
                ok=True,
                response_text=None if delivery.get("ok") else response_text,
                metadata={"session_key": session_key, "scope_clarification": True},
            )

        if scope_classification == "in_scope_info" and not needs_tool_call:
            if self._looks_like_capability_help_query(proposal_user_message):
                response_text = self._promarshal_capability_response()
                delivery = await self._deliver_response(
                    text=response_text,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    metadata={
                        "source": source,
                        "project_id": project_id,
                        "team_id": team_id,
                        "session_key": session_key,
                    },
                )
                await self._persist_turn_metadata(
                    session_key=session_key,
                    run_id=run_id,
                    status="completed",
                    source=source,
                    project_id=project_id,
                    user_id=user_id,
                    user_message=user_message,
                    request_meta={
                        "implicit_proposal": True,
                        "scope_classification": scope_classification,
                        "policy_reason_code": policy_reason_code or "capability_query",
                    },
                    executor_meta={"proposal_scope_enforced": True, "needs_tool_call": False},
                    delivery_meta=delivery,
                    response_text=response_text,
                    started_at=started_at,
                    finished_at=datetime.utcnow(),
                    error=None,
                )
                return OrchestratorResult(
                    ok=True,
                    response_text=None if delivery.get("ok") else response_text,
                    metadata={"session_key": session_key, "scope_capability_response": True},
                )
            response_text = build_scope_refusal_response()
            delivery = await self._deliver_response(
                text=response_text,
                channel_id=channel_id,
                thread_ts=thread_ts,
                metadata={
                    "source": source,
                    "project_id": project_id,
                    "team_id": team_id,
                    "session_key": session_key,
                },
            )
            await self._persist_turn_metadata(
                session_key=session_key,
                run_id=run_id,
                status="completed",
                source=source,
                project_id=project_id,
                user_id=user_id,
                user_message=user_message,
                request_meta={
                    "implicit_proposal": True,
                    "scope_classification": scope_classification,
                    "policy_reason_code": policy_reason_code or "in_scope_info_without_operation",
                },
                executor_meta={"proposal_scope_enforced": True, "needs_tool_call": False},
                delivery_meta=delivery,
                response_text=response_text,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                error="out_of_scope",
            )
            return OrchestratorResult(
                ok=False,
                response_text=None if delivery.get("ok") else response_text,
                error="out_of_scope",
                metadata={
                    "run_id": run_id,
                    "session_key": session_key,
                    "scope_policy": {
                        "classification": "out_of_scope",
                        "reason_code": policy_reason_code or "in_scope_info_without_operation",
                    },
                    "delivered": bool(delivery.get("ok", False)),
                    "delivery": delivery,
                },
            )

        if not proposal.is_actionable or not proposal.proposed_tool_calls:
            return None

        proposal_execution_context: Dict[str, Any] = {
            "project_id": project_id,
            "user_id": user_id,
            "actor_email": actor_email,
            "user_message": proposal_user_message,
            "latest_user_message": proposal_user_message,
            "session_key": session_key,
            "source": source,
            "run_id": run_id,
            "role": role,
            "connected_integrations": connected_integrations,
            "member_candidates": member_candidates,
            "message_context_source": message_context_source,
            "quality_prevalidated_fields": [],
            "repair_attempts_used": 0,
        }

        strict_missing = self._proposal_missing_required_args(proposal)
        if strict_missing:
            proposal_calls_for_repair: List[Dict[str, Any]] = [
                {
                    "tool_name": str(item.tool_name or "").strip(),
                    "args": self._canonicalize_tool_args_for_execution(
                        tool_name=str(item.tool_name or "").strip(),
                        args=dict(item.args),
                    ),
                }
                for item in proposal.proposed_tool_calls
                if str(item.tool_name or "").strip()
            ]
            repaired_calls = await self._repair_calls_with_context_if_needed(
                calls=proposal_calls_for_repair,
                execution_context=proposal_execution_context,
            )
            if repaired_calls:
                for idx, call in enumerate(proposal.proposed_tool_calls):
                    if idx >= len(repaired_calls):
                        break
                    repaired_item = repaired_calls[idx]
                    repaired_tool_name = str(repaired_item.get("tool_name") or "").strip()
                    if repaired_tool_name and repaired_tool_name != str(call.tool_name or "").strip():
                        continue
                    repaired_args = repaired_item.get("args")
                    if not isinstance(repaired_args, dict):
                        continue
                    canonical_args = self._canonicalize_tool_args_for_execution(
                        tool_name=str(call.tool_name or "").strip(),
                        args=repaired_args,
                    )
                    if canonical_args != dict(call.args or {}):
                        call.args = canonical_args
            repaired_missing = self._proposal_missing_required_args(proposal)
            strict_missing = repaired_missing
        quality_missing_fields: List[str] = []
        proposal_meta_prevalidated = self._proposal_prevalidated_tokens(proposal.proposed_tool_calls)
        quality_prevalidated_fields: List[str] = list(proposal_meta_prevalidated)
        proposal_execution_context["quality_prevalidated_fields"] = list(proposal_meta_prevalidated)
        proposal_calls_for_quality: List[Dict[str, Any]] = [
            {
                "tool_name": str(item.tool_name or "").strip(),
                "args": self._canonicalize_tool_args_for_execution(
                    tool_name=str(item.tool_name or "").strip(),
                    args=dict(item.args),
                ),
                "field_meta": dict(getattr(item, "field_meta", {}) or {}),
            }
            for item in proposal.proposed_tool_calls
            if str(item.tool_name or "").strip()
        ]
        if proposal_calls_for_quality:
            refined_calls, quality_missing_fields, refined_quality_prevalidated_fields = (
                await self._refine_proposal_calls_with_quality(
                    calls=proposal_calls_for_quality,
                    visible_tool_descriptors=visible_tool_descriptors,
                    execution_context=proposal_execution_context,
                )
            )
            if refined_calls:
                for idx, call in enumerate(proposal.proposed_tool_calls):
                    if idx >= len(refined_calls):
                        break
                    refined = refined_calls[idx]
                    if str(refined.get("tool_name") or "").strip() != str(call.tool_name or "").strip():
                        continue
                    args = refined.get("args")
                    if isinstance(args, dict):
                        call.args = self._canonicalize_tool_args_for_execution(
                            tool_name=str(call.tool_name or "").strip(),
                            args=args,
                        )
                    refined_field_meta = refined.get("field_meta")
                    if isinstance(refined_field_meta, dict):
                        call.field_meta = dict(refined_field_meta)
            quality_prevalidated_fields = sorted(
                set(quality_prevalidated_fields + refined_quality_prevalidated_fields)
            )
            proposal_execution_context["quality_prevalidated_fields"] = list(quality_prevalidated_fields)
        # Use repaired+validated runtime required fields as source of truth.
        combined_missing = self._coalesce_missing_field_tokens(strict_missing + quality_missing_fields)

        unresolved_delegate_names = self._infer_unresolved_delegate_names(
            project_context=project_context,
            user_message=proposal_user_message,
            member_candidates=member_candidates,
        )
        needs_confirmation = self._proposal_needs_confirmation(
            proposal=proposal,
            visible_tool_descriptors=visible_tool_descriptors,
        )
        if unresolved_delegate_names and not member_candidates:
            unresolved = ", ".join(unresolved_delegate_names[:3])
            response_text = (
                f"I understood the request, but I couldn't map `{unresolved}` to an active project member. "
                "Should I create the task unassigned, or share the member email so I can assign it?"
            )
            now = datetime.utcnow()
            pending_question = {
                "type": "proposal_unresolved_delegate",
                "run_id": run_id,
                "pending_question_expires_at": now + timedelta(seconds=self.QUESTION_TIMEOUT_SECONDS),
                "requested_at": now,
                "unresolved_delegate_names": unresolved_delegate_names[:5],
                "proposed_tool_calls": [
                    {
                        "tool_name": item.tool_name,
                        "args": self._canonicalize_tool_args_for_execution(
                            tool_name=str(item.tool_name or "").strip(),
                            args=dict(item.args),
                        ),
                    }
                    for item in proposal.proposed_tool_calls
                ],
                "execution_context": {
                    "project_id": project_id,
                    "user_id": user_id,
                    "actor_email": actor_email,
                    "user_message": proposal_user_message,
                    "latest_user_message": proposal_user_message,
                    "session_key": session_key,
                    "source": source,
                    "run_id": run_id,
                    "role": role,
                    "connected_integrations": connected_integrations,
                    "member_candidates": member_candidates,
                    "message_context_source": message_context_source,
                    "quality_prevalidated_fields": quality_prevalidated_fields,
                    "repair_attempts_used": int(proposal_execution_context.get("repair_attempts_used") or 0),
                },
            }
            await self.session_manager.set_pending_question(
                session_key=session_key,
                pending_question=pending_question,
            )
            await self._append_event(
                session_key=session_key,
                event_type="implicit_proposal_question_requested",
                payload={
                    "run_id": run_id,
                    "question_type": "proposal_unresolved_delegate",
                    "unresolved_delegate_names": unresolved_delegate_names[:5],
                },
            )
            delivery = await self._deliver_response(
                text=response_text,
                channel_id=channel_id,
                thread_ts=thread_ts,
                metadata={
                    "source": source,
                    "project_id": project_id,
                    "team_id": team_id,
                    "session_key": session_key,
                },
            )
            await self._persist_turn_metadata(
                session_key=session_key,
                run_id=run_id,
                status="awaiting_question",
                source=source,
                project_id=project_id,
                user_id=user_id,
                user_message=user_message,
                request_meta={
                    "implicit_proposal": True,
                    "proposal_confidence": proposal.confidence,
                },
                executor_meta={"proposal_unresolved_delegate_names": unresolved_delegate_names},
                delivery_meta=delivery,
                response_text=response_text,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                error=None,
            )
            return OrchestratorResult(
                ok=True,
                response_text=None if delivery.get("ok") else response_text,
                metadata={
                    "session_key": session_key,
                    "implicit_proposal": "unresolved_delegate",
                    "awaiting_question": True,
                },
            )

        if combined_missing:
            response_text = self._build_missing_fields_response(combined_missing)
            now = datetime.utcnow()
            pending_question = {
                "type": "proposal_missing_fields",
                "run_id": run_id,
                "pending_question_expires_at": now + timedelta(seconds=self.QUESTION_TIMEOUT_SECONDS),
                "requested_at": now,
                "missing_fields": combined_missing,
                "proposed_tool_calls": [
                    {
                        "tool_name": item.tool_name,
                        "args": self._canonicalize_tool_args_for_execution(
                            tool_name=str(item.tool_name or "").strip(),
                            args=dict(item.args),
                        ),
                    }
                    for item in proposal.proposed_tool_calls
                ],
                "execution_context": {
                    "project_id": project_id,
                    "user_id": user_id,
                    "actor_email": actor_email,
                    "user_message": proposal_user_message,
                    "latest_user_message": proposal_user_message,
                    "session_key": session_key,
                    "source": source,
                    "run_id": run_id,
                    "role": role,
                    "connected_integrations": connected_integrations,
                    "member_candidates": member_candidates,
                    "message_context_source": message_context_source,
                    "quality_prevalidated_fields": quality_prevalidated_fields,
                    "repair_attempts_used": int(proposal_execution_context.get("repair_attempts_used") or 0),
                },
            }
            await self.session_manager.set_pending_question(
                session_key=session_key,
                pending_question=pending_question,
            )
            await self._append_event(
                session_key=session_key,
                event_type="implicit_proposal_question_requested",
                payload={
                    "run_id": run_id,
                    "question_type": "proposal_missing_fields",
                    "missing_fields": combined_missing,
                },
            )
            delivery = await self._deliver_response(
                text=response_text,
                channel_id=channel_id,
                thread_ts=thread_ts,
                metadata={
                    "source": source,
                    "project_id": project_id,
                    "team_id": team_id,
                    "session_key": session_key,
                },
            )
            await self._persist_turn_metadata(
                session_key=session_key,
                run_id=run_id,
                status="awaiting_question",
                source=source,
                project_id=project_id,
                user_id=user_id,
                user_message=user_message,
                request_meta={
                    "implicit_proposal": True,
                    "proposal_confidence": proposal.confidence,
                },
                executor_meta={"proposal_missing_fields": combined_missing},
                delivery_meta=delivery,
                response_text=response_text,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                error=None,
            )
            return OrchestratorResult(
                ok=True,
                response_text=None if delivery.get("ok") else response_text,
                metadata={
                    "session_key": session_key,
                    "implicit_proposal": "missing_fields",
                    "awaiting_question": True,
                },
            )

        if needs_confirmation:
            now = datetime.utcnow()
            expires_at = now + timedelta(seconds=self.CONFIRMATION_TIMEOUT_SECONDS)
            proposal_summary = self._proposal_summary_text(proposal)
            pending_confirmation = {
                "type": "proposed_tool_calls",
                "run_id": run_id,
                "pending_tool_call_id": f"proposal:{run_id}",
                "pending_tool_name": str(
                    proposal.proposed_tool_calls[0].tool_name
                    if proposal.proposed_tool_calls
                    else "proposed_action"
                ),
                "pending_tool_expires_at": expires_at,
                "requested_at": now,
                "proposal_summary": proposal_summary,
                "proposal_confidence": proposal.confidence,
                "proposed_tool_calls": [
                    {
                        "tool_name": item.tool_name,
                        "args": self._canonicalize_tool_args_for_execution(
                            tool_name=str(item.tool_name or "").strip(),
                            args=dict(item.args),
                        ),
                    }
                    for item in proposal.proposed_tool_calls
                ],
                "execution_context": {
                    "project_id": project_id,
                    "user_id": user_id,
                    "actor_email": actor_email,
                    "user_message": proposal_user_message,
                    "latest_user_message": proposal_user_message,
                    "session_key": session_key,
                    "source": source,
                    "run_id": run_id,
                    "role": role,
                    "connected_integrations": connected_integrations,
                    "member_candidates": member_candidates,
                    "message_context_source": message_context_source,
                    "quality_prevalidated_fields": quality_prevalidated_fields,
                    "repair_attempts_used": int(proposal_execution_context.get("repair_attempts_used") or 0),
                },
            }
            await self.session_manager.set_pending_confirmation(
                session_key=session_key,
                pending_confirmation=pending_confirmation,
            )
            await self._append_event(
                session_key=session_key,
                event_type="implicit_proposal_confirmation_requested",
                payload={
                    "run_id": run_id,
                    "proposal_confidence": proposal.confidence,
                    "proposal_summary": proposal_summary,
                    "tool_calls": [item.tool_name for item in proposal.proposed_tool_calls],
                },
            )
            response_text = self._build_confirmation_prompt(pending_confirmation)
            delivery = await self._deliver_response(
                text=response_text,
                channel_id=channel_id,
                thread_ts=thread_ts,
                metadata={
                    "source": source,
                    "project_id": project_id,
                    "team_id": team_id,
                    "session_key": session_key,
                },
            )
            await self._persist_turn_metadata(
                session_key=session_key,
                run_id=run_id,
                status="awaiting_confirmation",
                source=source,
                project_id=project_id,
                user_id=user_id,
                user_message=user_message,
                request_meta={
                    "implicit_proposal": True,
                    "proposal_confidence": proposal.confidence,
                },
                executor_meta={
                    "implicit_proposal": True,
                    "proposal_tool_calls": [
                        {
                            "tool_name": item.tool_name,
                            "args": self._canonicalize_tool_args_for_execution(
                                tool_name=str(item.tool_name or "").strip(),
                                args=dict(item.args),
                            ),
                        }
                        for item in proposal.proposed_tool_calls
                    ],
                },
                delivery_meta=delivery,
                response_text=response_text,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                error=None,
            )
            return OrchestratorResult(
                ok=True,
                response_text=None if delivery.get("ok") else response_text,
                metadata={
                    "run_id": run_id,
                    "session_key": session_key,
                    "awaiting_confirmation": True,
                    "implicit_proposal": True,
                    "delivery": delivery,
                },
            )

        # Read-only or low-risk proposal execution path.
        pending_like = {
            "proposed_tool_calls": [
                {
                    "tool_name": item.tool_name,
                    "args": self._canonicalize_tool_args_for_execution(
                        tool_name=str(item.tool_name or "").strip(),
                        args=dict(item.args),
                    ),
                }
                for item in proposal.proposed_tool_calls
            ],
            "execution_context": {
                "project_id": project_id,
                "user_id": user_id,
                "actor_email": actor_email,
                "user_message": proposal_user_message,
                "latest_user_message": proposal_user_message,
                "session_key": session_key,
                "source": source,
                "run_id": run_id,
                "role": role,
                "connected_integrations": connected_integrations,
                "member_candidates": member_candidates,
                "message_context_source": message_context_source,
                "quality_prevalidated_fields": quality_prevalidated_fields,
                "repair_attempts_used": int(proposal_execution_context.get("repair_attempts_used") or 0),
            },
        }
        execution = await self._execute_confirmed_proposal_tool_calls(
            pending_confirmation=pending_like,
            session_key=session_key,
        )
        delivery = await self._deliver_response(
            text=str(execution.response_text or ""),
            channel_id=channel_id,
            thread_ts=thread_ts,
            metadata={
                "source": source,
                "project_id": project_id,
                "team_id": team_id,
                "session_key": session_key,
                "user_id": user_id,
                "tool_calls_count": 1,
            },
        )
        await self._persist_turn_metadata(
            session_key=session_key,
            run_id=run_id,
            status="completed" if execution.ok else "failed",
            source=source,
            project_id=project_id,
            user_id=user_id,
            user_message=user_message,
            request_meta={"implicit_proposal": True, "proposal_confidence": proposal.confidence},
            executor_meta={"implicit_proposal": True, "proposal_executed_without_confirmation": True},
            delivery_meta=delivery,
            response_text=execution.response_text,
            started_at=started_at,
            finished_at=datetime.utcnow(),
            error=execution.error if not execution.ok else None,
        )
        return OrchestratorResult(
            ok=execution.ok,
            response_text=None if delivery.get("ok") else execution.response_text,
            error=execution.error,
            metadata={"session_key": session_key, "implicit_proposal": "executed"},
        )

    def _should_try_implicit_tool_proposal(
        self,
        *,
        user_message: str,
        connected_integrations: List[str],
        member_candidates: List[Dict[str, Any]],
    ) -> bool:
        text = str(user_message or "").strip()
        if not text:
            return False
        available = {str(item).strip().lower() for item in connected_integrations}
        if not available:
            return False
        if is_task_write_intent(text):
            return True
        lowered = text.lower()
        implicit_markers = [
            "can you",
            "could you",
            "would you",
            "please",
            "work on",
            "handle",
            "take care",
            "follow up",
            "remind",
            "check on",
        ]
        if member_candidates:
            return True
        if any(marker in lowered for marker in implicit_markers):
            return True
        if "?" in lowered:
            return True
        if lowered.startswith(("what is ", "what are ", "how does ", "define ", "explain ", "tell me about ")):
            return True
        return False

    def _proposal_needs_confirmation(
        self,
        *,
        proposal: ToolProposal,
        visible_tool_descriptors: List[Dict[str, Any]],
    ) -> bool:
        descriptor_by_name = {
            str(item.get("name") or "").strip(): item
            for item in visible_tool_descriptors
            if isinstance(item, dict)
        }
        saw_any_call = False
        for call in proposal.proposed_tool_calls:
            saw_any_call = True
            descriptor = descriptor_by_name.get(str(call.tool_name or "").strip()) or {}
            requires_confirmation = bool(descriptor.get("requires_confirmation"))
            call_args = call.args if isinstance(call.args, dict) else {}
            tool_name = str(call.tool_name or "").strip()
            idempotency = str(descriptor.get("idempotency") or "").strip().lower()
            if not idempotency:
                try:
                    idempotency = str(
                        self.tool_registry.get(tool_name).normalized_spec().idempotency
                    ).strip().lower()
                except Exception:
                    idempotency = ""
            if requires_confirmation:
                if self._is_safe_explicit_id_write_call(tool_name=tool_name, args=call_args):
                    continue
                return True
            if idempotency in {"non_idempotent", ""}:
                if self._is_safe_explicit_id_write_call(tool_name=tool_name, args=call_args):
                    continue
                return True
        if saw_any_call:
            # For actionable read-only proposals, do not require explicit approval by default.
            return False
        return False

    def _is_safe_explicit_id_write_call(self, *, tool_name: str, args: Dict[str, Any]) -> bool:
        normalized_tool = str(tool_name or "").strip().lower()
        if normalized_tool not in self.SAFE_EXPLICIT_ID_WRITE_TOOLS:
            return False
        external_id = str((args or {}).get("external_id") or "").strip()
        if not external_id or external_id.lower() in {"none", "null", "unknown"}:
            return False
        if normalized_tool == "jira_update_status":
            return bool(str((args or {}).get("status") or "").strip())
        if normalized_tool == "jira_add_comment":
            return bool(str((args or {}).get("comment") or "").strip())
        if normalized_tool == "jira_update_description":
            return bool(str((args or {}).get("description") or "").strip())
        return False

    def _normalize_proposal_scope_classification(self, raw_value: str) -> str:
        token = str(raw_value or "").strip().lower().replace("-", "_").replace(" ", "_")
        if token in {"in_scope_action", "in_scope_info", "out_of_scope", "ambiguous"}:
            return token
        return "in_scope_action"

    def _looks_like_capability_help_query(self, user_message: str) -> bool:
        text = str(user_message or "").strip().lower()
        if not text:
            return False
        capability_markers = (
            "what can promarshal",
            "how can promarshal",
            "what can you do",
            "how can you help",
            "help me with",
            "what do you do",
            "capabilities",
        )
        return any(marker in text for marker in capability_markers)

    def _promarshal_capability_response(self) -> str:
        return (
            "I can help with project operations in ProMarshal: "
            "list/search tasks, check status, create or update tasks, assign owners, add comments, "
            "and summarize blockers or progress."
        )

    def _get_capability_resolver(self) -> CapabilityResolver:
        resolver = getattr(self, "capability_resolver", None)
        if resolver is None:
            resolver = CapabilityResolver(tool_registry=self.tool_registry)
            self.capability_resolver = resolver
        return resolver

    def _resolve_proposal_calls_to_tools(
        self,
        *,
        proposal: ToolProposal,
        role: str,
        connected_integrations: List[str],
        project_context: Dict[str, Any],
    ) -> Tuple[List[ProposedToolCall], List[Dict[str, Any]], List[Dict[str, Any]]]:
        resolved_calls: List[ProposedToolCall] = []
        resolved_meta: List[Dict[str, Any]] = []
        unresolved_meta: List[Dict[str, Any]] = []
        resolver = self._get_capability_resolver()

        for item in proposal.proposed_tool_calls:
            tool_name = str(item.tool_name or "").strip()
            capability = str(item.capability or "").strip().lower() or None
            provider_hint = str(item.provider or "").strip().lower() or None
            args = dict(item.args or {})

            if not capability:
                unresolved_meta.append(
                    {
                        "tool_name": tool_name,
                        "capability": "",
                        "provider_hint": provider_hint,
                        "reason_code": "missing_capability",
                    }
                )
                continue

            resolution = resolver.resolve(
                capability=capability,
                role=role,
                connected_integrations=connected_integrations,
                provider_hint=provider_hint,
                project_context=project_context,
            )
            if resolution.ok and str(resolution.tool_name or "").strip():
                resolved_tool_name = str(resolution.tool_name or "").strip()
                resolved_provider = str(resolution.provider or "").strip().lower() or provider_hint
                resolved_calls.append(
                    ProposedToolCall(
                        tool_name=resolved_tool_name,
                        capability=resolution.capability or capability,
                        provider=resolved_provider,
                        args=args,
                        confidence=item.confidence,
                        reason=item.reason,
                    )
                )
                resolved_meta.append(
                    {
                        "tool_name": resolved_tool_name,
                        "capability": resolution.capability or capability,
                        "provider": resolved_provider,
                        "reason_code": resolution.reason_code,
                        "candidate_providers": list(resolution.candidate_providers or []),
                    }
                )
                continue

            unresolved_meta.append(
                {
                    "tool_name": tool_name,
                    "capability": capability,
                    "provider_hint": provider_hint,
                    "args": dict(item.args or {}),
                    "reason_code": resolution.reason_code or "unsupported_capability",
                    "candidate_providers": list(resolution.candidate_providers or []),
                }
            )

        return resolved_calls, resolved_meta, unresolved_meta

    def _build_unsupported_operation_response(self, unresolved: List[Dict[str, Any]]) -> str:
        ambiguous_provider_entries = [
            item
            for item in unresolved
            if isinstance(item, dict) and str(item.get("reason_code") or "").strip().lower() == "ambiguous_provider"
        ]
        if ambiguous_provider_entries:
            providers: List[str] = []
            capability = ""
            for item in ambiguous_provider_entries:
                if not capability:
                    capability = str(item.get("capability") or "").strip().lower()
                for provider in (item.get("candidate_providers") or []):
                    normalized_provider = str(provider).strip().lower()
                    if normalized_provider and normalized_provider not in providers:
                        providers.append(normalized_provider)
            return self._reason_message(
                "ambiguous_provider",
                context={
                    "providers": providers,
                    "capability": capability,
                },
            )

        operations: List[str] = []
        for item in unresolved:
            if not isinstance(item, dict):
                continue
            label = (
                str(item.get("capability") or "").strip()
                or str(item.get("tool_name") or "").strip()
            )
            if label and label not in operations:
                operations.append(label)

        return self._reason_message(
            "unsupported_capability",
            context={"operations": operations},
        )

    def _collect_ambiguous_provider_payload(
        self,
        *,
        unresolved: List[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        ambiguous_entries = [
            item
            for item in unresolved
            if isinstance(item, dict) and str(item.get("reason_code") or "").strip().lower() == "ambiguous_provider"
        ]
        if not ambiguous_entries:
            return None

        providers: List[str] = []
        capability = ""
        unresolved_calls: List[Dict[str, Any]] = []
        for item in ambiguous_entries:
            if not capability:
                capability = str(item.get("capability") or "").strip().lower()
            for provider in (item.get("candidate_providers") or []):
                normalized_provider = str(provider).strip().lower()
                if normalized_provider and normalized_provider not in providers:
                    providers.append(normalized_provider)

            capability_id = str(item.get("capability") or "").strip().lower()
            args = item.get("args")
            call_args = dict(args) if isinstance(args, dict) else {}
            unresolved_calls.append(
                {
                    "tool_name": str(item.get("tool_name") or "").strip(),
                    "capability": capability_id,
                    "provider_hint": str(item.get("provider_hint") or "").strip().lower() or None,
                    "args": call_args,
                }
            )

        if len(providers) < 2:
            return None
        if not any(str(item.get("capability") or "").strip() for item in unresolved_calls):
            return None
        return {
            "providers": providers,
            "capability": capability,
            "unresolved_calls": unresolved_calls,
        }

    def _build_provider_selection_response(
        self,
        *,
        providers: List[str],
        capability: Optional[str] = None,
    ) -> str:
        provider_list = [str(item).strip().lower() for item in providers if str(item).strip()]
        if not provider_list:
            return self._reason_message("ambiguous_provider")
        base = self._reason_message(
            "ambiguous_provider",
            context={"providers": provider_list, "capability": str(capability or "").strip().lower() or None},
        )
        choices = ", ".join(f"`{item}`" for item in provider_list)
        return f"{base} Reply with one provider: {choices}."

    def _parse_provider_selection_decision(
        self,
        *,
        user_message: str,
        candidate_providers: List[str],
    ) -> Optional[str]:
        text = str(user_message or "").strip().lower()
        if not text:
            return None
        providers = [str(item).strip().lower() for item in candidate_providers if str(item).strip()]
        if not providers:
            return None
        for provider in providers:
            if text == provider:
                return provider
        for provider in providers:
            if re.search(rf"\b{re.escape(provider)}\b", text):
                return provider
        return None

    def _prepare_tool_calls_for_provider_selection_pending(
        self,
        *,
        pending_question: Dict[str, Any],
        user_message: str,
    ) -> tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        candidate_providers = [
            str(item).strip().lower()
            for item in (pending_question.get("candidate_providers") or [])
            if str(item).strip()
        ]
        selected_provider = self._parse_provider_selection_decision(
            user_message=user_message,
            candidate_providers=candidate_providers,
        )
        if not selected_provider:
            return None, self._build_provider_selection_response(
                providers=candidate_providers,
                capability=str(pending_question.get("capability") or "").strip().lower() or None,
            )

        execution_context = pending_question.get("execution_context") or {}
        if isinstance(execution_context, dict):
            execution_context["provider_selection_override"] = selected_provider
            execution_context["provider_hint"] = selected_provider

        role = str(execution_context.get("role") or "").strip() or "member"
        connected_integrations = [
            str(item).strip().lower()
            for item in (execution_context.get("connected_integrations") or [])
            if str(item).strip()
        ]
        project_context = execution_context.get("project_context")
        if not isinstance(project_context, dict):
            project_context = {}

        resolver = self._get_capability_resolver()
        raw_calls = pending_question.get("proposed_tool_calls") or []
        resolved_calls: List[Dict[str, Any]] = []
        unresolved_capabilities: List[str] = []
        for item in raw_calls:
            if not isinstance(item, dict):
                continue
            capability = str(item.get("capability") or "").strip().lower()
            args = item.get("args")
            call_args = dict(args) if isinstance(args, dict) else {}
            if not capability:
                unresolved_capabilities.append("unknown")
                continue
            resolution = resolver.resolve(
                capability=capability,
                role=role,
                connected_integrations=connected_integrations,
                provider_hint=selected_provider,
                project_context=project_context,
            )
            if not resolution.ok or not str(resolution.tool_name or "").strip():
                unresolved_capabilities.append(capability)
                continue
            resolved_calls.append(
                {
                    "tool_name": str(resolution.tool_name or "").strip(),
                    "args": call_args,
                }
            )

        if unresolved_capabilities:
            unresolved_text = self._join_action_texts(sorted(set(unresolved_capabilities)))
            return None, (
                "I couldn't apply that provider to all requested actions. "
                f"Unsupported capabilities: {unresolved_text}."
            )
        if not resolved_calls:
            return None, "I couldn't continue because no executable actions remained after provider selection."
        return resolved_calls, None

    async def _set_pending_provider_selection_question(
        self,
        *,
        session_key: str,
        candidate_providers: List[str],
        unresolved_calls: List[Dict[str, Any]],
        execution_context: Dict[str, Any],
        capability: Optional[str] = None,
    ) -> None:
        now = datetime.utcnow()
        providers = [
            str(item).strip().lower()
            for item in candidate_providers
            if str(item).strip()
        ]
        parser_understanding = {
            "candidate_providers": list(providers),
            "capability": str(capability or "").strip().lower() or None,
            "proposed_tool_calls": list(unresolved_calls or []),
        }
        clarification_payload = build_clarification_payload(
            reason_code="ambiguous_provider",
            original_user_text=(
                str(execution_context.get("latest_user_message") or "").strip()
                or str(execution_context.get("user_message") or "").strip()
            ),
            parser_understanding=parser_understanding,
            clarification_kind="provider_selection",
            retry_count=0,
            max_retries=1,
            extra={
                "session_key": str(session_key or "").strip(),
                "run_id": str(execution_context.get("run_id") or "").strip(),
                "candidate_providers": list(providers),
                "capability": str(capability or "").strip().lower() or None,
                "origin": "provider_selection",
            },
        )
        pending_question = {
            "type": "proposal_scope_confirmation",
            "origin": "provider_selection",
            "run_id": str(execution_context.get("run_id") or "").strip(),
            "pending_question_expires_at": now + timedelta(seconds=self.QUESTION_TIMEOUT_SECONDS),
            "requested_at": now,
            "candidate_providers": providers,
            "capability": str(capability or "").strip().lower() or None,
            "proposed_tool_calls": unresolved_calls,
            "execution_context": execution_context,
            "clarification_payload": clarification_payload,
        }
        await self.session_manager.set_pending_question(
            session_key=session_key,
            pending_question=pending_question,
        )
        await self._append_event(
            session_key=session_key,
            event_type="implicit_proposal_question_requested",
            payload={
                "run_id": str(execution_context.get("run_id") or "").strip(),
                "question_type": "proposal_scope_confirmation",
                "origin": "provider_selection",
                "candidate_provider_count": len(providers),
                "capability": str(capability or "").strip().lower() or None,
            },
        )

    def _reason_message(
        self,
        reason_code: str,
        *,
        fallback: str = "I need a clarification before I proceed.",
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        return message_for_reason_code(reason_code, fallback=fallback, context=context)

    def _normalize_capability_unresolved_entries(
        self,
        *,
        unresolved: List[Dict[str, Any]],
        path: str,
    ) -> List[Dict[str, Any]]:
        normalized_path = str(path or "").strip().lower() or "proposal"
        entries: List[Dict[str, Any]] = []
        for item in unresolved:
            if not isinstance(item, dict):
                continue
            capability = str(item.get("capability") or "").strip().lower()
            provider_hint = str(item.get("provider_hint") or item.get("provider") or "").strip().lower()
            tool_name = str(item.get("tool_name") or "").strip()
            reason_code = str(item.get("reason_code") or "").strip().lower() or "unsupported_capability"
            step_id = str(item.get("step_id") or "").strip()
            candidate_providers = [
                str(provider).strip().lower()
                for provider in (item.get("candidate_providers") or [])
                if str(provider).strip()
            ]
            entry: Dict[str, Any] = {
                "path": normalized_path,
                "capability": capability,
                "provider_hint": provider_hint or None,
                "tool_name": tool_name or None,
                "reason_code": reason_code,
            }
            if candidate_providers:
                entry["candidate_providers"] = candidate_providers
            if step_id:
                entry["step_id"] = step_id
            entries.append(entry)
        return entries

    def _normalize_capability_resolved_entries(
        self,
        *,
        resolved: List[Dict[str, Any]],
        path: str,
    ) -> List[Dict[str, Any]]:
        normalized_path = str(path or "").strip().lower() or "proposal"
        entries: List[Dict[str, Any]] = []
        for item in resolved:
            if not isinstance(item, dict):
                continue
            capability = str(item.get("capability") or "").strip().lower()
            provider = str(item.get("provider") or "").strip().lower()
            tool_name = str(item.get("tool_name") or "").strip()
            reason_code = str(item.get("reason_code") or "").strip().lower() or "resolved"
            entries.append(
                {
                    "path": normalized_path,
                    "capability": capability or None,
                    "provider_hint": provider or None,
                    "tool_name": tool_name or None,
                    "reason_code": reason_code,
                }
            )
        return entries

    def _deterministic_reason_code_from_plan_decision(self, reason: str) -> str:
        normalized = str(reason or "").strip().lower()
        if normalized.startswith("clarification_required"):
            return "clarification_required"
        if normalized.startswith("low_confidence"):
            return "low_confidence"
        if normalized in {"not_multi_action", "insufficient_step_count_after_normalization", "empty_message"}:
            return "deterministic_unmatched"
        if normalized in {"deterministic_plan_selected"}:
            return "resolved"
        return "deterministic_unmatched"

    def _parse_status_for_reason_code(self, reason_code: str) -> str:
        normalized = str(reason_code or "").strip().lower()
        if normalized in {"resolved"}:
            return "resolved"
        if normalized in {"ambiguous_target", "ambiguous_mixed_signals", "clarification_required", "ambiguous_provider"}:
            return "ambiguous"
        if normalized in {"low_confidence"}:
            return "low_confidence"
        if normalized in {"deterministic_unmatched"}:
            return "unmatched"
        if normalized in {"unsupported_capability", "missing_capability"}:
            return "unsupported"
        if normalized in {"invalid_input", "required_tool_call_missing"}:
            return "invalid"
        return "invalid"

    def _derive_parse_status_for_reason_codes(self, reason_codes: List[str]) -> str:
        statuses = [self._parse_status_for_reason_code(code) for code in reason_codes]
        if "ambiguous" in statuses:
            return "ambiguous"
        if "invalid" in statuses:
            return "invalid"
        if "unsupported" in statuses:
            return "unsupported"
        if "low_confidence" in statuses:
            return "low_confidence"
        if "unmatched" in statuses:
            return "unmatched"
        if statuses and all(item == "resolved" for item in statuses):
            return "resolved"
        return "invalid"

    def _reason_code_counts(self, reason_codes: List[str]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for code in reason_codes:
            normalized = str(code or "").strip().lower() or "unknown"
            counts[normalized] = counts.get(normalized, 0) + 1
        return counts

    async def _emit_cortex_parse_telemetry(
        self,
        *,
        session_key: str,
        run_id: str,
        event: str,
        reason_codes: List[str],
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        normalized_codes = [
            str(code or "").strip().lower()
            for code in reason_codes
            if str(code or "").strip()
        ]
        if not normalized_codes:
            normalized_codes = ["invalid_input"]
        parse_status = self._derive_parse_status_for_reason_codes(normalized_codes)
        reason_counts = self._reason_code_counts(normalized_codes)
        payload: Dict[str, Any] = {
            "run_id": str(run_id or "").strip(),
            "event": str(event or "").strip().lower() or "parse_outcome",
            "reason_code": normalized_codes[0],
            "reason_codes": normalized_codes,
            "reason_code_counts": reason_counts,
            "parse_status": parse_status,
            "requires_clarification": parse_status in {"ambiguous", "invalid", "unsupported"},
            "ambiguous_reason_count": sum(
                count
                for code, count in reason_counts.items()
                if self._parse_status_for_reason_code(code) == "ambiguous"
            ),
        }
        if isinstance(context, dict) and context:
            payload["context"] = context
        await self._append_event(
            session_key=session_key,
            event_type="parse_outcome",
            payload=payload,
        )
        print("Cortex parse telemetry: " + json.dumps(payload, ensure_ascii=True))

    async def _emit_capability_unresolved_event(
        self,
        *,
        session_key: str,
        run_id: str,
        unresolved: List[Dict[str, Any]],
        resolved: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        payload: Dict[str, Any] = {
            "run_id": str(run_id or "").strip(),
            "unresolved": unresolved,
            "resolved": resolved or [],
        }
        await self._append_event(
            session_key=session_key,
            event_type="capability_unresolved",
            payload=payload,
        )
        await self._emit_cortex_parse_telemetry(
            session_key=session_key,
            run_id=run_id,
            event="capability_unresolved",
            reason_codes=[
                str((item or {}).get("reason_code") or "").strip().lower()
                for item in unresolved
                if isinstance(item, dict)
            ],
            context={
                "path": sorted(
                    {
                        str((item or {}).get("path") or "").strip().lower()
                        for item in unresolved
                        if isinstance(item, dict) and str((item or {}).get("path") or "").strip()
                    }
                ),
                "unresolved_count": len(unresolved),
                "resolved_count": len(resolved or []),
            },
        )

    def _resolve_action_plan_step_tools(
        self,
        *,
        plan: ActionPlan,
        role: str,
        connected_integrations: List[str],
        project_context: Dict[str, Any],
    ) -> Tuple[ActionPlan, List[Dict[str, Any]]]:
        resolver = self._get_capability_resolver()
        unresolved: List[Dict[str, Any]] = []

        for step in plan.steps:
            step_id = str(getattr(step, "step_id", "") or "").strip()
            action_type = str(getattr(step, "action_type", "") or "").strip()
            tool_name = str(getattr(step, "tool_name", "") or "").strip()
            capability = str(getattr(step, "capability", "") or "").strip().lower() or None
            provider_hint = str(getattr(step, "provider", "") or "").strip().lower() or None

            if not capability:
                unresolved.append(
                    {
                        "step_id": step_id,
                        "action_type": action_type,
                        "tool_name": tool_name,
                        "capability": "",
                        "provider_hint": provider_hint,
                        "reason_code": "missing_capability",
                    }
                )
                continue

            resolution = resolver.resolve(
                capability=capability,
                role=role,
                connected_integrations=connected_integrations,
                provider_hint=provider_hint,
                project_context=project_context,
            )
            if resolution.ok and str(resolution.tool_name or "").strip():
                step.tool_name = str(resolution.tool_name or "").strip()
                step.capability = capability
                step.provider = str(resolution.provider or provider_hint or "").strip().lower() or None
                continue

            unresolved.append(
                {
                    "step_id": step_id,
                    "action_type": action_type,
                    "tool_name": tool_name,
                    "capability": capability,
                    "provider_hint": provider_hint,
                    "args": dict(getattr(step, "args", {}) or {}),
                    "reason_code": resolution.reason_code or "unsupported_capability",
                    "candidate_providers": list(resolution.candidate_providers or []),
                }
            )

        return plan, unresolved

    def _proposal_tool_contracts(
        self,
        visible_tool_descriptors: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        contracts: List[Dict[str, Any]] = []
        for descriptor in visible_tool_descriptors:
            if not isinstance(descriptor, dict):
                continue
            tool_name = str(descriptor.get("name") or "").strip()
            if not tool_name:
                continue
            allowed_fields = sorted(self._tool_allowed_arg_fields(tool_name=tool_name))
            allowed_fields = [
                field for field in allowed_fields if field not in self.SYSTEM_MANAGED_TOOL_FIELDS
            ]
            required_fields = sorted(self._proposal_required_fields_for_tool_call(tool_name))
            contracts.append(
                {
                    "name": tool_name,
                    "fields": allowed_fields,
                    "required_fields": required_fields,
                    "aliases": canonical_aliases_for_tool(tool_name),
                }
            )
        return contracts

    def _tool_allowed_arg_fields(self, *, tool_name: str) -> Set[str]:
        normalized = str(tool_name or "").strip()
        if not normalized:
            return set()
        try:
            tool = self.tool_registry.get(normalized)
        except KeyError:
            return set()

        input_model = getattr(tool, "input_model", None)
        if input_model is not None and hasattr(input_model, "model_fields"):
            return {str(name) for name in getattr(input_model, "model_fields", {}).keys()}
        return set()

    def _canonicalize_tool_args_for_execution(
        self,
        *,
        tool_name: str,
        args: Dict[str, Any],
    ) -> Dict[str, Any]:
        allowed_fields = self._tool_allowed_arg_fields(tool_name=tool_name)
        canonical = canonicalize_tool_args(
            tool_name=tool_name,
            args=args if isinstance(args, dict) else {},
            allowed_fields=allowed_fields if allowed_fields else None,
        )
        # Keep null/placeholder values out of runtime input so required-field checks remain strict
        # and optional enum fields are not poisoned by empty placeholders from model output.
        sanitized: Dict[str, Any] = {}
        for key, value in canonical.items():
            if value is None:
                continue
            if isinstance(value, str):
                clean = value.strip()
                if not clean:
                    continue
                if clean.lower() in {"none", "null"}:
                    continue
                sanitized[key] = clean
                continue
            sanitized[key] = value
        return sanitized

    async def _prepare_tool_args_for_orchestrator_execution(
        self,
        *,
        tool_name: str,
        args: Dict[str, Any],
        execution_context: Dict[str, Any],
    ) -> Tuple[Optional[Dict[str, Any]], List[str], Optional[str], Dict[str, Any]]:
        canonical_args = self._canonicalize_tool_args_for_execution(
            tool_name=tool_name,
            args=args,
        )
        write_parse_outcome = await parse_with_runtime_adapter(
            "cortex.write_args",
            tool_name=tool_name,
            args=canonical_args,
            parser_context={
                "path": "orchestrator_prepare_tool_args",
                "project_id": str(execution_context.get("project_id") or "").strip(),
                "user_id": str(execution_context.get("user_id") or "").strip(),
            },
        )
        gate_decision = enforce_mutation_gate(write_parse_outcome)
        session_key = str(execution_context.get("session_key") or "").strip()
        run_id = str(execution_context.get("run_id") or "").strip()
        if session_key and run_id:
            await self._emit_cortex_parse_telemetry(
                session_key=session_key,
                run_id=run_id,
                event="parse_outcome",
                reason_codes=[str(gate_decision.reason_code or "invalid_input")],
                context={
                    "path": "write_prepare",
                    "tool_name": str(tool_name or "").strip(),
                    "gate_action": str(gate_decision.action or "").strip(),
                },
            )
        if gate_decision.requires_clarification():
            payload = gate_decision.outcome.normalized_payload or {}
            clarification_tokens: List[str] = []
            if isinstance(payload, dict):
                for token in payload.get("missing_fields") or []:
                    clean = str(token or "").strip()
                    if clean:
                        clarification_tokens.append(clean)
            if not clarification_tokens:
                if str(tool_name or "").strip() == "jira_update_status":
                    clarification_tokens = [f"{tool_name}.status"]
                else:
                    clarification_tokens = [f"{tool_name}.input"]
            return (
                None,
                self._coalesce_missing_field_tokens(clarification_tokens),
                None,
                canonical_args,
            )
        if not gate_decision.allows_write():
            context = {}
            payload = gate_decision.outcome.normalized_payload or {}
            if isinstance(payload, dict):
                candidate_statuses = payload.get("candidate_statuses")
                if isinstance(candidate_statuses, list):
                    context["available_statuses"] = candidate_statuses
                requested_status = payload.get("status")
                if requested_status:
                    context["requested_status"] = str(requested_status)
            return (
                None,
                [],
                self._reason_message(
                    gate_decision.reason_code,
                    context=context or None,
                ),
                canonical_args,
            )
        parsed_payload = write_parse_outcome.normalized_payload or {}
        parsed_args = parsed_payload.get("args") if isinstance(parsed_payload, dict) else None
        if isinstance(parsed_args, dict):
            canonical_args = self._canonicalize_tool_args_for_execution(
                tool_name=tool_name,
                args=parsed_args,
            )
        if bool(getattr(settings, "cortex_write_target_guard_enabled", False)) and self._is_write_tool_call(tool_name):
            latest_user_message = str(
                execution_context.get("latest_user_message")
                or execution_context.get("user_message")
                or ""
            ).strip()
            if not self._is_write_target_grounded_in_user_message(
                tool_name=tool_name,
                args=canonical_args,
                user_message=latest_user_message,
            ):
                return (
                    None,
                    [],
                    (
                        "I need more specific details before I proceed. "
                        "Please provide the full request with exact target item(s) and the action to perform."
                    ),
                    canonical_args,
                )
        prepared = await self.executor.prepare_tool_args_for_execution(
            tool_name=tool_name,
            args=canonical_args,
            execution_context=execution_context,
        )
        if not isinstance(prepared, dict):
            return None, [], f"I couldn't prepare `{tool_name}` for execution.", canonical_args

        tool_error = prepared.get("_tool_error")
        if not isinstance(tool_error, dict):
            return dict(prepared), [], None, canonical_args

        error_code = str(tool_error.get("error_code") or "").strip().lower()
        user_message = str(tool_error.get("user_message") or "").strip()
        details = tool_error.get("details") if isinstance(tool_error.get("details"), dict) else {}
        issue_codes = {
            str(item or "").strip().lower()
            for item in (details.get("issue_codes") or [])
            if str(item or "").strip()
        }
        explicit_missing: List[str] = []
        for token in (details.get("insufficient_fields") or []):
            text = str(token or "").strip()
            if text:
                explicit_missing.append(text)

        if error_code == "insufficient_argument_quality":
            if issue_codes.intersection({"invalid_type", "constraint_violation"}) and not explicit_missing:
                return (
                    None,
                    [],
                    user_message or f"I couldn't execute `{tool_name}` because some arguments are invalid.",
                    canonical_args,
                )
            extracted = explicit_missing or self._extract_insufficient_fields_from_tool_error(
                tool_name=tool_name,
                result=tool_error,
            )
            return None, self._coalesce_missing_field_tokens(extracted), None, canonical_args

        return None, [], user_message or f"I couldn't prepare `{tool_name}`.", canonical_args

    def _extract_target_refs_from_user_message(self, user_message: str) -> Set[str]:
        text = str(user_message or "").strip().upper()
        if not text:
            return set()
        return {
            str(match or "").strip().upper()
            for match in re.findall(r"\b[A-Z][A-Z0-9_]*-\d+\b", text)
            if str(match or "").strip()
        }

    def _is_write_target_grounded_in_user_message(
        self,
        *,
        tool_name: str,
        args: Dict[str, Any],
        user_message: str,
    ) -> bool:
        if not self._is_write_tool_call(tool_name):
            return True
        target_values: Set[str] = set()
        for field in self.TARGET_REF_ARG_FIELDS:
            value = str((args or {}).get(field) or "").strip().upper()
            if value:
                target_values.add(value)
        if not target_values:
            return True
        grounded_refs = self._extract_target_refs_from_user_message(user_message)
        if not grounded_refs:
            return False
        return target_values.issubset(grounded_refs)

    def _proposal_summary_text(self, proposal: ToolProposal) -> str:
        inferred = self._build_structured_proposal_summary(proposal)
        if inferred:
            return inferred
        summary = str(proposal.summary or "").strip()
        if summary:
            return f"I can do this: {summary}."
        names = [str(item.tool_name or "").strip() for item in proposal.proposed_tool_calls if str(item.tool_name or "").strip()]
        if not names:
            return "I can proceed with your requested action."
        if len(names) == 1:
            return f"I can proceed using `{names[0]}`."
        return f"I can proceed with these actions: {', '.join(names)}."

    def _build_structured_proposal_summary(self, proposal: ToolProposal) -> str:
        segments: List[str] = []
        for call in proposal.proposed_tool_calls:
            tool_name = str(call.tool_name or "").strip()
            args = call.args if isinstance(call.args, dict) else {}
            if tool_name == "jira_create_work_item":
                title = str(args.get("title") or "").strip()
                assignee = str(args.get("assignee") or "").strip()
                if title and assignee:
                    segments.append(f"create work item '{title}' and assign it to {assignee}")
                elif title:
                    segments.append(f"create work item '{title}'")
                elif assignee:
                    segments.append(f"create a work item and assign it to {assignee}")
                else:
                    segments.append("create a work item")
                continue
            if tool_name == "jira_assign_work_item":
                external_id = str(args.get("external_id") or "").strip()
                assignee = str(args.get("assignee") or "").strip()
                if external_id and assignee:
                    segments.append(f"assign {external_id} to {assignee}")
                elif assignee:
                    segments.append(f"assign the work item to {assignee}")
                else:
                    segments.append("assign the work item")
                continue
            if tool_name == "jira_update_status":
                external_id = str(args.get("external_id") or "").strip()
                status = str(args.get("status") or "").strip()
                if external_id and status:
                    segments.append(f"update {external_id} to {status}")
                elif status:
                    segments.append(f"update work item status to {status}")
                else:
                    segments.append("update work item status")
                continue
            if tool_name == "jira_add_comment":
                external_id = str(args.get("external_id") or "").strip()
                if external_id:
                    segments.append(f"add a comment to {external_id}")
                else:
                    segments.append("add a work item comment")
                continue
            if tool_name == "jira_update_description":
                external_id = str(args.get("external_id") or "").strip()
                if external_id:
                    segments.append(f"update description for {external_id}")
                else:
                    segments.append("update work item description")
                continue
            if tool_name:
                segments.append(f"run {tool_name}")

        if not segments:
            return ""
        if len(segments) == 1:
            return f"I can {segments[0]}."
        return f"I can {', then '.join(segments)}."

    def _proposal_required_fields_for_tool_call(self, tool_name: str) -> List[str]:
        normalized = str(tool_name or "").strip()
        if not normalized:
            return []
        try:
            tool = self.tool_registry.get(normalized)
        except KeyError:
            tool = None
        input_model = getattr(tool, "input_model", None) if tool else None
        if input_model is not None and hasattr(input_model, "model_fields"):
            required: List[str] = []
            for field_name, field in getattr(input_model, "model_fields", {}).items():
                is_required_attr = getattr(field, "is_required", None)
                if callable(is_required_attr):
                    is_required = bool(is_required_attr())
                else:
                    is_required = bool(is_required_attr)
                if is_required:
                    name = str(field_name)
                    if name in self.SYSTEM_MANAGED_TOOL_FIELDS:
                        continue
                    required.append(name)
            if required:
                return required
        return []

    def _extract_insufficient_fields_from_tool_error(
        self,
        *,
        tool_name: str,
        result: Dict[str, Any],
    ) -> List[str]:
        details = result.get("details") if isinstance(result, dict) else {}
        detail_fields = details.get("insufficient_fields") if isinstance(details, dict) else []
        extracted: List[str] = []
        if isinstance(detail_fields, list):
            for token in detail_fields:
                text = str(token or "").strip()
                if text:
                    extracted.append(text)
        if extracted:
            return extracted
        required = self._proposal_required_fields_for_tool_call(tool_name)
        if required:
            return [f"{tool_name}.{required[0]}"]
        return [f"{tool_name}.input"]

    def _is_write_tool_call(self, tool_name: str) -> bool:
        normalized = str(tool_name or "").strip()
        if not normalized:
            return False
        try:
            tool = self.tool_registry.get(normalized)
        except KeyError:
            # Unknown tools are treated as write-risk for preflight safety.
            return True
        return tool.normalized_spec().idempotency != IdempotencyClass.READ_ONLY.value

    def _should_force_write_batch_preflight(self, calls: List[Dict[str, Any]]) -> bool:
        write_count = 0
        for item in calls:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool_name") or "").strip()
            if not tool_name:
                continue
            if self._is_write_tool_call(tool_name):
                write_count += 1
            if write_count > 1:
                return True
        return False

    async def _preflight_write_calls_for_proposal(
        self,
        *,
        calls: List[Dict[str, Any]],
        execution_context: Dict[str, Any],
    ) -> tuple[Dict[int, Dict[str, Any]], List[str], List[Dict[str, Any]], List[str]]:
        prepared_by_index: Dict[int, Dict[str, Any]] = {}
        missing_fields: List[str] = []
        unresolved_calls: List[Dict[str, Any]] = []
        failed: List[str] = []

        for idx, item in enumerate(calls):
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool_name") or "").strip()
            args = item.get("args")
            if not tool_name or not isinstance(args, dict):
                continue
            if not self._is_write_tool_call(tool_name):
                continue

            (
                prepared_args,
                call_missing,
                call_failed,
                canonical_args,
            ) = await self._prepare_tool_args_for_orchestrator_execution(
                tool_name=tool_name,
                args=args,
                execution_context=execution_context,
            )
            if call_missing:
                for token in call_missing:
                    if token not in missing_fields:
                        missing_fields.append(token)
                unresolved_calls.append({"tool_name": tool_name, "args": canonical_args})
                continue
            if call_failed:
                failed.append(call_failed)
                continue

            if isinstance(prepared_args, dict):
                prepared_by_index[idx] = dict(prepared_args)

        return prepared_by_index, missing_fields, unresolved_calls, failed

    def _proposal_missing_required_args(
        self,
        proposal: ToolProposal,
    ) -> List[str]:
        missing: List[str] = []
        for call in proposal.proposed_tool_calls:
            tool_name = str(call.tool_name or "").strip()
            raw_args = call.args if isinstance(call.args, dict) else {}
            args = self._canonicalize_tool_args_for_execution(
                tool_name=tool_name,
                args=raw_args,
            )
            for field in self._proposal_required_fields_for_tool_call(tool_name):
                if str(args.get(field) or "").strip():
                    continue
                token = f"{tool_name}.{field}"
                if token not in missing:
                    missing.append(token)
        return missing

    def _coalesce_missing_field_tokens(self, missing_fields: List[str]) -> List[str]:
        ordered_tokens: List[str] = []
        for item in missing_fields:
            token = str(item or "").strip()
            if not token:
                continue
            if "." in token:
                tool_name, field_name = token.split(".", 1)
                tool_name = str(tool_name or "").strip()
                field_name = str(field_name or "").strip()
                if not field_name or field_name in self.SYSTEM_MANAGED_TOOL_FIELDS:
                    continue
                token = f"{tool_name}.{field_name}" if tool_name else field_name
            else:
                field_name = token
                if field_name in self.SYSTEM_MANAGED_TOOL_FIELDS:
                    continue
            ordered_tokens.append(token)

        qualified_fields = {
            token.split(".", 1)[1]
            for token in ordered_tokens
            if "." in token and token.split(".", 1)[1]
        }

        deduped: List[str] = []
        seen: set[str] = set()
        for token in ordered_tokens:
            if "." not in token and token in qualified_fields:
                continue
            if token in seen:
                continue
            seen.add(token)
            deduped.append(token)
        return deduped

    def _build_missing_fields_response(self, missing_fields: List[str]) -> str:
        return build_missing_fields_response(missing_fields, max_items=4)

    def _consume_repair_budget(self, execution_context: Dict[str, Any]) -> bool:
        limit = max(0, int(settings.cortex_max_repair_attempts_per_turn or 1))
        if limit == 0:
            return False
        used = int(execution_context.get("repair_attempts_used") or 0)
        if used >= limit:
            return False
        execution_context["repair_attempts_used"] = used + 1
        return True

    def _proposal_prevalidated_tokens(self, calls: List[ProposedToolCall]) -> List[str]:
        allowed_provenance = {"explicit_user_text", "deterministic_parse", "followup_user"}
        min_confidence = float(settings.cortex_proposal_field_meta_min_confidence or 0.6)
        tokens: set[str] = set()
        for call in calls:
            tool_name = str(call.tool_name or "").strip().lower()
            field_meta = getattr(call, "field_meta", None)
            if not isinstance(field_meta, dict):
                continue
            for field_name, meta in field_meta.items():
                normalized_field = str(field_name or "").strip().lower()
                if not normalized_field:
                    continue
                if normalized_field in self.SYSTEM_MANAGED_TOOL_FIELDS:
                    continue
                provenance = str(getattr(meta, "provenance", "") or "").strip().lower()
                if provenance not in allowed_provenance:
                    continue
                confidence = float(getattr(meta, "confidence", 0.0) or 0.0)
                if confidence < min_confidence:
                    continue
                tokens.add(normalized_field)
                if tool_name:
                    tokens.add(f"{tool_name}.{normalized_field}")
        return sorted(tokens)

    def _is_all_matches_scope_request(self, execution_context: Dict[str, Any]) -> bool:
        text = str(
            execution_context.get("latest_user_message")
            or execution_context.get("user_message")
            or ""
        ).strip().lower()
        if not text:
            return False
        quantifier = bool(re.search(r"\b(all|every|each|both|entire|whole)\b", text))
        target_noun = bool(
            re.search(r"\b(task|tasks|issue|issues|item|items|ticket|tickets|work[\s_-]?item(?:s)?)\b", text)
        )
        explicit_plural_pronoun = bool(re.search(r"\b(all|both)\s+of\s+(them|these|those)\b", text))
        write_action = bool(
            re.search(r"\b(assign|move|set|change|update|add|post|comment|close|reopen)\b", text)
        )
        both_short = bool(re.search(r"\bboth\b", text))
        return (quantifier and target_noun) or explicit_plural_pronoun or (both_short and write_action)

    def _parse_scope_confirmation_decision(self, user_message: str) -> Optional[str]:
        text = " ".join(str(user_message or "").strip().lower().split())
        if not text:
            return None

        if text in {"yes", "y", "include all", "all", "all of them", "yes include all", "apply all", "update all"}:
            return "include_all"
        if text in {
            "no",
            "n",
            "only selected",
            "selected only",
            "only those",
            "keep selected",
            "skip missing",
            "don't include all",
            "do not include all",
            "dont include all",
        }:
            return "selected_only"

        if re.search(r"\b(include|apply|update)\s+all\b", text):
            return "include_all"
        if re.search(r"\b(only|just)\s+(selected|those|current)\b", text):
            return "selected_only"
        if re.search(r"\b(selected)\s+only\b", text):
            return "selected_only"
        if re.search(r"\b(do not|don't|dont|skip)\b.*\b(include\s+all|missing)\b", text):
            return "selected_only"
        return None

    def _target_identity_keys(self) -> Tuple[str, ...]:
        return ("external_id", "entity_id", "task_id", "issue_id", "id")

    def _extract_target_ref_from_mapping(self, value: Any) -> Optional[str]:
        if not isinstance(value, dict):
            return None
        for key in self._target_identity_keys():
            token = str(value.get(key) or "").strip()
            if token:
                return token.upper() if "-" in token else token
        for key, raw in value.items():
            name = str(key or "").strip().lower()
            if not name:
                continue
            if name == "operation_id":
                continue
            if name.endswith("_id"):
                token = str(raw or "").strip()
                if token:
                    return token.upper() if "-" in token else token
        return None

    def _extract_target_refs_from_result(self, result: Dict[str, Any]) -> List[str]:
        refs: List[str] = []
        if not isinstance(result, dict):
            return refs

        def _add(token: Optional[str]) -> None:
            clean = str(token or "").strip()
            if not clean:
                return
            if clean not in refs:
                refs.append(clean)

        _add(self._extract_target_ref_from_mapping(result))
        for key in ("task", "entity"):
            nested = result.get(key)
            if isinstance(nested, dict):
                _add(self._extract_target_ref_from_mapping(nested))

        items = result.get("items")
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                _add(self._extract_target_ref_from_mapping(item))
        return refs

    def _extract_target_refs_from_calls(self, calls: List[Dict[str, Any]]) -> List[str]:
        refs: List[str] = []
        for item in calls:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool_name") or "").strip()
            args = item.get("args")
            if not tool_name or not isinstance(args, dict):
                continue
            if not self._is_write_tool_call(tool_name):
                continue
            token = self._extract_target_ref_from_mapping(args)
            if token and token not in refs:
                refs.append(token)
        return refs

    def _build_write_target_templates(self, calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        templates: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in calls:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool_name") or "").strip()
            args = item.get("args")
            if not tool_name or not isinstance(args, dict):
                continue
            if not self._is_write_tool_call(tool_name):
                continue

            target_key = ""
            for key in self._target_identity_keys():
                token = str(args.get(key) or "").strip()
                if token:
                    target_key = key
                    break
            if not target_key:
                for key, raw in args.items():
                    name = str(key or "").strip()
                    lowered = name.lower()
                    if not name or lowered == "operation_id":
                        continue
                    if lowered.endswith("_id") and str(raw or "").strip():
                        target_key = name
                        break
            if not target_key:
                continue

            prototype_args: Dict[str, Any] = {}
            for key, value in args.items():
                name = str(key or "").strip()
                if not name:
                    continue
                lowered = name.lower()
                if lowered in {str(target_key).lower(), "operation_id"}:
                    continue
                prototype_args[name] = value
            signature = json.dumps(
                {"tool_name": tool_name, "target_key": target_key, "args": prototype_args},
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            if signature in seen:
                continue
            seen.add(signature)
            templates.append(
                {
                    "tool_name": tool_name,
                    "target_key": target_key,
                    "args": prototype_args,
                }
            )
        return templates

    def _expand_calls_with_targets(
        self,
        *,
        calls: List[Dict[str, Any]],
        templates: List[Dict[str, Any]],
        targets: List[str],
    ) -> List[Dict[str, Any]]:
        expanded: List[Dict[str, Any]] = []
        seen: set[str] = set()

        def _signature(tool_name: str, args: Dict[str, Any]) -> str:
            payload = json.dumps(
                {"tool_name": tool_name, "args": args},
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            return payload

        for item in calls:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool_name") or "").strip()
            args = item.get("args")
            if not tool_name or not isinstance(args, dict):
                continue
            sig = _signature(tool_name, args)
            if sig in seen:
                continue
            seen.add(sig)
            expanded.append({"tool_name": tool_name, "args": dict(args)})

        clean_targets = [str(item).strip() for item in targets if str(item).strip()]
        for target in clean_targets:
            for template in templates:
                if not isinstance(template, dict):
                    continue
                tool_name = str(template.get("tool_name") or "").strip()
                target_key = str(template.get("target_key") or "").strip()
                base_args = template.get("args")
                if not tool_name or not target_key or not isinstance(base_args, dict):
                    continue
                candidate_args = dict(base_args)
                candidate_args[target_key] = target
                sig = _signature(tool_name, candidate_args)
                if sig in seen:
                    continue
                seen.add(sig)
                expanded.append({"tool_name": tool_name, "args": candidate_args})
        return expanded

    def _compute_scope_mismatch(
        self,
        *,
        calls: List[Dict[str, Any]],
        candidate_targets: List[str],
    ) -> Optional[Dict[str, List[str]]]:
        candidates = [str(item).strip() for item in candidate_targets if str(item).strip()]
        if not candidates:
            return None
        selected = self._extract_target_refs_from_calls(calls)
        if not selected:
            return None
        candidate_set = {item.lower(): item for item in candidates}
        selected_set = {item.lower(): item for item in selected}
        missing_keys = [key for key in candidate_set.keys() if key not in selected_set]
        if not missing_keys:
            return None
        missing = [candidate_set[key] for key in missing_keys]
        return {
            "candidate_targets": sorted(candidate_set.values()),
            "selected_targets": sorted(selected_set.values()),
            "missing_targets": sorted(missing),
        }

    def _build_scope_confirmation_response(
        self,
        *,
        candidate_targets: List[str],
        selected_targets: List[str],
        missing_targets: List[str],
    ) -> str:
        candidate_count = len([item for item in candidate_targets if str(item).strip()])
        selected_count = len([item for item in selected_targets if str(item).strip()])
        missing = [str(item).strip() for item in missing_targets if str(item).strip()]
        preview = ", ".join(f"`{item}`" for item in missing[:3])
        if preview:
            preview = f" Missing: {preview}."
        return (
            f"I found {candidate_count} matching targets, but the prepared updates currently target {selected_count}."
            f"{preview} Reply `include all` to apply updates to every matching target, "
            "or `only selected` to continue with the current subset."
        )

    def _extract_addressed_name_tokens(self, user_message: str) -> List[str]:
        text = str(user_message or "").strip().lower()
        if not text:
            return []
        candidates: List[str] = []
        patterns = [
            r"^\s*(?:hi|hello|hey)\s+([a-z][a-z0-9_.-]{1,30})\b",
            r"^\s*([a-z][a-z0-9_.-]{1,30})\s*,\s*(?:can you|could you|would you|please)\b",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                token = str(match.group(1) or "").strip().lower()
                if token and token not in candidates:
                    candidates.append(token)
        stop = {"team", "everyone", "all", "bot", "promarshal"}
        return [item for item in candidates if item not in stop]

    def _infer_unresolved_delegate_names(
        self,
        *,
        project_context: Dict[str, Any],
        user_message: str,
        member_candidates: List[Dict[str, Any]],
    ) -> List[str]:
        addressed = self._extract_addressed_name_tokens(user_message)
        if not addressed:
            return []
        resolved_tokens: set[str] = set()
        for item in member_candidates:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip().lower()
            if name:
                resolved_tokens.add(name)
                first = name.split()[0]
                if first:
                    resolved_tokens.add(first)
            email = str(item.get("email") or "").strip().lower()
            if email:
                resolved_tokens.add(email)

        members = project_context.get("members") or []
        known_name_tokens: set[str] = set()
        if isinstance(members, list):
            for member in members:
                if not isinstance(member, dict):
                    continue
                if str(member.get("status") or "").strip().lower() != "active":
                    continue
                name = str(member.get("name") or "").strip().lower()
                if name:
                    known_name_tokens.add(name)
                    first = name.split()[0]
                    if first:
                        known_name_tokens.add(first)

        unresolved: List[str] = []
        for token in addressed:
            if token in resolved_tokens:
                continue
            if token in known_name_tokens:
                unresolved.append(token)
                continue
            unresolved.append(token)
        return unresolved

    def _extract_committed_action_summary_from_result(
        self,
        result: Dict[str, Any],
        *,
        tool_name: str,
    ) -> str:
        committed = result.get("committed_action")
        if isinstance(committed, dict):
            text = str(committed.get("summary") or "").strip()
            if text:
                return text
        for key in ("summary", "user_message"):
            text = str(result.get(key) or "").strip()
            if text:
                return text
        return f"{tool_name} completed"

    async def _maybe_handle_action_plan_turn(
        self,
        *,
        session_key: str,
        session: CortexSession,
        user_message: str,
        channel_id: str,
        thread_ts: Optional[str],
        source: str,
        project_id: str,
        user_id: str,
        role: str,
        actor_email: Optional[str],
        connected_integrations: list[str],
        visible_tool_descriptors: list[Dict[str, Any]],
        project_context: Dict[str, Any],
        run_id: str,
        started_at: datetime,
        team_id: Optional[str],
        member_candidates: List[Dict[str, Any]],
    ) -> Optional[OrchestratorResult]:
        pending_plan_payload = session.pending_plan if isinstance(session.pending_plan, dict) else None
        plan = None
        plan_source = "new_plan"
        plan_decision_reason = ""
        plan_confidence = 0.0

        if pending_plan_payload:
            if self._is_pending_plan_stale(pending_plan_payload):
                await self.session_manager.set_pending_plan(
                    session_key=session_key,
                    pending_plan=None,
                )
                await self._append_event(
                    session_key=session_key,
                    event_type="action_plan_discarded",
                    payload={
                        "run_id": run_id,
                        "reason": "pending_plan_stale",
                    },
                )
                pending_plan_payload = None

        if pending_plan_payload:
            resume_decision = self._parse_pending_plan_decision(user_message)
            if (
                resume_decision is None
                and settings.cortex_pending_plan_auto_resume_on_same_intent
                and self._is_same_intent_as_pending_plan(
                    user_message=user_message,
                    pending_plan_payload=pending_plan_payload,
                )
            ):
                resume_decision = "continue"

            if (
                resume_decision is None
                and settings.cortex_pending_plan_override_on_new_action
                and self._looks_like_action_request(user_message)
            ):
                await self.session_manager.set_pending_plan(
                    session_key=session_key,
                    pending_plan=None,
                )
                await self._append_event(
                    session_key=session_key,
                    event_type="action_plan_discarded",
                    payload={
                        "run_id": run_id,
                        "reason": "new_action_overrode_pending_plan",
                    },
                )
                pending_plan_payload = None

        if pending_plan_payload:
            if resume_decision == "cancel":
                await self.session_manager.set_pending_plan(
                    session_key=session_key,
                    pending_plan=None,
                )
                response_text = "Canceled the pending action plan. I will use your next message as a new request."
                delivery = await self._deliver_response(
                    text=response_text,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    metadata={
                        "source": source,
                        "project_id": project_id,
                        "team_id": team_id,
                        "session_key": session_key,
                    },
                )
                await self._append_event(
                    session_key=session_key,
                    event_type="action_plan_discarded",
                    payload={"run_id": run_id, "reason": "user_cancelled"},
                )
                await self._persist_turn_metadata(
                    session_key=session_key,
                    run_id=run_id,
                    status="completed",
                    source=source,
                    project_id=project_id,
                    user_id=user_id,
                    user_message=user_message,
                    request_meta={"deterministic_plan": "discarded"},
                    executor_meta={"engine": "deterministic_action_plan", "status": "discarded"},
                    delivery_meta=delivery,
                    response_text=response_text,
                    started_at=started_at,
                    finished_at=datetime.utcnow(),
                    error=None,
                )
                return OrchestratorResult(
                    ok=True,
                    response_text=None if delivery.get("ok") else response_text,
                    metadata={
                        "run_id": run_id,
                        "session_key": session_key,
                        "deterministic_plan": "discarded",
                        "delivery": delivery,
                    },
                )

            if resume_decision != "continue":
                pending = action_plan_from_dict(pending_plan_payload)
                remaining: list[str] = []
                if pending is not None:
                    remaining = [
                        self._humanize_action_summary(step.summary)
                        for step in pending.steps[pending.current_step_index :]
                        if step.summary
                    ]
                response_text = (
                    "You have unfinished actions from your previous request. "
                    "Reply with `continue` to resume or `cancel` to discard."
                )
                if remaining:
                    response_text = (
                        f"{response_text}\nRemaining: {self._join_action_texts(remaining)}."
                    )
                delivery = await self._deliver_response(
                    text=response_text,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    metadata={
                        "source": source,
                        "project_id": project_id,
                        "team_id": team_id,
                        "session_key": session_key,
                    },
                )
                await self._persist_turn_metadata(
                    session_key=session_key,
                    run_id=run_id,
                    status="completed",
                    source=source,
                    project_id=project_id,
                    user_id=user_id,
                    user_message=user_message,
                    request_meta={"deterministic_plan": "awaiting_continue_signal"},
                    executor_meta={"engine": "deterministic_action_plan", "status": "awaiting_continue_signal"},
                    delivery_meta=delivery,
                    response_text=response_text,
                    started_at=started_at,
                    finished_at=datetime.utcnow(),
                    error=None,
                )
                return OrchestratorResult(
                    ok=True,
                    response_text=None if delivery.get("ok") else response_text,
                    metadata={
                        "run_id": run_id,
                        "session_key": session_key,
                        "deterministic_plan": "awaiting_continue_signal",
                        "delivery": delivery,
                    },
                )

            plan = action_plan_from_dict(pending_plan_payload)
            if plan is None:
                await self.session_manager.set_pending_plan(session_key=session_key, pending_plan=None)
                return None
            plan_source = "resume_pending_plan"
            plan_confidence = float(plan.confidence)
            plan_decision_reason = "resume_pending_plan"
        else:
            plan_decision = self.plan_builder.build(
                user_message=user_message,
                visible_tools=visible_tool_descriptors,
                connected_integrations=connected_integrations,
            )
            plan_confidence = float(plan_decision.confidence)
            plan_decision_reason = str(plan_decision.reason)
            if plan_decision.plan is None:
                gate_metadata = (
                    dict(plan_decision.metadata)
                    if isinstance(getattr(plan_decision, "metadata", None), dict)
                    else {}
                )
                gate_action = str(gate_metadata.get("gate_action") or "enrichment").strip().lower()
                reason_code = str(
                    gate_metadata.get("reason_code")
                    or self._deterministic_reason_code_from_plan_decision(plan_decision_reason)
                ).strip().lower()
                await self._emit_cortex_parse_telemetry(
                    session_key=session_key,
                    run_id=run_id,
                    event="deterministic_gate",
                    reason_codes=[reason_code],
                    context={
                        "path": "deterministic_gate",
                        "gate_action": gate_action,
                        "plan_reason": plan_decision_reason,
                        "plan_confidence": float(plan_decision.confidence),
                        "match_count": int(gate_metadata.get("match_count") or 0),
                        "deterministic_confidence": float(gate_metadata.get("deterministic_confidence") or 0.0),
                        "deterministic_slot_coverage": float(gate_metadata.get("deterministic_slot_coverage") or 0.0),
                        "deterministic_signal_strength": float(gate_metadata.get("deterministic_signal_strength") or 0.0),
                    },
                )
                if gate_action == "clarification_required":
                    missing_fields = self._coalesce_missing_field_tokens(
                        [
                            str(item or "").strip()
                            for item in (gate_metadata.get("missing_fields") or [])
                            if str(item or "").strip()
                        ]
                    )
                    unresolved_calls = [
                        item
                        for item in (gate_metadata.get("unresolved_calls") or [])
                        if isinstance(item, dict) and str(item.get("tool_name") or "").strip()
                    ]
                    execution_context = {
                        "project_id": project_id,
                        "user_id": user_id,
                        "actor_email": actor_email,
                        "user_message": user_message,
                        "latest_user_message": user_message,
                        "session_key": session_key,
                        "source": source,
                        "run_id": run_id,
                        "role": role,
                        "connected_integrations": connected_integrations,
                        "member_candidates": member_candidates,
                        "deterministic_gate": "clarification_required",
                    }
                    if missing_fields and unresolved_calls:
                        await self._set_pending_missing_fields_question(
                            session_key=session_key,
                            missing_fields=missing_fields,
                            unresolved_calls=unresolved_calls,
                            execution_context=execution_context,
                        )
                    response_text = (
                        self._build_missing_fields_response(missing_fields)
                        if missing_fields
                        else self._reason_message(reason_code)
                    )
                    delivery = await self._deliver_response(
                        text=response_text,
                        channel_id=channel_id,
                        thread_ts=thread_ts,
                        metadata={
                            "source": source,
                            "project_id": project_id,
                            "team_id": team_id,
                            "session_key": session_key,
                        },
                    )
                    await self._persist_turn_metadata(
                        session_key=session_key,
                        run_id=run_id,
                        status="awaiting_question" if missing_fields else "completed",
                        source=source,
                        project_id=project_id,
                        user_id=user_id,
                        user_message=user_message,
                        request_meta={
                            "deterministic_plan": False,
                            "deterministic_gate_action": gate_action,
                            "deterministic_gate_reason": reason_code,
                            "plan_confidence": float(plan_decision.confidence),
                        },
                        executor_meta={
                            "engine": "deterministic_action_plan",
                            "status": "clarification_required",
                            "missing_fields": missing_fields,
                            "unresolved_calls": unresolved_calls,
                        },
                        delivery_meta=delivery,
                        response_text=response_text,
                        started_at=started_at,
                        finished_at=datetime.utcnow(),
                        error=None,
                    )
                    return OrchestratorResult(
                        ok=True,
                        response_text=None if delivery.get("ok") else response_text,
                        metadata={
                            "run_id": run_id,
                            "session_key": session_key,
                            "deterministic_gate_action": gate_action,
                            "deterministic_gate_reason": reason_code,
                            "awaiting_question": bool(missing_fields),
                            "delivery": delivery,
                        },
                    )
                return None
            plan = plan_decision.plan
            await self._append_event(
                session_key=session_key,
                event_type="action_plan_selected",
                payload={
                    "run_id": run_id,
                    "plan_id": plan.plan_id,
                    "source": plan.source.value,
                    "confidence": plan.confidence,
                    "step_count": len(plan.steps),
                    "reason": plan_decision.reason,
                },
            )

        if plan is None:
            return None

        plan, unresolved_plan_steps = self._resolve_action_plan_step_tools(
            plan=plan,
            role=role,
            connected_integrations=connected_integrations,
            project_context=project_context,
        )
        normalized_unresolved = self._normalize_capability_unresolved_entries(
            unresolved=unresolved_plan_steps,
            path="action_plan",
        )
        if normalized_unresolved:
            ambiguous_payload = self._collect_ambiguous_provider_payload(
                unresolved=unresolved_plan_steps,
            )
            if ambiguous_payload:
                provider_execution_context = {
                    "project_id": project_id,
                    "user_id": user_id,
                    "actor_email": actor_email,
                    "user_message": user_message,
                    "latest_user_message": user_message,
                    "session_key": session_key,
                    "source": source,
                    "run_id": run_id,
                    "role": role,
                    "connected_integrations": connected_integrations,
                    "project_context": project_context,
                    "member_candidates": member_candidates,
                    "deterministic_plan": True,
                }
                await self._set_pending_provider_selection_question(
                    session_key=session_key,
                    candidate_providers=ambiguous_payload.get("providers") or [],
                    unresolved_calls=ambiguous_payload.get("unresolved_calls") or [],
                    execution_context=provider_execution_context,
                    capability=ambiguous_payload.get("capability"),
                )
                response_text = self._build_provider_selection_response(
                    providers=ambiguous_payload.get("providers") or [],
                    capability=ambiguous_payload.get("capability"),
                )
                delivery = await self._deliver_response(
                    text=response_text,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    metadata={
                        "source": source,
                        "project_id": project_id,
                        "team_id": team_id,
                        "session_key": session_key,
                    },
                )
                await self._emit_capability_unresolved_event(
                    session_key=session_key,
                    run_id=run_id,
                    unresolved=normalized_unresolved,
                    resolved=[],
                )
                await self._persist_turn_metadata(
                    session_key=session_key,
                    run_id=run_id,
                    status="awaiting_question",
                    source=source,
                    project_id=project_id,
                    user_id=user_id,
                    user_message=user_message,
                    request_meta={
                        "deterministic_plan": True,
                        "plan_id": plan.plan_id,
                        "plan_source": plan_source,
                        "plan_confidence": plan_confidence,
                        "plan_reason": plan_decision_reason,
                        "step_count": len(plan.steps),
                    },
                    executor_meta={
                        "engine": "deterministic_action_plan",
                        "status": "ambiguous_provider",
                        "provider_selection_required": True,
                        "provider_candidates": ambiguous_payload.get("providers") or [],
                        "capability_routing": {
                            "resolved": [],
                            "unresolved": normalized_unresolved,
                        },
                    },
                    delivery_meta=delivery,
                    response_text=response_text,
                    started_at=started_at,
                    finished_at=datetime.utcnow(),
                    error=None,
                )
                return OrchestratorResult(
                    ok=True,
                    response_text=None if delivery.get("ok") else response_text,
                    metadata={
                        "run_id": run_id,
                        "session_key": session_key,
                        "deterministic_plan": True,
                        "plan_id": plan.plan_id,
                        "awaiting_question": True,
                        "provider_selection_required": True,
                        "capability_routing": {
                            "resolved": [],
                            "unresolved": normalized_unresolved,
                        },
                        "delivery": delivery,
                    },
                )

            response_text = self._build_unsupported_operation_response(normalized_unresolved)
            delivery = await self._deliver_response(
                text=response_text,
                channel_id=channel_id,
                thread_ts=thread_ts,
                metadata={
                    "source": source,
                    "project_id": project_id,
                    "team_id": team_id,
                    "session_key": session_key,
                },
            )
            await self._emit_capability_unresolved_event(
                session_key=session_key,
                run_id=run_id,
                unresolved=normalized_unresolved,
                resolved=[],
            )
            await self._persist_turn_metadata(
                session_key=session_key,
                run_id=run_id,
                status="completed",
                source=source,
                project_id=project_id,
                user_id=user_id,
                user_message=user_message,
                request_meta={
                    "deterministic_plan": True,
                    "plan_id": plan.plan_id,
                    "plan_source": plan_source,
                    "plan_confidence": plan_confidence,
                    "plan_reason": plan_decision_reason,
                    "step_count": len(plan.steps),
                },
                executor_meta={
                    "engine": "deterministic_action_plan",
                    "status": "unsupported_capability",
                    "capability_routing": {
                        "resolved": [],
                        "unresolved": normalized_unresolved,
                    },
                },
                delivery_meta=delivery,
                response_text=response_text,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                error="unsupported_capability",
            )
            return OrchestratorResult(
                ok=False,
                response_text=None if delivery.get("ok") else response_text,
                error="unsupported_capability",
                metadata={
                    "run_id": run_id,
                    "session_key": session_key,
                    "deterministic_plan": True,
                    "plan_id": plan.plan_id,
                    "capability_routing": {
                        "resolved": [],
                        "unresolved": normalized_unresolved,
                    },
                    "delivery": delivery,
                },
            )

        max_steps = max(1, int(settings.cortex_plan_max_steps_per_turn))
        execution_context = {
            "project_id": project_id,
            "user_id": user_id,
            "actor_email": actor_email,
            "user_message": user_message,
            "session_key": session_key,
            "source": source,
            "run_id": run_id,
            "role": role,
            "connected_integrations": connected_integrations,
            "project_context": project_context,
            "member_candidates": member_candidates,
        }
        execution_result = await self.plan_executor.execute(
            plan=plan,
            execution_context=execution_context,
            max_steps_per_turn=max_steps,
        )

        if execution_result.pending_plan is not None:
            serialized_plan = action_plan_to_dict(execution_result.pending_plan)
            metadata = serialized_plan.setdefault("metadata", {})
            metadata["pending_saved_at"] = datetime.utcnow().isoformat()
            metadata.setdefault(
                "source_message_hash",
                self._hash_user_message_for_plan(user_message),
            )
            await self.session_manager.set_pending_plan(
                session_key=session_key,
                pending_plan=serialized_plan,
            )
        else:
            await self.session_manager.set_pending_plan(session_key=session_key, pending_plan=None)

        if execution_result.completed_action_texts:
            await self._record_committed_actions(
                session_key=session_key,
                action_texts=execution_result.completed_action_texts,
            )

        response_text = self._format_action_plan_response(execution_result=execution_result)
        response_text, pending_prompt_sent = self._ensure_pending_plan_followup_prompt(
            response_text=response_text,
            execution_result=execution_result,
        )
        delivery = await self._deliver_response(
            text=response_text,
            channel_id=channel_id,
            thread_ts=thread_ts,
            metadata={
                "source": source,
                "project_id": project_id,
                "team_id": team_id,
                "session_key": session_key,
            },
        )
        finished_at = datetime.utcnow()

        await self._append_event(
            session_key=session_key,
            event_type="action_plan_executed",
            payload={
                "run_id": run_id,
                "plan_id": plan.plan_id,
                "plan_source": plan_source,
                "plan_confidence": plan_confidence,
                "plan_reason": plan_decision_reason,
                "status": execution_result.status.value,
                "completed_actions": execution_result.completed_action_texts,
                "pending_actions": execution_result.pending_action_texts,
                "error_code": execution_result.error_code,
                "error_message": execution_result.error_message,
            },
        )
        await self._persist_turn_metadata(
            session_key=session_key,
            run_id=run_id,
            status=execution_result.status.value,
            source=source,
            project_id=project_id,
            user_id=user_id,
            user_message=user_message,
            request_meta={
                "deterministic_plan": True,
                "plan_id": plan.plan_id,
                "plan_source": plan_source,
                "plan_confidence": plan_confidence,
                "plan_reason": plan_decision_reason,
                "step_count": len(plan.steps),
                "max_steps_per_turn": max_steps,
                "pending_prompt_sent": pending_prompt_sent,
            },
            executor_meta={
                "engine": "deterministic_action_plan",
                "status": execution_result.status.value,
                "completed_actions": execution_result.completed_action_texts,
                "pending_actions": execution_result.pending_action_texts,
                "error_code": execution_result.error_code,
                "error_message": execution_result.error_message,
                **execution_result.details,
            },
            delivery_meta=delivery,
            response_text=response_text,
            started_at=started_at,
            finished_at=finished_at,
            error=None if execution_result.ok else execution_result.error_code or "action_plan_failed",
        )

        return OrchestratorResult(
            ok=execution_result.ok,
            response_text=None if delivery.get("ok") else response_text,
            error=None if execution_result.ok else execution_result.error_code or "action_plan_failed",
            metadata={
                "run_id": run_id,
                "session_key": session_key,
                "deterministic_plan": True,
                "plan_id": plan.plan_id,
                "status": execution_result.status.value,
                "completed_actions": execution_result.completed_action_texts,
                "pending_actions": execution_result.pending_action_texts,
                "pending_prompt_sent": pending_prompt_sent,
                "delivery": delivery,
            },
        )

    def _parse_pending_plan_decision(self, user_message: str) -> Optional[str]:
        normalized = (user_message or "").strip().lower()
        if normalized in {"continue", "resume", "proceed", "go", "go ahead", "yes", "y", "ok", "okay"}:
            return "continue"
        if normalized in {"cancel", "stop", "discard", "decline", "no", "n"}:
            return "cancel"
        return None

    def _is_pending_plan_stale(self, pending_plan_payload: Dict[str, Any]) -> bool:
        metadata = pending_plan_payload.get("metadata")
        if not isinstance(metadata, dict):
            return False
        raw_saved_at = str(metadata.get("pending_saved_at") or "").strip()
        if not raw_saved_at:
            return False
        try:
            saved_at = datetime.fromisoformat(raw_saved_at.replace("Z", "+00:00"))
            if saved_at.tzinfo is not None:
                saved_at = saved_at.replace(tzinfo=None)
        except Exception:
            return False
        ttl_seconds = max(60, int(settings.cortex_pending_plan_ttl_seconds))
        return saved_at <= (datetime.utcnow() - timedelta(seconds=ttl_seconds))

    def _is_same_intent_as_pending_plan(
        self,
        *,
        user_message: str,
        pending_plan_payload: Dict[str, Any],
    ) -> bool:
        metadata = pending_plan_payload.get("metadata")
        if not isinstance(metadata, dict):
            return False
        pending_hash = str(metadata.get("source_message_hash") or "").strip()
        if not pending_hash:
            return False
        current_hash = self._hash_user_message_for_plan(user_message)
        return bool(current_hash and current_hash == pending_hash)

    def _looks_like_action_request(self, user_message: str) -> bool:
        return is_task_write_intent(user_message)

    def _hash_user_message_for_plan(self, user_message: str) -> str:
        normalized = " ".join(str(user_message or "").strip().lower().split())
        if not normalized:
            return ""
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"

    def _humanize_action_summary(self, summary: str) -> str:
        text = str(summary or "").strip()
        if not text:
            return text
        text = text.replace("__LAST_TASK__", "the newly created task")
        if text.lower().startswith("create task 'for "):
            text = text.replace("Create task 'for ", "Create task '", 1)
        return text

    def _format_action_plan_response(
        self,
        *,
        execution_result: ActionPlanExecutionResult,
    ) -> str:
        completed = [
            self._humanize_action_summary(item)
            for item in execution_result.completed_action_texts
        ]
        pending = [
            self._humanize_action_summary(item)
            for item in execution_result.pending_action_texts
        ]
        status = execution_result.status

        if status == PlanExecutionStatus.COMPLETED:
            if completed:
                return f"Completed: {self._join_action_texts(completed)}."
            return "Completed."

        if status == PlanExecutionStatus.PARTIAL:
            return (
                f"I completed part of your request. Completed: {self._join_action_texts(completed)}. "
                f"Remaining: {self._join_action_texts(pending, empty_fallback='the remaining steps')}. "
                "I can continue this when you're ready."
            )

        if status == PlanExecutionStatus.AWAITING_APPROVAL:
            approval_message = execution_result.error_message or (
                "Approval is required before the next step."
            )
            return (
                f"{approval_message} Completed so far: {self._join_action_texts(completed)}. "
                f"Pending: {self._join_action_texts(pending, empty_fallback='the next step')}."
            )

        if completed:
            return (
                f"I completed part of your request. Completed: {self._join_action_texts(completed)}. "
                f"Not completed: {self._join_action_texts(pending, empty_fallback='the remaining steps')}. "
                f"Reason: {execution_result.error_message or 'step execution failed'}"
            )
        return execution_result.error_message or (
            "I couldn't complete that request right now, and no changes were committed."
        )

    def _ensure_pending_plan_followup_prompt(
        self,
        *,
        response_text: str,
        execution_result: ActionPlanExecutionResult,
    ) -> Tuple[str, bool]:
        """Ensure same-turn prompt for pending deterministic action plans."""
        base = str(response_text or "").strip()
        has_pending = execution_result.pending_plan is not None or bool(
            execution_result.pending_action_texts
        )
        if not has_pending:
            return base, False

        prompt = "Reply `continue` to resume or `cancel` to discard."
        lowered = base.lower()
        if "reply `continue`" in lowered and "`cancel`" in lowered:
            return base, False
        if not base:
            return prompt, True
        return f"{base} {prompt}", True

    async def _record_committed_actions(
        self,
        *,
        session_key: str,
        action_texts: list[str],
    ) -> None:
        clean = [str(item).strip() for item in action_texts if str(item).strip()]
        if not clean:
            return

        existing: list[Dict[str, Any]] = []
        try:
            latest = await self.session_manager.get_session(session_key=session_key)
            if latest is not None and isinstance(latest.committed_actions, list):
                existing = [item for item in latest.committed_actions if isinstance(item, dict)]
        except Exception as exc:
            print(f"Cortex committed-actions read failed: {exc}")

        existing_summaries = {
            str(item.get("summary") or "").strip() for item in existing if str(item.get("summary") or "").strip()
        }
        now = datetime.utcnow()
        updated = list(existing)
        for text in clean:
            if text in existing_summaries:
                continue
            updated.append({"summary": text, "recorded_at": now})
            existing_summaries.add(text)
        updated = updated[-100:]

        try:
            await self.session_manager.update_session_fields(
                session_key=session_key,
                fields={"committed_actions": updated},
            )
        except Exception as exc:
            print(f"Cortex committed-actions write failed: {exc}")

    async def _load_committed_action_texts(
        self,
        *,
        session_key: str,
        baseline_actions: Any,
    ) -> list[str]:
        actions = baseline_actions
        try:
            latest = await self.session_manager.get_session(session_key=session_key)
            if latest is not None:
                actions = latest.committed_actions
        except Exception as exc:
            print(f"Cortex committed-actions lookup failed: {exc}")
        return self._action_texts_from_any(actions)

    def _extract_pending_action_texts(self, raw_response: Dict[str, Any]) -> list[str]:
        for key in ("pending_actions", "remaining_actions", "planned_actions"):
            if key in raw_response:
                return self._action_texts_from_any(raw_response.get(key))
        return []

    def _extract_committed_action_texts(self, raw_response: Dict[str, Any]) -> list[str]:
        for key in ("committed_actions", "completed_actions", "applied_actions"):
            if key in raw_response:
                return self._action_texts_from_any(raw_response.get(key))
        return []

    def _build_failed_turn_user_message(
        self,
        *,
        execute_result: ExecutorResult,
        committed_action_texts: list[str],
        pending_action_texts: list[str],
    ) -> str:
        raw = execute_result.raw_response or {}
        error_code = str(raw.get("error_code") or execute_result.error or "").strip().lower()
        is_max_turns = error_code == "max_turns_exceeded"
        if error_code == "required_tool_call_missing":
            return self._reason_message("required_tool_call_missing")

        if is_max_turns and committed_action_texts:
            return (
                "I completed part of your request before hitting my step limit. "
                f"Completed: {self._join_action_texts(committed_action_texts)}. "
                f"Remaining: {self._join_action_texts(pending_action_texts, empty_fallback='the remaining requested actions')}. "
                "Ask me to continue."
            )

        if is_max_turns:
            partial_summary = str(raw.get("partial_summary") or "").strip()
            if partial_summary:
                return (
                    "This request required more steps than I can handle in a single turn. "
                    f"Here's what I found so far: {partial_summary}. "
                    "You can ask me to continue."
                )
            return (
                "This request required more steps than I can handle in a single turn. "
                "You can ask me to continue."
            )

        if committed_action_texts:
            return (
                "I completed part of your request before the turn failed. "
                f"Completed: {self._join_action_texts(committed_action_texts)}. "
                f"Remaining: {self._join_action_texts(pending_action_texts, empty_fallback='the remaining requested actions')}. "
                "Ask me to continue."
            )

        if error_code in {"executor_all_models_failed", "executor_exception", "executor_not_available"}:
            return (
                "I'm having trouble processing your request right now. "
                "Our AI service is temporarily unavailable. Please try again shortly."
            )

        return "I couldn't complete that request right now, and no changes were committed. Please try again."

    def _action_texts_from_any(self, value: Any) -> list[str]:
        items: list[Any]
        if value is None:
            return []
        if isinstance(value, list):
            items = value
        else:
            items = [value]

        texts: list[str] = []
        for item in items:
            text = ""
            if isinstance(item, str):
                text = item.strip()
            elif isinstance(item, dict):
                for key in ("summary", "action", "description", "message", "title"):
                    candidate = str(item.get(key) or "").strip()
                    if candidate:
                        text = candidate
                        break
                if not text:
                    tool_name = str(item.get("tool_name") or "").strip()
                    target = str(item.get("target") or item.get("external_id") or "").strip()
                    if tool_name and target:
                        text = f"{tool_name} ({target})"
                    elif tool_name:
                        text = tool_name
            if text and text not in texts:
                texts.append(text)
        return texts

    def _join_action_texts(self, values: list[str], *, empty_fallback: str = "none") -> str:
        items = [str(value).strip() for value in values if str(value).strip()]
        if not items:
            return empty_fallback
        if len(items) == 1:
            return items[0]
        if len(items) == 2:
            return f"{items[0]}; {items[1]}"
        return "; ".join(items)

    def _normalize_member_candidates(self, value: Any) -> List[Dict[str, Any]]:
        if not isinstance(value, list):
            return []
        normalized: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, dict):
                continue
            email = str(item.get("email") or "").strip().lower()
            name = str(item.get("name") or "").strip()
            user_id = str(item.get("user_id") or "").strip()
            slack_user_id = str(item.get("slack_user_id") or "").strip().upper()
            source = str(item.get("source") or "").strip().lower() or "unknown"
            try:
                confidence = float(item.get("confidence") or 0.9)
            except Exception:
                confidence = 0.9
            confidence = max(0.0, min(1.0, confidence))
            dedupe_key = email or slack_user_id or user_id or name.lower()
            if not dedupe_key or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            normalized.append(
                {
                    "email": email,
                    "name": name,
                    "user_id": user_id,
                    "slack_user_id": slack_user_id,
                    "source": source,
                    "confidence": confidence,
                }
            )
        normalized.sort(key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
        return normalized

    def _resolve_member_candidates_for_turn(
        self,
        *,
        project_context: Dict[str, Any],
        user_message: str,
        actor_email: Optional[str],
        actor_user_id: str,
        ingress_candidates: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen: set[str] = set()

        def _add(candidate: Dict[str, Any]) -> None:
            email = str(candidate.get("email") or "").strip().lower()
            slack_user_id = str(candidate.get("slack_user_id") or "").strip().upper()
            user_id = str(candidate.get("user_id") or "").strip()
            name = str(candidate.get("name") or "").strip().lower()
            dedupe_key = email or slack_user_id or user_id or name
            if not dedupe_key or dedupe_key in seen:
                return
            seen.add(dedupe_key)
            merged.append(candidate)

        for item in self._normalize_member_candidates(ingress_candidates):
            _add(item)

        for item in self._extract_name_based_member_candidates(
            project_context=project_context,
            user_message=user_message,
            actor_email=actor_email,
            actor_user_id=actor_user_id,
        ):
            _add(item)

        merged.sort(key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
        return merged

    def _extract_name_based_member_candidates(
        self,
        *,
        project_context: Dict[str, Any],
        user_message: str,
        actor_email: Optional[str],
        actor_user_id: str,
    ) -> List[Dict[str, Any]]:
        text = str(user_message or "").strip().lower()
        if not text:
            return []
        members = project_context.get("members") or []
        if not isinstance(members, list):
            return []

        actor_email_norm = str(actor_email or "").strip().lower()
        actor_user_norm = str(actor_user_id or "").strip()

        first_name_counts: Dict[str, int] = {}
        for member in members:
            if not isinstance(member, dict):
                continue
            if str(member.get("status") or "").strip().lower() != "active":
                continue
            raw_name = str(member.get("name") or "").strip().lower()
            if not raw_name:
                continue
            first_name = raw_name.split()[0]
            if len(first_name) < 3:
                continue
            first_name_counts[first_name] = first_name_counts.get(first_name, 0) + 1

        candidates: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for member in members:
            if not isinstance(member, dict):
                continue
            if str(member.get("status") or "").strip().lower() != "active":
                continue
            email = str(member.get("email") or "").strip().lower()
            project_user_id = str(member.get("user_id") or "").strip()
            integration_ids = member.get("integration_ids") or {}
            slack_user_id = str(integration_ids.get("slack_user_id") or "").strip().upper()
            if actor_email_norm and email and email == actor_email_norm:
                continue
            if actor_user_norm and (
                project_user_id == actor_user_norm
                or slack_user_id == actor_user_norm.upper()
            ):
                continue

            raw_name = str(member.get("name") or "").strip()
            normalized_name = " ".join(raw_name.lower().split())
            if not normalized_name:
                continue
            dedupe_key = email or slack_user_id or project_user_id or normalized_name
            if not dedupe_key or dedupe_key in seen:
                continue

            matched = False
            confidence = 0.0
            source = ""
            full_name_pattern = rf"\b{re.escape(normalized_name)}\b"
            if re.search(full_name_pattern, text):
                matched = True
                confidence = 0.9
                source = "name_exact"
            else:
                first_name = normalized_name.split()[0]
                if len(first_name) >= 3 and first_name_counts.get(first_name, 0) == 1:
                    contextual_patterns = [
                        rf"^\s*(?:hi|hello|hey)\s+{re.escape(first_name)}\b",
                        rf"\b(?:to|for)\s+{re.escape(first_name)}\b",
                        rf"^\s*{re.escape(first_name)}\b.*\b(?:can you|please)\b",
                    ]
                    if any(re.search(pattern, text) for pattern in contextual_patterns):
                        matched = True
                        confidence = 0.82
                        source = "name_contextual"

            if not matched:
                continue

            seen.add(dedupe_key)
            candidates.append(
                {
                    "email": email,
                    "name": raw_name,
                    "user_id": project_user_id,
                    "slack_user_id": slack_user_id,
                    "source": source,
                    "confidence": confidence,
                }
            )

        candidates.sort(key=lambda item: float(item.get("confidence") or 0.0), reverse=True)
        return candidates

    def _build_session_snapshot(self, *, session: CortexSession) -> Dict[str, Any]:
        recent_events: List[Dict[str, Any]] = []
        for source in (session.recent_turns or [], session.transcript or []):
            if not isinstance(source, list):
                continue
            for event in source[-20:]:
                if isinstance(event, dict):
                    recent_events.append(event)

        target_refs: List[str] = []
        recent_tool_names: List[str] = []
        last_user_message = ""
        last_assistant_message = ""

        for event in recent_events:
            event_type = str(event.get("event_type") or "").strip().lower()
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if event_type == "user_message":
                text = str(payload.get("text") or "").strip()
                if text:
                    last_user_message = text
                continue
            if event_type != "llm_response":
                continue
            text = str(payload.get("text") or "").strip()
            if text:
                last_assistant_message = text
            executor = payload.get("executor") if isinstance(payload.get("executor"), dict) else {}
            tool_names = executor.get("tool_names_called")
            if isinstance(tool_names, list):
                for name in tool_names:
                    token = str(name or "").strip()
                    if token and token not in recent_tool_names:
                        recent_tool_names.append(token)
            refs = executor.get("target_refs")
            if isinstance(refs, list):
                for item in refs:
                    token = str(item or "").strip()
                    if not token:
                        continue
                    normalized = token.upper() if "-" in token else token
                    if normalized not in target_refs:
                        target_refs.append(normalized)

        if len(last_assistant_message) > 220:
            last_assistant_message = last_assistant_message[:220] + "...(truncated)"

        return {
            "turn_count": int(session.turn_count or 0),
            "target_refs": target_refs[-25:],
            "recent_tool_names": recent_tool_names[-15:],
            "last_user_message": last_user_message,
            "last_assistant_message": last_assistant_message,
        }


    # --- DEAD CODE START: _tool_descriptors_for_prompt (zero callers). ---
    # def _tool_descriptors_for_prompt(self) -> list[Dict[str, Any]]:
    # """Return all registered tool descriptors; prompt builder applies filtering."""
    # return [spec.to_prompt_descriptor() for spec in self.tool_registry.list_specs()]
    # --- DEAD CODE END: _tool_descriptors_for_prompt. ---


    def _connected_integration_names(self, project_context: Dict[str, Any]) -> list[str]:
        integrations = project_context.get("integrations") or {}
        if not isinstance(integrations, dict):
            return []
        names = []
        for name, cfg in integrations.items():
            if isinstance(cfg, dict) and cfg.get("status") == "connected":
                normalized = str(name).strip().lower()
                if normalized:
                    names.append(normalized)
        return sorted(set(names))

    def _should_force_required_tool_choice(
        self,
        *,
        user_message: str,
        connected_integrations: list[str],
    ) -> bool:
        text = str(user_message or "").strip()
        if not text:
            return False
        # Runtime turns are operational after deterministic ingress/scope gating.
        # Require at least one tool call for these turns to avoid free-text
        # responses derived from stale conversation context.
        return True

    def _prefer_grounded_tool_error_message(
        self,
        *,
        response_text: str,
        execute_result: ExecutorResult,
    ) -> str:
        """
        Use canonical tool error user_message when all invoked tools failed.

        This avoids model-invented generic summaries that drift from actual tool results.
        """
        raw = execute_result.raw_response or {}
        try:
            tool_calls = int(raw.get("tool_calls_count") or 0)
            tool_success = int(raw.get("tool_success_count") or 0)
            tool_errors = int(raw.get("tool_error_count") or 0)
        except Exception:
            return response_text

        if tool_calls <= 0 or tool_success > 0 or tool_errors <= 0:
            return response_text

        errors = raw.get("tool_errors")
        if not isinstance(errors, list):
            return response_text
        for item in errors:
            if not isinstance(item, dict):
                continue
            message = str(item.get("user_message") or "").strip()
            if message:
                error_code = str(item.get("error_code") or "").strip().lower()
                if error_code == "provider_type_clarification_required":
                    return message
                lowered = message.lower()
                if (
                    "`" in message
                    or "the tool " in lowered
                    or "tool `" in lowered
                    or "jira_" in lowered
                    or "_work_item" in lowered
                ):
                    return "I couldn't complete that action right now. Please try again."
                return message
        return response_text

    def _prefer_deterministic_clarification_response(
        self,
        *,
        response_text: str,
        execute_result: ExecutorResult,
    ) -> str:
        """
        Preserve tool-provided clarification copy verbatim when available.

        This avoids runtime rephrasing drift for pending-clarification turns where
        exact instruction text matters (for example show-more/list-all commands).
        """
        raw = execute_result.raw_response or {}
        clarification_text = str(raw.get("clarification_response_text") or "").strip()
        if clarification_text:
            return clarification_text
        return response_text

    def _normalize_partial_action_item_outcome_response(
        self,
        *,
        response_text: str,
        execute_result: ExecutorResult,
    ) -> str:
        """
        Force deterministic wording when action-item writes are partially successful.

        This prevents contradictory free-form language (for example "created" + "couldn't assign")
        by grounding user-visible text to committed-actions and tool error envelopes.
        """
        raw = execute_result.raw_response or {}
        try:
            tool_success = int(raw.get("tool_success_count") or 0)
            tool_errors = int(raw.get("tool_error_count") or 0)
        except Exception:
            return response_text
        if tool_success <= 0 or tool_errors <= 0:
            return response_text

        names = raw.get("tool_names_called")
        if not isinstance(names, list):
            return response_text
        action_item_tools = {
            "action_item_create",
            "action_item_update",
            "action_item_close",
        }
        called_action_item_tools = [str(name or "").strip() for name in names if str(name or "").strip() in action_item_tools]
        if not called_action_item_tools:
            return response_text

        completed = self._extract_committed_action_texts(raw)
        failed = self._extract_failed_tool_messages(raw)
        filtered_failed = self._filter_non_blocking_action_item_failures(
            raw_response=raw,
            completed=completed,
            failed=failed,
        )
        if not completed or not failed:
            return response_text
        if completed and not filtered_failed:
            return f"Completed: {self._join_action_texts(completed)}."
        return (
            f"I completed part of your request. "
            f"Completed: {self._join_action_texts(completed)}. "
            f"Not completed: {self._join_action_texts(filtered_failed)}."
        )

    def _normalize_partial_jira_create_assign_outcome_response(
        self,
        *,
        response_text: str,
        execute_result: ExecutorResult,
    ) -> str:
        """Normalize Jira create+assign partial outcomes using deterministic summaries."""
        raw = execute_result.raw_response or {}
        try:
            tool_success = int(raw.get("tool_success_count") or 0)
            tool_errors = int(raw.get("tool_error_count") or 0)
        except Exception:
            return response_text
        if tool_success <= 0 or tool_errors <= 0:
            return response_text

        names = raw.get("tool_names_called")
        if not isinstance(names, list):
            return response_text
        normalized_names = [str(name or "").strip() for name in names]
        if "jira_create_work_item" not in normalized_names or "jira_assign_work_item" not in normalized_names:
            return response_text

        completed = self._extract_committed_action_texts(raw)
        if not completed:
            return response_text
        assign_failures = self._extract_failed_tool_messages_for_tool(
            raw_response=raw,
            tool_name="jira_assign_work_item",
        )
        if not assign_failures:
            return response_text

        return (
            f"Completed: {self._join_action_texts(completed)}.\n\n"
            f"Assignment pending: {self._join_action_texts(assign_failures)}."
        )

    def _prefer_deterministic_action_item_create_summary(
        self,
        *,
        response_text: str,
        execute_result: ExecutorResult,
    ) -> str:
        raw = execute_result.raw_response or {}
        names = raw.get("tool_names_called")
        committed = raw.get("committed_actions")
        if not isinstance(names, list) or not isinstance(committed, list):
            return response_text
        if "action_item_create" not in [str(name or "").strip() for name in names]:
            return response_text
        for item in committed:
            token = str(item or "").strip()
            if token.startswith("Action Item Created:"):
                return token
        return response_text

    def _ensure_parent_line_for_jira_create_response(
        self,
        *,
        response_text: str,
        execute_result: ExecutorResult,
    ) -> str:
        """Ensure Jira create responses always include a parent line for consistency."""
        text = str(response_text or "").strip()
        if not text:
            return text
        raw = execute_result.raw_response or {}
        try:
            tool_success = int(raw.get("tool_success_count") or 0)
        except Exception:
            tool_success = 0
        if tool_success <= 0:
            return text
        names = raw.get("tool_names_called")
        if not isinstance(names, list):
            return text
        normalized_names = [str(name or "").strip() for name in names]
        if "jira_create_work_item" not in normalized_names:
            return text
        # Clarification turns already have deterministic text and should not be rewritten.
        if str(raw.get("clarification_response_text") or "").strip():
            return text

        # Normalize legacy label "Parent Task" to canonical "Parent" for consistent cross-type wording.
        text = re.sub(
            r"(?im)^(\s*[-*]?\s*)(?:\*\*)?\s*parent\s+task\s*(?:\*\*)?\s*:\s*",
            r"\1Parent: ",
            text,
        )

        if re.search(r"(?im)^\s*[-*]?\s*(?:\*\*)?\s*parent(?:\s+task)?\s*(?:\*\*)?\s*:", text):
            return text

        parent_key = ""
        for summary in self._extract_committed_action_texts(raw):
            match = re.search(r"\bunder\s+([A-Z][A-Z0-9]+-\d+)\b", str(summary), re.IGNORECASE)
            if match:
                parent_key = str(match.group(1) or "").strip().upper()
                break
        parent_line = f"- Parent: {parent_key or 'N/A'}"

        assistance_idx = text.lower().find("\nif you need further assistance")
        if assistance_idx >= 0:
            head = text[:assistance_idx].rstrip()
            tail = text[assistance_idx + 1 :].strip()
            return f"{head}\n{parent_line}\n\n{tail}".strip()
        return f"{text}\n{parent_line}".strip()

    def _filter_non_blocking_action_item_failures(
        self,
        *,
        raw_response: Dict[str, Any],
        completed: list[str],
        failed: list[str],
    ) -> list[str]:
        """
        Hide known non-blocking update-target clarification after successful create.

        In create-intent turns, model may attempt an unnecessary `action_item_update`
        immediately after `action_item_create`; target-grounding guard then returns
        clarification noise. User-facing output should prioritize successful create.
        """
        if not completed or not failed:
            return failed

        errors = raw_response.get("tool_errors")
        if not isinstance(errors, list):
            return failed

        suppression_messages: set[str] = set()
        for item in errors:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool_name") or "").strip()
            error_code = str(item.get("error_code") or "").strip()
            user_message = str(item.get("user_message") or "").strip()
            if tool_name != "action_item_update":
                continue
            if error_code == "target_scope_clarification_required" or (
                "which items to update before i continue" in user_message.lower()
            ):
                suppression_messages.add(user_message)

        if not suppression_messages:
            return failed
        return [token for token in failed if str(token or "").strip() not in suppression_messages]

    def _extract_failed_tool_messages(self, raw_response: Dict[str, Any]) -> list[str]:
        errors = raw_response.get("tool_errors")
        if not isinstance(errors, list):
            return []
        messages: list[str] = []
        for item in errors:
            if not isinstance(item, dict):
                continue
            token = str(item.get("user_message") or item.get("error_code") or "").strip()
            if token and token not in messages:
                messages.append(token)
        return messages

    def _extract_failed_tool_messages_for_tool(
        self,
        *,
        raw_response: Dict[str, Any],
        tool_name: str,
    ) -> list[str]:
        errors = raw_response.get("tool_errors")
        if not isinstance(errors, list):
            return []
        normalized_tool_name = str(tool_name or "").strip()
        messages: list[str] = []
        for item in errors:
            if not isinstance(item, dict):
                continue
            if str(item.get("tool_name") or "").strip() != normalized_tool_name:
                continue
            token = str(item.get("user_message") or item.get("error_code") or "").strip()
            if token and token not in messages:
                messages.append(token)
        return messages

    def _log_tool_outcomes_for_debug(
        self,
        *,
        run_id: str,
        session_key: str,
        execute_result: ExecutorResult,
        started_at: Optional[datetime] = None,
        delivery_ms: Optional[int] = None,
    ) -> None:
        raw = execute_result.raw_response or {}
        try:
            turn_total_ms = None
            if isinstance(started_at, datetime):
                turn_total_ms = max(
                    0,
                    int((datetime.utcnow() - started_at).total_seconds() * 1000),
                )
            executor_ms = int(raw.get("latency_ms") or 0)
            tool_ms = int(raw.get("tool_ms_total") or 0)
            llm_ms_estimate = max(0, executor_ms - tool_ms) if executor_ms > 0 else 0
            log_line = (
                "cortex_tool_outcomes "
                f"run_id={str(run_id or '').strip()} "
                f"session_key={str(session_key or '').strip()} "
                f"turn_total_ms={turn_total_ms} "
                f"executor_ms={executor_ms} "
                f"llm_ms_estimate={llm_ms_estimate} "
                f"tool_ms_total={tool_ms} "
                f"delivery_ms={int(delivery_ms or 0)} "
                f"tool_calls={int(raw.get('tool_calls_count') or 0)} "
                f"tool_success={int(raw.get('tool_success_count') or 0)} "
                f"tool_errors={int(raw.get('tool_error_count') or 0)} "
                f"tool_names={list(raw.get('tool_names_called') or [])} "
                f"tool_ms_by_name={dict(raw.get('tool_ms_by_name') or {})} "
                f"compose_runtime_used={bool(raw.get('compose_runtime_used'))} "
                f"compose_runtime_reason={str(raw.get('compose_runtime_reason') or '')} "
                f"compose_runtime_latency_ms={int(raw.get('compose_runtime_latency_ms') or 0)} "
                f"committed_actions={list(raw.get('committed_actions') or [])} "
                f"error_messages={self._extract_failed_tool_messages(raw)}"
            )
            print(log_line)
            logger.info(
                "cortex_tool_outcomes run_id=%s session_key=%s turn_total_ms=%s executor_ms=%s llm_ms_estimate=%s tool_ms_total=%s delivery_ms=%s tool_calls=%s tool_success=%s tool_errors=%s tool_names=%s tool_ms_by_name=%s compose_runtime_used=%s compose_runtime_reason=%s compose_runtime_latency_ms=%s committed_actions=%s error_messages=%s",
                str(run_id or "").strip(),
                str(session_key or "").strip(),
                turn_total_ms,
                executor_ms,
                llm_ms_estimate,
                tool_ms,
                int(delivery_ms or 0),
                int(raw.get("tool_calls_count") or 0),
                int(raw.get("tool_success_count") or 0),
                int(raw.get("tool_error_count") or 0),
                list(raw.get("tool_names_called") or []),
                dict(raw.get("tool_ms_by_name") or {}),
                bool(raw.get("compose_runtime_used")),
                str(raw.get("compose_runtime_reason") or ""),
                int(raw.get("compose_runtime_latency_ms") or 0),
                list(raw.get("committed_actions") or []),
                self._extract_failed_tool_messages(raw),
            )
        except Exception:
            # Debug logging must never impact runtime flow.
            return
