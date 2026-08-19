"""Shared runtime dependency/predicate gate helpers for DAG execution paths."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from app.cortex.plans.branch_evaluator import evaluate_branch_predicate


def dependency_gate(
    *,
    node_id: str,
    depends_on: Sequence[str],
    node_status_by_id: Dict[str, str],
    completed_statuses: Optional[Set[str]] = None,
    skipped_statuses: Optional[Set[str]] = None,
) -> Tuple[str, Optional[str], Optional[str]]:
    """Return dependency gate decision as (decision, error_code, message)."""
    completed = completed_statuses or {"completed"}
    skipped = skipped_statuses or {"skipped"}
    for raw_dep in depends_on or []:
        dep_id = str(raw_dep or "").strip()
        if not dep_id:
            continue
        dep_status = str(node_status_by_id.get(dep_id) or "").strip().lower()
        if not dep_status:
            return (
                "fail",
                "invalid_step_dependency",
                f"`{node_id}` depends on `{dep_id}`, but that dependency has no runtime status.",
            )
        if dep_status in completed:
            continue
        if dep_status in skipped:
            return (
                "skip",
                "dependency_skipped",
                f"Skipped `{node_id}` because dependency `{dep_id}` was skipped.",
            )
        return (
            "fail",
            "dependency_not_completed",
            f"`{node_id}` depends on `{dep_id}` which is `{dep_status}`.",
        )
    return "ok", None, None


def predicate_gate(
    *,
    node_id: str,
    predicate: Optional[Dict[str, Any]],
    node_results: Dict[str, Dict[str, Any]],
    node_status_by_id: Optional[Dict[str, str]] = None,
) -> Tuple[str, Optional[str], Optional[str]]:
    """Return predicate gate decision as (decision, error_code, message)."""
    selected, reason = evaluate_branch_predicate(
        predicate=predicate,
        node_results=node_results,
    )
    if reason:
        if str(reason).startswith("predicate_dependency_missing:"):
            dep = str(reason).split(":", 1)[-1].strip()
            dep_status = str((node_status_by_id or {}).get(dep) or "").strip().lower()
            if dep_status == "skipped":
                return (
                    "skip",
                    "predicate_dependency_skipped",
                    f"Skipped `{node_id}` because predicate dependency `{dep}` was skipped.",
                )
        return (
            "fail",
            "invalid_step_condition",
            f"`{node_id}` has an invalid condition ({reason}).",
        )
    if not selected:
        return (
            "skip",
            "predicate_not_selected",
            f"Skipped `{node_id}` because predicate did not match runtime data.",
        )
    return "ok", None, None
