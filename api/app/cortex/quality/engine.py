"""Shared quality pipeline for Cortex tool arguments."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import ValidationError

from app.core.config import settings
from app.cortex.read_source.resolver import ReadSourceResolver
from app.cortex.quality.assessor import QualityAssessorAdapter
from app.cortex.quality.heuristics import (
    appears_explicit_in_user_text,
    sanitize_args,
)
from app.cortex.quality.models import OperationProfile, QualityDecision, QualityIssue
from app.cortex.quality.policy import resolve_field_policy
from app.cortex.tools.base import CortexTool, IdempotencyClass
from app.cortex.tools.contracts import canonical_aliases_for_tool, canonicalize_tool_args


_WRITE_BLOCKING_CODES = {
    "missing_required",
    "invalid_type",
    "constraint_violation",
    "too_short",
    "semantic_vague",
    "semantic_procedural",
    "semantic_low_confidence",
    "semantic_unavailable",
    "repair_failed",
}

_READ_BLOCKING_CODES = {
    "missing_required",
    "invalid_type",
    "constraint_violation",
    "read_ambiguous_scope",
    "read_conflicting_filters",
    "read_source_unresolved",
}


class QualityEngine:
    """Operation-profile-aware argument quality evaluator."""

    def __init__(self, *, proposal_service: Any) -> None:
        self.assessor = QualityAssessorAdapter(proposal_service=proposal_service)
        self.read_source_resolver = ReadSourceResolver()

    async def evaluate(
        self,
        *,
        executor: Any,
        tool: CortexTool,
        args: Dict[str, Any],
        execution_context: Dict[str, Any],
        operation_profile: Optional[OperationProfile] = None,
    ) -> QualityDecision:
        profile = operation_profile or self._profile_from_tool(tool)
        normalized = self._normalize_args_for_tool(tool=tool, args=args)
        tool_name = tool.normalized_spec().name

        issues: List[QualityIssue] = []
        missing_fields: List[str] = []
        prevalidated_fields: Set[str] = set()

        schema_issues, schema_missing = self._validate_schema(
            tool=tool,
            args=normalized,
            operation_profile=profile,
        )
        issues.extend(schema_issues)
        missing_fields.extend(schema_missing)

        if profile == OperationProfile.READ_ONLY:
            read_source_decision = self.read_source_resolver.resolve(
                tool_name=tool_name,
                args=normalized,
                execution_context=execution_context,
            )
            read_source_map = execution_context.get("quality_read_source")
            if not isinstance(read_source_map, dict):
                read_source_map = {}
            read_source_map[str(tool_name)] = {
                "requested_mode": read_source_decision.requested_mode,
                "effective_mode": read_source_decision.effective_mode,
                "source": read_source_decision.source,
                "reason": read_source_decision.reason,
            }
            execution_context["quality_read_source"] = read_source_map
            if read_source_decision.blocking and read_source_decision.issue_code:
                issues.append(
                    QualityIssue(
                        field="read_source",
                        code=read_source_decision.issue_code,
                        message=read_source_decision.reason or "Read source could not be resolved.",
                        retryable=False,
                        details={"tool_name": tool_name},
                    )
                )
            blocking = self._has_blocking_issue(issues=issues, operation_profile=profile)
            return QualityDecision(
                status="clarification_required" if blocking else "accepted",
                normalized_args=normalized,
                issues=issues,
                prevalidated_fields=sorted(prevalidated_fields),
                missing_fields=sorted(set(missing_fields)),
                blocking=blocking,
            )

        write_issues, write_prevalidated, write_missing, effective_args = await self._evaluate_write_quality(
            executor=executor,
            tool=tool,
            args=normalized,
            execution_context=execution_context,
        )
        issues.extend(write_issues)
        missing_fields.extend(write_missing)
        prevalidated_fields.update(write_prevalidated)

        blocking = self._has_blocking_issue(issues=issues, operation_profile=profile)
        return QualityDecision(
            status="clarification_required" if blocking else "accepted",
            normalized_args=effective_args,
            issues=issues,
            prevalidated_fields=sorted(prevalidated_fields),
            missing_fields=sorted(set(missing_fields)),
            blocking=blocking,
        )

    async def _evaluate_write_quality(
        self,
        *,
        executor: Any,
        tool: CortexTool,
        args: Dict[str, Any],
        execution_context: Dict[str, Any],
    ) -> Tuple[List[QualityIssue], Set[str], List[str], Dict[str, Any]]:
        tool_name = tool.normalized_spec().name
        issues: List[QualityIssue] = []
        missing: List[str] = []
        prevalidated: Set[str] = set()
        insufficient: Set[str] = set()
        required_fields = {item.lower() for item in self._tool_required_arg_fields(tool)}
        confirmed_tokens = self._quality_semantic_skip_tokens(execution_context)
        effective_args: Dict[str, Any] = dict(args)

        for field_name, value in list(args.items()):
            policy = resolve_field_policy(
                tool_name=tool_name,
                field_name=field_name,
                operation_profile=OperationProfile.NON_IDEMPOTENT_WRITE,
            )
            text = str(value).strip() if isinstance(value, str) else str(value)
            min_chars = int(policy.get("min_chars") or 1)
            if len(text) < max(1, min_chars):
                if str(field_name).lower() not in required_fields:
                    # Optional fields should never block write execution.
                    effective_args.pop(field_name, None)
                    print(
                        "Cortex quality optional write field dropped (too_short): "
                        f"tool={tool_name} field={field_name}"
                    )
                    continue
                insufficient.add(field_name)
                issues.append(
                    QualityIssue(
                        field=field_name,
                        code="too_short",
                        message=f"{tool_name}.{field_name} must be at least {max(1, min_chars)} characters.",
                        retryable=False,
                        details={"tool_name": tool_name, "min_chars": max(1, min_chars)},
                    )
                )
                continue

            semantic_when = str(policy.get("semantic_when") or "never").strip().lower()
            if not bool(settings.cortex_quality_semantic_enabled):
                semantic_when = "never"
            if semantic_when == "never":
                prevalidated.add(field_name.lower())
                prevalidated.add(f"{tool_name}.{field_name}".lower())
                continue
            if semantic_when == "inferred_only":
                if self._skip_semantic_check(
                    tool_name=tool_name,
                    field_name=field_name,
                    value=value,
                    confirmed_tokens=confirmed_tokens,
                    execution_context=execution_context,
                ):
                    prevalidated.add(field_name.lower())
                    prevalidated.add(f"{tool_name}.{field_name}".lower())
                    continue

            assessment = await self.assessor.assess(
                executor=executor,
                tool_name=tool_name,
                field_name=field_name,
                current_value=text,
                policy=policy,
                context=execution_context,
            )
            if assessment is None:
                fail_mode = str(policy.get("assessor_fail_mode") or "allow_if_deterministic_pass").strip().lower()
                if fail_mode == "fail_closed":
                    if str(field_name).lower() not in required_fields:
                        effective_args.pop(field_name, None)
                        print(
                            "Cortex quality optional write field dropped (semantic_unavailable): "
                            f"tool={tool_name} field={field_name}"
                        )
                        continue
                    insufficient.add(field_name)
                    issues.append(
                        QualityIssue(
                            field=field_name,
                            code="semantic_unavailable",
                            message=f"{tool_name}.{field_name} could not be semantically validated.",
                            retryable=True,
                            details={"tool_name": tool_name},
                        )
                    )
                continue

            confidence = float(assessment.get("confidence") or 0.0)
            is_sufficient = bool(assessment.get("is_sufficient"))
            reason = str(assessment.get("reason") or "").strip()
            if (not is_sufficient) or confidence < 0.65:
                if str(field_name).lower() not in required_fields:
                    effective_args.pop(field_name, None)
                    print(
                        "Cortex quality optional write field dropped (semantic_low_confidence): "
                        f"tool={tool_name} field={field_name}"
                    )
                    continue
                insufficient.add(field_name)
                issues.append(
                    QualityIssue(
                        field=field_name,
                        code="semantic_low_confidence",
                        message=reason or f"{tool_name}.{field_name} is not specific enough.",
                        retryable=False,
                        details={"tool_name": tool_name, "confidence": confidence},
                    )
                )
                continue
            prevalidated.add(field_name.lower())
            prevalidated.add(f"{tool_name}.{field_name}".lower())

        if not insufficient:
            return issues, prevalidated, missing, effective_args

        repaired = await self._attempt_repair(
            executor=executor,
            tool=tool,
            args=effective_args,
            insufficient_fields=sorted(insufficient),
            execution_context=execution_context,
        )
        if repaired is None:
            for field_name in sorted(insufficient):
                token = f"{tool_name}.{field_name}"
                if token not in missing:
                    missing.append(token)
            return issues, prevalidated, missing, effective_args

        unresolved: List[str] = []
        for field_name in sorted(insufficient):
            policy = resolve_field_policy(
                tool_name=tool_name,
                field_name=field_name,
                operation_profile=OperationProfile.NON_IDEMPOTENT_WRITE,
            )
            value = repaired.get(field_name)
            text = str(value).strip() if isinstance(value, str) else str(value or "")
            min_chars = int(policy.get("min_chars") or 1)
            if len(text) < max(1, min_chars):
                unresolved.append(field_name)
                continue
            semantic_when = str(policy.get("semantic_when") or "never").strip().lower()
            if not bool(settings.cortex_quality_semantic_enabled):
                semantic_when = "never"
            if semantic_when == "never":
                continue
            if semantic_when == "inferred_only" and self._skip_semantic_check(
                tool_name=tool_name,
                field_name=field_name,
                value=value,
                confirmed_tokens=confirmed_tokens,
                execution_context=execution_context,
            ):
                continue
            assessed = await self.assessor.assess(
                executor=executor,
                tool_name=tool_name,
                field_name=field_name,
                current_value=text,
                policy=policy,
                context=execution_context,
            )
            if assessed is None:
                unresolved.append(field_name)
                continue
            if not bool(assessed.get("is_sufficient")):
                unresolved.append(field_name)
                continue
            if float(assessed.get("confidence") or 0.0) < 0.65:
                unresolved.append(field_name)

        if unresolved:
            for field_name in unresolved:
                token = f"{tool_name}.{field_name}"
                if token not in missing:
                    missing.append(token)
            issues.append(
                QualityIssue(
                    field=unresolved[0],
                    code="repair_failed",
                    message="Automatic argument repair could not resolve quality issues.",
                    retryable=False,
                    details={"tool_name": tool_name, "fields": unresolved},
                )
            )
            return issues, prevalidated, missing, effective_args

        filtered_issues: List[QualityIssue] = []
        repaired_fields = set(insufficient)
        for item in issues:
            if item.field in repaired_fields and item.code in {
                "too_short",
                "semantic_low_confidence",
                "semantic_vague",
                "semantic_unavailable",
            }:
                continue
            filtered_issues.append(item)
        for field_name in repaired_fields:
            prevalidated.add(field_name.lower())
            prevalidated.add(f"{tool_name}.{field_name}".lower())
        return filtered_issues, prevalidated, missing, repaired

    async def _attempt_repair(
        self,
        *,
        executor: Any,
        tool: CortexTool,
        args: Dict[str, Any],
        insufficient_fields: List[str],
        execution_context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if not self._consume_repair_budget(execution_context):
            print(
                "Cortex quality repair skipped: budget_exhausted "
                f"limit={int(settings.cortex_max_repair_attempts_per_turn or 1)}"
            )
            return None
        tool_name = tool.normalized_spec().name
        allowed_fields = [name for name in self._tool_allowed_arg_fields(tool) if name != "operation_id"]
        if not allowed_fields:
            return None

        tool_contracts = [
            {
                "name": tool_name,
                "fields": allowed_fields,
                "required_fields": self._tool_required_arg_fields(tool),
                "aliases": canonical_aliases_for_tool(tool_name),
            }
        ]
        repaired = await self.assessor.repair(
            executor=executor,
            tool_name=tool_name,
            current_args=args,
            tool_contracts=tool_contracts,
            context=execution_context,
        )
        if not isinstance(repaired, dict):
            return None
        merged = dict(args)
        for key, value in repaired.items():
            if key not in allowed_fields:
                continue
            if value is None:
                continue
            if isinstance(value, str):
                compact = value.strip()
                if not compact:
                    continue
                merged[key] = compact
                continue
            merged[key] = value
        merged = self._normalize_args_for_tool(tool=tool, args=merged)
        for field_name in insufficient_fields:
            if not str(merged.get(field_name) or "").strip():
                return None
        return merged

    def _consume_repair_budget(self, execution_context: Dict[str, Any]) -> bool:
        limit = max(0, int(settings.cortex_max_repair_attempts_per_turn or 1))
        if limit == 0:
            return False
        used = int(execution_context.get("repair_attempts_used") or 0)
        if used >= limit:
            return False
        execution_context["repair_attempts_used"] = used + 1
        return True

    def _normalize_args_for_tool(self, *, tool: CortexTool, args: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = tool.normalized_spec().name
        allowed_fields = set(self._tool_allowed_arg_fields(tool))
        canonical = canonicalize_tool_args(
            tool_name=tool_name,
            args=args if isinstance(args, dict) else {},
            allowed_fields=allowed_fields or None,
        )
        return sanitize_args(canonical)

    def _tool_allowed_arg_fields(self, tool: CortexTool) -> List[str]:
        input_model = getattr(tool, "input_model", None)
        if input_model is not None and hasattr(input_model, "model_fields"):
            return [str(name) for name in getattr(input_model, "model_fields", {}).keys()]
        return []

    def _tool_required_arg_fields(self, tool: CortexTool) -> List[str]:
        input_model = getattr(tool, "input_model", None)
        if input_model is None or not hasattr(input_model, "model_fields"):
            return []
        required: List[str] = []
        for field_name, field in getattr(input_model, "model_fields", {}).items():
            is_required_attr = getattr(field, "is_required", None)
            is_required = bool(is_required_attr()) if callable(is_required_attr) else bool(is_required_attr)
            if is_required and str(field_name) != "operation_id":
                required.append(str(field_name))
        return required

    def _quality_semantic_skip_tokens(self, execution_context: Dict[str, Any]) -> Set[str]:
        values: List[Any] = []
        raw_user = execution_context.get("quality_user_confirmed_fields")
        if isinstance(raw_user, list):
            values.extend(raw_user)
        raw_prevalidated = execution_context.get("quality_prevalidated_fields")
        if isinstance(raw_prevalidated, list):
            values.extend(raw_prevalidated)
        tokens: Set[str] = set()
        for item in values:
            token = str(item or "").strip().lower()
            if not token:
                continue
            tokens.add(token)
        return tokens

    def _skip_semantic_check(
        self,
        *,
        tool_name: str,
        field_name: str,
        value: Any,
        confirmed_tokens: Set[str],
        execution_context: Dict[str, Any],
    ) -> bool:
        field_token = str(field_name or "").strip().lower()
        scoped_token = f"{str(tool_name or '').strip().lower()}.{field_token}"
        if field_token and (field_token in confirmed_tokens or scoped_token in confirmed_tokens):
            return True
        return appears_explicit_in_user_text(value=value, context=execution_context)

    def _profile_from_tool(self, tool: CortexTool) -> OperationProfile:
        idem = tool.normalized_spec().idempotency
        if idem == IdempotencyClass.READ_ONLY.value:
            return OperationProfile.READ_ONLY
        if idem == IdempotencyClass.NON_IDEMPOTENT.value:
            return OperationProfile.NON_IDEMPOTENT_WRITE
        return OperationProfile.IDEMPOTENT_WRITE

    def _validate_schema(
        self,
        *,
        tool: CortexTool,
        args: Dict[str, Any],
        operation_profile: OperationProfile,
    ) -> Tuple[List[QualityIssue], List[str]]:
        input_model = getattr(tool, "input_model", None)
        if input_model is None or not hasattr(input_model, "model_validate"):
            return [], []

        candidate_args = dict(args)
        allowed_fields = set(self._tool_allowed_arg_fields(tool))
        if "operation_id" in allowed_fields and not str(candidate_args.get("operation_id") or "").strip():
            candidate_args["operation_id"] = "auto-quality-validation"

        tool_name = tool.normalized_spec().name
        try:
            input_model.model_validate(candidate_args)
            return [], []
        except ValidationError as exc:
            issues: List[QualityIssue] = []
            missing: List[str] = []
            for error in exc.errors():
                err_type = str(error.get("type") or "").strip().lower()
                loc = error.get("loc") or ()
                field_name = str(loc[0]).strip() if isinstance(loc, (list, tuple)) and loc else ""
                if field_name == "operation_id":
                    continue
                message = str(error.get("msg") or "").strip() or "Invalid value."
                token = f"{tool_name}.{field_name}" if field_name else tool_name
                if err_type == "extra_forbidden":
                    continue
                if err_type == "missing" and field_name:
                    missing.append(token)
                    issues.append(
                        QualityIssue(
                            field=field_name,
                            code="missing_required",
                            message=f"{token} is required.",
                            retryable=False,
                            details={"tool_name": tool_name},
                        )
                    )
                    continue
                code = "invalid_type" if "type" in err_type else "constraint_violation"
                issues.append(
                    QualityIssue(
                        field=field_name or "input",
                        code=code,
                        message=f"{token}: {message}",
                        retryable=False,
                        details={
                            "tool_name": tool_name,
                            "operation_profile": operation_profile.value,
                            "validation_type": err_type,
                        },
                    )
                )
            return issues, missing

    def _has_blocking_issue(
        self,
        *,
        issues: List[QualityIssue],
        operation_profile: OperationProfile,
    ) -> bool:
        if not issues:
            return False
        blocking_codes = _READ_BLOCKING_CODES if operation_profile == OperationProfile.READ_ONLY else _WRITE_BLOCKING_CODES
        for item in issues:
            if item.code in blocking_codes:
                return True
        return False
