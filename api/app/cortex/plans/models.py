"""Action-plan contracts for deterministic multi-step execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


LAST_TASK_REF = "__LAST_TASK__"


class PlanSource(str, Enum):
    """Origin for plan generation."""

    DETERMINISTIC = "deterministic"
    LLM = "llm"


class PlanStepStatus(str, Enum):
    """Lifecycle state for plan steps."""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    AWAITING_APPROVAL = "awaiting_approval"


class PlanExecutionStatus(str, Enum):
    """Overall execution state for an action plan."""

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    AWAITING_APPROVAL = "awaiting_approval"


@dataclass
class ActionStep:
    """Single executable action in a plan."""

    step_id: str
    action_type: str
    tool_name: str
    args: Dict[str, Any]
    summary: str
    capability: Optional[str] = None
    provider: Optional[str] = None
    depends_on: List[str] = field(default_factory=list)
    condition: Optional[Dict[str, Any]] = None
    status: PlanStepStatus = PlanStepStatus.PENDING
    result: Optional[Dict[str, Any]] = None


@dataclass
class ActionPlan:
    """Ordered plan of actions for one user request."""

    plan_id: str
    source: PlanSource
    confidence: float
    steps: List[ActionStep]
    rationale: str
    current_step_index: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlanDecision:
    """Planner output for deterministic plan selection."""

    plan: Optional[ActionPlan]
    confidence: float
    reason: str
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionPlanExecutionResult:
    """Execution summary for a plan run."""

    ok: bool
    status: PlanExecutionStatus
    completed_action_texts: List[str]
    pending_action_texts: List[str]
    pending_plan: Optional[ActionPlan] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


def action_step_to_dict(step: ActionStep) -> Dict[str, Any]:
    """Serialize ActionStep for persistence."""
    return {
        "step_id": step.step_id,
        "action_type": step.action_type,
        "tool_name": step.tool_name,
        "args": dict(step.args or {}),
        "summary": step.summary,
        "capability": str(step.capability or "").strip() or None,
        "provider": str(step.provider or "").strip() or None,
        "depends_on": list(step.depends_on or []),
        "condition": dict(step.condition or {}) if isinstance(step.condition, dict) else None,
        "status": step.status.value,
        "result": step.result,
    }


def action_step_from_dict(payload: Dict[str, Any]) -> ActionStep:
    """Deserialize ActionStep from persisted payload."""
    raw_status = str(payload.get("status") or PlanStepStatus.PENDING.value).strip().lower()
    try:
        status = PlanStepStatus(raw_status)
    except Exception:
        status = PlanStepStatus.PENDING
    return ActionStep(
        step_id=str(payload.get("step_id") or "").strip(),
        action_type=str(payload.get("action_type") or "").strip(),
        tool_name=str(payload.get("tool_name") or "").strip(),
        args=dict(payload.get("args") or {}),
        summary=str(payload.get("summary") or "").strip(),
        capability=str(payload.get("capability") or "").strip() or None,
        provider=str(payload.get("provider") or "").strip() or None,
        depends_on=[str(item) for item in (payload.get("depends_on") or []) if str(item).strip()],
        condition=dict(payload.get("condition") or {}) if isinstance(payload.get("condition"), dict) else None,
        status=status,
        result=payload.get("result") if isinstance(payload.get("result"), dict) else None,
    )


def action_plan_to_dict(plan: ActionPlan) -> Dict[str, Any]:
    """Serialize ActionPlan for session persistence."""
    return {
        "plan_id": plan.plan_id,
        "source": plan.source.value,
        "confidence": float(plan.confidence),
        "steps": [action_step_to_dict(step) for step in plan.steps],
        "rationale": plan.rationale,
        "current_step_index": int(plan.current_step_index),
        "metadata": dict(plan.metadata or {}),
    }


def action_plan_from_dict(payload: Dict[str, Any]) -> Optional[ActionPlan]:
    """Deserialize ActionPlan from session payload."""
    if not isinstance(payload, dict):
        return None
    plan_id = str(payload.get("plan_id") or "").strip()
    if not plan_id:
        return None

    raw_source = str(payload.get("source") or PlanSource.DETERMINISTIC.value).strip().lower()
    try:
        source = PlanSource(raw_source)
    except Exception:
        source = PlanSource.DETERMINISTIC

    steps_raw = payload.get("steps") or []
    steps: List[ActionStep] = []
    for item in steps_raw:
        if not isinstance(item, dict):
            continue
        step = action_step_from_dict(item)
        if step.step_id and (step.tool_name or step.capability):
            steps.append(step)

    if not steps:
        return None

    return ActionPlan(
        plan_id=plan_id,
        source=source,
        confidence=float(payload.get("confidence") or 0.0),
        steps=steps,
        rationale=str(payload.get("rationale") or "").strip(),
        current_step_index=max(0, int(payload.get("current_step_index") or 0)),
        metadata=dict(payload.get("metadata") or {}),
    )
