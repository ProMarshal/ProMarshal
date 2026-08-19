"""Cortex deterministic action planning package."""

from app.cortex.plans.models import (
    ActionPlan,
    ActionPlanExecutionResult,
    ActionStep,
    PlanDecision,
    PlanExecutionStatus,
    PlanSource,
    PlanStepStatus,
    action_plan_from_dict,
    action_plan_to_dict,
)


def __getattr__(name: str):
    if name == "ActionPlanBuilder":
        from app.cortex.plans.planner import ActionPlanBuilder

        return ActionPlanBuilder
    if name == "ActionPlanExecutor":
        from app.cortex.plans.executor import ActionPlanExecutor

        return ActionPlanExecutor
    raise AttributeError(name)

__all__ = [
    "ActionPlan",
    "ActionStep",
    "PlanSource",
    "PlanStepStatus",
    "PlanExecutionStatus",
    "PlanDecision",
    "ActionPlanExecutionResult",
    "ActionPlanBuilder",
    "ActionPlanExecutor",
    "action_plan_to_dict",
    "action_plan_from_dict",
]
