"""Provider selection policy helpers for capability materialization."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence, Set


PM_PROVIDER_HINTS: Set[str] = {
    "jira",
    "linear",
    "asana",
    "clickup",
    "github",
    "gitlab",
}


def normalize_provider(value: Any) -> str:
    """Return normalized provider identifier."""
    return str(value or "").strip().lower()


def is_pm_provider(provider: str) -> bool:
    """Return whether provider should be treated as project-management provider."""
    normalized = normalize_provider(provider)
    if not normalized:
        return False
    if normalized in PM_PROVIDER_HINTS:
        return True
    return normalized.startswith("pm_")


def connected_pm_providers_from_project_context(project_context: Mapping[str, Any]) -> list[str]:
    """Extract connected PM providers from project integrations."""
    integrations = (project_context or {}).get("integrations") or {}
    if not isinstance(integrations, dict):
        return []

    providers: list[str] = []
    for name, cfg in integrations.items():
        provider = normalize_provider(name)
        if not provider or not isinstance(cfg, dict):
            continue
        if str(cfg.get("status") or "").strip().lower() != "connected":
            continue
        if not is_pm_provider(provider):
            continue
        providers.append(provider)
    return sorted(set(providers))


def resolve_active_provider(
    *,
    project_context: Mapping[str, Any],
    connected_pm_providers: Sequence[str],
) -> Optional[str]:
    """Return active provider only when it is currently connected."""
    connected = {
        str(item).strip().lower()
        for item in connected_pm_providers
        if str(item).strip()
    }
    active_provider = normalize_provider((project_context or {}).get("active_provider"))
    if active_provider and active_provider in connected:
        return active_provider
    return None


def resolve_materialized_pm_providers(
    *,
    connected_pm_providers: Sequence[str],
    active_provider: Optional[str],
) -> tuple[str, ...]:
    """Resolve materialized provider set for runtime tool visibility."""
    if active_provider:
        return (active_provider,)
    if len(connected_pm_providers) == 1:
        return (str(connected_pm_providers[0]).strip().lower(),)
    return tuple()


def is_provider_ambiguous(
    *,
    connected_pm_providers: Sequence[str],
    active_provider: Optional[str],
) -> bool:
    """Return whether runtime provider selection requires clarification."""
    return not bool(active_provider) and len(connected_pm_providers) > 1


def resolve_active_provider_after_disconnect(
    *,
    disconnected_provider: str,
    current_active_provider: Optional[str],
    remaining_connected_pm_providers: Sequence[str],
) -> Optional[str]:
    """Apply Contract-M active-provider fallback policy after provider disconnect."""
    disconnected = normalize_provider(disconnected_provider)
    current = normalize_provider(current_active_provider)
    remaining = [
        normalize_provider(item)
        for item in remaining_connected_pm_providers
        if normalize_provider(item)
    ]
    if not disconnected:
        return current or None
    if current != disconnected:
        return current or None
    if len(remaining) == 1:
        return remaining[0]
    return None
