"""Canonical provider-agnostic comment mutation event contract."""

from __future__ import annotations

from typing import Any, Dict, Optional


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def build_comment_mutation_event(
    *,
    project_id: str,
    task_key: str,
    comment_text: str,
    actor_user_id: Optional[str],
    updated_by_user_id: Optional[str] = None,
    provider: str = "jira",
    mutation_timestamp: Optional[str] = None,
    source: str = "task_comment_mutation",
    source_event_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a normalized canonical comment-mutation event payload."""
    return {
        "provider": _normalize_text(provider) or "unknown",
        "source": _normalize_text(source) or "unknown",
        "project_id": _normalize_text(project_id),
        "task_key": _normalize_text(task_key),
        "comment_text": _normalize_text(comment_text),
        "actor_user_id": _normalize_text(actor_user_id),
        "updated_by_user_id": _normalize_text(updated_by_user_id),
        "mutation_timestamp": _normalize_text(mutation_timestamp),
        "source_event_key": _normalize_text(source_event_key),
    }


def normalize_comment_mutation_event(event: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Normalize an incoming canonical/legacy event dict to canonical fields.

    Accepts legacy aliases to preserve backwards compatibility:
    - posted_comment_text -> comment_text
    """
    payload = event or {}
    return build_comment_mutation_event(
        project_id=_normalize_text(payload.get("project_id")),
        task_key=_normalize_text(payload.get("task_key")),
        comment_text=(
            _normalize_text(payload.get("comment_text"))
            or _normalize_text(payload.get("posted_comment_text"))
        ),
        actor_user_id=_normalize_text(payload.get("actor_user_id")),
        updated_by_user_id=_normalize_text(payload.get("updated_by_user_id")),
        provider=_normalize_text(payload.get("provider")) or "unknown",
        mutation_timestamp=_normalize_text(payload.get("mutation_timestamp")),
        source=_normalize_text(payload.get("source")) or "unknown",
        source_event_key=_normalize_text(payload.get("source_event_key")),
    )


def validate_comment_mutation_event(event: Dict[str, Any]) -> Optional[str]:
    """
    Validate canonical event required fields.

    Returns:
      None when valid; otherwise stable reason code.
    """
    if not _normalize_text((event or {}).get("project_id")):
        return "missing_project_id"
    if not _normalize_text((event or {}).get("task_key")):
        return "missing_task_key"
    if not _normalize_text((event or {}).get("comment_text")):
        return "missing_comment_text"
    return None

