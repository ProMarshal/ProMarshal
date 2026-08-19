"""
Agent Module for Project Charter
Provides orchestration, context management, and tools for agent-based charter creation.
"""

from app.planner.agent.orchestrator import CharterOrchestrator, AgentMode, AgentAction, AgentResponse
from app.planner.agent.context_manager import ContextManager, AgentContext
from app.planner.agent.tools.web_search import WebSearchTool
from app.planner.agent.tools.finalize_check import FinalizeCheckTool
from app.planner.agent.tools.template_loader import TemplateLoaderTool
from app.planner.agent.tools.chat import ChatTool

__all__ = [
    # Orchestrator
    "CharterOrchestrator",
    "AgentMode",
    "AgentAction",
    "AgentResponse",
    # Context
    "ContextManager",
    "AgentContext",
    # Tools
    "WebSearchTool",
    "FinalizeCheckTool",
    "TemplateLoaderTool",
    "ChatTool",
]
