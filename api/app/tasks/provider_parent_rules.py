"""Shared parent-rule resolution/filtering for work-item parent clarification."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.core.database import get_brain_collection


_PROVIDER_METADATA_ENTITY_TYPE = "work_item_provider_metadata"
_JIRA_METADATA_KIND_ALLOWED_CREATE_TYPES = "jira_allowed_create_types"


def normalize_text(value: Any) -> str:
    return str(value or "").strip()


def canonical_issue_type_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_text(value).lower())


def unique_provider_types(values: Any) -> List[str]:
    deduped: List[str] = []
    seen: set[str] = set()
    for value in values or []:
        label = normalize_text(value)
        token = canonical_issue_type_token(label)
        if not label or not token or token in seen:
            continue
        seen.add(token)
        deduped.append(label)
    return deduped


def _jira_fallback_parent_rule(
    *,
    child_token: str,
    allowed_create_types: List[str],
) -> Dict[str, Any]:
    normalized_allowed = unique_provider_types(allowed_create_types)
    allowed_by_token = {
        canonical_issue_type_token(item): item
        for item in normalized_allowed
        if canonical_issue_type_token(item)
    }

    if child_token == "epic":
        return {
            "parent_allowed": False,
            "parent_required": False,
            "allowed_parent_provider_types": [],
            "source": "jira_fallback_epic_no_parent",
        }

    if child_token == "subtask":
        ordered = ["task", "story", "bug"]
        candidates = [allowed_by_token[token] for token in ordered if token in allowed_by_token]
        if not candidates:
            candidates = [
                label
                for token, label in allowed_by_token.items()
                if token not in {"subtask", "epic"}
            ]
        if not candidates:
            candidates = ["Task", "Story", "Bug"]
        return {
            "parent_allowed": True,
            "parent_required": True,
            "allowed_parent_provider_types": unique_provider_types(candidates),
            "source": "jira_fallback_subtask",
        }

    if child_token in {"task", "story", "bug"}:
        epic_parent = [allowed_by_token["epic"]] if "epic" in allowed_by_token else []
        return {
            "parent_allowed": bool(epic_parent),
            "parent_required": False,
            "allowed_parent_provider_types": epic_parent,
            "source": "jira_fallback_standard_optional_parent",
        }

    return {
        "parent_allowed": False,
        "parent_required": False,
        "allowed_parent_provider_types": [],
        "source": "jira_fallback_default_no_parent",
    }


def normalize_parent_rules_for_allowed_types(
    *,
    parent_rules: Dict[str, Any],
    allowed_create_types: List[str],
) -> Dict[str, Dict[str, Any]]:
    allowed_tokens = {
        canonical_issue_type_token(item)
        for item in unique_provider_types(allowed_create_types)
        if canonical_issue_type_token(item)
    }
    normalized_rules: Dict[str, Dict[str, Any]] = {}
    for child_key, raw_rule in (parent_rules or {}).items():
        if not isinstance(raw_rule, dict):
            continue
        child_token = canonical_issue_type_token(child_key or raw_rule.get("provider_type"))
        if not child_token:
            continue
        provider_type = normalize_text(raw_rule.get("provider_type")) or normalize_text(child_key)
        allowed_parents = unique_provider_types(raw_rule.get("allowed_parent_provider_types") or [])
        if allowed_tokens:
            allowed_parents = [
                item for item in allowed_parents if canonical_issue_type_token(item) in allowed_tokens
            ]
        normalized_rules[child_token] = {
            "provider_type": provider_type,
            "parent_allowed": bool(raw_rule.get("parent_allowed") or allowed_parents),
            "parent_required": bool(raw_rule.get("parent_required")),
            "allowed_parent_provider_types": allowed_parents,
            "source": normalize_text(raw_rule.get("source")) or "metadata",
        }
    return normalized_rules


def convert_legacy_parent_rules_to_type_rules(
    *,
    parent_rules: Dict[str, Any],
    allowed_create_types: List[str],
) -> Dict[str, Dict[str, Any]]:
    normalized_legacy = normalize_parent_rules_for_allowed_types(
        parent_rules=parent_rules,
        allowed_create_types=allowed_create_types,
    )
    converted: Dict[str, Dict[str, Any]] = {}
    for child_token, legacy_rule in normalized_legacy.items():
        allowed_parent_provider_types = unique_provider_types(
            legacy_rule.get("allowed_parent_provider_types") or []
        )
        parent_required = bool(legacy_rule.get("parent_required"))
        parent_allowed = bool(
            legacy_rule.get("parent_allowed") or parent_required or allowed_parent_provider_types
        )
        converted[child_token] = {
            "provider_type": normalize_text(legacy_rule.get("provider_type")) or child_token,
            "parent": {
                "allowed": parent_allowed,
                "required": parent_required,
                "allowed_parent_provider_types": allowed_parent_provider_types,
                "source": normalize_text(legacy_rule.get("source")) or "metadata",
            },
        }
    return converted


def _normalize_type_rules_for_allowed_types(
    *,
    type_rules: Dict[str, Any],
    allowed_create_types: List[str],
) -> Dict[str, Dict[str, Any]]:
    allowed_tokens = {
        canonical_issue_type_token(item)
        for item in unique_provider_types(allowed_create_types)
        if canonical_issue_type_token(item)
    }
    normalized: Dict[str, Dict[str, Any]] = {}
    for child_key, raw_rule in (type_rules or {}).items():
        if not isinstance(raw_rule, dict):
            continue
        child_token = canonical_issue_type_token(child_key or raw_rule.get("provider_type"))
        if not child_token:
            continue
        provider_type = normalize_text(raw_rule.get("provider_type")) or normalize_text(child_key)
        parent_block = raw_rule.get("parent") if isinstance(raw_rule.get("parent"), dict) else {}
        allowed = bool(parent_block.get("allowed"))
        required = bool(parent_block.get("required"))
        allowed_parents = unique_provider_types(parent_block.get("allowed_parent_provider_types") or [])
        if allowed_tokens:
            allowed_parents = [
                item for item in allowed_parents if canonical_issue_type_token(item) in allowed_tokens
            ]
        if not allowed and required:
            allowed = True
        if not allowed and allowed_parents:
            allowed = True
        normalized[child_token] = {
            "provider_type": provider_type,
            "parent": {
                "allowed": allowed,
                "required": required,
                "allowed_parent_provider_types": allowed_parents,
                "source": normalize_text(parent_block.get("source")) or "metadata",
            },
        }
    return normalized


def _effective_type_rule(
    *,
    provider: str,
    child_token: str,
    provider_type_label: str,
    metadata_rule: Dict[str, Any],
    allowed_create_types: List[str],
) -> Dict[str, Any]:
    normalized_provider = normalize_text(provider).lower()
    parent_block = metadata_rule.get("parent") if isinstance(metadata_rule.get("parent"), dict) else {}
    metadata_allowed_flag = bool(parent_block.get("allowed"))
    metadata_required = bool(parent_block.get("required"))
    metadata_allowed = unique_provider_types(parent_block.get("allowed_parent_provider_types") or [])
    metadata_source = normalize_text(parent_block.get("source")) or "metadata"
    if metadata_required:
        metadata_allowed_flag = True
    if metadata_allowed:
        metadata_allowed_flag = True

    if normalized_provider != "jira":
        return {
            "provider_type": provider_type_label,
            "parent": {
                "allowed": metadata_allowed_flag,
                "required": metadata_required,
                "allowed_parent_provider_types": metadata_allowed,
                "source": metadata_source,
            },
        }

    metadata_present = bool(metadata_rule)
    fallback_rule = _jira_fallback_parent_rule(
        child_token=child_token,
        allowed_create_types=allowed_create_types,
    )
    fallback_allowed_flag = bool(fallback_rule.get("parent_allowed"))
    fallback_allowed = unique_provider_types(fallback_rule.get("allowed_parent_provider_types") or [])
    fallback_required = bool(fallback_rule.get("parent_required"))
    if fallback_required:
        fallback_allowed_flag = True
    if fallback_allowed:
        fallback_allowed_flag = True
    if metadata_present:
        resolved_allowed_flag = metadata_allowed_flag
        resolved_required = metadata_required
    else:
        resolved_allowed_flag = fallback_allowed_flag
        resolved_required = fallback_required
    resolved_allowed = metadata_allowed or fallback_allowed
    # Jira invariant: Sub-task cannot be parented to Epic.
    if child_token == "subtask":
        resolved_allowed = [
            item
            for item in resolved_allowed
            if canonical_issue_type_token(item) != "epic"
        ]
        metadata_allowed = [
            item
            for item in metadata_allowed
            if canonical_issue_type_token(item) != "epic"
        ]
        fallback_allowed = [
            item
            for item in fallback_allowed
            if canonical_issue_type_token(item) != "epic"
        ]
        resolved_required = True
        resolved_allowed_flag = True
    if resolved_allowed:
        resolved_allowed_flag = True
    source = "metadata"
    if not metadata_present:
        source = str(fallback_rule.get("source") or "fallback")
    elif not metadata_allowed and fallback_allowed:
        source = "metadata_plus_fallback_allowed"
    return {
        "provider_type": provider_type_label,
        "parent": {
            "allowed": resolved_allowed_flag,
            "required": resolved_required,
            "allowed_parent_provider_types": resolved_allowed,
            "source": source,
        },
    }


def build_effective_type_rules(
    *,
    provider: str,
    parent_rules: Dict[str, Any],
    type_rules: Dict[str, Any],
    allowed_create_types: List[str],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    normalized_provider = normalize_text(provider).lower()
    normalized_from_type_rules = _normalize_type_rules_for_allowed_types(
        type_rules=type_rules,
        allowed_create_types=allowed_create_types,
    )
    # Lean schema source of truth: type_rules only.
    # `parent_rules` is retained only for function signature compatibility.
    _ = parent_rules
    merged_metadata: Dict[str, Dict[str, Any]] = dict(normalized_from_type_rules)

    allowed_labels = unique_provider_types(allowed_create_types)
    by_token = {
        canonical_issue_type_token(label): label
        for label in allowed_labels
        if canonical_issue_type_token(label)
    }
    effective: Dict[str, Dict[str, Any]] = {}
    for child_token, label in by_token.items():
        metadata_rule = dict(merged_metadata.get(child_token) or {})
        if "parent" not in metadata_rule:
            # Backward compatibility from legacy normalized block.
            metadata_rule = {
                "provider_type": normalize_text(metadata_rule.get("provider_type")) or label,
                "parent": {
                    "allowed": bool(
                        metadata_rule.get("parent_allowed")
                        or metadata_rule.get("parent_required")
                        or metadata_rule.get("allowed_parent_provider_types")
                    ),
                    "required": bool(metadata_rule.get("parent_required")),
                    "allowed_parent_provider_types": unique_provider_types(
                        metadata_rule.get("allowed_parent_provider_types") or []
                    ),
                    "source": normalize_text(metadata_rule.get("source")) or "metadata",
                },
            }
        effective[child_token] = _effective_type_rule(
            provider=normalized_provider,
            child_token=child_token,
            provider_type_label=normalize_text(metadata_rule.get("provider_type")) or label,
            metadata_rule=metadata_rule,
            allowed_create_types=allowed_labels,
        )

    # Preserve metadata rules for types not present in current allowed_create_types.
    for child_token, metadata_rule in merged_metadata.items():
        if child_token in effective:
            continue
        provider_label = normalize_text(metadata_rule.get("provider_type")) or child_token
        effective[child_token] = _effective_type_rule(
            provider=normalized_provider,
            child_token=child_token,
            provider_type_label=provider_label,
            metadata_rule=metadata_rule,
            allowed_create_types=allowed_labels,
        )

    raw: Dict[str, Dict[str, Any]] = {}
    for child_token, metadata_rule in merged_metadata.items():
        provider_label = normalize_text(metadata_rule.get("provider_type")) or child_token
        parent_block = metadata_rule.get("parent") if isinstance(metadata_rule.get("parent"), dict) else {}
        if not parent_block:
            parent_block = {
                "allowed": bool(
                    metadata_rule.get("parent_allowed")
                    or metadata_rule.get("parent_required")
                    or metadata_rule.get("allowed_parent_provider_types")
                ),
                "required": bool(metadata_rule.get("parent_required")),
                "allowed_parent_provider_types": unique_provider_types(
                    metadata_rule.get("allowed_parent_provider_types") or []
                ),
                "source": normalize_text(metadata_rule.get("source")) or "metadata",
            }
        raw[child_token] = {
            "provider_type": provider_label,
            "parent": {
                "allowed": bool(parent_block.get("allowed") or parent_block.get("required")),
                "required": bool(parent_block.get("required")),
                "allowed_parent_provider_types": unique_provider_types(
                    parent_block.get("allowed_parent_provider_types") or []
                ),
                "source": normalize_text(parent_block.get("source")) or "metadata",
            },
        }

    return effective, raw


def build_effective_parent_rules(
    *,
    provider: str,
    parent_rules: Dict[str, Any],
    allowed_create_types: List[str],
) -> Dict[str, Dict[str, Any]]:
    # Backward-compatible helper retained for legacy callers/tests.
    converted_type_rules = convert_legacy_parent_rules_to_type_rules(
        parent_rules=parent_rules,
        allowed_create_types=allowed_create_types,
    )
    effective_type_rules, _ = build_effective_type_rules(
        provider=provider,
        parent_rules={},
        type_rules=converted_type_rules,
        allowed_create_types=allowed_create_types,
    )
    legacy_shape: Dict[str, Dict[str, Any]] = {}
    for child_token, rule in effective_type_rules.items():
        parent = rule.get("parent") if isinstance(rule.get("parent"), dict) else {}
        legacy_shape[child_token] = {
            "provider_type": normalize_text(rule.get("provider_type")) or child_token,
            "parent_allowed": bool(parent.get("allowed") or parent.get("required")),
            "parent_required": bool(parent.get("required")),
            "allowed_parent_provider_types": unique_provider_types(
                parent.get("allowed_parent_provider_types") or []
            ),
            "source": normalize_text(parent.get("source")) or "metadata",
        }
    return legacy_shape


async def resolve_parent_rule_filter(
    *,
    project: Dict[str, Any],
    create_payload: Dict[str, Any],
) -> Dict[str, Any]:
    provider = normalize_text(create_payload.get("provider")).lower() or "jira"
    project_id = normalize_text(project.get("project_id"))
    if provider != "jira" or not project_id:
        return {}

    jira_project_key = normalize_text(
        ((project.get("integrations") or {}).get("jira") or {}).get("default_project_key")
    ).upper()
    if not jira_project_key:
        return {}

    child_provider_type = normalize_text(create_payload.get("provider_type"))
    child_token = canonical_issue_type_token(child_provider_type)
    if not child_token:
        return {}

    collection = get_brain_collection(project_id)
    metadata_doc = await collection.find_one(
        {
            "entity_type": _PROVIDER_METADATA_ENTITY_TYPE,
            "provider": "jira",
            "metadata_kind": _JIRA_METADATA_KIND_ALLOWED_CREATE_TYPES,
            "project_key": jira_project_key,
        },
        {"type_rules": 1, "allowed_create_types": 1},
    )
    type_rules = (metadata_doc or {}).get("type_rules") if isinstance(metadata_doc, dict) else None
    allowed_create_types = list((metadata_doc or {}).get("allowed_create_types") or [])
    effective_type_rules, _raw_rules = build_effective_type_rules(
        provider=provider,
        parent_rules={},
        type_rules=type_rules if isinstance(type_rules, dict) else {},
        allowed_create_types=allowed_create_types,
    )
    metadata_rule = effective_type_rules.get(child_token) or {}
    if metadata_rule:
        parent = metadata_rule.get("parent") if isinstance(metadata_rule.get("parent"), dict) else {}
        return {
            "provider": provider,
            "child_provider_type": child_provider_type,
            "parent_allowed": bool(parent.get("allowed") or parent.get("required")),
            "parent_required": bool(parent.get("required")),
            "allowed_parent_provider_types": unique_provider_types(
                parent.get("allowed_parent_provider_types") or []
            ),
            "source": normalize_text(parent.get("source")) or "metadata",
        }

    # Compatibility safety for old docs that may not have effective rules yet.
    fallback_rule = _jira_fallback_parent_rule(
        child_token=child_token,
        allowed_create_types=allowed_create_types,
    )

    fallback_allowed = unique_provider_types(fallback_rule.get("allowed_parent_provider_types") or [])
    return {
        "provider": provider,
        "child_provider_type": child_provider_type,
        "parent_allowed": bool(
            fallback_rule.get("parent_allowed")
            or fallback_rule.get("parent_required")
            or fallback_allowed
        ),
        "parent_required": bool(fallback_rule.get("parent_required")),
        "allowed_parent_provider_types": fallback_allowed,
        "source": str(fallback_rule.get("source") or "fallback"),
    }


def filter_candidates_by_parent_rule(
    *,
    candidates: List[Dict[str, Any]],
    parent_rule_filter: Dict[str, Any],
) -> List[Dict[str, Any]]:
    allowed_parent_provider_types = unique_provider_types(
        (parent_rule_filter or {}).get("allowed_parent_provider_types") or []
    )
    if not allowed_parent_provider_types:
        return list(candidates)
    allowed_tokens = {canonical_issue_type_token(item) for item in allowed_parent_provider_types}
    filtered = [
        candidate
        for candidate in candidates
        if canonical_issue_type_token(candidate.get("provider_type")) in allowed_tokens
    ]
    # Fail safe: if filtering removes everything (stale/incomplete metadata), keep original pool.
    return filtered or list(candidates)
