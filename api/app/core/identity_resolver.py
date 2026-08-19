"""Tool-agnostic project member identity resolution helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class IdentityResolutionResult:
    status: str  # resolved | ambiguous | not_found | invalid
    normalized_hint: str
    promarshal_user_id: Optional[str] = None
    matched_members: List[Dict[str, Any]] = field(default_factory=list)
    provider_ids: Dict[str, str] = field(default_factory=dict)
    reason: Optional[str] = None


def _normalize_text(value: Optional[str]) -> str:
    return str(value or "").strip()


def _normalize_lookup_key(value: str) -> str:
    return re.sub(r"[\s_\-]+", "", _normalize_text(value).lower())


def _normalize_identity_hint(value: str) -> str:
    hint = _normalize_text(value)
    if hint:
        # Remove common surrounding punctuation from conversational text.
        hint = hint.strip().strip("`'\"")
        hint = re.sub(r"[.,!?;:]+$", "", hint).strip()
    if hint.startswith("<@") and hint.endswith(">") and len(hint) > 3:
        return hint[2:-1].strip()
    if hint.startswith("@"):
        return hint[1:].strip()
    return hint


def _is_actor_self_alias(value: str) -> bool:
    token = _normalize_lookup_key(value)
    return token in {"me", "myself", "self", "i", "my", "mine"}


def _contains_actor_self_alias_phrase(value: str) -> bool:
    normalized = _normalize_text(value).lower()
    if not normalized:
        return False
    # Handles noisy owner hints where the model passes full user utterance.
    patterns = (
        r"\bassign(?:ed)?\s+to\s+me\b",
        r"\bfor\s+me\b",
        r"\bto\s+me\b",
        r"\bmyself\b",
        r"\bfor\s+myself\b",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def _active_project_members(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    members = project.get("members") or []
    if not isinstance(members, list):
        return []
    return [m for m in members if isinstance(m, dict) and _normalize_text(m.get("status")).lower() == "active"]


def _member_provider_ids(member: Dict[str, Any]) -> Dict[str, str]:
    integration_ids = member.get("integration_ids") or {}
    if not isinstance(integration_ids, dict):
        return {}
    result: Dict[str, str] = {}
    for key, value in integration_ids.items():
        norm_key = _normalize_text(str(key))
        norm_value = _normalize_text(str(value))
        if norm_key and norm_value:
            result[norm_key] = norm_value
    return result


def _member_label(member: Dict[str, Any]) -> str:
    user_id = _normalize_text(member.get("user_id")) or "unknown"
    name = _normalize_text(member.get("name"))
    email = _normalize_text(member.get("email"))
    if name:
        return f"{user_id} ({name})"
    if email:
        return f"{user_id} ({email})"
    return user_id


def _unique_members_by_user_id(members: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    unique: Dict[str, Dict[str, Any]] = {}
    for member in members:
        user_id = _normalize_text(member.get("user_id"))
        if user_id:
            unique[user_id] = member
    return list(unique.values())


def _resolved_result(normalized_hint: str, member: Dict[str, Any]) -> IdentityResolutionResult:
    return IdentityResolutionResult(
        status="resolved",
        normalized_hint=normalized_hint,
        promarshal_user_id=_normalize_text(member.get("user_id")) or None,
        matched_members=[member],
        provider_ids=_member_provider_ids(member),
    )


def resolve_project_member_identity(
    project: Dict[str, Any],
    hint: str,
    *,
    actor_user_id: Optional[str] = None,
    actor_aliases_enabled: bool = True,
) -> IdentityResolutionResult:
    """
    Resolve a user hint to a project member.

    Matching priority:
    1) ProMarshal user_id exact
    2) Any integration id exact (slack/jira/future provider IDs)
    3) exact normalized name/email/email-local-part
    4) unique prefix match on normalized name
    """
    normalized_hint = _normalize_identity_hint(hint)
    if not normalized_hint:
        return IdentityResolutionResult(status="invalid", normalized_hint="", reason="empty_hint")

    members = _active_project_members(project)
    if not members:
        return IdentityResolutionResult(
            status="not_found",
            normalized_hint=normalized_hint,
            reason="no_active_members",
        )

    normalized_actor_user_id = _normalize_text(actor_user_id)
    if (
        actor_aliases_enabled
        and normalized_actor_user_id
        and (
            _is_actor_self_alias(normalized_hint)
            or _contains_actor_self_alias_phrase(normalized_hint)
        )
    ):
        actor_matches = [
            m
            for m in members
            if _normalize_text(m.get("user_id")) == normalized_actor_user_id
        ]
        actor_unique = _unique_members_by_user_id(actor_matches)
        if len(actor_unique) == 1:
            return _resolved_result(normalized_hint, actor_unique[0])
        return IdentityResolutionResult(
            status="not_found",
            normalized_hint=normalized_hint,
            matched_members=_unique_members_by_user_id(members),
            reason="actor_not_active_member",
        )

    hint_lower = normalized_hint.lower()
    hint_key = _normalize_lookup_key(normalized_hint)

    by_user_id = [
        m for m in members if _normalize_text(m.get("user_id")).lower() == hint_lower and _normalize_text(m.get("user_id"))
    ]
    by_user_id_unique = _unique_members_by_user_id(by_user_id)
    if len(by_user_id_unique) == 1:
        return _resolved_result(normalized_hint, by_user_id_unique[0])

    by_provider_id: List[Dict[str, Any]] = []
    for member in members:
        provider_ids = _member_provider_ids(member)
        if any(str(v).lower() == hint_lower for v in provider_ids.values()):
            by_provider_id.append(member)
    by_provider_id_unique = _unique_members_by_user_id(by_provider_id)
    if len(by_provider_id_unique) == 1:
        return _resolved_result(normalized_hint, by_provider_id_unique[0])
    if len(by_provider_id_unique) > 1:
        return IdentityResolutionResult(
            status="ambiguous",
            normalized_hint=normalized_hint,
            matched_members=by_provider_id_unique,
            reason="multiple_provider_id_matches",
        )

    exact_matches: List[Dict[str, Any]] = []
    for member in members:
        user_id = _normalize_text(member.get("user_id"))
        if not user_id:
            continue
        member_name_key = _normalize_lookup_key(_normalize_text(member.get("name")))
        member_email = _normalize_text(member.get("email")).lower()
        member_email_local = member_email.split("@")[0] if "@" in member_email else member_email

        if member_name_key and member_name_key == hint_key:
            exact_matches.append(member)
            continue
        if member_email and member_email == hint_lower:
            exact_matches.append(member)
            continue
        if member_email_local and _normalize_lookup_key(member_email_local) == hint_key:
            exact_matches.append(member)
            continue

    exact_unique = _unique_members_by_user_id(exact_matches)
    if len(exact_unique) == 1:
        return _resolved_result(normalized_hint, exact_unique[0])
    if len(exact_unique) > 1:
        return IdentityResolutionResult(
            status="ambiguous",
            normalized_hint=normalized_hint,
            matched_members=exact_unique,
            reason="multiple_exact_matches",
        )

    prefix_matches: List[Dict[str, Any]] = []
    if hint_key:
        for member in members:
            user_id = _normalize_text(member.get("user_id"))
            if not user_id:
                continue
            member_name_key = _normalize_lookup_key(_normalize_text(member.get("name")))
            if member_name_key and member_name_key.startswith(hint_key):
                prefix_matches.append(member)

    prefix_unique = _unique_members_by_user_id(prefix_matches)
    if len(prefix_unique) == 1:
        return _resolved_result(normalized_hint, prefix_unique[0])
    if len(prefix_unique) > 1:
        return IdentityResolutionResult(
            status="ambiguous",
            normalized_hint=normalized_hint,
            matched_members=prefix_unique,
            reason="multiple_prefix_matches",
        )

    return IdentityResolutionResult(
        status="not_found",
        normalized_hint=normalized_hint,
        matched_members=_unique_members_by_user_id(members),
        reason="no_match",
    )


def format_member_candidates(members: List[Dict[str, Any]], limit: int = 5) -> str:
    labels: List[str] = []
    for member in members[: max(1, limit)]:
        labels.append(_member_label(member))
    return ", ".join(labels)


def format_project_member_candidates(project: Dict[str, Any], limit: int = 5) -> str:
    return format_member_candidates(_active_project_members(project), limit=limit)


def _rank_members_for_hint(
    members: List[Dict[str, Any]],
    *,
    normalized_hint: str,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    hint = _normalize_text(normalized_hint).lower()
    hint_key = _normalize_lookup_key(normalized_hint)
    if not members:
        return []
    if not hint and not hint_key:
        return members[: max(1, limit)]

    scored: List[tuple[int, Dict[str, Any]]] = []
    for member in members:
        user_id = _normalize_text(member.get("user_id")).lower()
        name = _normalize_text(member.get("name")).lower()
        email = _normalize_text(member.get("email")).lower()
        email_local = email.split("@")[0] if "@" in email else email

        score = 0
        if user_id and user_id == hint:
            score += 100
        if email and email == hint:
            score += 90
        if _normalize_lookup_key(name) == hint_key and hint_key:
            score += 80
        if _normalize_lookup_key(email_local) == hint_key and hint_key:
            score += 75
        if hint and name.startswith(hint):
            score += 60
        if hint and email_local.startswith(hint):
            score += 55
        if hint and hint in name:
            score += 40
        if hint and hint in email_local:
            score += 35
        if hint_key and _normalize_lookup_key(name).startswith(hint_key):
            score += 30
        if score > 0:
            scored.append((score, member))

    if not scored:
        return members[: max(1, limit)]
    scored.sort(key=lambda item: item[0], reverse=True)

    ordered: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for _score, member in scored:
        user_id = _normalize_text(member.get("user_id"))
        if not user_id or user_id in seen:
            continue
        seen.add(user_id)
        ordered.append(member)
        if len(ordered) >= max(1, limit):
            break
    return ordered


def _build_suggestion_tail(
    *,
    result: IdentityResolutionResult,
    suggestion_limit: int = 3,
) -> str:
    candidate_pool = [m for m in (result.matched_members or []) if isinstance(m, dict)]
    if not candidate_pool:
        return ""
    ranked = _rank_members_for_hint(
        candidate_pool,
        normalized_hint=result.normalized_hint,
        limit=suggestion_limit,
    )
    if not ranked:
        return ""
    labels = [_member_label(member) for member in ranked]
    if len(labels) == 1:
        return f" Not able to identify exact owner. Would you like to assign to {labels[0]}?"
    return (
        " Not able to identify exact owner. "
        f"Would you like to assign to one of these: {', '.join(labels)}?"
    )


def build_identity_followup_message(
    result: IdentityResolutionResult,
    *,
    subject: str = "assignee",
    project: Optional[Dict[str, Any]] = None,
    suggestion_limit: int = 3,
) -> str:
    target = (subject or "assignee").strip() or "assignee"
    title = target.capitalize()
    if result.status == "invalid":
        return f"{title} is required."
    if result.status == "not_found":
        if result.reason == "no_active_members":
            return f"No active project members found to assign {target}."
        source_members = result.matched_members
        if not source_members and isinstance(project, dict):
            source_members = _active_project_members(project)
        sample = format_member_candidates(source_members)
        suggestion_tail = _build_suggestion_tail(
            result=IdentityResolutionResult(
                status=result.status,
                normalized_hint=result.normalized_hint,
                promarshal_user_id=result.promarshal_user_id,
                matched_members=list(source_members or []),
                provider_ids=result.provider_ids,
                reason=result.reason,
            ),
            suggestion_limit=suggestion_limit,
        )
        if sample:
            return (
                f"Couldn't resolve {target} `{result.normalized_hint}` to an active project member. "
                f"Use ProMarshal user id (examples: {sample}).{suggestion_tail}"
            )
        return f"Couldn't resolve {target}. Use ProMarshal user id."
    if result.status == "ambiguous":
        candidates = format_member_candidates(result.matched_members)
        suggestion_tail = _build_suggestion_tail(result=result, suggestion_limit=suggestion_limit)
        return (
            f"{title} `{result.normalized_hint}` is ambiguous: {candidates}. "
            f"Please assign using exact ProMarshal user id.{suggestion_tail}"
        )
    return f"Unable to resolve {target}. Use ProMarshal user id."
