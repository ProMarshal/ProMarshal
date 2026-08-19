"""Feature migration guardrails for shared LLM platform adoption."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import FrozenSet


class FeatureName(str, Enum):
    """Feature identifiers participating in staged LLM migration."""

    CORTEX = "cortex"
    PLANNER = "planner"
    REMINDERS = "reminders"


class OrchestrationSemantics(str, Enum):
    """Feature-specific orchestration modes that must remain independent."""

    CORTEX_AGENT_LOOP = "cortex_agent_loop"
    PLANNER_GUIDED_WORKFLOW = "planner_guided_workflow"
    REMINDERS_STATE_MACHINE = "reminders_state_machine"


class SharedLLMPrimitive(str, Enum):
    """Cross-feature reusable primitives that do not alter orchestration flow."""

    GATEWAY = "gateway"
    TELEMETRY = "telemetry"
    ERROR_ENVELOPE = "error_envelope"
    HEALTH_CALLBACKS = "health_callbacks"
    RETRY_POLICY = "retry_policy"
    PROVIDER_ROUTING = "provider_routing"


@dataclass(frozen=True)
class FeatureMigrationPolicy:
    """Guardrail policy for one feature's migration boundary."""

    feature: FeatureName
    semantics: OrchestrationSemantics
    allowed_primitives: FrozenSet[SharedLLMPrimitive] = field(default_factory=frozenset)


DEFAULT_FEATURE_MIGRATION_POLICIES: dict[FeatureName, FeatureMigrationPolicy] = {
    FeatureName.CORTEX: FeatureMigrationPolicy(
        feature=FeatureName.CORTEX,
        semantics=OrchestrationSemantics.CORTEX_AGENT_LOOP,
        allowed_primitives=frozenset(
            {
                SharedLLMPrimitive.GATEWAY,
                SharedLLMPrimitive.TELEMETRY,
                SharedLLMPrimitive.ERROR_ENVELOPE,
                SharedLLMPrimitive.HEALTH_CALLBACKS,
                SharedLLMPrimitive.RETRY_POLICY,
                SharedLLMPrimitive.PROVIDER_ROUTING,
            }
        ),
    ),
    FeatureName.PLANNER: FeatureMigrationPolicy(
        feature=FeatureName.PLANNER,
        semantics=OrchestrationSemantics.PLANNER_GUIDED_WORKFLOW,
        allowed_primitives=frozenset(
            {
                SharedLLMPrimitive.GATEWAY,
                SharedLLMPrimitive.TELEMETRY,
                SharedLLMPrimitive.ERROR_ENVELOPE,
                SharedLLMPrimitive.RETRY_POLICY,
                SharedLLMPrimitive.PROVIDER_ROUTING,
            }
        ),
    ),
    FeatureName.REMINDERS: FeatureMigrationPolicy(
        feature=FeatureName.REMINDERS,
        semantics=OrchestrationSemantics.REMINDERS_STATE_MACHINE,
        allowed_primitives=frozenset(
            {
                SharedLLMPrimitive.GATEWAY,
                SharedLLMPrimitive.TELEMETRY,
                SharedLLMPrimitive.ERROR_ENVELOPE,
                SharedLLMPrimitive.RETRY_POLICY,
                SharedLLMPrimitive.PROVIDER_ROUTING,
            }
        ),
    ),
}


def validate_migration_plan(
    *,
    feature: FeatureName,
    semantics: OrchestrationSemantics,
    requested_primitives: FrozenSet[SharedLLMPrimitive],
) -> list[str]:
    """
    Validate migration intent against staged guardrails.

    Returns a list of policy violations. Empty list means valid.
    """
    violations: list[str] = []
    policy = DEFAULT_FEATURE_MIGRATION_POLICIES[feature]

    if semantics != policy.semantics:
        violations.append(
            f"{feature.value}: orchestration semantics mismatch "
            f"(expected={policy.semantics.value}, got={semantics.value})"
        )

    disallowed = requested_primitives - policy.allowed_primitives
    if disallowed:
        names = ", ".join(sorted(item.value for item in disallowed))
        violations.append(
            f"{feature.value}: requested primitives outside allowed set ({names})"
        )

    return violations
