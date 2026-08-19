"""Deterministic action-plan execution engine."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

from app.agent_runtime.executor import AgentExecutor
from app.cortex.plans.models import (
    ActionPlan,
    ActionPlanExecutionResult,
    ActionStep,
    LAST_TASK_REF,
    PlanExecutionStatus,
    PlanStepStatus,
)
from app.cortex.plans.runtime_gates import dependency_gate, predicate_gate
from app.cortex.tools.runtime_helpers import resolve_self_alias_to_actor_email
from app.cortex.tools.schemas import normalize_external_id

IDENTITY_ARG_KEYS = {
    "assignee",
    "assignee_value",
    "requester",
    "owner",
    "member",
    "actor_email",
}


class ActionPlanExecutor:
    """Execute plan steps via existing Cortex tool execution safety gates."""

    def __init__(self, *, executor: AgentExecutor) -> None:
        self.executor = executor

    async def execute(
        self,
        *,
        plan: ActionPlan,
        execution_context: Dict[str, Any],
        max_steps_per_turn: int,
    ) -> ActionPlanExecutionResult:
        if not plan.steps:
            return ActionPlanExecutionResult(
                ok=False,
                status=PlanExecutionStatus.FAILED,
                completed_action_texts=[],
                pending_action_texts=[],
                error_code="empty_action_plan",
                error_message="No executable actions were found in the plan.",
            )

        runtime_plan = deepcopy(plan)
        completed_action_texts: List[str] = []
        last_known_external_id = self._seed_last_known_external_id(runtime_plan)
        project_context = (
            execution_context.get("project_context")
            if isinstance(execution_context.get("project_context"), dict)
            else {}
        )
        actor_email_hint = str(execution_context.get("actor_email") or "").strip().lower() or None
        slack_user_id = str(execution_context.get("user_id") or "").strip() or None
        actor_identity = (
            execution_context.get("actor_identity")
            if isinstance(execution_context.get("actor_identity"), dict)
            else {}
        )
        actor_jira_account_id = str(actor_identity.get("jira_account_id") or "").strip() or None

        start_index = max(0, min(int(runtime_plan.current_step_index), len(runtime_plan.steps)))
        executed_count = 0
        step_status_by_id, step_result_by_id = self._bootstrap_step_runtime_state(
            plan=runtime_plan,
            start_index=start_index,
        )

        for idx in range(start_index, len(runtime_plan.steps)):
            step = runtime_plan.steps[idx]
            if executed_count >= max_steps_per_turn:
                runtime_plan.current_step_index = idx
                pending = [item.summary for item in runtime_plan.steps[idx:] if item.summary]
                return ActionPlanExecutionResult(
                    ok=True,
                    status=PlanExecutionStatus.PARTIAL,
                    completed_action_texts=completed_action_texts,
                    pending_action_texts=pending,
                    pending_plan=runtime_plan,
                    details={"reason": "step_limit_reached", "step_limit": max_steps_per_turn},
                )

            dep_decision, dep_error_code, dep_message = dependency_gate(
                node_id=step.step_id,
                depends_on=step.depends_on or [],
                node_status_by_id={key: value.value for key, value in step_status_by_id.items()},
                completed_statuses={PlanStepStatus.COMPLETED.value},
                skipped_statuses={PlanStepStatus.SKIPPED.value},
            )
            if dep_decision == "skip":
                step.status = PlanStepStatus.SKIPPED
                step.result = {
                    "ok": True,
                    "skipped": True,
                    "error_code": dep_error_code or "dependency_not_completed",
                    "user_message": dep_message or "Skipped due to dependency state.",
                }
                step_status_by_id[step.step_id] = step.status
                step_result_by_id[step.step_id] = dict(step.result)
                runtime_plan.current_step_index = idx + 1
                continue
            if dep_decision == "fail":
                step.status = PlanStepStatus.FAILED
                step.result = {
                    "ok": False,
                    "error_code": dep_error_code or "invalid_step_dependency",
                    "user_message": dep_message or "Invalid step dependency.",
                }
                step_status_by_id[step.step_id] = step.status
                step_result_by_id[step.step_id] = dict(step.result)
                runtime_plan.current_step_index = idx
                pending = [item.summary for item in runtime_plan.steps[idx + 1 :] if item.summary]
                return ActionPlanExecutionResult(
                    ok=False,
                    status=PlanExecutionStatus.FAILED,
                    completed_action_texts=completed_action_texts,
                    pending_action_texts=pending,
                    error_code=str(step.result.get("error_code") or "invalid_step_dependency"),
                    error_message=str(step.result.get("user_message") or "Invalid step dependency."),
                    pending_plan=runtime_plan if pending else None,
                    details={"failed_step": step.step_id, "tool_name": step.tool_name},
                )

            cond_decision, cond_error_code, cond_message = predicate_gate(
                node_id=step.step_id,
                predicate=step.condition if isinstance(step.condition, dict) else None,
                node_results=step_result_by_id,
                node_status_by_id={key: value.value for key, value in step_status_by_id.items()},
            )
            if cond_decision == "skip":
                step.status = PlanStepStatus.SKIPPED
                step.result = {
                    "ok": True,
                    "skipped": True,
                    "error_code": cond_error_code or "predicate_not_selected",
                    "user_message": cond_message or "Skipped because predicate did not select this step.",
                }
                step_status_by_id[step.step_id] = step.status
                step_result_by_id[step.step_id] = dict(step.result)
                runtime_plan.current_step_index = idx + 1
                continue
            if cond_decision == "fail":
                step.status = PlanStepStatus.FAILED
                step.result = {
                    "ok": False,
                    "error_code": cond_error_code or "invalid_step_condition",
                    "user_message": cond_message or "Invalid step condition.",
                }
                step_status_by_id[step.step_id] = step.status
                step_result_by_id[step.step_id] = dict(step.result)
                runtime_plan.current_step_index = idx
                pending = [item.summary for item in runtime_plan.steps[idx + 1 :] if item.summary]
                return ActionPlanExecutionResult(
                    ok=False,
                    status=PlanExecutionStatus.FAILED,
                    completed_action_texts=completed_action_texts,
                    pending_action_texts=pending,
                    error_code=str(step.result.get("error_code") or "invalid_step_condition"),
                    error_message=str(step.result.get("user_message") or "Invalid step condition."),
                    pending_plan=runtime_plan if pending else None,
                    details={"failed_step": step.step_id, "tool_name": step.tool_name},
                )

            resolved_args, resolve_error = self._resolve_step_args(
                step=step,
                last_known_external_id=last_known_external_id,
                project_context=project_context,
                actor_email_hint=actor_email_hint,
                slack_user_id=slack_user_id,
                actor_jira_account_id=actor_jira_account_id,
            )
            if resolve_error:
                step.status = PlanStepStatus.FAILED
                step.result = {
                    "ok": False,
                    "error_code": "unresolved_step_arguments",
                    "user_message": resolve_error,
                }
                step_status_by_id[step.step_id] = step.status
                step_result_by_id[step.step_id] = dict(step.result)
                runtime_plan.current_step_index = idx
                pending = [item.summary for item in runtime_plan.steps[idx + 1 :] if item.summary]
                return ActionPlanExecutionResult(
                    ok=False,
                    status=PlanExecutionStatus.FAILED,
                    completed_action_texts=completed_action_texts,
                    pending_action_texts=pending,
                    error_code="unresolved_step_arguments",
                    error_message=resolve_error,
                    pending_plan=runtime_plan if pending else None,
                    details={"failed_step": step.step_id, "tool_name": step.tool_name},
                )

            step_execution_context = self._with_step_quality_context(
                execution_context=execution_context,
                step=step,
                resolved_args=resolved_args,
            )
            prepared_args = await self.executor.prepare_tool_args_for_execution(
                tool_name=step.tool_name,
                args=resolved_args,
                execution_context=step_execution_context,
            )
            if isinstance(prepared_args, dict) and prepared_args.get("_tool_error"):
                tool_error = prepared_args.get("_tool_error") if isinstance(prepared_args.get("_tool_error"), dict) else {}
                step.status = PlanStepStatus.FAILED
                step.result = tool_error or {
                    "ok": False,
                    "error_code": "step_quality_failed",
                    "user_message": "Step quality preflight failed.",
                }
                step_status_by_id[step.step_id] = step.status
                step_result_by_id[step.step_id] = dict(step.result)
                runtime_plan.current_step_index = idx
                pending = [item.summary for item in runtime_plan.steps[idx + 1 :] if item.summary]
                return ActionPlanExecutionResult(
                    ok=False,
                    status=PlanExecutionStatus.FAILED,
                    completed_action_texts=completed_action_texts,
                    pending_action_texts=pending,
                    error_code=str(step.result.get("error_code") or "step_quality_failed"),
                    error_message=str(step.result.get("user_message") or "Step quality preflight failed."),
                    pending_plan=runtime_plan if pending else None,
                    details={"failed_step": step.step_id, "tool_name": step.tool_name},
                )

            prepared_payload = prepared_args if isinstance(prepared_args, dict) else {}
            step_args_for_execution = dict(prepared_payload or resolved_args)
            result = await self.executor.execute_tool_call(
                tool_name=step.tool_name,
                args=step_args_for_execution,
                execution_context=step_execution_context,
                enforce_approval=True,
            )
            executed_count += 1
            step.result = result if isinstance(result, dict) else {"ok": False}

            if not isinstance(result, dict):
                step.status = PlanStepStatus.FAILED
                step_status_by_id[step.step_id] = step.status
                step_result_by_id[step.step_id] = dict(step.result)
                runtime_plan.current_step_index = idx
                pending = [item.summary for item in runtime_plan.steps[idx + 1 :] if item.summary]
                return ActionPlanExecutionResult(
                    ok=False,
                    status=PlanExecutionStatus.FAILED,
                    completed_action_texts=completed_action_texts,
                    pending_action_texts=pending,
                    error_code="invalid_tool_result",
                    error_message=f"Tool `{step.tool_name}` returned an invalid result.",
                    pending_plan=runtime_plan if pending else None,
                    details={"failed_step": step.step_id, "tool_name": step.tool_name},
                )

            if bool(result.get("ok")):
                step.status = PlanStepStatus.COMPLETED
                step_status_by_id[step.step_id] = step.status
                step_result_by_id[step.step_id] = dict(result)
                committed_text = self._committed_action_summary(
                    tool_name=step.tool_name,
                    result=result,
                )
                if committed_text:
                    completed_action_texts.append(committed_text)
                last_known_external_id = self._update_last_known_external_id(
                    last_known_external_id=last_known_external_id,
                    step_args=step_args_for_execution,
                    result=result,
                )
                runtime_plan.current_step_index = idx + 1
                continue

            error_code = str(result.get("error_code") or "").strip().lower()
            user_message = str(result.get("user_message") or "").strip()
            if error_code == "approval_required":
                step.status = PlanStepStatus.AWAITING_APPROVAL
                step_status_by_id[step.step_id] = step.status
                step_result_by_id[step.step_id] = dict(result)
                runtime_plan.current_step_index = idx
                pending = [item.summary for item in runtime_plan.steps[idx:] if item.summary]
                return ActionPlanExecutionResult(
                    ok=True,
                    status=PlanExecutionStatus.AWAITING_APPROVAL,
                    completed_action_texts=completed_action_texts,
                    pending_action_texts=pending,
                    pending_plan=runtime_plan,
                    error_code=error_code,
                    error_message=user_message,
                    details={
                        "failed_step": step.step_id,
                        "tool_name": step.tool_name,
                        "approval_required": True,
                    },
                )

            step.status = PlanStepStatus.FAILED
            step_status_by_id[step.step_id] = step.status
            step_result_by_id[step.step_id] = dict(result)
            runtime_plan.current_step_index = idx
            pending = [item.summary for item in runtime_plan.steps[idx + 1 :] if item.summary]
            return ActionPlanExecutionResult(
                ok=False,
                status=PlanExecutionStatus.FAILED,
                completed_action_texts=completed_action_texts,
                pending_action_texts=pending,
                error_code=error_code or "tool_step_failed",
                error_message=user_message or f"Tool `{step.tool_name}` failed.",
                pending_plan=runtime_plan if pending else None,
                details={"failed_step": step.step_id, "tool_name": step.tool_name},
            )

        runtime_plan.current_step_index = len(runtime_plan.steps)
        return ActionPlanExecutionResult(
            ok=True,
            status=PlanExecutionStatus.COMPLETED,
            completed_action_texts=completed_action_texts,
            pending_action_texts=[],
            pending_plan=None,
        )

    def _bootstrap_step_runtime_state(
        self,
        *,
        plan: ActionPlan,
        start_index: int,
    ) -> Tuple[Dict[str, PlanStepStatus], Dict[str, Dict[str, Any]]]:
        statuses: Dict[str, PlanStepStatus] = {}
        results: Dict[str, Dict[str, Any]] = {}
        cutoff = max(0, min(int(start_index), len(plan.steps)))
        for idx in range(cutoff):
            step = plan.steps[idx]
            statuses[step.step_id] = step.status
            if isinstance(step.result, dict):
                results[step.step_id] = dict(step.result)
        return statuses, results

    def _with_step_quality_context(
        self,
        *,
        execution_context: Dict[str, Any],
        step: ActionStep,
        resolved_args: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Attach deterministic step provenance hints for shared quality engine."""
        next_context = dict(execution_context or {})
        existing_tokens = [
            str(item).strip()
            for item in (next_context.get("quality_user_confirmed_fields") or [])
            if str(item).strip()
        ]
        seen = {token.lower() for token in existing_tokens}
        merged = list(existing_tokens)
        tool_name = str(step.tool_name or "").strip()
        for key, value in (resolved_args or {}).items():
            field = str(key or "").strip()
            if not field:
                continue
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            for token in (field, f"{tool_name}.{field}"):
                lowered = token.lower()
                if not lowered or lowered in seen:
                    continue
                seen.add(lowered)
                merged.append(token)
        next_context["quality_user_confirmed_fields"] = merged
        return next_context

    def _resolve_step_args(
        self,
        *,
        step: ActionStep,
        last_known_external_id: Optional[str],
        project_context: Dict[str, Any],
        actor_email_hint: Optional[str],
        slack_user_id: Optional[str],
        actor_jira_account_id: Optional[str],
    ) -> tuple[Dict[str, Any], Optional[str]]:
        resolved = dict(step.args or {})
        for key, value in list(resolved.items()):
            if isinstance(value, str) and value == LAST_TASK_REF:
                if not last_known_external_id:
                    return {}, (
                        f"I couldn't resolve task reference for step `{step.step_id}`. "
                        "Please provide the task ID explicitly."
                    )
                resolved[key] = last_known_external_id
                continue

            if key in IDENTITY_ARG_KEYS and isinstance(value, str):
                actor_bound, used_alias = resolve_self_alias_to_actor_email(
                    value,
                    project=project_context,
                    actor_email=actor_email_hint,
                    slack_user_id=slack_user_id,
                    jira_account_id=actor_jira_account_id,
                )
                if used_alias and not actor_bound:
                    return {}, (
                        f"I couldn't resolve your member identity for `{key}=me` in this project. "
                        "Please provide your Jira-linked email."
                    )
                if actor_bound:
                    resolved[key] = actor_bound

        external_id = str(resolved.get("external_id") or "").strip()
        if external_id:
            try:
                resolved["external_id"] = normalize_external_id(external_id)
            except Exception:
                return {}, (
                    f"The task ID `{external_id}` could not be normalized for step `{step.step_id}`."
                )
        return resolved, None

    def _committed_action_summary(self, *, tool_name: str, result: Dict[str, Any]) -> str:
        committed = result.get("committed_action")
        if isinstance(committed, dict):
            summary = str(committed.get("summary") or "").strip()
            if summary:
                return summary
        task = result.get("task")
        if isinstance(task, dict):
            external_id = str(task.get("external_id") or "").strip()
            if external_id:
                return f"{tool_name} on {external_id}"
        return tool_name

    def _seed_last_known_external_id(self, plan: ActionPlan) -> Optional[str]:
        for idx in range(max(0, int(plan.current_step_index) - 1), -1, -1):
            step = plan.steps[idx]
            result = step.result or {}
            if isinstance(result, dict):
                task = result.get("task")
                if isinstance(task, dict):
                    external_id = str(task.get("external_id") or "").strip()
                    if external_id:
                        return external_id
            explicit = str((step.args or {}).get("external_id") or "").strip()
            if explicit and explicit != LAST_TASK_REF:
                return explicit
        return None

    def _update_last_known_external_id(
        self,
        *,
        last_known_external_id: Optional[str],
        step_args: Dict[str, Any],
        result: Dict[str, Any],
    ) -> Optional[str]:
        task = result.get("task")
        if isinstance(task, dict):
            external_id = str(task.get("external_id") or "").strip()
            if external_id:
                return external_id

        explicit = str(step_args.get("external_id") or "").strip()
        if explicit:
            return explicit
        return last_known_external_id
