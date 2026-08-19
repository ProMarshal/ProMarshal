"""Shared tool registry interface for Cortex and Cadence.

Wraps CortexToolRegistry so both features get tool descriptors through
a single shared surface without importing from cortex/ directly.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from app.cortex.tools.registry import CortexToolRegistry


class AgentToolRegistry:
    """Thin shared interface over CortexToolRegistry."""

    def __init__(self) -> None:
        self._registry = CortexToolRegistry()
        self._registry.register_launch_toolset()

    def visible_descriptors(
        self,
        *,
        role: Optional[str],
        connected_integrations: Optional[List[str]] = None,
    ) -> List[Dict[str, object]]:
        """Return prompt-friendly tool descriptors for the given role and integrations."""
        return self._registry.visible_tool_descriptors(
            role=role,
            connected_integrations=connected_integrations or [],
        )

    def get_tool(self, name: str):
        """Return a registered CortexTool by name."""
        return self._registry.get(name)

    def underlying(self) -> CortexToolRegistry:
        """Return the underlying CortexToolRegistry for direct access."""
        return self._registry


# Module-level singleton — shared across both Cortex and Cadence
agent_tool_registry = AgentToolRegistry()
