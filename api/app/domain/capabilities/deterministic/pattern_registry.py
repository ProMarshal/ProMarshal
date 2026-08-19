"""Canonical deterministic action pattern registry."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Dict, Iterable, List, Optional, Tuple

from app.domain.capabilities.deterministic.models import ActionPatternSpec


@dataclass(frozen=True)
class RegisteredActionPattern:
    """Pattern registration record with provider ownership metadata."""

    provider: str
    capability_id: str
    bundle_id: str
    spec: ActionPatternSpec


class ActionPatternRegistry:
    """Registry for deterministic planner pattern specs."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._items: Dict[Tuple[str, str, str], RegisteredActionPattern] = {}

    def reset(self) -> None:
        with self._lock:
            self._items.clear()

    def register(
        self,
        spec: ActionPatternSpec,
        *,
        provider: str,
        bundle_id: str = "",
    ) -> None:
        normalized_provider = str(provider or "").strip().lower()
        capability_id = spec.normalized_capability_id()
        action_type = spec.normalized_action_type()
        if not normalized_provider:
            raise ValueError("provider is required for pattern registration")
        if not capability_id:
            raise ValueError("capability_id is required for pattern registration")
        if not action_type:
            raise ValueError("action_type is required for pattern registration")
        key = (normalized_provider, capability_id, action_type)
        record = RegisteredActionPattern(
            provider=normalized_provider,
            capability_id=capability_id,
            bundle_id=str(bundle_id or "").strip().lower(),
            spec=spec,
        )
        with self._lock:
            existing = self._items.get(key)
            if existing is not None:
                existing_pattern = str(existing.spec.regex.pattern)
                new_pattern = str(spec.regex.pattern)
                if existing_pattern != new_pattern:
                    raise ValueError(
                        "duplicate_action_pattern:"
                        f"provider={normalized_provider},capability_id={capability_id},action_type={action_type}"
                    )
                return
            self._items[key] = record

    def register_many(
        self,
        specs: Iterable[ActionPatternSpec],
        *,
        provider: str,
        bundle_id: str = "",
    ) -> None:
        for spec in specs:
            self.register(spec, provider=provider, bundle_id=bundle_id)

    def list(
        self,
        *,
        provider: Optional[str] = None,
        capability_ids: Optional[Iterable[str]] = None,
    ) -> List[ActionPatternSpec]:
        normalized_provider = str(provider or "").strip().lower()
        capability_set = {
            str(item or "").strip().lower()
            for item in (capability_ids or [])
            if str(item or "").strip()
        }
        with self._lock:
            items = sorted(
                self._items.items(),
                key=lambda item: (item[0][0], item[0][1], item[0][2]),
            )
            selected: List[ActionPatternSpec] = []
            for (item_provider, item_capability, _item_action), record in items:
                if normalized_provider and item_provider != normalized_provider:
                    continue
                if capability_set and item_capability not in capability_set:
                    continue
                selected.append(record.spec)
            return selected


pattern_registry = ActionPatternRegistry()


def list_action_patterns(
    *,
    provider: Optional[str] = None,
    capability_ids: Optional[Iterable[str]] = None,
) -> List[ActionPatternSpec]:
    """Convenience access for planner and compatibility modules."""
    return pattern_registry.list(provider=provider, capability_ids=capability_ids)

