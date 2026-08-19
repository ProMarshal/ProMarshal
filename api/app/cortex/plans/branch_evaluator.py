"""Branch predicate evaluator for execution graph nodes."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def evaluate_branch_predicate(
    *,
    predicate: Optional[Dict[str, Any]],
    node_results: Dict[str, Dict[str, Any]],
) -> Tuple[bool, Optional[str]]:
    """
    Evaluate one predicate against prior node results.

    Returns:
    - (selected, None) on success
    - (False, reason) when predicate is malformed/unresolvable
    """
    if not isinstance(predicate, dict) or not predicate:
        return True, None

    kind = str(predicate.get("type") or "").strip().lower()
    if kind in {"always", ""}:
        return True, None

    depends_on = str(predicate.get("depends_on") or "").strip()
    if not depends_on:
        return False, "predicate_missing_depends_on"
    upstream = node_results.get(depends_on)
    if not isinstance(upstream, dict):
        return False, f"predicate_dependency_missing:{depends_on}"

    key = str(predicate.get("result_key") or "").strip()
    value = _extract_path(upstream, key) if key else upstream

    if kind == "if_empty":
        return _is_empty(value), None
    if kind == "if_non_empty":
        return not _is_empty(value), None
    if kind == "if_equals":
        expected = predicate.get("value")
        return value == expected, None
    if kind == "if_not_equals":
        expected = predicate.get("value")
        return value != expected, None
    if kind == "if_truthy":
        return bool(value), None
    if kind == "if_falsy":
        return not bool(value), None

    return False, f"predicate_unknown_type:{kind}"


def _extract_path(payload: Dict[str, Any], path: str) -> Any:
    if not path:
        return payload
    current: Any = payload
    for raw in path.split("."):
        token = str(raw or "").strip()
        if not token:
            continue
        if isinstance(current, dict):
            current = current.get(token)
            continue
        return None
    return current


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False

