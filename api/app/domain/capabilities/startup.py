"""Startup installers for built-in capability bundles."""

from __future__ import annotations

from threading import Lock
from typing import List, Optional

from app.cortex.tools.contracts import ToolArgContract, get_tool_arg_contract
from app.domain.capabilities.bundle_models import CapabilityBundle
from app.domain.capabilities.bundle_registry import BundleRegistry, bundle_registry
from app.domain.capabilities.action_item_bundles import get_action_item_launch_bundle
from app.domain.capabilities.jira_bundles import get_jira_launch_bundle
from app.domain.capabilities.team_poll_bundles import get_team_poll_launch_bundle


_install_lock = Lock()


def get_builtin_bundles() -> List[CapabilityBundle]:
    """Return built-in launch bundles that preserve current runtime tool surface."""
    from app.cortex.tools.brain_tools import get_launch_brain_tools

    brain_tools = get_launch_brain_tools()
    return [
        get_jira_launch_bundle(),
        get_action_item_launch_bundle(),
        get_team_poll_launch_bundle(),
        CapabilityBundle(
            bundle_id="builtin.brain.launch",
            capability_id="launch_toolset",
            provider="brain",
            cortex_tool_factories=_factories_for_tools(brain_tools),
            arg_contracts=_arg_contracts_for_tools(brain_tools),
        ),
    ]


def install_builtin_bundles(
    *,
    tool_registry: Optional[object] = None,
    registry: BundleRegistry = bundle_registry,
) -> None:
    """
    Install built-in bundles through one canonical startup path.

    This function is process-safe and idempotent for repeated entrypoint calls.
    """
    with _install_lock:
        registry.install_many(get_builtin_bundles(), tool_registry=tool_registry)


def _factories_for_tools(tools: List[object]) -> List:
    factories = []
    for tool in tools:
        tool_cls = tool.__class__
        factories.append(lambda tool_cls=tool_cls: tool_cls())
    return factories


def _arg_contracts_for_tools(tools: List[object]) -> dict[str, ToolArgContract]:
    contracts: dict[str, ToolArgContract] = {}
    for tool in tools:
        tool_name = str(tool.normalized_spec().name or "").strip()
        if not tool_name:
            continue
        contract = get_tool_arg_contract(tool_name)
        if _contract_has_data(contract):
            contracts[tool_name] = contract
    return contracts


def _contract_has_data(contract: ToolArgContract) -> bool:
    return bool(
        contract.aliases
        or contract.field_quality
        or contract.field_classes
        or contract.policy_overrides
        or contract.read_source_capabilities
    )
