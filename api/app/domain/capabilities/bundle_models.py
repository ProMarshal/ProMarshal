"""Capability bundle contracts for plugin-style registration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from app.cadence.action_adapters.base import CadenceActionAdapter
from app.cortex.tools.base import CortexTool
from app.cortex.tools.contracts import ToolArgContract


@dataclass(frozen=True)
class ResolverAdapterRegistration:
    """Resolver adapter declaration captured during bundle install."""

    provider: str
    capability_id: str
    adapter: Any


@dataclass(frozen=True)
class CapabilityBundle:
    """Single install unit for capability assets."""

    bundle_id: str
    capability_id: str
    provider: str
    cortex_tool_factories: List[Callable[[], CortexTool]]
    arg_contracts: dict[str, ToolArgContract] = field(default_factory=dict)
    planner_patterns: List[Any] = field(default_factory=list)
    resolver_adapters: List[ResolverAdapterRegistration] = field(default_factory=list)
    cadence_adapters: List[CadenceActionAdapter] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def normalized_bundle_id(self) -> str:
        return str(self.bundle_id or "").strip().lower()

    def normalized_provider(self) -> str:
        return str(self.provider or "").strip().lower()

    def normalized_capability_id(self) -> str:
        return str(self.capability_id or "").strip().lower()

    def operation_type(self) -> Optional[str]:
        value = str((self.metadata or {}).get("operation_type") or "").strip().lower()
        if value in {"read", "write"}:
            return value
        return None
