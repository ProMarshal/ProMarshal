"""Built-in Jira capability bundles and deterministic pattern specs."""

from __future__ import annotations

import re
from typing import List

from app.cortex.tools.contracts import ToolArgContract, get_tool_arg_contract
from app.domain.capabilities.bundle_models import CapabilityBundle
from app.domain.capabilities.deterministic.models import ActionPatternSpec


CREATE_PATTERN = re.compile(
    r"\b(?:create|add|new)\s+(?:a\s+|an\s+|new\s+)?(?P<provider_type>task|work\s*item|story|epic|bug|sub[\s-]?task)\b(?:\s*[:\-]\s*|\s+)"
    r"(?P<title>.+?)"
    r"(?:\s+(?:under|parent(?:ed)?\s+to)\s+(?P<parent_external_id>[A-Z][A-Z0-9]+-\d+))?"
    r"(?=(?:\band\b|\bthen\b|\bassign\b|\breassign\b|\bmove\b|\bset\b|\bchange\b|\bupdate\b|\badd\s+comment\b|\bcomment\b|$))",
    re.IGNORECASE,
)
ASSIGN_PATTERN = re.compile(
    r"\b(?:assign|reassign)\b(?:\s+(?P<target>[a-z][a-z0-9_]*(?:[\s_-]?\d+)|it|this(?:\s+(?:task|work\s*item))?))?"
    r"\s+(?:to|for)\s+(?P<assignee>.+?)"
    r"(?=(?:\band\b|\bthen\b|\bmove\b|\bset\b|\bchange\b|\bupdate\b|\badd\s+comment\b|\bcomment\b|$))",
    re.IGNORECASE,
)
STATUS_PATTERN = re.compile(
    r"\b(?:move|set|change|update|make)\b(?:\s+(?P<target>[a-z][a-z0-9_]*(?:[\s_-]?\d+)|it|this(?:\s+(?:task|work\s*item))?))?"
    r"\s+(?:(?:to|as)\s+)?(?P<status>to do|todo|in[\s_-]?progress|done|completed|closed)\b",
    re.IGNORECASE,
)
COMMENT_PATTERN = re.compile(
    r"(?:"
    r"\b(?:add|post)\s+comment\b(?:\s+(?:to|on)\s+(?P<target_add>[a-z][a-z0-9_]*(?:[\s_-]?\d+)|it|this(?:\s+task)?))?"
    r"(?:\s*[:\-]\s*|\s+)(?P<comment_add>.+?)(?=(?:\band\b|\bthen\b|$))"
    r"|"
    r"\b(?:update|edit|revise)\b\s+(?P<target_update>[a-z][a-z0-9_]*(?:[\s_-]?\d+)|it|this(?:\s+task)?)\s+comments?\b"
    r"(?:\s*[:\-]\s*|\s*,\s*|\s+)(?P<comment_update>.+?)(?=(?:\bthen\b|$))"
    r"|"
    r"\b(?:task|issue|work\s*item)\s+(?P<target_followup>[a-z][a-z0-9_]*(?:[\s_-]?\d+)|it|this(?:\s+(?:task|work\s*item))?)"
    r"(?:\s*[\.\-:]\s*|\s+)comments?\b(?:\s*[:\-]\s*|\s+)(?P<comment_followup>.+?)(?=(?:\bthen\b|$))"
    r")",
    re.IGNORECASE,
)


def get_jira_deterministic_pattern_specs() -> List[ActionPatternSpec]:
    """Return deterministic Jira action pattern specs used by planner."""
    return [
        ActionPatternSpec(
            action_type="create_work_item",
            capability_id="workitem_create",
            tool_name="jira_create_work_item",
            regex=CREATE_PATTERN,
            field_extractors={
                "provider_type": "provider_type",
                "title": "title",
                "parent_external_id": "parent_external_id",
            },
            required_fields=["title"],
        ),
        ActionPatternSpec(
            action_type="assign_work_item",
            capability_id="workitem_assign",
            tool_name="jira_assign_work_item",
            regex=ASSIGN_PATTERN,
            field_extractors={"target": "target", "assignee": "assignee"},
            required_fields=["assignee"],
        ),
        ActionPatternSpec(
            action_type="update_status",
            capability_id="workitem_update_status",
            tool_name="jira_update_status",
            regex=STATUS_PATTERN,
            field_extractors={"target": "target", "status": "status"},
            required_fields=["status"],
        ),
        ActionPatternSpec(
            action_type="add_comment",
            capability_id="workitem_add_comment",
            tool_name="jira_add_comment",
            regex=COMMENT_PATTERN,
            field_extractors={
                "target_add": "target",
                "target_update": "target",
                "target_followup": "target",
                "comment_add": "comment",
                "comment_update": "comment",
                "comment_followup": "comment",
            },
            required_fields=["comment"],
        ),
    ]


def get_jira_launch_bundle() -> CapabilityBundle:
    """Return built-in Jira launch bundle."""
    from app.cadence.action_adapters.providers import get_jira_cadence_adapters
    from app.cortex.tools.providers.jira_tools import get_launch_jira_tools

    tools = get_launch_jira_tools()
    tool_factories = [lambda cls=tool.__class__: cls() for tool in tools]
    arg_contracts: dict[str, ToolArgContract] = {}
    for tool in tools:
        name = str(tool.normalized_spec().name or "").strip()
        if not name:
            continue
        contract = get_tool_arg_contract(name)
        if _contract_has_data(contract):
            arg_contracts[name] = contract
    return CapabilityBundle(
        bundle_id="builtin.jira.launch",
        capability_id="launch_toolset",
        provider="jira",
        cortex_tool_factories=tool_factories,
        arg_contracts=arg_contracts,
        planner_patterns=get_jira_deterministic_pattern_specs(),
        cadence_adapters=get_jira_cadence_adapters(),
    )


def _contract_has_data(contract: ToolArgContract) -> bool:
    return bool(
        contract.aliases
        or contract.field_quality
        or contract.field_classes
        or contract.policy_overrides
        or contract.read_source_capabilities
    )
