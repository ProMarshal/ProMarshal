"""Gateway-backed recommendation text-signal extractor with deterministic fallback."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ValidationError, confloat

from app.llm import (
    LLMGateway,
    LLMGatewayRequest,
    LLMMessage,
    LLMMessageRole,
    LLMTelemetryEvent,
    LLMTelemetryEventType,
    LLMTelemetrySeverity,
    LoggingLLMTelemetryAdapter,
    get_shared_llm_gateway,
)

logger = logging.getLogger("app.projects.recommendations.recommendation_text_extractor")


class ExtractedSignal(BaseModel):
    task_key: str
    risk_summary: str
    action_hint: str
    confidence: confloat(ge=0.0, le=1.0)


@dataclass(frozen=True)
class _TaskSignalInput:
    task_key: str
    title: str
    status: str
    priority: str
    note: str


def _normalize_task_input(task: Dict[str, Any]) -> Optional[_TaskSignalInput]:
    task_key = str(task.get("external_id") or task.get("task_key") or "").strip()
    if not task_key:
        return None
    title = str(task.get("title") or "").strip()
    status = str(task.get("status") or "").strip().lower()
    priority = str(task.get("priority") or "").strip().lower()
    note = str(task.get("last_comment") or task.get("comment") or task.get("health_reason") or "").strip()
    return _TaskSignalInput(
        task_key=task_key,
        title=title,
        status=status,
        priority=priority,
        note=note,
    )


def _deterministic_signal(task: _TaskSignalInput) -> Dict[str, Any]:
    risk = "Needs PM review."
    action = "Confirm owner update and next milestone."
    note_lc = task.note.lower()
    if "blocked" in note_lc or "blocker" in note_lc or "waiting" in note_lc:
        risk = "Work may be blocked based on latest update."
        action = "Escalate blocker and set unblock owner/date."
    elif "review" in task.status:
        risk = "Task appears to be waiting in review."
        action = "Nudge reviewer and confirm review SLA."
    elif task.priority in {"high", "highest", "critical"}:
        risk = "High-priority task needs close follow-up."
        action = "Set same-day check-in and confirm ownership."
    return {
        "task_key": task.task_key,
        "risk_summary": risk,
        "action_hint": action,
        "confidence": 0.55,
    }


class RecommendationTextExtractor:
    """
    Uses shared LLM gateway as primary extractor and deterministic fallback otherwise.
    No per-feature retry loops are implemented here.
    """

    def __init__(
        self,
        *,
        gateway: Optional[LLMGateway] = None,
        telemetry: Optional[LoggingLLMTelemetryAdapter] = None,
    ) -> None:
        self._gateway = gateway if gateway is not None else get_shared_llm_gateway()
        self._telemetry = telemetry or LoggingLLMTelemetryAdapter(logger=logging.getLogger("llm.telemetry"))

    @staticmethod
    def _coerce_json_text(raw_text: str) -> str:
        """Conservative normalization before JSON parse.

        Defensive-only normalization:
        - trim outer whitespace
        - strip markdown code fences when present
        """
        text = str(raw_text or "").strip()
        if not text:
            return ""
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3 and lines[-1].strip().startswith("```"):
                # Remove leading fence line (with optional language) and trailing fence.
                inner = "\n".join(lines[1:-1]).strip()
                return inner
        return text

    @staticmethod
    def _strict_response_format() -> Dict[str, Any]:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "pm_recommendation_signals",
                "schema": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "task_key": {"type": "string"},
                            "risk_summary": {"type": "string"},
                            "action_hint": {"type": "string"},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                        "required": ["task_key", "risk_summary", "action_hint", "confidence"],
                    },
                },
                "strict": True,
            },
        }

    async def extract_signals(
        self,
        tasks: List[Dict[str, Any]],
        *,
        project_id: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        normalized = [item for item in (_normalize_task_input(task) for task in tasks or []) if item is not None]
        if not normalized:
            return {}

        if self._gateway is None:
            self._emit_fallback(project_id=project_id, reason="gateway_unavailable")
            return {item.task_key: _deterministic_signal(item) for item in normalized}

        request = LLMGatewayRequest(
            messages=[
                LLMMessage(
                    role=LLMMessageRole.SYSTEM,
                    content=(
                        "Extract recommendation text signals as strict JSON array only. "
                        "Each item: task_key, risk_summary, action_hint, confidence(0..1)."
                    ),
                ),
                LLMMessage(
                    role=LLMMessageRole.USER,
                    content=json.dumps(
                        {
                            "tasks": [
                                {
                                    "task_key": item.task_key,
                                    "title": item.title,
                                    "status": item.status,
                                    "priority": item.priority,
                                    "note": item.note,
                                }
                                for item in normalized[:40]
                            ]
                        },
                        ensure_ascii=True,
                    ),
                ),
            ],
            response_format=self._strict_response_format(),
            metadata={"feature": "pm_recommendation_extractor"},
        )
        response = await self._gateway.generate(request)
        if response.retry_count > 0:
            self._telemetry.emit(
                LLMTelemetryEvent(
                    event_type=LLMTelemetryEventType.RETRY,
                    severity=LLMTelemetrySeverity.INFO,
                    feature="pm_recommendation_extractor",
                    retry_count=int(response.retry_count),
                    project_id=project_id,
                    provider=response.provider,
                    model=response.model,
                )
            )
        if not response.ok or not str(response.text or "").strip():
            self._emit_fallback(project_id=project_id, reason="gateway_error")
            return {item.task_key: _deterministic_signal(item) for item in normalized}

        try:
            normalized_text = self._coerce_json_text(response.text)
            parsed_raw = json.loads(normalized_text)
            if not isinstance(parsed_raw, list):
                raise ValueError("extractor output must be a JSON list")
            parsed = [ExtractedSignal.model_validate(row) for row in parsed_raw]
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            sample = str(response.text or "").strip().replace("\n", " ")[:240]
            logger.warning(
                "recommendation_text_extractor_schema_invalid error=%s raw_sample=%r",
                exc,
                sample,
            )
            self._emit_fallback(project_id=project_id, reason="schema_invalid")
            return {item.task_key: _deterministic_signal(item) for item in normalized}

        return {
            item.task_key: {
                "task_key": item.task_key,
                "risk_summary": item.risk_summary,
                "action_hint": item.action_hint,
                "confidence": float(item.confidence),
            }
            for item in parsed
        }

    def _emit_fallback(self, *, project_id: Optional[str], reason: str) -> None:
        self._telemetry.emit(
            LLMTelemetryEvent(
                event_type=LLMTelemetryEventType.FALLBACK,
                severity=LLMTelemetrySeverity.WARNING,
                feature="pm_recommendation_extractor",
                project_id=project_id,
                message=reason,
            )
        )
