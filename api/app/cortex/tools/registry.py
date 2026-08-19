"""Cortex tool registry scaffold."""

from typing import Dict, Iterable, List, Optional

from app.cortex.tools.base import CortexTool, ToolSpec


class CortexToolRegistry:
    """In-memory registry for Cortex tools and lookups."""

    def __init__(self) -> None:
        self._tools: Dict[str, CortexTool] = {}

    def register(self, tool: CortexTool) -> None:
        """Register a tool by its spec name."""
        normalized_spec = tool.normalized_spec()
        if not normalized_spec.name:
            raise ValueError("Tool name is required")

        self._tools[normalized_spec.name] = tool

    def get(self, name: str) -> CortexTool:
        """Return a registered tool by name."""
        return self._tools[name]

    def list_names(self) -> List[str]:
        """Return ordered tool names."""
        return sorted(self._tools.keys())

    def iter_tools(self) -> Iterable[CortexTool]:
        """Yield registered tools."""
        return self._tools.values()

    def list_specs(self) -> List[ToolSpec]:
        """Return normalized tool specs sorted by tool name."""
        return [self._tools[name].normalized_spec() for name in sorted(self._tools.keys())]

    def list_by_capability(
        self,
        capability: str,
        *,
        role: Optional[str] = None,
        connected_integrations: Optional[List[str]] = None,
    ) -> List[CortexTool]:
        """Return visible tools supporting a capability."""
        normalized_capability = str(capability or "").strip().lower()
        if not normalized_capability:
            return []
        visible = self.visible_tools(
            role=role,
            connected_integrations=connected_integrations or [],
        )
        supported: List[CortexTool] = []
        for tool in visible:
            spec = tool.normalized_spec()
            if normalized_capability in set(spec.capabilities):
                supported.append(tool)
        return sorted(
            supported,
            key=lambda item: item.normalized_spec().name,
        )

    def list_by_provider_and_capability(
        self,
        *,
        provider: str,
        capability: str,
        role: Optional[str] = None,
        connected_integrations: Optional[List[str]] = None,
    ) -> List[CortexTool]:
        """Return visible tools for a provider+capability pair."""
        normalized_provider = str(provider or "").strip().lower()
        if not normalized_provider:
            return []
        matching: List[CortexTool] = []
        for tool in self.list_by_capability(
            capability=capability,
            role=role,
            connected_integrations=connected_integrations or [],
        ):
            if str(tool.normalized_spec().provider or "").strip().lower() == normalized_provider:
                matching.append(tool)
        return matching

    def capability_available(
        self,
        capability: str,
        *,
        role: Optional[str] = None,
        connected_integrations: Optional[List[str]] = None,
    ) -> bool:
        """Return True when at least one visible tool supports the capability."""
        return bool(
            self.list_by_capability(
                capability=capability,
                role=role,
                connected_integrations=connected_integrations or [],
            )
        )

    def get_idempotency(self, name: str) -> str:
        """Return tool idempotency classification."""
        return self.get(name).normalized_spec().idempotency

    def requires_confirmation(self, name: str) -> bool:
        """Return whether tool execution requires explicit approval."""
        return bool(self.get(name).normalized_spec().requires_confirmation)

    def visible_tools(
        self,
        *,
        role: Optional[str],
        connected_integrations: Optional[List[str]] = None,
    ) -> List[CortexTool]:
        """
        Return tools visible for given role and integration set.

        Filtering uses ToolSpec metadata only:
        - role requirements
        - integration requirements
        """
        integrations = connected_integrations or []
        visible: List[CortexTool] = []
        for name in sorted(self._tools.keys()):
            tool = self._tools[name]
            if not tool.supports_role(role):
                continue
            if not tool.supports_integrations(integrations):
                continue
            visible.append(tool)
        return visible

    def visible_tool_descriptors(
        self,
        *,
        role: Optional[str],
        connected_integrations: Optional[List[str]] = None,
    ) -> List[Dict[str, object]]:
        """Return prompt-friendly descriptors for visible tools."""
        return [
            tool.normalized_spec().to_prompt_descriptor()
            for tool in self.visible_tools(
                role=role,
                connected_integrations=connected_integrations or [],
            )
        ]

    def register_launch_toolset(self) -> None:
        """Register Cortex launch toolset (7 Jira + 3 Brain tools)."""
        from app.domain.capabilities.startup import install_builtin_bundles

        install_builtin_bundles(tool_registry=self)
