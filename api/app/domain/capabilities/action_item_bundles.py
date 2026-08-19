"""Built-in Action Item capability bundles."""

from __future__ import annotations

from app.cortex.tools.contracts import ToolArgContract, get_tool_arg_contract
from app.domain.capabilities.bundle_models import CapabilityBundle


def get_action_item_launch_bundle() -> CapabilityBundle:
    """Return built-in Action Item launch bundle."""
    from app.cortex.tools.providers.action_item_tools import get_launch_action_item_tools

    tools = get_launch_action_item_tools()
    tool_factories = [lambda cls=tool.__class__: cls() for tool in tools]
    arg_contracts: dict[str, ToolArgContract] = {}
    for tool in tools:
        name = str(tool.normalized_spec().name or "").strip()
        if not name:
            continue
        contract = get_tool_arg_contract(name)
        if _contract_has_data(contract):
            arg_contracts[name] = contract
    return CapabilityBundle(
        bundle_id="builtin.promarshal.action_items.launch",
        capability_id="action_items_launch_toolset",
        provider="promarshal",
        cortex_tool_factories=tool_factories,
        arg_contracts=arg_contracts,
    )


def _contract_has_data(contract: ToolArgContract) -> bool:
    return bool(
        contract.aliases
        or contract.field_quality
        or contract.field_classes
        or contract.policy_overrides
        or contract.read_source_capabilities
    )

