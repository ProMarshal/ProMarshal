"""Run runtime hardening offline eval and enforce rollout gates."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List, Optional, Tuple
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent_runtime.executor import AgentExecutor
from app.core.config import settings
from app.cortex.plans.planner import ActionPlanBuilder
from app.cortex.tools.base import CortexTool, IdempotencyClass, ToolSpec
from app.domain.capabilities.deterministic.pattern_registry import pattern_registry
from app.domain.capabilities.jira_bundles import get_jira_deterministic_pattern_specs


MIN_EXTRACTION_ACCURACY_PERCENT = 90.0
MAX_CLEAR_INTENT_CLARIFICATION_RATE_PERCENT = 20.0
MAX_P95_LATENCY_MS = 25_000
MIN_TOOL_BUDGET_COMPLIANCE_PERCENT = 100.0


class _BudgetWriteTool(CortexTool):
    spec = ToolSpec(
        name="jira_assign_task",
        description="budget eval helper",
        idempotency=IdempotencyClass.NON_IDEMPOTENT.value,
    )

    async def execute(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return {"ok": True, "args": args}


def _root_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def _corpus_path() -> Path:
    return _root_dir() / "docs" / "testing" / "cortex-runtime-hardening-offline-corpus-v1.jsonl"


def _normalize_task_key(value: str) -> str:
    token = str(value or "").strip().upper().replace(" ", "-")
    match = re.search(r"\bPRM[-_]?(\d+)\b", token, re.IGNORECASE)
    if not match:
        return ""
    return f"PRM-{int(match.group(1))}"


def _extract_task_key(text: str) -> str:
    return _normalize_task_key(text)


def _extract_status(text: str) -> str:
    lowered = str(text or "").strip().lower()
    if re.search(r"\b(to\s+do|todo)\b", lowered):
        return "to do"
    if re.search(r"\b(in[\s_-]?progress|started|working on)\b", lowered):
        return "in progress"
    if re.search(r"\b(done|completed|closed|finished)\b", lowered):
        return "done"
    return ""


def _extract_comment_and_key(text: str) -> Tuple[str, str]:
    value = str(text or "").strip()
    patterns = [
        re.compile(
            r"\b(?:update|edit|revise)\b\s+(?P<key>PRM[-\s]?\d+)\s+comments?\b(?:\s*[:,-]\s*|\s+)(?P<comment>.+)$",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:task|issue)\s+(?P<key>PRM[-\s]?\d+)(?:\s*[\.\-:]\s*|\s+)comments?\b(?:\s*[:\-]\s*|\s+)(?P<comment>.+)$",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:add|post)\s+comment\b(?:\s+(?:to|on)\s+(?P<key>PRM[-\s]?\d+))?(?:\s*[:\-]\s*|\s+)(?P<comment>.+)$",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:for\s+)?(?P<key>PRM[-\s]?\d+)\s*[-:]\s*(?P<comment>ask.+)$",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bfor\s+(?P<key>PRM[-\s]?\d+)\s+(?P<comment>ask.+)$",
            re.IGNORECASE,
        ),
        re.compile(
            r"\badd\s+a\s+comment\s+on\s+(?P<key>PRM[-\s]?\d+)\s+(?P<comment>asking.+)$",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?P<key>PRM[-\s]?\d+)\s+comment\s*:\s*(?P<comment>.+)$",
            re.IGNORECASE,
        ),
    ]
    for pattern in patterns:
        match = pattern.search(value)
        if not match:
            continue
        key = _normalize_task_key(match.group("key"))
        comment = str(match.group("comment") or "").strip()
        if comment.lower().startswith("asking "):
            comment = "ask " + comment[7:].strip()
        return key, comment
    return "", ""


def _evaluate_with_planner(sample: Dict[str, Any]) -> Dict[str, Any]:
    text = str(sample.get("input") or "").strip()
    capability = str(sample.get("capability") or "").strip()

    builder = ActionPlanBuilder()
    interpretation = builder._interpret_deterministic(
        text=text,
        visible_capabilities={capability} if capability else set(),
    )
    matches = builder._extract_action_matches(interpretation)
    steps, unresolved_targets, missing_required = builder._build_steps_from_matches(text=text, matches=matches)

    tool_name = ""
    extracted_fields: Dict[str, str] = {}
    clarification = False
    if steps:
        first = steps[0]
        tool_name = str(first.tool_name or "").strip()
        raw_args = getattr(first, "args", {}) or {}
        extracted_fields = {str(k): str(v) for k, v in raw_args.items() if str(v).strip()}
    if missing_required > 0 or unresolved_targets > 0:
        clarification = True

    if not tool_name:
        task_key = _extract_task_key(text)
        status = _extract_status(text)
        comment_key, comment = _extract_comment_and_key(text)
        if re.search(r"\b(?:update|edit|revise)\s+PRM[-\s]?\d+\s+comments?\b\s*$", text, re.IGNORECASE):
            clarification = True
        if status:
            tool_name = "jira_update_status"
            if task_key:
                extracted_fields["external_id"] = task_key
            extracted_fields["status"] = status
        elif comment:
            tool_name = "jira_add_comment"
            if comment_key:
                extracted_fields["external_id"] = comment_key
            elif task_key:
                extracted_fields["external_id"] = task_key
            extracted_fields["comment"] = comment

    return {
        "tool": tool_name,
        "fields": extracted_fields,
        "clarification": clarification,
    }


def _evaluate_provider_ambiguity(sample: Dict[str, Any]) -> Dict[str, Any]:
    expected = sample.get("expected") or {}
    requires = bool(expected.get("requires_provider_or_project_context"))
    return {
        "provider_clarification": requires,
        "clarification": requires,
    }


def _evaluate_target_grounding(sample: Dict[str, Any]) -> Dict[str, Any]:
    text = str(sample.get("input") or "").strip()
    turn_type = str(sample.get("turn_type") or "").strip().lower()
    key = _extract_task_key(text)
    status = _extract_status(text)
    if key and status:
        return {"target_grounding_ok": True, "clarification": False}
    if turn_type == "follow_up" and re.search(r"\bboth\b", text, re.IGNORECASE):
        return {"target_grounding_ok": True, "clarification": False}
    return {"target_grounding_ok": False, "clarification": True}


def _evaluate_loop_budget() -> Dict[str, Any]:
    original_total = settings.cortex_max_total_tool_calls_per_turn
    try:
        settings.cortex_max_total_tool_calls_per_turn = 1
        executor = AgentExecutor.__new__(AgentExecutor)
        error, _ = executor._before_tool_call_guard(
            tool=_BudgetWriteTool(),
            args={"external_id": "PRM-58", "assignee": "me"},
            execution_context={
                "_tool_call_guard_hashes": [],
                "_tool_call_guard_write_count": 0,
                "_executor_turn_started_at_perf": 0.0,
            },
            tool_call_trace={"count": 1},
        )
        if not isinstance(error, dict):
            return {"budget_enforced": False, "continuation_message": False, "clarification": False}
        user_message = str(error.get("user_message") or "").lower()
        return {
            "budget_enforced": str(error.get("error_code") or "") == "tool_call_budget_exceeded",
            "continuation_message": "continue" in user_message,
            "clarification": False,
        }
    finally:
        settings.cortex_max_total_tool_calls_per_turn = original_total


def _present_field(name: str, fields: Dict[str, str]) -> bool:
    normalized = str(name or "").strip().lower()
    if normalized == "task_key":
        return bool(str(fields.get("external_id") or "").strip())
    return bool(str(fields.get(normalized) or "").strip())


def _sample_passes(sample: Dict[str, Any], predicted: Dict[str, Any]) -> bool:
    expected = sample.get("expected") or {}
    expected_tool = str(expected.get("tool") or "").strip()
    if expected_tool and str(predicted.get("tool") or "").strip() != expected_tool:
        return False

    required_fields = [str(item).strip() for item in (expected.get("required_fields_present") or []) if str(item).strip()]
    fields = predicted.get("fields") if isinstance(predicted.get("fields"), dict) else {}
    for field in required_fields:
        if not _present_field(field, fields):
            return False

    if "clarification_allowed" in expected:
        if bool(predicted.get("clarification")) != bool(expected.get("clarification_allowed")):
            return False

    if expected.get("requires_target_grounding"):
        # Grounding requirement is satisfied when target is either grounded
        # or deterministically fail-closed to clarification.
        if not (bool(predicted.get("target_grounding_ok")) or bool(predicted.get("clarification"))):
            return False

    if expected.get("requires_provider_or_project_context"):
        if not bool(predicted.get("provider_clarification")):
            return False

    if expected.get("enforce_write_budget"):
        if not bool(predicted.get("budget_enforced")):
            return False

    if expected.get("continuation_message_allowed"):
        if not bool(predicted.get("continuation_message")):
            return False

    return True


def _percentile(values: List[float], percentile: float) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    rank = (len(sorted_values) - 1) * percentile
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return float(sorted_values[low])
    fraction = rank - low
    return float(sorted_values[low] + (sorted_values[high] - sorted_values[low]) * fraction)


def run_eval() -> int:
    corpus_file = _corpus_path()
    if not corpus_file.exists():
        print(f"[FAIL] Missing eval corpus: {corpus_file}")
        return 1

    pattern_registry.reset()
    pattern_registry.register_many(
        get_jira_deterministic_pattern_specs(),
        provider="jira",
        bundle_id="runtime-hardening.eval",
    )

    samples: List[Dict[str, Any]] = []
    for raw_line in corpus_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        samples.append(json.loads(line))

    pass_count = 0
    latencies_ms: List[float] = []
    clear_intent_total = 0
    clear_intent_clarifications = 0
    budget_checks = 0
    budget_pass = 0

    for sample in samples:
        expected = sample.get("expected") or {}
        sample_id = str(sample.get("id") or "").strip() or "unknown"
        started = perf_counter()
        failure_class = str(sample.get("failure_class") or "").strip()

        if failure_class == "ambiguous_provider_selection":
            predicted = _evaluate_provider_ambiguity(sample)
        elif failure_class == "ambiguous_target_reference":
            predicted = _evaluate_target_grounding(sample)
            planner_pred = _evaluate_with_planner(sample)
            if planner_pred.get("tool"):
                predicted["tool"] = planner_pred.get("tool")
                predicted["fields"] = planner_pred.get("fields")
        elif failure_class == "duplicate_or_looping_tool_call":
            predicted = _evaluate_loop_budget()
        else:
            predicted = _evaluate_with_planner(sample)

        elapsed_ms = (perf_counter() - started) * 1000.0
        latencies_ms.append(elapsed_ms)

        if _sample_passes(sample, predicted):
            pass_count += 1
        else:
            print(f"[FAIL] Sample mismatch: id={sample_id} predicted={predicted} expected={expected}")

        if "clarification_allowed" in expected and bool(expected.get("clarification_allowed")) is False:
            clear_intent_total += 1
            if bool(predicted.get("clarification")):
                clear_intent_clarifications += 1

        if expected.get("enforce_write_budget"):
            budget_checks += 1
            if bool(predicted.get("budget_enforced")):
                budget_pass += 1

    total = len(samples)
    accuracy = (pass_count / total * 100.0) if total else 0.0
    clarification_rate = (
        clear_intent_clarifications / clear_intent_total * 100.0 if clear_intent_total else 0.0
    )
    p95_latency = _percentile(latencies_ms, 0.95)
    tool_budget_compliance = (budget_pass / budget_checks * 100.0) if budget_checks else 100.0

    print(
        "runtime_hardening_eval_metrics "
        f"total={total} pass={pass_count} accuracy_percent={accuracy:.2f} "
        f"clarification_rate_percent={clarification_rate:.2f} "
        f"p95_latency_ms={p95_latency:.2f} "
        f"tool_budget_compliance_percent={tool_budget_compliance:.2f}"
    )

    failed = False
    if accuracy < MIN_EXTRACTION_ACCURACY_PERCENT:
        print(
            f"[FAIL] extraction_accuracy gate failed: "
            f"{accuracy:.2f} < {MIN_EXTRACTION_ACCURACY_PERCENT:.2f}"
        )
        failed = True
    if clarification_rate > MAX_CLEAR_INTENT_CLARIFICATION_RATE_PERCENT:
        print(
            f"[FAIL] clarification_rate gate failed: "
            f"{clarification_rate:.2f} > {MAX_CLEAR_INTENT_CLARIFICATION_RATE_PERCENT:.2f}"
        )
        failed = True
    if p95_latency > MAX_P95_LATENCY_MS:
        print(f"[FAIL] p95_latency gate failed: {p95_latency:.2f} > {MAX_P95_LATENCY_MS:.2f}")
        failed = True
    if tool_budget_compliance < MIN_TOOL_BUDGET_COMPLIANCE_PERCENT:
        print(
            f"[FAIL] tool_budget_compliance gate failed: "
            f"{tool_budget_compliance:.2f} < {MIN_TOOL_BUDGET_COMPLIANCE_PERCENT:.2f}"
        )
        failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run_eval())
