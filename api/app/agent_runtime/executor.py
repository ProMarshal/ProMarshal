"""Cortex executor shell wired to OpenAI Agents SDK + LiteLLM."""

from dataclasses import dataclass
import hashlib
import json
import re
from time import perf_counter
from typing import Any, Dict, List, Optional, Tuple

from app.core.config import settings
from app.agent_runtime.gateway_model_provider import (
    GatewayToolCallingModelProvider,
)
from app.cortex.domain.tasks.query_intent import (
    TaskLifecycleVisibility,
    is_task_read_self_scope_request,
    parse_task_read_intent,
    parse_task_lifecycle_visibility,
)
from app.cortex.proposals.service import CortexProposalService
from app.cortex.clarification_formatting import render_missing_field_labels
from app.cortex.llm.litellm_config import (
    LiteLLMRuntimeConfig,
    build_litellm_runtime_config,
    seed_litellm_api_keys,
    to_litellm_model,
)
from app.cortex.operation_replay import CortexOperationReplayCache
from app.cortex.permissions import CortexPermissionService
from app.cortex.quality.engine import QualityEngine
from app.cortex.quality.models import OperationProfile
from app.cortex.quality.policy_registry import quality_policy_runtime_metadata
from app.cortex.tools.base import CortexTool, IdempotencyClass
from app.cortex.tools.contracts import (
    canonicalize_tool_args,
    field_classes_for_tool,
)
from app.cortex.tools.error_envelope import build_permission_denied_error, build_tool_error
from app.cortex.tools.registry import CortexToolRegistry
from app.domain.parsing.registry import message_for_reason_code
from app.domain.parsing.service import enforce_mutation_gate, parse_with_runtime_adapter
from app.llm.gateway import LLMErrorClass, LLMHealthEvent, LLMHealthEventType
from app.llm.telemetry import (
    LLMTelemetryAdapter,
    LLMTelemetryEvent,
    LLMTelemetryEventType,
    LLMTelemetrySeverity,
    LoggingLLMTelemetryAdapter,
)

try:
    from agents import Agent, ModelSettings, RunConfig, RunContextWrapper, Runner, function_tool
    from agents.tool import FunctionTool, ToolContext
    from agents.exceptions import MaxTurnsExceeded
    from agents.extensions.models.litellm_provider import LitellmProvider
except Exception:  # pragma: no cover - import guard for partial environments
    Agent = None
    ModelSettings = None
    RunConfig = None
    RunContextWrapper = None
    Runner = None
    function_tool = None
    FunctionTool = None
    ToolContext = None
    MaxTurnsExceeded = None
    LitellmProvider = None


@dataclass(frozen=True)
class ExecutorResult:
    """Execution result payload from the Cortex runtime adapter."""

    ok: bool
    output_text: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


class AgentExecutor:
    """Shared LLM agent executor used by both Cortex and Cadence."""

    SYSTEM_MANAGED_TOOL_FIELDS = {"operation_id"}

    def __init__(
        self,
        *,
        runtime_config: Optional[LiteLLMRuntimeConfig] = None,
        telemetry_adapter: Optional[LLMTelemetryAdapter] = None,
        tool_registry: Optional[CortexToolRegistry] = None,
        permission_service: Optional[CortexPermissionService] = None,
        operation_replay_cache: Optional[CortexOperationReplayCache] = None,
        proposal_service: Optional[CortexProposalService] = None,
    ) -> None:
        self.runtime_config = runtime_config or build_litellm_runtime_config()
        self.telemetry = telemetry_adapter or LoggingLLMTelemetryAdapter()
        self.tool_registry = tool_registry or CortexToolRegistry()
        if not self.tool_registry.list_names():
            self.tool_registry.register_launch_toolset()
        self.permission_service = permission_service or CortexPermissionService()
        self.operation_replay_cache = operation_replay_cache or CortexOperationReplayCache(
            ttl_seconds=settings.cortex_op_ttl_seconds
        )
        self.proposal_service = proposal_service or CortexProposalService()
        self.quality_engine = QualityEngine(proposal_service=self.proposal_service)
        seed_litellm_api_keys(
            provider=self.runtime_config.primary_provider,
            fallback_provider=self.runtime_config.fallback_provider,
        )

    async def run(self, payload: Dict[str, Any]) -> ExecutorResult:
        """Execute a prompt payload and return normalized output."""
        prompt = str(payload.get("prompt") or "").strip()
        if not prompt:
            return ExecutorResult(ok=False, error="missing_prompt")

        context = payload.get("context") or {}
        if "_executor_turn_started_at_perf" not in context:
            context["_executor_turn_started_at_perf"] = perf_counter()
        if "_tool_call_guard_hashes" not in context:
            context["_tool_call_guard_hashes"] = []
        if "_tool_call_guard_write_count" not in context:
            context["_tool_call_guard_write_count"] = 0
        project_id = str(context.get("project_id") or "")
        user_id = str(context.get("user_id") or "")
        session_key = str(context.get("session_key") or "")

        user_input = self._extract_user_input(payload)
        model_candidates = self._model_candidates()
        require_tool_call = bool(context.get("force_tool_choice"))
        last_error = "executor_no_attempts"
        last_failure_meta: Dict[str, Any] = {}

        for attempt, model_name in enumerate(model_candidates):
            provider, model = self._split_model(model_name)
            started = perf_counter()
            try:
                output_text, runtime_meta = await self._run_with_agents_litellm(
                    model_name=model_name,
                    system_prompt=prompt,
                    user_input=user_input,
                    tool_descriptors=payload.get("tools") or [],
                    execution_context=context,
                )
                runtime_meta = dict(runtime_meta or {})
                runtime_meta.setdefault(
                    "llm_path",
                    "gateway_model_provider",
                )
                await self._maybe_emit_shadow_parity(
                    payload=payload,
                    prompt=prompt,
                    user_input=user_input,
                    model_name=model_name,
                    execution_context=context,
                    primary_output=output_text,
                    primary_runtime_meta=runtime_meta,
                )
                latency_ms = int((perf_counter() - started) * 1000)
                self._emit_success(
                    provider=provider,
                    model=model,
                    project_id=project_id,
                    user_id=user_id,
                    session_id=session_key,
                    latency_ms=latency_ms,
                    metadata={"attempt": attempt + 1, **runtime_meta},
                )
                runtime_tool_count = int(runtime_meta.get("runtime_tool_count") or 0)
                tool_calls_count = int(runtime_meta.get("tool_calls_count") or 0)
                if require_tool_call and runtime_tool_count > 0 and tool_calls_count <= 0:
                    last_error = "required_tool_call_missing"
                    if attempt < len(model_candidates) - 1:
                        continue
                    return ExecutorResult(
                        ok=False,
                        error="required_tool_call_missing",
                        raw_response={
                            "provider": provider,
                            "model": model,
                            "attempt": attempt + 1,
                            "latency_ms": latency_ms,
                            "error_code": "required_tool_call_missing",
                            "required_tool_call": True,
                            **runtime_meta,
                        },
                    )
                return ExecutorResult(
                    ok=True,
                    output_text=output_text,
                    raw_response={
                        "provider": provider,
                        "model": model,
                        "attempt": attempt + 1,
                        "latency_ms": latency_ms,
                        **runtime_meta,
                    },
                )
            except Exception as exc:
                latency_ms = int((perf_counter() - started) * 1000)
                last_error = str(exc)
                failure_trace = getattr(exc, "cortex_tool_call_trace", None)
                failure_meta: Dict[str, Any] = {}
                if isinstance(failure_trace, dict):
                    failure_meta = {
                        "runtime_tool_count": int(len(payload.get("tools") or [])),
                        "tool_calls_count": int(failure_trace.get("count") or 0),
                        "tool_names_called": list(failure_trace.get("names") or []),
                        "tool_success_count": int(failure_trace.get("success_count") or 0),
                        "tool_error_count": int(failure_trace.get("error_count") or 0),
                        "tool_errors": list(failure_trace.get("errors") or []),
                        "committed_actions": list(failure_trace.get("committed_actions") or []),
                        "target_refs": list(failure_trace.get("target_refs") or []),
                    }
                last_failure_meta = {
                    "provider": provider,
                    "model": model,
                    "attempt": attempt + 1,
                    "latency_ms": latency_ms,
                    **failure_meta,
                }
                self._emit_failure(
                    provider=provider,
                    model=model,
                    project_id=project_id,
                    user_id=user_id,
                    session_id=session_key,
                    latency_ms=latency_ms,
                    message=last_error,
                    metadata={"attempt": attempt + 1, **failure_meta},
                )
                if MaxTurnsExceeded and isinstance(exc, MaxTurnsExceeded):
                    return ExecutorResult(
                        ok=False,
                        error="max_turns_exceeded",
                        raw_response={
                            "provider": provider,
                            "model": model,
                            "attempt": attempt + 1,
                            "latency_ms": latency_ms,
                            "error_code": "max_turns_exceeded",
                            "error_message": last_error,
                            "runtime_tool_count": len(payload.get("tools") or []),
                            **failure_meta,
                        },
                    )

                if attempt < len(model_candidates) - 1:
                    fallback_provider, fallback_model = self._split_model(model_candidates[attempt + 1])
                    self._emit_fallback(
                        provider=provider,
                        model=model,
                        fallback_provider=fallback_provider,
                        fallback_model=fallback_model,
                        project_id=project_id,
                        user_id=user_id,
                        session_id=session_key,
                        metadata={"attempt": attempt + 1},
                    )

        print(f"Cortex executor failed for all models: {last_error}")
        return ExecutorResult(
            ok=False,
            error="executor_all_models_failed",
            raw_response={
                "last_error": last_error,
                "attempts": len(model_candidates),
                **last_failure_meta,
            },
        )

    async def _run_with_agents_litellm(
        self,
        *,
        model_name: str,
        system_prompt: str,
        user_input: str,
        tool_descriptors: List[Dict[str, Any]],
        execution_context: Dict[str, Any],
    ) -> Tuple[str, Dict[str, Any]]:
        if not all([Agent, Runner, RunConfig, ModelSettings, LitellmProvider]):
            raise RuntimeError("agents_or_litellm_provider_unavailable")

        tool_call_trace: Dict[str, Any] = {
            "count": 0,
            "names": [],
            "success_count": 0,
            "error_count": 0,
            "errors": [],
            "committed_actions": [],
            "target_refs": [],
            "read_tool_name": "",
            "read_items_count": 0,
            "read_items": [],
            "tool_ms_total": 0,
            "tool_ms_by_name": {},
        }
        runtime_tools = self._build_runtime_tools(
            tool_descriptors=tool_descriptors,
            execution_context=execution_context,
            tool_call_trace=tool_call_trace,
        )
        force_tool_choice = bool(execution_context.get("force_tool_choice"))
        tool_choice = "required" if force_tool_choice and runtime_tools else None

        agent = Agent(
            name="AgentExecutor",
            instructions=system_prompt,
            model=model_name,
            tools=runtime_tools,
            model_settings=ModelSettings(
                temperature=self.runtime_config.temperature,
                max_tokens=self.runtime_config.max_tokens,
                tool_choice=tool_choice,
                parallel_tool_calls=False if runtime_tools else None,
                extra_args={
                    "timeout": self.runtime_config.timeout_seconds,
                    "num_retries": self.runtime_config.retries,
                },
            ),
        )

        try:
            run_result = await Runner.run(
                starting_agent=agent,
                input=user_input,
                max_turns=self.runtime_config.max_turns,
                run_config=RunConfig(
                    model_provider=self._build_model_provider(
                        execution_context=execution_context,
                    ),
                    tracing_disabled=not bool(settings.cortex_agents_tracing_enabled),
                ),
            )
        except Exception as exc:
            # Preserve partial tool-call trace for failure-state truth messaging.
            try:
                setattr(exc, "cortex_tool_call_trace", dict(tool_call_trace))
            except Exception:
                pass
            raise

        approval_interruptions = self._serialize_interruptions(
            list(getattr(run_result, "interruptions", []) or [])
        )
        output_text = self._coerce_output_text(run_result.final_output)
        if not output_text and not approval_interruptions:
            raise RuntimeError("empty_executor_output")

        raw_responses = len(getattr(run_result, "raw_responses", []) or [])
        interruptions = len(approval_interruptions)
        policy_meta = quality_policy_runtime_metadata()
        return output_text, {
            "raw_responses": raw_responses,
            "interruptions": interruptions,
            "approval_interruptions": approval_interruptions,
            "last_response_id": getattr(run_result, "last_response_id", None),
            "runtime_tool_count": len(runtime_tools),
            "tool_choice": tool_choice or "auto",
            "tool_calls_count": int(tool_call_trace.get("count") or 0),
            "tool_names_called": list(tool_call_trace.get("names") or []),
            "tool_success_count": int(tool_call_trace.get("success_count") or 0),
            "tool_error_count": int(tool_call_trace.get("error_count") or 0),
            "tool_errors": list(tool_call_trace.get("errors") or []),
            "committed_actions": list(tool_call_trace.get("committed_actions") or []),
            "target_refs": list(tool_call_trace.get("target_refs") or []),
            "read_tool_name": str(tool_call_trace.get("read_tool_name") or "").strip(),
            "read_items_count": int(tool_call_trace.get("read_items_count") or 0),
            "read_items": list(tool_call_trace.get("read_items") or []),
            "clarification_response_text": str(
                tool_call_trace.get("clarification_response_text") or ""
            ).strip(),
            "clarification_type": str(tool_call_trace.get("clarification_type") or "").strip(),
            "clarification_tool_name": str(
                tool_call_trace.get("clarification_tool_name") or ""
            ).strip(),
            "tool_ms_total": int(tool_call_trace.get("tool_ms_total") or 0),
            "tool_ms_by_name": dict(tool_call_trace.get("tool_ms_by_name") or {}),
            "quality_policy_schema_version": str(
                policy_meta.get("quality_policy_schema_version") or ""
            ),
            "quality_policy_loaded": bool(policy_meta.get("quality_policy_loaded")),
            "llm_path": (
                "gateway_model_provider"
            ),
        }

    async def execute_tool_call(
        self,
        *,
        tool_name: str,
        args: Dict[str, Any],
        execution_context: Dict[str, Any],
        enforce_approval: bool = True,
    ) -> Dict[str, Any]:
        """
        Execute one tool call through the same permission/idempotency path used by agent runtime.

        This is used by deterministic orchestrator paths (for example multi-step action plans).
        """
        normalized_name = str(tool_name or "").strip()
        if not normalized_name:
            return build_tool_error(
                error_code="missing_tool_name",
                error_class="validation",
                retryable=False,
                http_status=400,
                user_message="Tool name is required.",
                details={},
            )

        if normalized_name not in self.tool_registry.list_names():
            return build_tool_error(
                error_code="tool_not_registered",
                error_class="not_found",
                retryable=False,
                http_status=404,
                user_message=(
                    "I couldn't run that action right now because a required capability is unavailable."
                ),
                details={"tool_name": normalized_name},
            )

        tool = self.tool_registry.get(normalized_name)
        call_args = dict(args or {})
        guard_error, guarded_args = self._before_tool_call_guard(
            tool=tool,
            args=call_args,
            execution_context=execution_context,
            tool_call_trace=None,
        )
        if guard_error is not None:
            return guard_error
        call_args = dict(guarded_args or {})

        if enforce_approval:
            role = str(execution_context.get("role") or "").strip().lower() or None
            approval = self.permission_service.needs_approval(
                role=role,
                tool=tool,
                tool_args=call_args,
            )
            if approval.required:
                return build_tool_error(
                    error_code="approval_required",
                    error_class="policy",
                    retryable=False,
                    http_status=409,
                    user_message="Approval is required before running this action.",
                    details={
                        "tool_name": normalized_name,
                        "approval_reason": approval.reason,
                    },
                )

        try:
            result = await self._invoke_tool_with_permission(
                tool=tool,
                args=call_args,
                execution_context=execution_context,
            )
            if isinstance(result, dict):
                return result
            return build_tool_error(
                error_code="invalid_tool_result",
                error_class="internal",
                retryable=False,
                http_status=500,
                user_message=f"Tool `{normalized_name}` returned an invalid result.",
                details={"tool_name": normalized_name},
            )
        except Exception as exc:
            print(f"Cortex direct tool call failed: tool={normalized_name} error={exc}")
            return build_tool_error(
                error_code="tool_execution_exception",
                error_class="internal",
                retryable=True,
                http_status=500,
                user_message=f"I couldn't complete `{normalized_name}` right now.",
                details={"tool_name": normalized_name, "reason": str(exc)},
            )

    async def prepare_tool_args_for_execution(
        self,
        *,
        tool_name: str,
        args: Dict[str, Any],
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Prepare tool args via canonicalization + quality policy without executing tool.

        Used by orchestrator proposal refinement so all paths share one quality engine.
        """
        normalized_name = str(tool_name or "").strip()
        if not normalized_name:
            return {
                "_tool_error": build_tool_error(
                    error_code="missing_tool_name",
                    error_class="validation",
                    retryable=False,
                    http_status=400,
                    user_message="Tool name is required.",
                    details={},
                )
            }
        if normalized_name not in self.tool_registry.list_names():
            return {
                "_tool_error": build_tool_error(
                    error_code="tool_not_registered",
                    error_class="not_found",
                    retryable=False,
                    http_status=404,
                    user_message=(
                        "I couldn't run that action right now because a required capability is unavailable."
                    ),
                    details={"tool_name": normalized_name},
                )
            }
        tool = self.tool_registry.get(normalized_name)
        return await self._prepare_tool_args_with_quality(
            tool=tool,
            args=dict(args or {}),
            execution_context=execution_context,
        )

    def _tool_names_from_descriptors(self, descriptors: List[Dict[str, Any]]) -> List[str]:
        names: List[str] = []
        for descriptor in descriptors:
            if not isinstance(descriptor, dict):
                continue
            name = str(descriptor.get("name") or "").strip()
            if not name or name in names:
                continue
            names.append(name)
        return names

    def _build_runtime_tools(
        self,
        *,
        tool_descriptors: List[Dict[str, Any]],
        execution_context: Dict[str, Any],
        tool_call_trace: Optional[Dict[str, Any]] = None,
    ) -> List[Any]:
        if not FunctionTool and not function_tool:
            return []

        runtime_tools: List[Any] = []
        for tool_name in self._tool_names_from_descriptors(tool_descriptors):
            if tool_name not in self.tool_registry.list_names():
                continue
            runtime_tools.append(
                self._make_function_tool(
                    tool=self.tool_registry.get(tool_name),
                    execution_context=execution_context,
                    tool_call_trace=tool_call_trace,
                )
            )
        return runtime_tools

    def _make_function_tool(
        self,
        *,
        tool: CortexTool,
        execution_context: Dict[str, Any],
        tool_call_trace: Optional[Dict[str, Any]] = None,
    ) -> Any:
        normalized_spec = tool.normalized_spec()

        async def _run_tool_call(tool_args: Dict[str, Any]) -> Dict[str, Any]:
            tool_started_at = perf_counter()
            tool_args = tool_args or {}
            if isinstance(tool_args, dict) and "args" in tool_args:
                maybe_args = tool_args.get("args")
                if isinstance(maybe_args, dict):
                    tool_args = maybe_args
            guard_error, guarded_args = self._before_tool_call_guard(
                tool=tool,
                args=tool_args,
                execution_context=execution_context,
                tool_call_trace=tool_call_trace,
            )
            if guard_error is not None:
                if isinstance(tool_call_trace, dict):
                    tool_call_trace["error_count"] = int(tool_call_trace.get("error_count") or 0) + 1
                    errors = tool_call_trace.setdefault("errors", [])
                    if isinstance(errors, list):
                        errors.append(
                            {
                                "tool_name": normalized_spec.name,
                                "error_code": str(guard_error.get("error_code") or "").strip(),
                                "error_class": str(guard_error.get("error_class") or "").strip(),
                                "user_message": str(guard_error.get("user_message") or "").strip(),
                                "details": dict(guard_error.get("details") or {}),
                                "arguments": dict(guarded_args or {}),
                            }
                        )
                    elapsed_ms = int((perf_counter() - tool_started_at) * 1000)
                    tool_call_trace["tool_ms_total"] = int(
                        tool_call_trace.get("tool_ms_total") or 0
                    ) + elapsed_ms
                    by_name = tool_call_trace.setdefault("tool_ms_by_name", {})
                    if isinstance(by_name, dict):
                        by_name[normalized_spec.name] = int(
                            by_name.get(normalized_spec.name) or 0
                        ) + elapsed_ms
                return guard_error
            if isinstance(tool_call_trace, dict):
                tool_call_trace["count"] = int(tool_call_trace.get("count") or 0) + 1
                names = tool_call_trace.setdefault("names", [])
                if isinstance(names, list):
                    names.append(normalized_spec.name)
            result = await self._invoke_tool_with_permission(
                tool=tool,
                args=guarded_args,
                execution_context=execution_context,
            )
            if isinstance(result, dict):
                if bool(result.get("ok")):
                    if isinstance(tool_call_trace, dict):
                        tool_call_trace["success_count"] = int(
                            tool_call_trace.get("success_count") or 0
                        ) + 1
                        if bool(result.get("requires_clarification")):
                            clarification_text = str(result.get("response_text") or "").strip()
                            if clarification_text:
                                tool_call_trace["clarification_response_text"] = clarification_text
                            clarification_type = str(result.get("clarification_type") or "").strip()
                            if clarification_type:
                                tool_call_trace["clarification_type"] = clarification_type
                            tool_call_trace["clarification_tool_name"] = normalized_spec.name
                        committed_actions = tool_call_trace.setdefault("committed_actions", [])
                        committed_summary = self._extract_committed_action_summary(result)
                        if (
                            committed_summary
                            and isinstance(committed_actions, list)
                            and committed_summary not in committed_actions
                        ):
                            committed_actions.append(committed_summary)
                        target_refs = tool_call_trace.setdefault("target_refs", [])
                        if isinstance(target_refs, list):
                            for token in self._extract_target_refs_from_tool_result(result):
                                if token not in target_refs:
                                    target_refs.append(token)
                        runtime_refs = execution_context.setdefault("runtime_target_refs", [])
                        if isinstance(runtime_refs, list):
                            for token in self._extract_target_refs_from_tool_result(result):
                                if token not in runtime_refs:
                                    runtime_refs.append(token)
                        read_summary = self._extract_work_item_read_summary(
                            tool_name=normalized_spec.name,
                            result=result,
                        )
                        if read_summary:
                            tool_call_trace["read_tool_name"] = str(
                                read_summary.get("tool_name") or normalized_spec.name
                            ).strip()
                            tool_call_trace["read_items_count"] = int(
                                read_summary.get("count") or 0
                            )
                            tool_call_trace["read_items"] = list(
                                read_summary.get("items") or []
                            )
                else:
                    if isinstance(tool_call_trace, dict):
                        tool_call_trace["error_count"] = int(
                            tool_call_trace.get("error_count") or 0
                        ) + 1
                        errors = tool_call_trace.setdefault("errors", [])
                        if isinstance(errors, list):
                            errors.append(
                                {
                                    "tool_name": normalized_spec.name,
                                    "error_code": str(result.get("error_code") or "").strip(),
                                    "error_class": str(result.get("error_class") or "").strip(),
                                    "user_message": str(result.get("user_message") or "").strip(),
                                    "details": dict(result.get("details") or {}),
                                    "arguments": dict(guarded_args or {}),
                                }
                            )
            if isinstance(tool_call_trace, dict):
                elapsed_ms = int((perf_counter() - tool_started_at) * 1000)
                tool_call_trace["tool_ms_total"] = int(
                    tool_call_trace.get("tool_ms_total") or 0
                ) + elapsed_ms
                by_name = tool_call_trace.setdefault("tool_ms_by_name", {})
                if isinstance(by_name, dict):
                    by_name[normalized_spec.name] = int(
                        by_name.get(normalized_spec.name) or 0
                    ) + elapsed_ms
            return result

        async def _needs_approval(
            _ctx: "RunContextWrapper[Dict[str, Any]]",
            tool_parameters: Dict[str, Any],
            _call_id: str,
        ) -> bool:
            role = str(execution_context.get("role") or "").strip().lower() or None
            tool_args = tool_parameters
            if isinstance(tool_parameters, dict) and "args" in tool_parameters:
                maybe_args = tool_parameters.get("args")
                if isinstance(maybe_args, dict):
                    tool_args = maybe_args

            approval = self.permission_service.needs_approval(
                role=role,
                tool=tool,
                tool_args=tool_args,
            )
            return bool(approval.required)

        if FunctionTool is not None:
            params_schema = self._tool_params_json_schema(tool)

            async def _invoke_with_schema(
                _tool_context: "ToolContext[Dict[str, Any]]",
                tool_arguments: str,
            ) -> Dict[str, Any]:
                try:
                    parsed = json.loads(tool_arguments or "{}")
                except Exception as exc:
                    return build_tool_error(
                        error_code="invalid_tool_arguments_json",
                        error_class="validation",
                        retryable=False,
                        http_status=400,
                        user_message="Tool arguments could not be parsed.",
                        details={
                            "tool_name": normalized_spec.name,
                            "reason": str(exc),
                        },
                    )
                if not isinstance(parsed, dict):
                    return build_tool_error(
                        error_code="invalid_tool_arguments_type",
                        error_class="validation",
                        retryable=False,
                        http_status=400,
                        user_message="Tool arguments must be a JSON object.",
                        details={
                            "tool_name": normalized_spec.name,
                        },
                    )
                return await _run_tool_call(parsed)

            return FunctionTool(
                name=normalized_spec.name,
                description=normalized_spec.description,
                params_json_schema=params_schema,
                on_invoke_tool=_invoke_with_schema,
                strict_json_schema=False,
                needs_approval=_needs_approval,
            )

        # Backward-compatible fallback path if FunctionTool class is unavailable.
        async def _invoke(
            _ctx: "RunContextWrapper[Dict[str, Any]]",
            args: Dict[str, Any],
        ) -> Dict[str, Any]:
            return await _run_tool_call(args or {})

        return function_tool(
            _invoke,
            name_override=normalized_spec.name,
            description_override=normalized_spec.description,
            strict_mode=False,
            needs_approval=_needs_approval,
        )

    def _before_tool_call_guard(
        self,
        *,
        tool: CortexTool,
        args: Dict[str, Any],
        execution_context: Dict[str, Any],
        tool_call_trace: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        """Apply shared deterministic guard checks before any tool invocation."""
        effective_args = self._normalize_args_for_tool(tool=tool, args=dict(args or {}))
        if not bool(getattr(settings, "cortex_before_tool_call_wrapper_enabled", True)):
            return None, effective_args

        if not bool(getattr(settings, "cortex_write_loop_guard_enabled", True)):
            return None, effective_args

        max_total = max(1, int(getattr(settings, "cortex_max_total_tool_calls_per_turn", 6) or 6))
        max_write = max(1, int(getattr(settings, "cortex_max_write_tool_calls_per_turn", 3) or 3))
        max_runtime_seconds = max(
            1,
            int(getattr(settings, "cortex_max_turn_runtime_seconds", 10) or 10),
        )
        current_total = 0
        if isinstance(tool_call_trace, dict):
            current_total = int(tool_call_trace.get("count") or 0)
        if current_total >= max_total:
            return (
                build_tool_error(
                    error_code="tool_call_budget_exceeded",
                    error_class="policy",
                    retryable=False,
                    http_status=400,
                    user_message=(
                        "This request needs more steps than I can safely run in one turn. "
                        "Please confirm the next action and I can continue."
                    ),
                    details={"tool_name": tool.normalized_spec().name, "max_total_tool_calls": max_total},
                ),
                effective_args,
            )

        started_at = float(execution_context.get("_executor_turn_started_at_perf") or 0.0)
        if started_at > 0.0:
            elapsed = perf_counter() - started_at
            if elapsed >= float(max_runtime_seconds):
                return (
                    build_tool_error(
                        error_code="turn_runtime_budget_exceeded",
                        error_class="policy",
                        retryable=False,
                        http_status=400,
                        user_message=(
                            "I reached my safe runtime limit for this turn. "
                            "Please ask me to continue."
                        ),
                        details={
                            "tool_name": tool.normalized_spec().name,
                            "max_turn_runtime_seconds": max_runtime_seconds,
                        },
                    ),
                    effective_args,
                )

        is_write = tool.normalized_spec().idempotency != IdempotencyClass.READ_ONLY.value
        if is_write:
            current_write = int(execution_context.get("_tool_call_guard_write_count") or 0)
            if current_write >= max_write:
                return (
                    build_tool_error(
                        error_code="write_tool_call_budget_exceeded",
                        error_class="policy",
                        retryable=False,
                        http_status=400,
                        user_message=(
                            "I reached the write-action limit for one turn. "
                            "Please confirm the next action and I can continue."
                        ),
                        details={
                            "tool_name": tool.normalized_spec().name,
                            "max_write_tool_calls": max_write,
                        },
                    ),
                    effective_args,
                )

        args_hash = self._stable_args_hash(effective_args)
        dedupe_key = f"{tool.normalized_spec().name}:{args_hash}"
        raw_hashes = execution_context.get("_tool_call_guard_hashes")
        hashes: List[str] = raw_hashes if isinstance(raw_hashes, list) else []
        if dedupe_key in hashes:
            return (
                build_tool_error(
                    error_code="duplicate_tool_call_blocked",
                    error_class="policy",
                    retryable=False,
                    http_status=409,
                    user_message=(
                        "I detected a duplicate action in this turn and stopped it "
                        "to avoid repeated changes."
                    ),
                    details={"tool_name": tool.normalized_spec().name},
                ),
                effective_args,
            )
        hashes.append(dedupe_key)
        execution_context["_tool_call_guard_hashes"] = hashes
        if is_write:
            execution_context["_tool_call_guard_write_count"] = int(
                execution_context.get("_tool_call_guard_write_count") or 0
            ) + 1
        return None, effective_args

    def _extract_committed_action_summary(self, result: Dict[str, Any]) -> str:
        """Extract one committed-action summary from a successful tool result."""
        if not isinstance(result, dict):
            return ""

        committed = result.get("committed_action")
        if isinstance(committed, dict):
            for key in ("summary", "message", "description", "title"):
                value = str(committed.get(key) or "").strip()
                if value:
                    return value

        for key in ("committed_summary", "summary"):
            value = str(result.get(key) or "").strip()
            if value:
                return value
        return ""

    def _extract_target_refs_from_tool_result(self, result: Dict[str, Any]) -> List[str]:
        refs: List[str] = []
        if not isinstance(result, dict):
            return refs

        def _add(value: Any) -> None:
            token = str(value or "").strip()
            if not token:
                return
            normalized = token.upper() if "-" in token else token
            if normalized not in refs:
                refs.append(normalized)

        def _extract_from_mapping(value: Any) -> None:
            if not isinstance(value, dict):
                return
            for key in ("external_id", "entity_id", "task_id", "issue_id", "id", "entity_key"):
                _add(value.get(key))
            for key, raw in value.items():
                name = str(key or "").strip().lower()
                if not name or name == "operation_id":
                    continue
                if name.endswith("_id"):
                    _add(raw)

        _extract_from_mapping(result)
        for nested_key in ("task", "entity", "result"):
            nested = result.get(nested_key)
            if isinstance(nested, dict):
                _extract_from_mapping(nested)
        items = result.get("items")
        if isinstance(items, list):
            for item in items:
                _extract_from_mapping(item)
        return refs

    def _extract_work_item_read_summary(
        self,
        *,
        tool_name: str,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(result, dict):
            return {}
        normalized_tool = str(tool_name or "").strip().lower()
        if not self._is_work_item_read_tool_name(normalized_tool):
            return {}
        if not bool(result.get("ok")):
            return {}

        if normalized_tool.endswith("_search_work_items"):
            raw_items = result.get("items")
            if not isinstance(raw_items, list):
                return {}
            compact_items = self._compact_work_item_rows(raw_items)
            return {
                "tool_name": normalized_tool,
                "count": int(result.get("count") or len(raw_items)),
                "items": compact_items,
            }

        if normalized_tool.endswith("_get_work_item_details"):
            task = result.get("task")
            if not isinstance(task, dict):
                return {}
            compact_items = self._compact_work_item_rows([task])
            return {
                "tool_name": normalized_tool,
                "count": 1,
                "items": compact_items,
            }
        return {}

    @staticmethod
    def _is_work_item_read_tool_name(tool_name: str) -> bool:
        name = str(tool_name or "").strip().lower()
        return name.endswith("_search_work_items") or name.endswith("_get_work_item_details")

    def _compact_work_item_rows(self, rows: List[Any]) -> List[Dict[str, str]]:
        compact: List[Dict[str, str]] = []
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            description_value = str(raw.get("description") or "").strip()
            if len(description_value) > 600:
                description_value = description_value[:597].rstrip() + "..."
            link_value = (
                str(raw.get("link") or "").strip()
                or str(raw.get("url") or "").strip()
                or str(raw.get("issue_url") or "").strip()
                or str(raw.get("html_url") or "").strip()
            )
            compact.append(
                {
                    "external_id": str(raw.get("external_id") or raw.get("id") or "").strip(),
                    "title": str(raw.get("title") or "").strip()[:240],
                    "provider_type": str(raw.get("provider_type") or raw.get("issue_type") or "").strip(),
                    "status": str(raw.get("status") or "").strip(),
                    "status_raw": str(raw.get("status_raw") or "").strip(),
                    "status_display": str(raw.get("status_display") or raw.get("status_raw") or "").strip(),
                    "assignee_name": str(raw.get("assignee_name") or "").strip(),
                    "due_date": str(raw.get("due_date") or "").strip(),
                    "description": description_value,
                    "link": link_value,
                    "created_by": (
                        str(raw.get("created_by") or "").strip()
                        or str(raw.get("reporter_name") or "").strip()
                        or str(raw.get("reporter") or "").strip()
                    ),
                    "created_at": str(raw.get("created_at") or "").strip(),
                    "updated_at": str(raw.get("updated_at") or "").strip(),
                }
            )
            if len(compact) >= 20:
                break
        return compact

    def _tool_params_json_schema(self, tool: CortexTool) -> Dict[str, Any]:
        """
        Build tool parameter schema from tool input_model when available.

        This preserves explicit field contracts (ids, enums, required fields) instead of
        exposing a generic opaque `args` object.
        """
        input_model = getattr(tool, "input_model", None)
        if input_model is not None and hasattr(input_model, "model_json_schema"):
            try:
                schema = input_model.model_json_schema()
                if isinstance(schema, dict):
                    schema.setdefault("type", "object")
                    properties = schema.get("properties")
                    if isinstance(properties, dict):
                        schema["properties"] = {
                            key: value
                            for key, value in properties.items()
                            if key not in self.SYSTEM_MANAGED_TOOL_FIELDS
                        }
                    required_fields = schema.get("required")
                    if isinstance(required_fields, list):
                        schema["required"] = [
                            field
                            for field in required_fields
                            if str(field) not in self.SYSTEM_MANAGED_TOOL_FIELDS
                        ]
                    return schema
            except Exception as exc:
                print(
                    "Cortex tool schema generation failed: "
                    f"tool={tool.normalized_spec().name} reason={exc}"
                )
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": True,
        }

    async def _invoke_tool_with_permission(
        self,
        *,
        tool: CortexTool,
        args: Dict[str, Any],
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        effective_args = self._apply_task_read_lifecycle_defaults(
            tool=tool,
            args=args,
            execution_context=execution_context,
        )
        role = str(execution_context.get("role") or "").strip().lower() or None
        connected_integrations = [
            str(name).strip().lower()
            for name in (execution_context.get("connected_integrations") or [])
            if str(name).strip()
        ]

        decision = self.permission_service.can_execute_tool(
            role=role,
            tool=tool,
            connected_integrations=connected_integrations,
        )
        if not decision.allowed:
            return build_permission_denied_error(
                tool_name=tool.normalized_spec().name,
                reason=decision.reason,
            )

        prepared = await self._prepare_tool_args_with_quality(
            tool=tool,
            args=effective_args,
            execution_context=execution_context,
        )
        if isinstance(prepared, dict) and prepared.get("_tool_error"):
            return prepared.get("_tool_error")
        effective_args = dict(prepared or {})
        operation_profile = self._operation_profile_for_tool(tool)
        grounding_error = self._enforce_write_target_grounding(
            tool=tool,
            args=effective_args,
            execution_context=execution_context,
            operation_profile=operation_profile,
        )
        if grounding_error is not None:
            return grounding_error

        if tool.normalized_spec().idempotency == IdempotencyClass.NON_IDEMPOTENT.value:
            return await self._invoke_non_idempotent_tool(
                tool=tool,
                args=effective_args,
                execution_context=execution_context,
                args_prepared=True,
            )

        return await tool.execute_with_context(effective_args, execution_context)

    def _enforce_write_target_grounding(
        self,
        *,
        tool: CortexTool,
        args: Dict[str, Any],
        execution_context: Dict[str, Any],
        operation_profile: OperationProfile,
    ) -> Optional[Dict[str, Any]]:
        if operation_profile != OperationProfile.NON_IDEMPOTENT_WRITE:
            return None
        if not isinstance(args, dict):
            return None

        tool_name = str(tool.normalized_spec().name or "").strip()
        field_classes = field_classes_for_tool(tool_name)
        if not isinstance(field_classes, dict) or not field_classes:
            return None

        identifier_fields = [
            str(field).strip()
            for field, cls in field_classes.items()
            if str(cls).strip().lower() == "identifier_like" and str(field).strip()
        ]
        if not identifier_fields:
            return None

        target_values: List[str] = []
        for field in identifier_fields:
            token = str(args.get(field) or "").strip()
            if not token:
                continue
            normalized = token.upper() if "-" in token else token
            if normalized not in target_values:
                target_values.append(normalized)
        if not target_values:
            return None

        message = str(
            execution_context.get("latest_user_message")
            or execution_context.get("user_message")
            or ""
        ).strip()
        message_lower = message.lower()
        snapshot_refs = [
            str(item).strip()
            for item in (execution_context.get("session_snapshot_target_refs") or [])
            if str(item).strip()
        ]
        runtime_refs = [
            str(item).strip()
            for item in (execution_context.get("runtime_target_refs") or [])
            if str(item).strip()
        ]
        snapshot_lookup = {item.lower() for item in snapshot_refs}
        runtime_lookup = {item.lower() for item in runtime_refs}

        ungrounded: List[str] = []
        for token in target_values:
            token_lower = token.lower()
            in_message = token_lower in message_lower if message_lower else False
            in_snapshot = token_lower in snapshot_lookup
            in_runtime = token_lower in runtime_lookup
            if in_message or in_snapshot or in_runtime:
                continue
            ungrounded.append(token)

        if not ungrounded:
            return None

        return build_tool_error(
            error_code="target_scope_clarification_required",
            error_class="validation",
            retryable=False,
            http_status=400,
            user_message=(
                "I need clarification on which items to update before I continue. "
                "Please confirm the exact targets or ask me to fetch the current list first."
            ),
            details={
                "tool_name": tool_name,
                "ungrounded_targets": ungrounded[:10],
                "grounding_sources": ["current_user_message", "session_snapshot", "fresh_read"],
            },
        )

    def _apply_task_read_lifecycle_defaults(
        self,
        *,
        tool: CortexTool,
        args: Dict[str, Any],
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        normalized_spec = tool.normalized_spec()
        effective_args = self._apply_task_read_intent_defaults(
            tool=tool,
            args=args,
            execution_context=execution_context,
        )
        if not normalized_spec.task_read_scope:
            return effective_args

        lifecycle_arg = str(normalized_spec.lifecycle_status_arg or "").strip()
        if not lifecycle_arg:
            return effective_args

        existing_value = effective_args.get(lifecycle_arg)
        if str(existing_value or "").strip():
            return effective_args

        user_message = str(
            execution_context.get("latest_user_message")
            or execution_context.get("user_message")
            or ""
        ).strip()
        visibility = parse_task_lifecycle_visibility(user_message)
        lifecycle_value = (
            normalized_spec.lifecycle_closed_value
            if visibility == TaskLifecycleVisibility.CLOSED_ONLY
            else normalized_spec.lifecycle_non_closed_value
        )
        if not str(lifecycle_value or "").strip():
            return effective_args

        effective_args[lifecycle_arg] = str(lifecycle_value).strip()
        print(
            "Cortex task-read lifecycle default applied: "
            f"tool={normalized_spec.name} arg={lifecycle_arg} value={effective_args[lifecycle_arg]}"
        )
        return effective_args

    def _apply_task_read_intent_defaults(
        self,
        *,
        tool: CortexTool,
        args: Dict[str, Any],
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        normalized_spec = tool.normalized_spec()
        effective_args = dict(args or {})
        if not normalized_spec.task_read_scope:
            return effective_args

        user_message = str(
            execution_context.get("latest_user_message")
            or execution_context.get("user_message")
            or ""
        ).strip()
        if (
            is_task_read_self_scope_request(user_message)
            and not str(effective_args.get("assignee_mode") or "").strip()
        ):
            effective_args["assignee_mode"] = "me"
            print(
                "Cortex task-read self-scope default applied: "
                f"tool={normalized_spec.name} key=assignee_mode"
            )

        intent = parse_task_read_intent(user_message)
        if intent is None:
            return effective_args

        applied: Dict[str, Any] = {}
        allowed_fields = set(self._tool_allowed_arg_fields(tool))
        intent_assignee_mode = str((intent.args or {}).get("assignee_mode") or "").strip().lower()
        if "assignee_mode" in allowed_fields and intent_assignee_mode == "me":
            # Deterministic safety: first-person task-read requests must not inherit
            # stale assignee scope from prior turns/model context.
            effective_args["assignee_mode"] = "me"
            if "assignee_value" in allowed_fields:
                effective_args.pop("assignee_value", None)
            if "assignee" in allowed_fields:
                effective_args.pop("assignee", None)
            applied["assignee_mode"] = "me"

        for key, value in (intent.args or {}).items():
            if key not in allowed_fields:
                continue
            # Canonical read-filter keys are intent-authoritative and should not
            # inherit stale model/runtime values from prior turns.
            if key in {
                "assignee_mode",
                "assignee_value",
                "include_unassigned",
                "due_date_mode",
                "provider_types",
            }:
                effective_args[key] = value
                applied[key] = value
                continue
            # Status policy (tool-agnostic):
            # - If user explicitly mentions status, apply that status.
            # - If user does not mention status explicitly, enforce non-closed default.
            # This must override stale/model-provided status narrowing.
            if key == "status" and "status" in allowed_fields:
                effective_args["status"] = value
                applied["status"] = value
                continue
            existing = effective_args.get(key)
            if str(existing or "").strip():
                continue
            effective_args[key] = value
            applied[key] = value

        # For read tools using include_closed instead of status, keep the same policy:
        # include closed only when user explicitly asks for closed/done/completed.
        if "include_closed" in allowed_fields:
            status_value = str((intent.args or {}).get("status") or "").strip().lower()
            include_closed = bool(intent.status_explicit and status_value == "done")
            effective_args["include_closed"] = include_closed
            applied["include_closed"] = include_closed

        if applied:
            print(
                "Cortex task-read intent defaults applied: "
                f"tool={normalized_spec.name} keys={sorted(applied.keys())}"
            )
        return effective_args

    async def _invoke_non_idempotent_tool(
        self,
        *,
        tool: CortexTool,
        args: Dict[str, Any],
        execution_context: Dict[str, Any],
        args_prepared: bool = False,
    ) -> Dict[str, Any]:
        tool_name = tool.normalized_spec().name
        guard_ok, guard_reason = self.operation_replay_cache.guard_status()
        degraded_allowed = bool(getattr(settings, "cortex_allow_redis_degraded", False))
        if not guard_ok and not degraded_allowed:
            return build_tool_error(
                error_code="redis_guard_unavailable",
                error_class="integration",
                retryable=True,
                http_status=503,
                user_message=(
                    "I can't run write actions right now because safety guards are unavailable. "
                    "Please try again shortly."
                ),
                details={
                    "tool_name": tool_name,
                    "guard_reason": guard_reason,
                    "degraded_allowed": degraded_allowed,
                },
            )
        if not guard_ok and degraded_allowed:
            print(
                f"Cortex non-idempotent Redis guard unavailable; "
                f"continuing due to degraded-mode opt-in: tool={tool_name} reason={guard_reason}"
            )

        scope_hash = self._execution_scope_hash(execution_context)
        effective_args = dict(args or {})
        if not args_prepared:
            prep = await self._prepare_tool_args_with_quality(
                tool=tool,
                args=args,
                execution_context=execution_context,
            )
            if isinstance(prep, dict) and prep.get("_tool_error"):
                return prep.get("_tool_error")
            effective_args = dict(prep or {})

        # Server-managed operation_id for write actions: do not trust model/user input.
        effective_args.pop("operation_id", None)
        operation_id = self._derive_auto_operation_id(
            tool_name=tool_name,
            args=effective_args,
            execution_context=execution_context,
            scope_hash=scope_hash,
        )
        effective_args["operation_id"] = operation_id

        args_hash = self._stable_args_hash(effective_args)
        cached = self.operation_replay_cache.get(operation_id=operation_id)
        if cached:
            cached_tool = str(cached.get("tool_name") or "")
            cached_args_hash = str(cached.get("args_hash") or "")
            cached_scope_hash = str(cached.get("scope_hash") or "")
            if (
                cached_tool != tool_name
                or cached_args_hash != args_hash
                or cached_scope_hash != scope_hash
            ):
                return build_tool_error(
                    error_code="operation_id_conflict",
                    error_class="conflict",
                    retryable=False,
                    http_status=409,
                    user_message="operation_id has already been used for a different action.",
                    details={
                        "tool_name": tool_name,
                        "operation_id": operation_id,
                    },
                )

            cached_result = cached.get("result")
            if isinstance(cached_result, dict):
                return cached_result

        result = await tool.execute_with_context(effective_args, execution_context)
        if isinstance(result, dict) and bool(result.get("ok")):
            self.operation_replay_cache.set(
                operation_id=operation_id,
                value={
                    "tool_name": tool_name,
                    "args_hash": args_hash,
                    "scope_hash": scope_hash,
                    "result": result,
                },
                ttl_seconds=settings.cortex_op_ttl_seconds,
            )

        return result

    def _tool_allowed_arg_fields(self, tool: CortexTool) -> List[str]:
        input_model = getattr(tool, "input_model", None)
        if input_model is not None and hasattr(input_model, "model_fields"):
            return [str(name) for name in getattr(input_model, "model_fields", {}).keys()]
        return []

    def _normalize_args_for_tool(self, *, tool: CortexTool, args: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = tool.normalized_spec().name
        allowed_fields = set(self._tool_allowed_arg_fields(tool))
        normalized = canonicalize_tool_args(
            tool_name=tool_name,
            args=args if isinstance(args, dict) else {},
            allowed_fields=allowed_fields or None,
        )
        sanitized: Dict[str, Any] = {}
        for key, value in normalized.items():
            if value is None:
                continue
            if isinstance(value, str):
                compact = value.strip()
                if not compact:
                    continue
                sanitized[key] = compact
                continue
            sanitized[key] = value
        return sanitized

    def _apply_write_optional_field_safety(
        self,
        *,
        tool: CortexTool,
        args: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Drop known optional fields when value shape is invalid/ambiguous."""
        tool_name = str(tool.normalized_spec().name or "").strip()
        effective = dict(args or {})
        if tool_name not in {"jira_create_work_item", "jira_update_status"}:
            return effective

        if "sprint_directive" in effective:
            raw = effective.get("sprint_directive")
            if isinstance(raw, str):
                normalized = raw.strip().lower().replace("-", "_").replace(" ", "_")
                if normalized in {"auto", "active_sprint", "backlog"}:
                    effective["sprint_directive"] = normalized
                else:
                    effective.pop("sprint_directive", None)
                    print(
                        "Cortex optional write arg dropped: "
                        f"tool={tool_name} field=sprint_directive raw={raw}"
                    )
            elif raw is None:
                effective.pop("sprint_directive", None)
            else:
                # Unknown/non-string payload type from model runtime.
                effective.pop("sprint_directive", None)
                print(
                    "Cortex optional write arg dropped: "
                    f"tool={tool_name} field=sprint_directive type={type(raw).__name__}"
                )

        return effective

    def _apply_write_self_assignee_defaults(
        self,
        *,
        tool: CortexTool,
        args: Dict[str, Any],
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Deterministic write-time self assignment:
        when user explicitly asks self assignment, bind assignee fields to `me`.
        """
        effective = dict(args or {})
        if not effective:
            return effective

        allowed_fields = set(self._tool_allowed_arg_fields(tool))
        has_assignee_field = bool(
            {"assignee", "assignee_value", "assignee_mode"}.intersection(allowed_fields)
        )
        if not has_assignee_field:
            return effective

        user_message = str(
            execution_context.get("latest_user_message")
            or execution_context.get("user_message")
            or ""
        ).strip().lower()
        if not user_message:
            return effective

        self_markers = [
            r"\bassign(?:ed)?\s+(?:it|this|task)?\s*(?:to|for)\s+me\b",
            r"\bassign\s+me\b",
            r"\bfor\s+me\b",
            r"\bto\s+me\b",
            r"\bmyself\b",
        ]
        if not any(re.search(pattern, user_message) for pattern in self_markers):
            return effective

        tool_name = str(tool.normalized_spec().name or "").strip()
        if "assignee" in allowed_fields and "assignee" in effective:
            prior = str(effective.get("assignee") or "").strip()
            effective["assignee"] = "me"
            if prior.lower() != "me":
                print(
                    "Cortex write self-assignee override applied: "
                    f"tool={tool_name} field=assignee prior={prior or '<empty>'}"
                )

        if "assignee_mode" in allowed_fields:
            prior_mode = str(effective.get("assignee_mode") or "").strip().lower()
            if prior_mode != "me":
                effective["assignee_mode"] = "me"
                if "assignee_value" in allowed_fields:
                    effective.pop("assignee_value", None)
                print(
                    "Cortex write self-assignee override applied: "
                    f"tool={tool_name} field=assignee_mode prior={prior_mode or '<empty>'}"
                )

        if "assignee_value" in allowed_fields and "assignee_mode" not in allowed_fields:
            prior_value = str(effective.get("assignee_value") or "").strip()
            effective["assignee_value"] = "me"
            if prior_value.lower() != "me":
                print(
                    "Cortex write self-assignee override applied: "
                    f"tool={tool_name} field=assignee_value prior={prior_value or '<empty>'}"
                )

        return effective

    def _apply_read_personal_scope_defaults(
        self,
        *,
        tool: CortexTool,
        args: Dict[str, Any],
        execution_context: Dict[str, Any],
        operation_profile: OperationProfile,
    ) -> Dict[str, Any]:
        """
        Global read-scope default:
        For assignee-capable read tools, map first-person ownership asks to assignee=me
        when caller did not provide an explicit assignee target.
        """
        if operation_profile != OperationProfile.READ_ONLY:
            return dict(args or {})

        effective = dict(args or {})
        allowed_fields = set(self._tool_allowed_arg_fields(tool))
        supports_mode = "assignee_mode" in allowed_fields
        supports_legacy_assignee = "assignee" in allowed_fields
        supports_assignee_value = "assignee_value" in allowed_fields
        if not (supports_mode or supports_legacy_assignee):
            return effective

        mode = str(effective.get("assignee_mode") or "").strip().lower()
        assignee_value = str(effective.get("assignee_value") or "").strip()
        assignee = str(effective.get("assignee") or "").strip()

        # Respect explicit assignee scope from caller/model.
        if mode and mode not in {"any", "all"}:
            return effective
        if supports_assignee_value and assignee_value:
            return effective
        if supports_legacy_assignee and assignee and assignee.lower() not in {"any", "all", "anyone"}:
            return effective

        message = str(
            execution_context.get("latest_user_message")
            or execution_context.get("user_message")
            or ""
        ).strip().lower()
        if not message:
            return effective

        personal_markers = [
            r"\bmy\s+tasks?\b",
            r"\bassigned\s+to\s+me\b",
            r"\bdo\s+i\s+have\b",
            r"\bi\s+have\b",
            r"\bfor\s+me\b",
            r"\bmine\b",
        ]
        if not any(re.search(pattern, message) for pattern in personal_markers):
            return effective

        tool_name = tool.normalized_spec().name
        if supports_mode:
            effective["assignee_mode"] = "me"
            if supports_assignee_value:
                effective.pop("assignee_value", None)
            print(
                "Cortex read personal scope default applied: "
                f"tool={tool_name} field=assignee_mode value=me"
            )
            return effective

        if supports_legacy_assignee:
            effective["assignee"] = "me"
            print(
                "Cortex read personal scope default applied: "
                f"tool={tool_name} field=assignee value=me"
            )
        return effective

    async def _prepare_tool_args_with_quality(
        self,
        *,
        tool: CortexTool,
        args: Dict[str, Any],
        execution_context: Dict[str, Any],
        forced_profile: Optional[OperationProfile] = None,
    ) -> Dict[str, Any]:
        normalized_args = self._normalize_args_for_tool(tool=tool, args=args)
        # Cadence sessions manage quality via system prompt + session context.
        # Skip all plan-gate checks (proposal, repair, quality engine) — the plan gate
        # has no cadence context and actively blocks valid session writes.
        if str(execution_context.get("source") or "").strip().lower() == "cadence":
            return dict(normalized_args)
        operation_profile = forced_profile or self._operation_profile_for_tool(tool)
        if operation_profile != OperationProfile.READ_ONLY:
            parsed_or_error = await self._apply_write_parse_gate(
                tool=tool,
                args=normalized_args,
                execution_context=execution_context,
            )
            if isinstance(parsed_or_error, dict) and parsed_or_error.get("_tool_error"):
                return parsed_or_error
            normalized_args = dict(parsed_or_error or normalized_args)
            normalized_args = self._apply_write_optional_field_safety(
                tool=tool,
                args=normalized_args,
            )
            normalized_args = self._apply_write_self_assignee_defaults(
                tool=tool,
                args=normalized_args,
                execution_context=execution_context,
            )
            comment_guard_error = self._apply_comment_command_text_guard(
                tool=tool,
                args=normalized_args,
            )
            if comment_guard_error is not None:
                return {"_tool_error": comment_guard_error}
        normalized_args = self._apply_read_personal_scope_defaults(
            tool=tool,
            args=normalized_args,
            execution_context=execution_context,
            operation_profile=operation_profile,
        )
        if str(tool.normalized_spec().name or "").strip() == "jira_create_work_item":
            print(
                "Cortex write args prepared: "
                f"tool=jira_create_work_item args={normalized_args}"
            )
        if not bool(settings.cortex_quality_engine_enabled):
            return dict(normalized_args)

        if (
            operation_profile == OperationProfile.READ_ONLY
            and not bool(settings.cortex_quality_read_enabled)
        ):
            return dict(normalized_args)

        decision = await self.quality_engine.evaluate(
            executor=self,
            tool=tool,
            args=normalized_args,
            execution_context=execution_context,
            operation_profile=operation_profile,
        )
        policy_meta = quality_policy_runtime_metadata()
        execution_context["quality_policy_schema_version"] = str(
            policy_meta.get("quality_policy_schema_version") or ""
        )
        execution_context["quality_policy_loaded"] = bool(
            policy_meta.get("quality_policy_loaded")
        )
        self._log_quality_decision(
            tool_name=tool.normalized_spec().name,
            operation_profile=operation_profile,
            decision=decision,
            shadow=bool(settings.cortex_quality_shadow_mode)
            or (
                operation_profile == OperationProfile.READ_ONLY
                and bool(settings.cortex_quality_read_shadow_mode)
            ),
        )
        if decision.blocking:
            if (
                operation_profile == OperationProfile.READ_ONLY
                and bool(settings.cortex_quality_read_fail_open_default)
            ):
                return dict(decision.normalized_args)
            detail_fields = decision.missing_fields or [f"{tool.normalized_spec().name}.input"]
            labels = render_missing_field_labels(detail_fields, max_items=3)
            clarify_label = ", ".join(labels[:3]) if labels else "the missing details"
            return {
                "_tool_error": build_tool_error(
                    error_code="insufficient_argument_quality",
                    error_class="validation",
                    retryable=False,
                    http_status=400,
                    user_message=(
                        "I need a bit more detail before I can continue. "
                        f"Please give precise input again."
                    ),
                    details={
                        "tool_name": tool.normalized_spec().name,
                        "insufficient_fields": detail_fields,
                        "issue_codes": [issue.code for issue in decision.issues],
                        "operation_profile": operation_profile.value,
                    },
                )
            }
        return dict(decision.normalized_args)

    def _apply_comment_command_text_guard(
        self,
        *,
        tool: CortexTool,
        args: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Fail closed when comment-like fields contain only command-shell phrasing."""
        tool_name = str(tool.normalized_spec().name or "").strip()
        if not tool_name:
            return None
        field_classes = field_classes_for_tool(tool_name)
        if not isinstance(field_classes, dict) or not field_classes:
            return None
        for field_name, field_class in field_classes.items():
            if str(field_class or "").strip().lower() != "comment_like":
                continue
            value = str((args or {}).get(str(field_name)) or "").strip()
            if not value:
                continue
            if not self._looks_like_comment_command_shell(value):
                continue
            return build_tool_error(
                error_code="insufficient_argument_quality",
                error_class="validation",
                retryable=False,
                http_status=400,
                user_message=(
                    "I need a bit more detail before I can continue. "
                    "Please share: comment."
                ),
                details={
                    "tool_name": tool_name,
                    "insufficient_fields": [f"{tool_name}.{field_name}"],
                    "issue_codes": ["command_shell_comment_without_content"],
                    "operation_profile": OperationProfile.NON_IDEMPOTENT_WRITE.value,
                },
            )
        return None

    def _looks_like_comment_command_shell(self, value: str) -> bool:
        text = " ".join(str(value or "").strip().split())
        if not text:
            return False
        normalized = re.sub(r"[,:.\-]+$", "", text, flags=re.IGNORECASE).strip()
        if not normalized:
            return False

        lowered = normalized.lower()
        # Bare command shells without actionable content.
        if lowered in {
            "comment",
            "comments",
            "add comment",
            "add a comment",
            "post comment",
            "post a comment",
            "update comments",
            "edit comments",
            "revise comments",
            "please add comment",
            "please add a comment",
            "please post comment",
            "please post a comment",
            "please update comments",
        }:
            return True

        issue_key = r"[a-z][a-z0-9_]*[-\s]?\d+"
        patterns = [
            # update PRM-59 comments
            rf"^(?:please\s+)?(?:update|edit|revise)\s+{issue_key}\s+comments?\s*$",
            # add/post comment for|to|on PRM-59
            rf"^(?:please\s+)?(?:add|post)\s+(?:a\s+)?comments?\s+(?:for|to|on)\s+(?:task|issue\s+)?{issue_key}\s*$",
            # comment for|to|on PRM-59
            rf"^(?:please\s+)?comments?\s+(?:for|to|on)\s+(?:task|issue\s+)?{issue_key}\s*$",
            # task PRM-59 comment(s)
            rf"^(?:task|issue)\s+{issue_key}\s+comments?\s*$",
            # for/on/to PRM-59
            rf"^(?:for|to|on)\s+(?:task|issue\s+)?{issue_key}\s*$",
            # just PRM-59 or issue PRM-59
            rf"^(?:(?:task|issue)\s+)?{issue_key}\s*$",
        ]
        return any(re.match(pattern, normalized, flags=re.IGNORECASE) for pattern in patterns)

    async def _apply_write_parse_gate(
        self,
        *,
        tool: CortexTool,
        args: Dict[str, Any],
        execution_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        tool_name = str(tool.normalized_spec().name or "").strip()
        parse_outcome = await parse_with_runtime_adapter(
            "cortex.write_args",
            tool_name=tool_name,
            args=dict(args or {}),
            parser_context={
                "path": "cortex_executor_prepare_tool_args",
                "project_id": str(execution_context.get("project_id") or "").strip(),
                "user_id": str(execution_context.get("user_id") or "").strip(),
                "session_key": str(execution_context.get("session_key") or "").strip(),
            },
        )
        gate_decision = enforce_mutation_gate(parse_outcome)
        if gate_decision.allows_write():
            payload = (
                parse_outcome.normalized_payload
                if isinstance(parse_outcome.normalized_payload, dict)
                else {}
            )
            parsed_args = payload.get("args")
            if isinstance(parsed_args, dict):
                return self._normalize_args_for_tool(tool=tool, args=parsed_args)
            return dict(args or {})

        payload = (
            parse_outcome.normalized_payload
            if isinstance(parse_outcome.normalized_payload, dict)
            else {}
        )
        missing_fields = [
            str(token).strip()
            for token in (payload.get("missing_fields") or [])
            if str(token).strip()
        ]
        if not missing_fields and tool_name == "jira_update_status":
            missing_fields = [f"{tool_name}.status"]
        candidate_statuses = [
            str(item).strip()
            for item in (payload.get("candidate_statuses") or [])
            if str(item).strip()
        ]
        requested_status = str(payload.get("status") or "").strip()
        message_context: Dict[str, Any] = {}
        if candidate_statuses:
            message_context["available_statuses"] = candidate_statuses
        if requested_status:
            message_context["requested_status"] = requested_status
        reason_code = str(gate_decision.reason_code or "clarification_required").strip().lower()
        user_message = message_for_reason_code(
            reason_code,
            fallback="I need a quick clarification before I continue.",
            context=message_context or None,
        )
        return {
            "_tool_error": build_tool_error(
                error_code=reason_code,
                error_class=(
                    "validation"
                    if gate_decision.requires_clarification()
                    else "policy"
                ),
                retryable=False,
                http_status=400,
                user_message=user_message,
                details={
                    "tool_name": tool_name,
                    "parse_status": str(parse_outcome.status or "").strip(),
                    "parse_reason_code": reason_code,
                    "insufficient_fields": missing_fields,
                    "candidate_statuses": candidate_statuses,
                    "requested_status": requested_status,
                },
            )
        }

    def _operation_profile_for_tool(self, tool: CortexTool) -> OperationProfile:
        idempotency = tool.normalized_spec().idempotency
        if idempotency == IdempotencyClass.READ_ONLY.value:
            return OperationProfile.READ_ONLY
        if idempotency == IdempotencyClass.NON_IDEMPOTENT.value:
            return OperationProfile.NON_IDEMPOTENT_WRITE
        return OperationProfile.IDEMPOTENT_WRITE

    def _log_quality_decision(
        self,
        *,
        tool_name: str,
        operation_profile: OperationProfile,
        decision: Any,
        shadow: bool,
    ) -> None:
        if not (shadow or decision.blocking):
            return
        issue_codes = [getattr(issue, "code", "") for issue in getattr(decision, "issues", [])]
        policy_meta = quality_policy_runtime_metadata()
        policy_schema = str(policy_meta.get("quality_policy_schema_version") or "").strip() or "builtin"
        policy_loaded = bool(policy_meta.get("quality_policy_loaded"))
        print(
            "Cortex quality decision: "
            f"tool={tool_name} profile={operation_profile.value} "
            f"status={decision.status} blocking={decision.blocking} "
            f"missing={decision.missing_fields} issues={issue_codes} shadow={shadow} "
            f"policy_schema={policy_schema} policy_loaded={policy_loaded}"
        )

    def _derive_auto_operation_id(
        self,
        *,
        tool_name: str,
        args: Dict[str, Any],
        execution_context: Dict[str, Any],
        scope_hash: str,
    ) -> str:
        payload = {
            "tool_name": tool_name,
            "run_id": str(execution_context.get("run_id") or ""),
            "session_key": str(execution_context.get("session_key") or ""),
            "scope_hash": scope_hash,
            "args_hash": self._stable_args_hash(args),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
        return f"auto-{digest}"

    def _serialize_interruptions(self, interruptions: List[Any]) -> List[Dict[str, Any]]:
        serialized: List[Dict[str, Any]] = []
        for item in interruptions:
            tool_name = getattr(item, "name", None) or getattr(item, "tool_name", None)
            call_id = getattr(item, "call_id", None)
            raw_arguments = getattr(item, "arguments", None)
            parsed_arguments: Any = None
            if isinstance(raw_arguments, str):
                try:
                    parsed_arguments = json.loads(raw_arguments)
                except Exception:
                    parsed_arguments = raw_arguments
            else:
                parsed_arguments = raw_arguments

            serialized.append(
                {
                    "tool_name": str(tool_name) if tool_name is not None else "",
                    "call_id": str(call_id) if call_id is not None else "",
                    "arguments": parsed_arguments,
                }
            )
        return serialized

    def _model_candidates(self) -> list[str]:
        primary = to_litellm_model(
            self.runtime_config.primary_provider,
            self.runtime_config.primary_model,
        )
        fallback = to_litellm_model(
            self.runtime_config.fallback_provider,
            self.runtime_config.fallback_model,
        )
        models = [m for m in [primary, fallback] if m]
        deduped: list[str] = []
        for model in models:
            if model not in deduped:
                deduped.append(model)
        return deduped or ["openai/gpt-4o-mini"]

    def _build_model_provider(
        self,
        *,
        execution_context: Dict[str, Any],
    ) -> Any:
        async def _on_health_event(event: LLMHealthEvent) -> None:
            self._emit_gateway_health_event(event=event, execution_context=execution_context)

        return GatewayToolCallingModelProvider(
            runtime_config=self.runtime_config,
            on_health_event=_on_health_event,
            project_id=str(execution_context.get("project_id") or "").strip() or None,
        )

    async def _maybe_emit_shadow_parity(
        self,
        *,
        payload: Dict[str, Any],
        prompt: str,
        user_input: str,
        model_name: str,
        execution_context: Dict[str, Any],
        primary_output: str,
        primary_runtime_meta: Dict[str, Any],
    ) -> None:
        # Legacy gateway-vs-direct parity compare is not applicable after hardwiring provider path.
        _ = payload, prompt, user_input, model_name, execution_context, primary_output, primary_runtime_meta
        return

    def _emit_gateway_health_event(
        self,
        *,
        event: LLMHealthEvent,
        execution_context: Dict[str, Any],
    ) -> None:
        event_type_map = {
            LLMHealthEventType.SUCCESS: LLMTelemetryEventType.SUCCESS,
            LLMHealthEventType.FAILURE: LLMTelemetryEventType.FAILURE,
            LLMHealthEventType.RETRY: LLMTelemetryEventType.RETRY,
            LLMHealthEventType.PROVIDER_SWITCH: LLMTelemetryEventType.PROVIDER_SWITCH,
            LLMHealthEventType.DEGRADATION_CHANGE: LLMTelemetryEventType.DEGRADATION_CHANGE,
            LLMHealthEventType.AUTH_QUARANTINE: LLMTelemetryEventType.AUTH_QUARANTINE,
            LLMHealthEventType.AUTH_RESTORE: LLMTelemetryEventType.DEGRADATION_CHANGE,
        }
        telemetry_type = event_type_map.get(event.event_type, LLMTelemetryEventType.FAILURE)
        severity = (
            LLMTelemetrySeverity.INFO
            if telemetry_type == LLMTelemetryEventType.SUCCESS
            else LLMTelemetrySeverity.WARNING
            if telemetry_type
            in {
                LLMTelemetryEventType.RETRY,
                LLMTelemetryEventType.FALLBACK,
                LLMTelemetryEventType.PROVIDER_SWITCH,
                LLMTelemetryEventType.DEGRADATION_CHANGE,
            }
            else LLMTelemetrySeverity.ERROR
        )
        self.telemetry.emit(
            LLMTelemetryEvent(
                event_type=telemetry_type,
                severity=severity,
                feature="cortex",
                provider=str(event.provider or "").strip() or None,
                model=str(event.model or "").strip() or None,
                project_id=str(execution_context.get("project_id") or "").strip() or None,
                user_id=str(execution_context.get("user_id") or "").strip() or None,
                session_id=str(execution_context.get("session_key") or "").strip() or None,
                message=str(event.reason or "").strip() or None,
                metadata=dict(event.details or {}),
            )
        )

    def _extract_user_input(self, payload: Dict[str, Any]) -> str:
        explicit = str(payload.get("input") or "").strip()
        if explicit:
            return explicit

        for section in payload.get("sections") or []:
            if not isinstance(section, dict):
                continue
            if section.get("id") == "current_user_message":
                text = str(section.get("content") or "").strip()
                if text:
                    return text

        return "Proceed with the request using available project context."

    def _coerce_output_text(self, output: Any) -> str:
        if output is None:
            return ""
        if isinstance(output, str):
            return output.strip()
        if isinstance(output, dict):
            for key in ("output_text", "text", "answer", "content", "message"):
                value = output.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return str(output).strip()

    def _split_model(self, model_name: str) -> tuple[str, str]:
        if "/" not in model_name:
            return self.runtime_config.primary_provider, model_name
        provider, model = model_name.split("/", 1)
        return provider, model

    def _stable_args_hash(self, args: Dict[str, Any]) -> str:
        payload = {key: value for key, value in args.items() if key != "operation_id"}
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _execution_scope_hash(self, execution_context: Dict[str, Any]) -> str:
        payload = {
            "project_id": str(execution_context.get("project_id") or ""),
            "user_id": str(execution_context.get("user_id") or ""),
            "session_key": str(execution_context.get("session_key") or ""),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _emit_success(
        self,
        *,
        provider: str,
        model: str,
        project_id: str,
        user_id: str,
        session_id: str,
        latency_ms: int,
        metadata: Dict[str, Any],
    ) -> None:
        self.telemetry.emit(
            LLMTelemetryEvent(
                event_type=LLMTelemetryEventType.SUCCESS,
                severity=LLMTelemetrySeverity.INFO,
                feature="cortex",
                provider=provider,
                model=model,
                project_id=project_id or None,
                user_id=user_id or None,
                session_id=session_id or None,
                latency_ms=latency_ms,
                metadata=metadata,
            )
        )

    def _emit_failure(
        self,
        *,
        provider: str,
        model: str,
        project_id: str,
        user_id: str,
        session_id: str,
        latency_ms: int,
        message: str,
        metadata: Dict[str, Any],
    ) -> None:
        self.telemetry.emit(
            LLMTelemetryEvent(
                event_type=LLMTelemetryEventType.FAILURE,
                severity=LLMTelemetrySeverity.ERROR,
                feature="cortex",
                provider=provider,
                model=model,
                project_id=project_id or None,
                user_id=user_id or None,
                session_id=session_id or None,
                latency_ms=latency_ms,
                error_class=LLMErrorClass.INTERNAL,
                message=message[:300],
                metadata=metadata,
            )
        )

    def _emit_fallback(
        self,
        *,
        provider: str,
        model: str,
        fallback_provider: str,
        fallback_model: str,
        project_id: str,
        user_id: str,
        session_id: str,
        metadata: Dict[str, Any],
    ) -> None:
        self.telemetry.emit(
            LLMTelemetryEvent(
                event_type=LLMTelemetryEventType.FALLBACK,
                severity=LLMTelemetrySeverity.WARNING,
                feature="cortex",
                provider=provider,
                model=model,
                fallback_provider=fallback_provider,
                fallback_model=fallback_model,
                project_id=project_id or None,
                user_id=user_id or None,
                session_id=session_id or None,
                metadata=metadata,
            )
        )
