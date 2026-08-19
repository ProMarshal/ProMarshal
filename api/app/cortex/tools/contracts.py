"""Shared tool argument contracts and canonicalization helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Set


@dataclass(frozen=True)
class ToolArgContract:
    """Canonical argument aliases for one runtime tool."""

    aliases: Dict[str, str] = field(default_factory=dict)
    field_quality: Dict[str, Dict[str, object]] = field(default_factory=dict)
    field_classes: Dict[str, str] = field(default_factory=dict)
    policy_overrides: Dict[str, Dict[str, object]] = field(default_factory=dict)
    read_source_capabilities: Dict[str, object] = field(default_factory=dict)


_TOOL_ARG_CONTRACTS: Dict[str, ToolArgContract] = {
    "jira_create_work_item": ToolArgContract(
        aliases={
            "task_title": "title",
            "task_description": "description",
            "task_assignee": "assignee",
            "issue_title": "title",
            "issue_description": "description",
            "issue_assignee": "assignee",
            "issue_type": "provider_type",
            "type": "provider_type",
            "parent_id": "parent_external_id",
            "parent_key": "parent_external_id",
            "parent": "parent_external_id",
        },
        field_quality={
            "title": {
                "min_chars": 4,
                "semantic_required": True,
                "semantic_hint": "A concise, concrete task title that states the deliverable.",
            }
        },
        field_classes={
            "title": "title_like",
            "description": "description_like",
            "assignee": "assignee_like",
            "provider_type": "status_like",
            "work_item_type": "status_like",
            "parent_external_id": "identifier_like",
            "sprint_directive": "status_like",
        },
        policy_overrides={
            # Optional enum-like control; validate deterministically, never block
            # via semantic vagueness checks.
            "sprint_directive": {
                "min_chars": 1,
                "semantic_when": "never",
                "assessor_fail_mode": "allow_if_deterministic_pass",
            },
        },
    ),
    "jira_assign_work_item": ToolArgContract(
        aliases={
            "task_id": "external_id",
            "issue_id": "external_id",
            "issue_key": "external_id",
            "task_assignee": "assignee",
            "assignee_email": "assignee",
        },
        field_classes={
            "external_id": "identifier_like",
            "assignee": "assignee_like",
        },
    ),
    "jira_update_status": ToolArgContract(
        aliases={
            "task_id": "external_id",
            "issue_id": "external_id",
            "issue_key": "external_id",
            "new_status": "status",
        },
        field_classes={
            "external_id": "identifier_like",
            "status": "status_like",
            "sprint_directive": "status_like",
        },
        policy_overrides={
            # Jira workflows can expose custom status labels (for example QA TEST),
            # so preflight quality checks must not hard-block non-canonical values.
            "status": {
                "min_chars": 1,
                "semantic_when": "never",
                "assessor_fail_mode": "allow_if_deterministic_pass",
            },
            "sprint_directive": {
                "min_chars": 1,
                "semantic_when": "never",
                "assessor_fail_mode": "allow_if_deterministic_pass",
            },
        },
    ),
    "jira_add_comment": ToolArgContract(
        aliases={
            "task_id": "external_id",
            "issue_id": "external_id",
            "issue_key": "external_id",
            "task_comment": "comment",
            "note": "comment",
        },
        field_classes={
            "external_id": "identifier_like",
            "comment": "comment_like",
        },
    ),
    "jira_update_description": ToolArgContract(
        aliases={
            "task_id": "external_id",
            "issue_id": "external_id",
            "issue_key": "external_id",
            "task_description": "description",
            "issue_description": "description",
            "details": "description",
        },
        field_classes={
            "external_id": "identifier_like",
            "description": "description_like",
        },
    ),
    "jira_update_due_date": ToolArgContract(
        aliases={
            "task_id": "external_id",
            "issue_id": "external_id",
            "issue_key": "external_id",
            "due": "due_date",
            "due_on": "due_date",
            "deadline": "due_date",
        },
        field_classes={
            "external_id": "identifier_like",
            "due_date": "status_like",
        },
    ),
    "jira_clear_due_date": ToolArgContract(
        aliases={
            "task_id": "external_id",
            "issue_id": "external_id",
            "issue_key": "external_id",
        },
        field_classes={
            "external_id": "identifier_like",
        },
    ),
    "jira_update_start_date": ToolArgContract(
        aliases={
            "task_id": "external_id",
            "issue_id": "external_id",
            "issue_key": "external_id",
            "start": "start_date",
            "start_on": "start_date",
        },
        field_classes={
            "external_id": "identifier_like",
            "start_date": "status_like",
        },
    ),
    "jira_clear_start_date": ToolArgContract(
        aliases={
            "task_id": "external_id",
            "issue_id": "external_id",
            "issue_key": "external_id",
        },
        field_classes={
            "external_id": "identifier_like",
        },
    ),
    "jira_get_work_item_details": ToolArgContract(
        aliases={
            "task_id": "external_id",
            "issue_id": "external_id",
            "issue_key": "external_id",
        },
        field_classes={
            "external_id": "identifier_like",
            "freshness_reason": "comment_like",
        },
        read_source_capabilities={
            "supports_brain": True,
            "supports_live": True,
            "default_mode": "brain_first",
        },
    ),
    "jira_search_work_items": ToolArgContract(
        aliases={
            "type": "provider_types",
            "types": "provider_types",
            "issue_type": "provider_types",
            "issue_types": "provider_types",
            "provider_type": "provider_types",
        },
        field_classes={
            "assignee_mode": "assignee_like",
            "assignee_value": "assignee_like",
            "status": "status_like",
            "provider_types": "status_like",
            "due_date_mode": "status_like",
            "priority": "status_like",
            "keyword": "text_like",
            "freshness_reason": "comment_like",
        },
        read_source_capabilities={
            "supports_brain": True,
            "supports_live": True,
            "default_mode": "brain_first",
        },
    ),
    "jira_analyze_work_items": ToolArgContract(
        field_classes={
            "assignee_mode": "assignee_like",
            "assignee_value": "assignee_like",
            "due_date_mode": "status_like",
        },
        read_source_capabilities={
            "supports_brain": True,
            "supports_live": False,
            "default_mode": "brain_only",
        },
    ),
    "brain_search_entities": ToolArgContract(
        field_classes={
            "entity_type": "identifier_like",
            "status": "status_like",
            "assignee": "assignee_like",
            "keyword": "text_like",
        },
        read_source_capabilities={
            "supports_brain": True,
            "supports_live": False,
            "default_mode": "brain_only",
        },
    ),
    "brain_get_entity": ToolArgContract(
        field_classes={
            "external_id": "identifier_like",
            "entity_id": "identifier_like",
        },
        read_source_capabilities={
            "supports_brain": True,
            "supports_live": False,
            "default_mode": "brain_only",
        },
    ),
    "brain_query_relationships": ToolArgContract(
        field_classes={
            "entity_key": "identifier_like",
            "relationship_type": "identifier_like",
        },
        read_source_capabilities={
            "supports_brain": True,
            "supports_live": False,
            "default_mode": "brain_only",
        },
    ),
    "action_item_create": ToolArgContract(
        aliases={
            "assignee": "owner_user_id",
            "owner": "owner_user_id",
            "context": "related_to",
            "topic": "related_to",
            "reference": "related_to",
        },
        field_classes={
            "title": "title_like",
            "owner_user_id": "assignee_like",
            "related_to": "text_like",
            "related_type": "status_like",
            "related_id": "identifier_like",
            "due_type": "status_like",
        },
    ),
    "action_item_list": ToolArgContract(
        field_classes={
            "status": "status_like",
            "owner_user_id": "assignee_like",
            "due_type": "status_like",
            "source": "status_like",
        },
        read_source_capabilities={
            "supports_brain": True,
            "supports_live": False,
            "default_mode": "brain_only",
        },
    ),
    "action_item_update": ToolArgContract(
        aliases={
            "action_item_id": "action_item_ref",
            "display_key": "action_item_ref",
            "assignee": "owner_user_id",
            "owner": "owner_user_id",
            "context": "related_to",
            "topic": "related_to",
        },
        field_classes={
            "action_item_ref": "identifier_like",
            "title": "title_like",
            "owner_user_id": "assignee_like",
            "related_to": "text_like",
            "related_type": "status_like",
            "related_id": "identifier_like",
            "status": "status_like",
            "due_type": "status_like",
        },
    ),
    "action_item_close": ToolArgContract(
        aliases={
            "action_item_id": "action_item_ref",
            "display_key": "action_item_ref",
        },
        field_classes={
            "action_item_ref": "identifier_like",
            "status": "status_like",
        },
    ),
}


def get_tool_arg_contract(tool_name: str) -> ToolArgContract:
    """Return contract for tool, or empty contract."""
    key = str(tool_name or "").strip()
    return _TOOL_ARG_CONTRACTS.get(key, ToolArgContract())


def canonical_aliases_for_tool(tool_name: str) -> Dict[str, str]:
    """Return alias->canonical mapping for tool."""
    return dict(get_tool_arg_contract(tool_name).aliases)


def field_quality_policies_for_tool(tool_name: str) -> Dict[str, Dict[str, object]]:
    """Return field-quality policies for tool."""
    return dict(get_tool_arg_contract(tool_name).field_quality)


def field_classes_for_tool(tool_name: str) -> Dict[str, str]:
    """Return field-class mapping for tool."""
    return dict(get_tool_arg_contract(tool_name).field_classes)


def policy_overrides_for_tool(tool_name: str) -> Dict[str, Dict[str, object]]:
    """Return per-field policy overrides for tool."""
    return dict(get_tool_arg_contract(tool_name).policy_overrides)


def read_source_capabilities_for_tool(tool_name: str) -> Dict[str, object]:
    """Return read-source capabilities for tool."""
    return dict(get_tool_arg_contract(tool_name).read_source_capabilities)


def canonicalize_tool_args(
    *,
    tool_name: str,
    args: Dict[str, object],
    allowed_fields: Optional[Set[str]] = None,
) -> Dict[str, object]:
    """
    Canonicalize argument keys using tool aliases.

    - Keys are normalized case-insensitively.
    - Unknown keys are dropped when allowed_fields is provided.
    - First canonical value wins (later duplicates are ignored).
    """
    if not isinstance(args, dict):
        return {}

    aliases = canonical_aliases_for_tool(tool_name)
    alias_lookup = {str(k).strip().lower(): str(v).strip() for k, v in aliases.items() if str(k).strip()}
    canonicalized: Dict[str, object] = {}
    for raw_key, value in args.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        lower_key = key.lower()
        canonical_key = alias_lookup.get(lower_key, key)
        if allowed_fields is not None and canonical_key not in allowed_fields:
            continue
        if canonical_key in canonicalized:
            continue
        canonicalized[canonical_key] = value
    return canonicalized
