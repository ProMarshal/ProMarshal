"""Shared structured LLM telemetry contracts and adapter helpers."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional, Protocol

from app.llm.gateway import LLMErrorClass


class LLMTelemetryEventType(str, Enum):
    """Canonical telemetry event types for cross-feature LLM operations."""

    SUCCESS = "success"
    FAILURE = "failure"
    RETRY = "retry"
    FALLBACK = "fallback"
    PROVIDER_SWITCH = "provider_switch"
    DEGRADATION_CHANGE = "degradation_change"
    AUTH_FAILURE = "auth_failure"
    AUTH_QUARANTINE = "auth_quarantine"


class LLMTelemetrySeverity(str, Enum):
    """Severity levels aligned with rollout monitoring requirements."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(frozen=True)
class LLMTelemetryEvent:
    """Structured telemetry payload for shared LLM runtime observability."""

    event_type: LLMTelemetryEventType
    severity: LLMTelemetrySeverity
    feature: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    request_id: Optional[str] = None
    project_id: Optional[str] = None
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    fallback_provider: Optional[str] = None
    fallback_model: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 0
    degraded: bool = False
    latency_ms: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    error_class: Optional[LLMErrorClass] = None
    error_code: Optional[str] = None
    http_status: Optional[int] = None
    message: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


class LLMTelemetryAdapter(Protocol):
    """Protocol for feature-agnostic LLM telemetry adapters."""

    def emit(self, event: LLMTelemetryEvent) -> None:
        """Emit one structured telemetry event."""


def telemetry_event_to_dict(event: LLMTelemetryEvent) -> dict[str, Any]:
    """Convert an event to a JSON-safe dictionary."""
    payload = asdict(event)
    payload["event_type"] = event.event_type.value
    payload["severity"] = event.severity.value
    payload["timestamp"] = event.timestamp.isoformat()
    if event.error_class is not None:
        payload["error_class"] = event.error_class.value
    return payload


class LoggingLLMTelemetryAdapter:
    """Default adapter that emits structured JSON lines using stdlib logging."""

    def __init__(self, logger: Optional[logging.Logger] = None) -> None:
        self.logger = logger or logging.getLogger("llm.telemetry")

    def emit(self, event: LLMTelemetryEvent) -> None:
        payload = telemetry_event_to_dict(event)
        message = json.dumps(payload, default=str, separators=(",", ":"))
        if event.severity == LLMTelemetrySeverity.INFO:
            self.logger.info(message)
        elif event.severity == LLMTelemetrySeverity.WARNING:
            self.logger.warning(message)
        elif event.severity == LLMTelemetrySeverity.ERROR:
            self.logger.error(message)
        else:
            self.logger.critical(message)
