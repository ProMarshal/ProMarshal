"""Shared LLM gateway contract for Cortex-first adoption across features."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping, Optional, Protocol, Sequence


class LLMMessageRole(str, Enum):
    """Supported chat message roles."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class LLMErrorClass(str, Enum):
    """Normalized, provider-agnostic error categories."""

    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    NETWORK = "network"
    INVALID_REQUEST = "invalid_request"
    PROVIDER = "provider"
    INTERNAL = "internal"
    UNAVAILABLE = "unavailable"


class LLMToolType(str, Enum):
    """Supported tool declaration types for gateway-native tool calls."""

    FUNCTION = "function"


class LLMHealthEventType(str, Enum):
    """Health events emitted by the gateway/provider layer."""

    SUCCESS = "success"
    FAILURE = "failure"
    RETRY = "retry"
    PROVIDER_SWITCH = "provider_switch"
    COOLDOWN_ENTER = "cooldown_enter"
    COOLDOWN_EXIT = "cooldown_exit"
    AUTH_QUARANTINE = "auth_quarantine"
    AUTH_RESTORE = "auth_restore"
    DEGRADATION_CHANGE = "degradation_change"


@dataclass(frozen=True)
class LLMMessage:
    """A normalized input message for provider-agnostic requests."""

    role: LLMMessageRole
    content: str
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Sequence["LLMToolCall"] = field(default_factory=tuple)


@dataclass(frozen=True)
class LLMUsage:
    """Token usage and request accounting details."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class LLMToolSpec:
    """Normalized tool declaration for provider-agnostic gateway requests."""

    name: str
    description: str = ""
    json_schema: Mapping[str, Any] = field(default_factory=dict)
    type: LLMToolType = LLMToolType.FUNCTION


@dataclass(frozen=True)
class LLMToolCall:
    """Normalized tool call emitted by gateway responses."""

    call_id: str
    name: str
    arguments_json: str = "{}"
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMErrorEnvelope:
    """Standardized error shape expected by all gateway callers."""

    ok: bool
    error_code: str
    error_class: LLMErrorClass
    retryable: bool
    http_status: Optional[int]
    user_message: str
    provider: Optional[str] = None
    model: Optional[str] = None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMGatewayRequest:
    """Gateway request contract used by feature runtimes."""

    messages: Sequence[LLMMessage]
    provider: Optional[str] = None
    model: Optional[str] = None
    fallback_provider: Optional[str] = None
    fallback_model: Optional[str] = None
    max_tokens: int = 2048
    temperature: float = 0.3
    retries: int = 3
    timeout_seconds: int = 60
    tools: Sequence[LLMToolSpec] = field(default_factory=tuple)
    tool_choice: Optional[str] = None
    parallel_tool_calls: Optional[bool] = None
    response_format: Optional[Mapping[str, Any]] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMGatewayResponse:
    """Gateway response contract with normalized success/error envelope."""

    ok: bool
    text: str = ""
    provider: Optional[str] = None
    model: Optional[str] = None
    usage: LLMUsage = field(default_factory=LLMUsage)
    retry_count: int = 0
    degraded: bool = False
    finish_reason: Optional[str] = None
    tool_calls: Sequence[LLMToolCall] = field(default_factory=tuple)
    raw_message: Mapping[str, Any] = field(default_factory=dict)
    error: Optional[LLMErrorEnvelope] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMEmbeddingRequest:
    """Gateway request contract for embedding generation."""

    inputs: Sequence[str]
    provider: Optional[str] = None
    model: Optional[str] = None
    fallback_provider: Optional[str] = None
    fallback_model: Optional[str] = None
    retries: int = 2
    timeout_seconds: int = 30
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMEmbeddingResponse:
    """Gateway response contract for embedding generation."""

    ok: bool
    vectors: Sequence[Sequence[float]] = field(default_factory=tuple)
    provider: Optional[str] = None
    model: Optional[str] = None
    retry_count: int = 0
    usage: LLMUsage = field(default_factory=LLMUsage)
    error: Optional[LLMErrorEnvelope] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMHealthEvent:
    """Health callback event emitted by gateway implementations."""

    event_type: LLMHealthEventType
    provider: str
    model: Optional[str] = None
    project_id: Optional[str] = None
    reason: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    details: Mapping[str, Any] = field(default_factory=dict)


LLMHealthCallback = Callable[[LLMHealthEvent], Optional[Awaitable[None]]]


class LLMGateway(Protocol):
    """Shared async LLM gateway protocol for all feature-level callers."""

    async def generate(
        self,
        request: LLMGatewayRequest,
        *,
        on_health_event: Optional[LLMHealthCallback] = None,
    ) -> LLMGatewayResponse:
        """Execute provider-routed completion and return normalized response."""

    async def embed(
        self,
        request: LLMEmbeddingRequest,
        *,
        on_health_event: Optional[LLMHealthCallback] = None,
    ) -> LLMEmbeddingResponse:
        """Execute provider-routed embedding generation and return normalized vectors."""
