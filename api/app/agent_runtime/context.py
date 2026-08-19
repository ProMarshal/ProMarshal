"""AgentContext — structured input passed to SharedAgentRuntime.run()."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class AgentContext:
    """
    Unified context for every SharedAgentRuntime invocation.

    Both Cortex and Cadence populate this before calling SharedAgentRuntime.run().
    The fields that differ per feature are isolated in feature_context.
    """

    user_message: str
    project_id: str
    user_id: str
    session_key: str

    # "cortex" | "cadence"
    source: str

    # Output of DeterministicInterpretationService.interpret():
    #   normalized_text, confidence, slot_coverage, signal_strength,
    #   matches (action_type, tool_name, extracted_slots, missing_required_fields),
    #   missing_slots
    pre_processed: Dict[str, Any] = field(default_factory=dict)

    # Recent turns and session summary from the conversation store
    session_history: Dict[str, Any] = field(default_factory=dict)

    # Project name, connected integrations, member list
    project_context: Dict[str, Any] = field(default_factory=dict)

    # Cortex: {role, member_candidates, connected_integrations}
    # Cadence: {session_state, current_task, task_index, all_tasks}
    feature_context: Dict[str, Any] = field(default_factory=dict)

    # Unique identifier for this runtime invocation (for tracing/telemetry)
    run_id: str = ""
