"""Project-scoped capability materialization and provider selection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from app.domain.capabilities.provider_selection_policy import (
    connected_pm_providers_from_project_context as _connected_pm_providers_from_project_context,
    is_pm_provider as _is_pm_provider,
    is_provider_ambiguous,
    normalize_provider as _normalize_provider,
    resolve_active_provider as _resolve_active_provider,
    resolve_materialized_pm_providers,
)


@dataclass(frozen=True)
class CapabilityContext:
    """Materialized capability view for one project context."""

    project_id: str
    connected_pm_providers: tuple[str, ...]
    active_provider: Optional[str]
    materialized_pm_providers: tuple[str, ...]
    provider_ambiguous: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "connected_pm_providers": list(self.connected_pm_providers),
            "active_provider": self.active_provider,
            "materialized_pm_providers": list(self.materialized_pm_providers),
            "provider_ambiguous": self.provider_ambiguous,
        }


def connected_pm_providers_from_project_context(project_context: Dict[str, Any]) -> List[str]:
    """Extract connected PM providers from integrations config."""
    return _connected_pm_providers_from_project_context(project_context)


def resolve_active_provider(
    *,
    project_context: Dict[str, Any],
    connected_pm_providers: Sequence[str],
) -> Optional[str]:
    """Return active provider only if currently connected."""
    return _resolve_active_provider(
        project_context=project_context,
        connected_pm_providers=connected_pm_providers,
    )


def build_capability_context(
    *,
    project_context: Dict[str, Any],
) -> CapabilityContext:
    """Build deterministic project-scoped provider materialization context."""
    project_id = str((project_context or {}).get("project_id") or "").strip()
    connected_pm_providers = connected_pm_providers_from_project_context(project_context)
    active_provider = resolve_active_provider(
        project_context=project_context,
        connected_pm_providers=connected_pm_providers,
    )

    materialized_pm_providers = resolve_materialized_pm_providers(
        connected_pm_providers=connected_pm_providers,
        active_provider=active_provider,
    )
    provider_ambiguous = is_provider_ambiguous(
        connected_pm_providers=connected_pm_providers,
        active_provider=active_provider,
    )
    return CapabilityContext(
        project_id=project_id,
        connected_pm_providers=tuple(connected_pm_providers),
        active_provider=active_provider,
        materialized_pm_providers=materialized_pm_providers,
        provider_ambiguous=provider_ambiguous,
    )


def materialize_visible_tool_descriptors(
    *,
    visible_tools: Iterable[Dict[str, Any]],
    capability_context: CapabilityContext,
) -> List[Dict[str, Any]]:
    """
    Filter visible tools to project-materialized PM providers while preserving non-PM tools.

    Non-PM providers (e.g. Brain utilities) remain visible.
    """
    if not isinstance(capability_context, CapabilityContext):
        return [tool for tool in visible_tools if isinstance(tool, dict)]

    materialized_pm = set(capability_context.materialized_pm_providers)
    filtered: List[Dict[str, Any]] = []
    for item in visible_tools:
        if not isinstance(item, dict):
            continue
        provider = _normalize_provider(item.get("provider"))
        if not _is_pm_provider(provider):
            filtered.append(item)
            continue
        if not materialized_pm:
            # Ambiguous PM provider state: hide PM tools until provider is explicit.
            continue
        if provider in materialized_pm:
            filtered.append(item)
    return filtered


def resolve_cadence_task_provider(*, project_context: Dict[str, Any]) -> Optional[str]:
    """Resolve provider pinned at Cadence session start."""
    capability_context = build_capability_context(project_context=project_context)
    if capability_context.materialized_pm_providers:
        return capability_context.materialized_pm_providers[0]
    return None
