"""Deterministic multi-step planner for action-oriented user requests."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

from app.core.config import settings
from app.cortex.domain.normalization import resolve_enum_alias
from app.cortex.plans.models import (
    ActionPlan,
    ActionStep,
    LAST_TASK_REF,
    PlanDecision,
    PlanSource,
)
from app.cortex.tools.schemas import normalize_external_id, normalize_text
from app.domain.capabilities.deterministic.pattern_registry import list_action_patterns
from app.domain.capabilities.deterministic.models import (
    DeterministicInterpretation,
    DeterministicMatchResult,
)
from app.domain.capabilities.deterministic.service import deterministic_interpretation_service


@dataclass(frozen=True)
class _ActionMatch:
    action_type: str
    capability_id: str
    tool_name: str
    required_fields: Tuple[str, ...]
    missing_required_fields: Tuple[str, ...]
    start: int
    end: int
    payload: Dict[str, Any]

SPRINT_BACKLOG_PATTERN = re.compile(
    r"\b(?:keep|leave|stay)\b.{0,24}\bbacklog\b|\bbacklog\b.{0,24}\b(?:only|please|keep)\b",
    re.IGNORECASE,
)
SPRINT_ACTIVE_PATTERN = re.compile(
    r"\b(?:add|move|put)\b.{0,24}\b(?:active|current)?\s*sprint\b|\bin\s+(?:the\s+)?(?:active|current)\s+sprint\b",
    re.IGNORECASE,
)

STATUS_CANONICAL_MAP = {
    "todo": "todo",
    "to do": "todo",
    "in progress": "in_progress",
    "in_progress": "in_progress",
    "in-progress": "in_progress",
    "done": "done",
    "completed": "done",
    "closed": "done",
}

ACTION_TOKEN_PATTERN = re.compile(r"[a-z0-9_@.\-]+")


class ActionPlanBuilder:
    """Build deterministic ActionPlan when confidence is sufficient."""

    def __init__(self) -> None:
        self.threshold = float(settings.cortex_plan_confidence_threshold)
        self.min_actions = max(2, int(settings.cortex_plan_min_actions))

    def build(
        self,
        *,
        user_message: str,
        visible_tools: Sequence[Dict[str, Any]],
        connected_integrations: Sequence[str],
    ) -> PlanDecision:
        text = str(user_message or "").strip()
        if not text:
            return PlanDecision(
                plan=None,
                confidence=0.0,
                reason="empty_message",
                metadata={
                    "gate_action": "enrichment",
                    "reason_code": "deterministic_unmatched",
                    "match_count": 0,
                },
            )

        visible_tool_names = {
            str(item.get("name") or "").strip()
            for item in visible_tools
            if isinstance(item, dict)
        }
        visible_capabilities = {
            str(capability).strip().lower()
            for item in visible_tools
            if isinstance(item, dict)
            for capability in (item.get("capabilities") or [])
            if str(capability).strip()
        }
        normalized_integrations = {
            str(name).strip().lower() for name in connected_integrations if str(name).strip()
        }

        interpretation = self._interpret_deterministic(
            text=text,
            visible_capabilities=visible_capabilities,
        )
        matches = self._extract_action_matches(interpretation)
        clarification_missing_fields, clarification_calls = self._build_clarification_payload(matches)
        if clarification_missing_fields:
            return PlanDecision(
                plan=None,
                confidence=float(interpretation.confidence),
                reason="clarification_required:missing_required_slots",
                metadata={
                    "gate_action": "clarification_required",
                    "reason_code": "clarification_required",
                    "missing_fields": clarification_missing_fields,
                    "unresolved_calls": clarification_calls,
                    "match_count": len(matches),
                    "deterministic_confidence": interpretation.confidence,
                    "deterministic_slot_coverage": interpretation.slot_coverage,
                    "deterministic_signal_strength": interpretation.signal_strength,
                    "deterministic_missing_slots": interpretation.missing_slots,
                },
            )
        if len(matches) < self.min_actions:
            reason_code = "deterministic_unmatched" if not matches else "low_confidence"
            return PlanDecision(
                plan=None,
                confidence=float(interpretation.confidence),
                reason="not_multi_action",
                metadata={
                    "gate_action": "enrichment",
                    "reason_code": reason_code,
                    "match_count": len(matches),
                    "deterministic_confidence": interpretation.confidence,
                    "deterministic_slot_coverage": interpretation.slot_coverage,
                    "deterministic_signal_strength": interpretation.signal_strength,
                    "deterministic_missing_slots": interpretation.missing_slots,
                },
            )

        steps, unresolved_targets, missing_required = self._build_steps_from_matches(
            text=text,
            matches=matches,
        )
        if len(steps) < self.min_actions:
            return PlanDecision(
                plan=None,
                confidence=float(interpretation.confidence),
                reason="insufficient_step_count_after_normalization",
                metadata={
                    "gate_action": "enrichment",
                    "reason_code": "low_confidence",
                    "match_count": len(matches),
                    "deterministic_confidence": interpretation.confidence,
                    "deterministic_slot_coverage": interpretation.slot_coverage,
                    "deterministic_signal_strength": interpretation.signal_strength,
                    "deterministic_missing_slots": interpretation.missing_slots,
                },
            )

        mapping_score, missing_tools = self._mapping_score(
            steps=steps,
            visible_tool_names=visible_tool_names,
            visible_capabilities=visible_capabilities,
            connected_integrations=normalized_integrations,
        )
        confidence = self._confidence_score(
            text=text,
            matches=matches,
            steps=steps,
            unresolved_targets=unresolved_targets,
            missing_required=missing_required,
            mapping_score=mapping_score,
            deterministic_confidence=interpretation.confidence,
        )
        confidence = max(0.0, min(1.0, confidence))
        if confidence < self.threshold:
            return PlanDecision(
                plan=None,
                confidence=confidence,
                reason=(
                    "low_confidence:"
                    f"missing_required={missing_required},unresolved_targets={unresolved_targets},missing_tools={missing_tools}"
                ),
                metadata={
                    "gate_action": "enrichment",
                    "reason_code": "low_confidence",
                    "match_count": len(matches),
                    "missing_required": missing_required,
                    "unresolved_targets": unresolved_targets,
                    "missing_tools": missing_tools,
                    "deterministic_confidence": interpretation.confidence,
                    "deterministic_slot_coverage": interpretation.slot_coverage,
                    "deterministic_signal_strength": interpretation.signal_strength,
                    "deterministic_missing_slots": interpretation.missing_slots,
                },
            )

        plan = ActionPlan(
            plan_id=str(uuid4()),
            source=PlanSource.DETERMINISTIC,
            confidence=confidence,
            steps=steps,
            rationale="deterministic_compound_action_parse",
            metadata={
                "match_count": len(matches),
                "missing_required": missing_required,
                "unresolved_targets": unresolved_targets,
                "missing_tools": missing_tools,
                "deterministic_signal_strength": interpretation.signal_strength,
                "deterministic_slot_coverage": interpretation.slot_coverage,
                "deterministic_missing_slots": interpretation.missing_slots,
            },
        )
        return PlanDecision(
            plan=plan,
            confidence=confidence,
            reason="deterministic_plan_selected",
            metadata={
                "gate_action": "deterministic_plan",
                "reason_code": "resolved",
                "match_count": len(matches),
                "missing_required": missing_required,
                "unresolved_targets": unresolved_targets,
                "missing_tools": missing_tools,
                "deterministic_confidence": interpretation.confidence,
                "deterministic_slot_coverage": interpretation.slot_coverage,
                "deterministic_signal_strength": interpretation.signal_strength,
                "deterministic_missing_slots": interpretation.missing_slots,
            },
        )

    def _interpret_deterministic(
        self,
        *,
        text: str,
        visible_capabilities: set[str],
    ) -> DeterministicInterpretation:
        capabilities = sorted(
            {
                str(item or "").strip().lower()
                for item in (visible_capabilities or set())
                if str(item or "").strip()
            }
        )
        pattern_specs = list_action_patterns(capability_ids=capabilities or None)
        return deterministic_interpretation_service.interpret(
            text=text,
            pattern_specs=pattern_specs,
        )

    def _extract_action_matches(self, interpretation: DeterministicInterpretation) -> List[_ActionMatch]:
        matches: List[_ActionMatch] = []
        for match in interpretation.matches:
            matches.append(self._from_interpretation_match(match))
        return matches

    def _from_interpretation_match(self, match: DeterministicMatchResult) -> _ActionMatch:
        payload = {
            str(key or "").strip(): str(value or "")
            for key, value in (match.extracted_slots or {}).items()
            if str(key or "").strip()
        }
        return _ActionMatch(
            action_type=str(match.action_type or "").strip().lower(),
            capability_id=str(match.capability_id or "").strip().lower(),
            tool_name=str(match.tool_name or "").strip(),
            required_fields=tuple(str(item).strip() for item in (match.required_fields or []) if str(item).strip()),
            missing_required_fields=tuple(
                str(item).strip() for item in (match.missing_required_fields or []) if str(item).strip()
            ),
            start=int(match.start),
            end=int(match.end),
            payload=payload,
        )

    def _build_steps_from_matches(
        self,
        *,
        text: str,
        matches: Sequence[_ActionMatch],
    ) -> Tuple[List[ActionStep], int, int]:
        steps: List[ActionStep] = []
        unresolved_targets = 0
        missing_required = 0
        has_prior_task_context = False
        sprint_directive = self._extract_sprint_directive(text)

        for index, match in enumerate(matches):
            step_id = f"step_{index + 1}"
            action = match.action_type
            payload = match.payload
            args: Dict[str, Any] = {}
            summary = ""

            if action == "create_work_item":
                title = self._normalize_task_title(str(payload.get("title") or ""))
                provider_type = normalize_text(str(payload.get("provider_type") or ""))
                parent_external_id = normalize_external_id(
                    str(payload.get("parent_external_id") or "")
                )
                if title:
                    args["title"] = title
                if provider_type:
                    args["provider_type"] = provider_type
                if parent_external_id:
                    args["parent_external_id"] = parent_external_id
                if sprint_directive:
                    args["sprint_directive"] = sprint_directive
                if not title:
                    missing_required += 1
                summary = (
                    f"Create {provider_type or 'work item'} '{title or 'untitled'}'"
                )
                step = ActionStep(
                    step_id=step_id,
                    action_type=action,
                    tool_name=str(match.tool_name or "jira_create_work_item"),
                    args=args,
                    summary=summary,
                    capability=match.capability_id or "workitem_create",
                )
                steps.append(step)
                has_prior_task_context = True
                continue

            if action == "assign_work_item":
                assignee = normalize_text(str(payload.get("assignee") or ""))
                target = self._normalize_target(str(payload.get("target") or ""))
                if assignee:
                    args["assignee"] = assignee
                if not assignee:
                    missing_required += 1
                if target:
                    args["external_id"] = target
                elif has_prior_task_context:
                    args["external_id"] = LAST_TASK_REF
                else:
                    unresolved_targets += 1
                    missing_required += 1
                summary = (
                    f"Assign {args.get('external_id', 'target work item')} to {assignee or 'assignee'}"
                )
                step = ActionStep(
                    step_id=step_id,
                    action_type=action,
                    tool_name=str(match.tool_name or "jira_assign_work_item"),
                    args=args,
                    summary=summary,
                    capability=match.capability_id or "workitem_assign",
                )
                steps.append(step)
                has_prior_task_context = True
                continue

            if action == "update_status":
                target = self._normalize_target(str(payload.get("target") or ""))
                status = self._normalize_status(str(payload.get("status") or ""))
                if target:
                    args["external_id"] = target
                elif has_prior_task_context:
                    args["external_id"] = LAST_TASK_REF
                else:
                    unresolved_targets += 1
                    missing_required += 1
                if status:
                    args["status"] = status
                    if sprint_directive:
                        args["sprint_directive"] = sprint_directive
                else:
                    missing_required += 1
                summary = (
                    f"Move {args.get('external_id', 'target work item')} to {status or 'target status'}"
                )
                step = ActionStep(
                    step_id=step_id,
                    action_type=action,
                    tool_name=str(match.tool_name or "jira_update_status"),
                    args=args,
                    summary=summary,
                    capability=match.capability_id or "workitem_update_status",
                )
                steps.append(step)
                has_prior_task_context = True
                continue

            if action == "add_comment":
                target = self._normalize_target(str(payload.get("target") or ""))
                comment = normalize_text(str(payload.get("comment") or ""))
                if target:
                    args["external_id"] = target
                elif has_prior_task_context:
                    args["external_id"] = LAST_TASK_REF
                else:
                    unresolved_targets += 1
                    missing_required += 1
                if comment:
                    args["comment"] = comment
                else:
                    missing_required += 1
                summary = (
                    f"Add comment to {args.get('external_id', 'target work item')}"
                )
                step = ActionStep(
                    step_id=step_id,
                    action_type=action,
                    tool_name=str(match.tool_name or "jira_add_comment"),
                    args=args,
                    summary=summary,
                    capability=match.capability_id or "workitem_add_comment",
                )
                steps.append(step)
                has_prior_task_context = True

        steps = self._compact_create_assign_steps(steps)
        steps = self._populate_last_task_dependencies(steps)
        return steps, unresolved_targets, missing_required

    def _extract_sprint_directive(self, text: str) -> Optional[str]:
        raw = str(text or "").strip()
        if not raw:
            return None
        if SPRINT_BACKLOG_PATTERN.search(raw):
            return "backlog"
        if SPRINT_ACTIVE_PATTERN.search(raw):
            return "active_sprint"
        return None

    def _build_clarification_payload(
        self,
        matches: Sequence[_ActionMatch],
    ) -> Tuple[List[str], List[Dict[str, Any]]]:
        missing_tokens: List[str] = []
        unresolved_calls: List[Dict[str, Any]] = []
        seen_missing: set[str] = set()
        seen_calls: set[str] = set()

        for match in matches:
            tool_name = str(match.tool_name or "").strip()
            if not tool_name:
                continue

            call_args: Dict[str, Any] = {}
            for key, value in (match.payload or {}).items():
                field_name = str(key or "").strip()
                field_value = str(value or "").strip()
                if not field_name or not field_value:
                    continue
                call_args[field_name] = field_value

            call_key = f"{tool_name}:{sorted(call_args.items())}"
            if call_key not in seen_calls:
                seen_calls.add(call_key)
                unresolved_calls.append({"tool_name": tool_name, "args": call_args})

            for field_name in match.missing_required_fields:
                clean_field = str(field_name or "").strip()
                if not clean_field:
                    continue
                token = f"{tool_name}.{clean_field}".lower()
                if token in seen_missing:
                    continue
                seen_missing.add(token)
                missing_tokens.append(token)

        return missing_tokens, unresolved_calls

    def _compact_create_assign_steps(self, steps: List[ActionStep]) -> List[ActionStep]:
        """Fold immediate assign(last_task) into create when possible."""
        if len(steps) < 2:
            return steps
        compacted: List[ActionStep] = []
        i = 0
        while i < len(steps):
            current = steps[i]
            nxt = steps[i + 1] if i + 1 < len(steps) else None
            if (
                nxt
                and current.action_type == "create_work_item"
                and nxt.action_type == "assign_work_item"
                and str(nxt.args.get("external_id") or "") == LAST_TASK_REF
                and str(current.args.get("assignee") or "").strip() == ""
                and str(nxt.args.get("assignee") or "").strip()
            ):
                current.args["assignee"] = str(nxt.args.get("assignee")).strip()
                current.summary = (
                    f"Create work item '{current.args.get('title', 'untitled')}' and assign to {current.args['assignee']}"
                )
                compacted.append(current)
                i += 2
                continue
            compacted.append(current)
            i += 1
        return compacted

    def _populate_last_task_dependencies(self, steps: List[ActionStep]) -> List[ActionStep]:
        """Attach dependency edges for steps that reference LAST_TASK_REF."""
        prior_context_step_id: Optional[str] = None
        for step in steps:
            merged_deps: List[str] = []
            seen: set[str] = set()
            for dep in step.depends_on or []:
                token = str(dep or "").strip()
                lowered = token.lower()
                if not token or lowered in seen:
                    continue
                seen.add(lowered)
                merged_deps.append(token)

            external_id = str((step.args or {}).get("external_id") or "").strip()
            if external_id == LAST_TASK_REF and prior_context_step_id:
                lowered = prior_context_step_id.lower()
                if lowered not in seen:
                    merged_deps.append(prior_context_step_id)
            step.depends_on = merged_deps
            prior_context_step_id = step.step_id
        return steps

    def _normalize_target(self, raw_target: str) -> Optional[str]:
        target = normalize_text(raw_target)
        if not target:
            return None
        lowered = target.lower()
        if lowered in {"it", "this task"}:
            return None
        try:
            return normalize_external_id(target)
        except Exception:
            return None

    def _normalize_status(self, raw_status: str) -> Optional[str]:
        mapped = resolve_enum_alias(str(raw_status or ""), STATUS_CANONICAL_MAP)
        if mapped in {"todo", "in_progress", "done"}:
            return mapped
        return None

    def _normalize_task_title(self, raw_title: str) -> str:
        title = normalize_text(raw_title)
        title = title.strip().strip(",.;:-").strip()
        lowered = title.lower()
        if lowered.startswith("for "):
            title = title[4:].strip()
        return title

    def _mapping_score(
        self,
        *,
        steps: Sequence[ActionStep],
        visible_tool_names: set[str],
        visible_capabilities: set[str],
        connected_integrations: set[str],
    ) -> Tuple[float, List[str]]:
        if not steps:
            return 0.0, []
        missing_tools: List[str] = []
        for step in steps:
            capability = str(step.capability or "").strip().lower()
            tool_name = str(step.tool_name or "").strip()
            if capability:
                if capability not in visible_capabilities:
                    missing_tools.append(f"capability:{capability}")
                continue
            if tool_name and tool_name not in visible_tool_names:
                missing_tools.append(tool_name)
            if not tool_name and not capability:
                missing_tools.append(f"step:{step.step_id}")
        if missing_tools:
            return 0.0, sorted(set(missing_tools))
        return 1.0, []

    def _confidence_score(
        self,
        *,
        text: str,
        matches: Sequence[_ActionMatch],
        steps: Sequence[ActionStep],
        unresolved_targets: int,
        missing_required: int,
        mapping_score: float,
        deterministic_confidence: float,
    ) -> float:
        message_len = max(1, len(text))
        covered = sum(max(0, item.end - item.start) for item in matches)
        coverage_score = min(1.0, covered / float(message_len))

        required_score = 1.0
        total_required = 0
        missing = 0
        for step in steps:
            action_type = str(step.action_type or "").strip().lower()
            if action_type == "create_work_item":
                total_required += 2
                if not str(step.args.get("title") or "").strip():
                    missing += 1
                if not str(step.args.get("assignee") or "").strip():
                    missing += 1
            elif action_type == "assign_work_item":
                total_required += 2
                if not str(step.args.get("external_id") or "").strip():
                    missing += 1
                if not str(step.args.get("assignee") or "").strip():
                    missing += 1
            elif action_type == "update_status":
                total_required += 2
                if not str(step.args.get("external_id") or "").strip():
                    missing += 1
                if not str(step.args.get("status") or "").strip():
                    missing += 1
            elif action_type == "add_comment":
                total_required += 2
                if not str(step.args.get("external_id") or "").strip():
                    missing += 1
                if not str(step.args.get("comment") or "").strip():
                    missing += 1
        if total_required > 0:
            required_score = max(0.0, 1.0 - (missing / float(total_required)))

        roundtrip_score = self._roundtrip_score(text=text, steps=steps)
        ambiguity_penalty = min(
            0.6,
            (0.15 * float(unresolved_targets)) + (0.08 * float(missing_required)),
        )

        score = (
            0.20 * coverage_score
            + 0.20 * required_score
            + 0.20 * mapping_score
            + 0.20 * roundtrip_score
            + 0.20 * max(0.0, min(1.0, float(deterministic_confidence)))
            - ambiguity_penalty
        )
        return score

    def _roundtrip_score(self, *, text: str, steps: Sequence[ActionStep]) -> float:
        if not steps:
            return 0.0
        synthesized = " ".join(step.summary for step in steps if step.summary).strip().lower()
        if not synthesized:
            return 0.0
        input_tokens = set(ACTION_TOKEN_PATTERN.findall(text.lower()))
        output_tokens = set(ACTION_TOKEN_PATTERN.findall(synthesized))
        if not input_tokens or not output_tokens:
            return 0.0
        overlap = len(input_tokens.intersection(output_tokens))
        return max(0.0, min(1.0, overlap / float(max(1, len(output_tokens)))))
