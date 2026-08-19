"""Capability bundle installation registry with conflict diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Dict, Iterable, List, Optional, Protocol, Set, Tuple

from app.cortex.tools.base import CortexTool
from app.cortex.tools.contracts import ToolArgContract
from app.domain.capabilities.bundle_models import CapabilityBundle, ResolverAdapterRegistration


@dataclass(frozen=True)
class ConflictDiagnostic:
    """Deterministic conflict payload emitted during validation failures."""

    bundle_id: str
    provider: str
    capability_id: str
    conflict_type: str
    details: str


class BundleInstallError(ValueError):
    """Raised when bundle installation fails validation."""

    def __init__(self, diagnostics: List[ConflictDiagnostic]) -> None:
        ordered = sorted(
            diagnostics,
            key=lambda item: (
                str(item.conflict_type or "").strip().lower(),
                str(item.bundle_id or "").strip().lower(),
                str(item.provider or "").strip().lower(),
                str(item.capability_id or "").strip().lower(),
            ),
        )
        self.diagnostics = ordered
        message = "; ".join(
            [
                (
                    f"{item.conflict_type}"
                    f"(bundle_id={item.bundle_id},provider={item.provider},"
                    f"capability_id={item.capability_id}):{item.details}"
                )
                for item in ordered
            ]
        )
        super().__init__(message or "bundle_install_failed")


class _ToolRegistryLike(Protocol):
    def list_names(self) -> List[str]:
        ...

    def register(self, tool: CortexTool) -> None:
        ...


class BundleRegistry:
    """Installs bundles and tracks deterministic conflict constraints."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._bundles: Dict[str, CapabilityBundle] = {}
        self._bundle_tool_names: Dict[str, List[str]] = {}
        self._provider_capability_owner: Dict[Tuple[str, str], str] = {}
        self._tool_name_owner: Dict[str, str] = {}
        self._resolver_adapters: Dict[Tuple[str, str], List[ResolverAdapterRegistration]] = {}
        self._arg_contracts: Dict[str, ToolArgContract] = {}
        self._pattern_owner: Dict[Tuple[str, str, str], str] = {}
        self._cadence_adapter_owner: Dict[Tuple[str, str], str] = {}
        # One bundle may be installed globally once and then applied to multiple tool registries.
        self._applied_tool_registries: Dict[int, Set[str]] = {}

    def reset(self) -> None:
        with self._lock:
            self._bundles.clear()
            self._bundle_tool_names.clear()
            self._provider_capability_owner.clear()
            self._tool_name_owner.clear()
            self._resolver_adapters.clear()
            self._arg_contracts.clear()
            self._pattern_owner.clear()
            self._cadence_adapter_owner.clear()
            self._applied_tool_registries.clear()
            from app.domain.capabilities.deterministic.pattern_registry import pattern_registry

            pattern_registry.reset()
            from app.cadence.action_adapters.registry import cadence_action_adapter_registry

            cadence_action_adapter_registry.reset()

    def list_bundle_ids(self) -> List[str]:
        with self._lock:
            return sorted(self._bundles.keys())

    def get(self, bundle_id: str) -> Optional[CapabilityBundle]:
        normalized = str(bundle_id or "").strip().lower()
        with self._lock:
            return self._bundles.get(normalized)

    def list_resolver_adapters(
        self,
        *,
        provider: str,
        capability_id: str,
    ) -> List[ResolverAdapterRegistration]:
        key = (
            str(provider or "").strip().lower(),
            str(capability_id or "").strip().lower(),
        )
        with self._lock:
            return list(self._resolver_adapters.get(key, []))

    def install(
        self,
        bundle: CapabilityBundle,
        *,
        tool_registry: Optional[_ToolRegistryLike] = None,
    ) -> None:
        with self._lock:
            diagnostics = self.validate_bundle(bundle)
            if diagnostics:
                raise BundleInstallError(diagnostics)

            bundle_id = bundle.normalized_bundle_id()
            if bundle_id not in self._bundles:
                tool_names = self._tool_names_for_bundle(bundle)
                self._bundles[bundle_id] = bundle
                self._bundle_tool_names[bundle_id] = tool_names
                provider_key = (bundle.normalized_provider(), bundle.normalized_capability_id())
                self._provider_capability_owner[provider_key] = bundle_id
                for tool_name in tool_names:
                    self._tool_name_owner[tool_name] = bundle_id
                for tool_name, contract in (bundle.arg_contracts or {}).items():
                    normalized_tool_name = str(tool_name or "").strip()
                    if normalized_tool_name:
                        self._arg_contracts[normalized_tool_name] = contract
                adapter_key = (bundle.normalized_provider(), bundle.normalized_capability_id())
                self._resolver_adapters[adapter_key] = list(bundle.resolver_adapters or [])
                for spec in bundle.planner_patterns or []:
                    action_key = (
                        provider_key[0],
                        spec.normalized_capability_id(),
                        spec.normalized_action_type(),
                    )
                    self._pattern_owner[action_key] = bundle_id
                for adapter_provider, adapter_capability in self._cadence_adapter_keys_for_bundle(bundle):
                    self._cadence_adapter_owner[(adapter_provider, adapter_capability)] = bundle_id
                try:
                    self._register_planner_patterns(bundle=bundle)
                except ValueError as exc:
                    raise BundleInstallError(
                        [
                            ConflictDiagnostic(
                                bundle_id=bundle_id,
                                provider=bundle.normalized_provider(),
                                capability_id=bundle.normalized_capability_id(),
                                conflict_type="duplicate_planner_action_type",
                                details=str(exc),
                            )
                        ]
                    ) from exc
                try:
                    self._register_cadence_adapters(bundle=bundle)
                except ValueError as exc:
                    raise BundleInstallError(
                        [
                            ConflictDiagnostic(
                                bundle_id=bundle_id,
                                provider=bundle.normalized_provider(),
                                capability_id=bundle.normalized_capability_id(),
                                conflict_type="duplicate_cadence_adapter",
                                details=str(exc),
                            )
                        ]
                    ) from exc

            if tool_registry is not None:
                self._apply_bundle_to_tool_registry(bundle_id=bundle_id, tool_registry=tool_registry)

    def install_many(
        self,
        bundles: Iterable[CapabilityBundle],
        *,
        tool_registry: Optional[_ToolRegistryLike] = None,
    ) -> None:
        for bundle in bundles:
            self.install(bundle, tool_registry=tool_registry)

    def validate_bundle(self, bundle: CapabilityBundle) -> List[ConflictDiagnostic]:
        diagnostics: List[ConflictDiagnostic] = []
        bundle_id = bundle.normalized_bundle_id()
        provider = bundle.normalized_provider()
        capability_id = bundle.normalized_capability_id()
        tool_names = self._tool_names_for_bundle(bundle)

        if not bundle_id:
            diagnostics.append(
                ConflictDiagnostic(
                    bundle_id="",
                    provider=provider,
                    capability_id=capability_id,
                    conflict_type="invalid_bundle_id",
                    details="bundle_id is required",
                )
            )
        if not provider:
            diagnostics.append(
                ConflictDiagnostic(
                    bundle_id=bundle_id,
                    provider="",
                    capability_id=capability_id,
                    conflict_type="invalid_provider",
                    details="provider is required",
                )
            )
        if not capability_id:
            diagnostics.append(
                ConflictDiagnostic(
                    bundle_id=bundle_id,
                    provider=provider,
                    capability_id="",
                    conflict_type="invalid_capability_id",
                    details="capability_id is required",
                )
            )
        if not bundle.cortex_tool_factories:
            diagnostics.append(
                ConflictDiagnostic(
                    bundle_id=bundle_id,
                    provider=provider,
                    capability_id=capability_id,
                    conflict_type="missing_tools",
                    details="cortex_tool_factories must include at least one tool",
                )
            )

        existing = self._bundles.get(bundle_id)
        if existing is not None:
            if (
                existing.normalized_provider() != provider
                or existing.normalized_capability_id() != capability_id
            ):
                diagnostics.append(
                    ConflictDiagnostic(
                        bundle_id=bundle_id,
                        provider=provider,
                        capability_id=capability_id,
                        conflict_type="bundle_id_collision",
                        details=(
                            "bundle_id already registered with a different provider/capability "
                            f"(existing_provider={existing.normalized_provider()}, "
                            f"existing_capability_id={existing.normalized_capability_id()})"
                        ),
                    )
                )
            return diagnostics

        key = (provider, capability_id)
        owner_bundle = self._provider_capability_owner.get(key)
        if owner_bundle:
            diagnostics.append(
                ConflictDiagnostic(
                    bundle_id=bundle_id,
                    provider=provider,
                    capability_id=capability_id,
                    conflict_type="duplicate_provider_capability",
                    details=f"(provider, capability_id) already owned by `{owner_bundle}`",
                )
            )

        local_seen: Set[str] = set()
        for tool_name in tool_names:
            if tool_name in local_seen:
                diagnostics.append(
                    ConflictDiagnostic(
                        bundle_id=bundle_id,
                        provider=provider,
                        capability_id=capability_id,
                        conflict_type="duplicate_tool_name_in_bundle",
                        details=f"tool_name `{tool_name}` appears multiple times in bundle",
                    )
                )
                continue
            local_seen.add(tool_name)

            existing_owner = self._tool_name_owner.get(tool_name)
            if existing_owner:
                diagnostics.append(
                    ConflictDiagnostic(
                        bundle_id=bundle_id,
                        provider=provider,
                        capability_id=capability_id,
                        conflict_type="duplicate_tool_name",
                        details=f"tool_name `{tool_name}` already owned by `{existing_owner}`",
                    )
                )

            if tool_name in self._arg_contracts and tool_name in (bundle.arg_contracts or {}):
                diagnostics.append(
                    ConflictDiagnostic(
                        bundle_id=bundle_id,
                        provider=provider,
                        capability_id=capability_id,
                        conflict_type="duplicate_arg_contract_key",
                        details=f"arg_contract key `{tool_name}` already registered",
                    )
                )

        local_pattern_seen: Set[Tuple[str, str, str]] = set()
        for spec in bundle.planner_patterns or []:
            action_type = str(getattr(spec, "normalized_action_type", lambda: "")() or "").strip().lower()
            pattern_capability = str(getattr(spec, "normalized_capability_id", lambda: "")() or "").strip().lower()
            if not action_type:
                diagnostics.append(
                    ConflictDiagnostic(
                        bundle_id=bundle_id,
                        provider=provider,
                        capability_id=capability_id,
                        conflict_type="invalid_planner_action_type",
                        details="planner pattern action_type is required",
                    )
                )
                continue
            if not pattern_capability:
                diagnostics.append(
                    ConflictDiagnostic(
                        bundle_id=bundle_id,
                        provider=provider,
                        capability_id=capability_id,
                        conflict_type="invalid_planner_capability",
                        details="planner pattern capability_id is required",
                    )
                )
                continue
            pattern_key = (provider, pattern_capability, action_type)
            if pattern_key in local_pattern_seen:
                diagnostics.append(
                    ConflictDiagnostic(
                        bundle_id=bundle_id,
                        provider=provider,
                        capability_id=capability_id,
                        conflict_type="duplicate_planner_action_type_in_bundle",
                        details=f"planner action_type `{action_type}` duplicated in bundle",
                    )
                )
                continue
            local_pattern_seen.add(pattern_key)
            existing_pattern_owner = self._pattern_owner.get(pattern_key)
            if existing_pattern_owner:
                diagnostics.append(
                    ConflictDiagnostic(
                        bundle_id=bundle_id,
                        provider=provider,
                        capability_id=capability_id,
                        conflict_type="duplicate_planner_action_type",
                        details=(
                            f"planner action_type `{action_type}` for capability `{pattern_capability}` "
                            f"already owned by `{existing_pattern_owner}`"
                        ),
                    )
                )

        local_adapter_seen: Set[Tuple[str, str]] = set()
        for adapter in bundle.cadence_adapters or []:
            adapter_provider = str(getattr(adapter, "provider", "") or provider).strip().lower()
            adapter_capability = str(getattr(adapter, "capability_id", "")).strip().lower()
            if not adapter_provider:
                diagnostics.append(
                    ConflictDiagnostic(
                        bundle_id=bundle_id,
                        provider=provider,
                        capability_id=capability_id,
                        conflict_type="invalid_cadence_adapter_provider",
                        details="cadence adapter provider is required",
                    )
                )
                continue
            if adapter_provider != provider:
                diagnostics.append(
                    ConflictDiagnostic(
                        bundle_id=bundle_id,
                        provider=provider,
                        capability_id=capability_id,
                        conflict_type="invalid_cadence_adapter_provider",
                        details=(
                            "cadence adapter provider must match bundle provider "
                            f"(adapter_provider={adapter_provider})"
                        ),
                    )
                )
                continue
            if not adapter_capability:
                diagnostics.append(
                    ConflictDiagnostic(
                        bundle_id=bundle_id,
                        provider=provider,
                        capability_id=capability_id,
                        conflict_type="invalid_cadence_adapter_capability_id",
                        details="cadence adapter capability_id is required",
                    )
                )
                continue
            adapter_key = (adapter_provider, adapter_capability)
            if adapter_key in local_adapter_seen:
                diagnostics.append(
                    ConflictDiagnostic(
                        bundle_id=bundle_id,
                        provider=provider,
                        capability_id=capability_id,
                        conflict_type="duplicate_cadence_adapter_in_bundle",
                        details=f"cadence adapter capability `{adapter_capability}` duplicated in bundle",
                    )
                )
                continue
            local_adapter_seen.add(adapter_key)
            existing_owner = self._cadence_adapter_owner.get(adapter_key)
            if existing_owner:
                diagnostics.append(
                    ConflictDiagnostic(
                        bundle_id=bundle_id,
                        provider=provider,
                        capability_id=capability_id,
                        conflict_type="duplicate_cadence_adapter",
                        details=(
                            f"cadence adapter capability `{adapter_capability}` already owned by `{existing_owner}`"
                        ),
                    )
                )

        return diagnostics

    def _apply_bundle_to_tool_registry(
        self,
        *,
        bundle_id: str,
        tool_registry: _ToolRegistryLike,
    ) -> None:
        registry_key = id(tool_registry)
        applied = self._applied_tool_registries.setdefault(registry_key, set())
        if bundle_id in applied:
            return

        bundle = self._bundles[bundle_id]
        existing_names = {str(name).strip() for name in tool_registry.list_names()}
        for factory in bundle.cortex_tool_factories:
            tool = factory()
            tool_name = str(tool.normalized_spec().name or "").strip()
            if not tool_name:
                raise BundleInstallError(
                    [
                        ConflictDiagnostic(
                            bundle_id=bundle_id,
                            provider=bundle.normalized_provider(),
                            capability_id=bundle.normalized_capability_id(),
                            conflict_type="invalid_tool_name",
                            details="factory returned tool with empty name",
                        )
                    ]
                )
            if tool_name in existing_names:
                raise BundleInstallError(
                    [
                        ConflictDiagnostic(
                            bundle_id=bundle_id,
                            provider=bundle.normalized_provider(),
                            capability_id=bundle.normalized_capability_id(),
                            conflict_type="tool_registry_collision",
                            details=f"tool_name `{tool_name}` already registered in target tool registry",
                        )
                    ]
                )
            tool_registry.register(tool)
            existing_names.add(tool_name)

        applied.add(bundle_id)

    def _tool_names_for_bundle(self, bundle: CapabilityBundle) -> List[str]:
        names: List[str] = []
        for factory in bundle.cortex_tool_factories or []:
            tool = factory()
            if not isinstance(tool, CortexTool):
                raise BundleInstallError(
                    [
                        ConflictDiagnostic(
                            bundle_id=bundle.normalized_bundle_id(),
                            provider=bundle.normalized_provider(),
                            capability_id=bundle.normalized_capability_id(),
                            conflict_type="invalid_tool_factory",
                            details=f"factory `{factory}` did not return CortexTool",
                        )
                    ]
                )
            name = str(tool.normalized_spec().name or "").strip()
            names.append(name)
        return names

    def _register_planner_patterns(self, *, bundle: CapabilityBundle) -> None:
        if not bundle.planner_patterns:
            return
        from app.domain.capabilities.deterministic.pattern_registry import pattern_registry

        pattern_registry.register_many(
            bundle.planner_patterns,
            provider=bundle.normalized_provider(),
            bundle_id=bundle.normalized_bundle_id(),
        )

    def _cadence_adapter_keys_for_bundle(self, bundle: CapabilityBundle) -> List[Tuple[str, str]]:
        keys: List[Tuple[str, str]] = []
        bundle_provider = bundle.normalized_provider()
        for adapter in bundle.cadence_adapters or []:
            adapter_provider = str(getattr(adapter, "provider", "") or bundle_provider).strip().lower()
            adapter_capability = str(getattr(adapter, "capability_id", "")).strip().lower()
            if adapter_provider and adapter_capability:
                keys.append((adapter_provider, adapter_capability))
        return keys

    def _register_cadence_adapters(self, *, bundle: CapabilityBundle) -> None:
        if not bundle.cadence_adapters:
            return
        from app.cadence.action_adapters.registry import cadence_action_adapter_registry

        cadence_action_adapter_registry.register_many(
            bundle.cadence_adapters,
            provider=bundle.normalized_provider(),
            bundle_id=bundle.normalized_bundle_id(),
        )


def validate_bundle(bundle: CapabilityBundle, registry: BundleRegistry) -> List[ConflictDiagnostic]:
    """Compatibility helper for explicit contract usage from docs."""
    return registry.validate_bundle(bundle)


bundle_registry = BundleRegistry()
