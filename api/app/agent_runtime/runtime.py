"""SharedAgentRuntime — single LLM execution loop for Cortex and Cadence.

Both features build an AgentContext, pass their tools, and call run().
Internally delegates to AgentExecutor._run_with_agents_litellm() (Runner.run()).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.agent_runtime.context import AgentContext
from app.agent_runtime.executor import AgentExecutor, ExecutorResult
from app.cortex.tools.base import CortexTool


@dataclass(frozen=True)
class AgentRuntimeResult:
    """Normalised result returned to Cortex and Cadence callers."""

    ok: bool
    output_text: Optional[str] = None
    error: Optional[str] = None
    raw_response: Optional[Dict[str, Any]] = None


class SharedAgentRuntime:
    """
    Unified LLM agent runtime.

    Cortex and Cadence both call run() with their respective AgentContext
    and tool list. This class owns the LLM loop — it does not make decisions
    about routing or state transitions.
    """

    def __init__(self, *, executor: Optional[AgentExecutor] = None) -> None:
        # AgentExecutor holds the Runner.run() implementation.
        # SharedAgentRuntime is a thin coordinator on top.
        self._executor = executor or AgentExecutor()

    async def run(
        self,
        *,
        context: AgentContext,
        system_prompt: str,
        tools: List[Dict[str, Any]],
    ) -> AgentRuntimeResult:
        """
        Execute one LLM turn and return the result.

        Parameters
        ----------
        context:
            AgentContext populated by the calling feature (Cortex or Cadence).
        system_prompt:
            Full system prompt built by the feature's prompt builder,
            including pre-processed intent section.
        tools:
            Prompt-friendly tool descriptors from visible_tool_descriptors()
            (or cadence tool descriptors for Cadence calls).
        """
        execution_context: Dict[str, Any] = {
            "project_id": context.project_id,
            "user_id": context.user_id,
            "session_key": context.session_key,
            "source": context.source,
            "run_id": context.run_id,
            **context.feature_context,
        }

        payload: Dict[str, Any] = {
            "prompt": system_prompt,
            "input": context.user_message,
            "context": execution_context,
            "tools": tools,
        }

        result: ExecutorResult = await self._executor.run(payload)

        return AgentRuntimeResult(
            ok=result.ok,
            output_text=result.output_text,
            error=result.error,
            raw_response=result.raw_response,
        )


# Module-level singleton — Cortex and Cadence import this directly
shared_agent_runtime = SharedAgentRuntime()
