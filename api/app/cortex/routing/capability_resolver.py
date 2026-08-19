"""Deterministic capability resolver for provider/tool routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.cortex.tools.registry import CortexToolRegistry
from app.domain.capabilities.materialization import (
    build_capability_context,
    connected_pm_providers_from_project_context,
)


@dataclass(frozen=True)
class CapabilityResolution:
    """Resolved concrete tool for a capability request."""

    ok: bool
    capability: str
    tool_name: Optional[str] = None
    provider: Optional[str] = None
    candidate_providers: Optional[List[str]] = None
    reason_code: str = ""
    reason: str = ""


class CapabilityResolver:
    """Map a capability intent to one visible concrete tool."""

    def __init__(self, *, tool_registry: CortexToolRegistry) -> None:
        self.tool_registry = tool_registry

    def resolve(
        self,
        *,
        capability: str,
        role: Optional[str],
        connected_integrations: Optional[List[str]],
        preferred_provider: Optional[str] = None,
        provider_hint: Optional[str] = None,
        project_context: Optional[Dict[str, Any]] = None,
    ) -> CapabilityResolution:
        normalized_capability = str(capability or "").strip().lower()
        if not normalized_capability:
            return CapabilityResolution(
                ok=False,
                capability="",
                reason_code="missing_capability",
                reason="Capability is required for capability routing.",
            )

        candidates = self.tool_registry.list_by_capability(
            normalized_capability,
            role=role,
            connected_integrations=connected_integrations or [],
        )
        if not candidates:
            return CapabilityResolution(
                ok=False,
                capability=normalized_capability,
                reason_code="unsupported_capability",
                reason=f"No visible tools support capability `{normalized_capability}`.",
            )

        context = project_context if isinstance(project_context, dict) else {}
        capability_context = build_capability_context(project_context=context)
        connected_pm_providers = set(
            connected_pm_providers_from_project_context(context)
            or [
                str(item).strip().lower()
                for item in (connected_integrations or [])
                if str(item).strip()
            ]
        )
        candidate_provider_names = sorted(
            {
                str(item.normalized_spec().provider or "").strip().lower()
                for item in candidates
                if str(item.normalized_spec().provider or "").strip()
            }
        )
        candidate_pm_providers = sorted(
            [
                provider
                for provider in candidate_provider_names
                if provider in connected_pm_providers
            ]
        )

        normalized_provider_hint = str(provider_hint or "").strip().lower() or None
        if normalized_provider_hint:
            provider_candidates = [
                item
                for item in candidates
                if str(item.normalized_spec().provider or "").strip().lower() == normalized_provider_hint
            ]
            if not provider_candidates:
                return CapabilityResolution(
                    ok=False,
                    capability=normalized_capability,
                    reason_code="provider_not_available_for_capability",
                    reason=(
                        f"Provider `{normalized_provider_hint}` is not available for capability "
                        f"`{normalized_capability}`."
                    ),
                    candidate_providers=candidate_provider_names,
                )
            chosen = self._choose_best_candidate(
                candidates=provider_candidates,
                connected_integrations=connected_integrations or [],
            )
            return CapabilityResolution(
                ok=True,
                capability=normalized_capability,
                tool_name=chosen.normalized_spec().name,
                provider=chosen.normalized_spec().provider,
                candidate_providers=candidate_provider_names,
                reason_code="provider_hint",
                reason=f"Selected provider hint `{normalized_provider_hint}`.",
            )

        normalized_preferred_provider = str(preferred_provider or "").strip().lower() or None
        if not normalized_preferred_provider:
            normalized_preferred_provider = capability_context.active_provider
        if not normalized_preferred_provider and not capability_context.provider_ambiguous:
            normalized_preferred_provider = self._provider_preference_from_project_context(
                project_context=project_context or {},
                connected_integrations=connected_integrations or [],
            )
        if normalized_preferred_provider:
            preferred_candidates = [
                item
                for item in candidates
                if str(item.normalized_spec().provider or "").strip().lower()
                == normalized_preferred_provider
            ]
            if preferred_candidates:
                chosen = self._choose_best_candidate(
                    candidates=preferred_candidates,
                    connected_integrations=connected_integrations or [],
                )
                return CapabilityResolution(
                    ok=True,
                    capability=normalized_capability,
                    tool_name=chosen.normalized_spec().name,
                    provider=chosen.normalized_spec().provider,
                    candidate_providers=candidate_provider_names,
                    reason_code="preferred_provider",
                    reason=f"Selected preferred provider `{normalized_preferred_provider}`.",
                )
            if len(candidate_pm_providers) > 1:
                return CapabilityResolution(
                    ok=False,
                    capability=normalized_capability,
                    reason_code="ambiguous_provider",
                    reason=(
                        f"Preferred provider `{normalized_preferred_provider}` cannot satisfy this capability "
                        "and multiple providers remain."
                    ),
                    candidate_providers=candidate_pm_providers,
                )

        if capability_context.provider_ambiguous and len(candidate_pm_providers) > 1:
            return CapabilityResolution(
                ok=False,
                capability=normalized_capability,
                reason_code="ambiguous_provider",
                reason=(
                    "Multiple connected providers support this capability and no active provider is set. "
                    "Please specify provider."
                ),
                candidate_providers=candidate_pm_providers,
            )

        chosen = self._choose_best_candidate(
            candidates=candidates,
            connected_integrations=connected_integrations or [],
        )
        return CapabilityResolution(
            ok=True,
            capability=normalized_capability,
            tool_name=chosen.normalized_spec().name,
            provider=chosen.normalized_spec().provider,
            candidate_providers=candidate_provider_names,
            reason_code="default_selection",
            reason="Selected deterministic default candidate.",
        )

    def _choose_best_candidate(
        self,
        *,
        candidates: List[Any],
        connected_integrations: List[str],
    ) -> Any:
        connected = {
            str(name or "").strip().lower()
            for name in connected_integrations
            if str(name or "").strip()
        }

        def _sort_key(item: Any) -> tuple[int, int, str]:
            spec = item.normalized_spec()
            provider = str(spec.provider or "").strip().lower()
            provider_connected_rank = 0 if provider and provider in connected else 1
            provider_specific_rank = 0 if provider else 1
            return (
                provider_connected_rank,
                provider_specific_rank,
                str(spec.name or "").strip().lower(),
            )

        return sorted(candidates, key=_sort_key)[0]

    def _provider_preference_from_project_context(
        self,
        *,
        project_context: Dict[str, Any],
        connected_integrations: List[str],
    ) -> Optional[str]:
        context = project_context if isinstance(project_context, dict) else {}

        explicit_candidates = [
            context.get("preferred_pm_provider"),
            context.get("pm_provider"),
            context.get("preferred_provider"),
        ]
        for candidate in explicit_candidates:
            normalized = str(candidate or "").strip().lower()
            if normalized:
                return normalized

        integration_config = context.get("integrations")
        if isinstance(integration_config, dict):
            for name, cfg in integration_config.items():
                normalized_name = str(name or "").strip().lower()
                if not normalized_name:
                    continue
                status = str((cfg or {}).get("status") or "").strip().lower()
                if status == "connected":
                    return normalized_name

        for item in connected_integrations:
            normalized = str(item or "").strip().lower()
            if normalized:
                return normalized
        return None
