"""
Tool Registry and Base Classes
Provides common infrastructure for all agent tools.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from dataclasses import dataclass


@dataclass
class ToolResult:
    """Standard result format for all tools."""
    success: bool
    data: Any
    error: Optional[str] = None
    metadata: Optional[Dict] = None


class BaseTool(ABC):
    """
    Base class for all agent tools.
    
    Each tool should:
    - Have a clear, single purpose
    - Be callable standalone or via orchestrator
    - Return standardized ToolResult
    """
    
    name: str = "base_tool"
    description: str = "Base tool class"
    
    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool with given parameters."""
        pass
    
    @abstractmethod
    def can_handle(self, context: Dict) -> bool:
        """
        Determine if this tool should handle the given context.
        Used by orchestrator for automatic tool selection.
        """
        pass


# Import tools for registration
from app.planner.agent.tools.web_search import WebSearchTool
from app.planner.agent.tools.finalize_check import FinalizeCheckTool
from app.planner.agent.tools.template_loader import TemplateLoaderTool


# Tool registry for orchestrator discovery
TOOL_REGISTRY = {
    "web_search": WebSearchTool,
    "finalize_check": FinalizeCheckTool,
    "template_loader": TemplateLoaderTool,
}


def get_tool(tool_name: str) -> BaseTool:
    """Get a tool instance by name."""
    if tool_name not in TOOL_REGISTRY:
        raise ValueError(f"Unknown tool: {tool_name}")
    return TOOL_REGISTRY[tool_name]()


def get_available_tools() -> list:
    """Get list of available tool names."""
    return list(TOOL_REGISTRY.keys())
