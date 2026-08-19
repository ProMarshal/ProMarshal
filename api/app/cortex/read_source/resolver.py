"""Global read source resolver (brain/live) with policy + capability checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.core.config import settings
from app.cortex.tools.contracts import read_source_capabilities_for_tool


@dataclass(frozen=True)
class ReadSourceDecision:
    """Effective read source decision for one tool call."""

    requested_mode: str
    effective_mode: str
    source: str
    blocking: bool = False
    issue_code: Optional[str] = None
    reason: str = ""


class ReadSourceResolver:
    """Resolve effective read source from global mode, user hints, and tool capabilities."""

    def resolve(
        self,
        *,
        tool_name: str,
        args: Dict[str, Any],
        execution_context: Dict[str, Any],
    ) -> ReadSourceDecision:
        caps = read_source_capabilities_for_tool(tool_name)
        supports_brain = bool(caps.get("supports_brain", True))
        supports_live = bool(caps.get("supports_live", False))
        contract_default = str(caps.get("default_mode") or "").strip().lower()

        global_mode = str(getattr(settings, "cortex_read_source_mode", "brain_first") or "brain_first").strip().lower()
        requested = self._requested_mode_from_context(
            args=args,
            execution_context=execution_context,
            fallback=(contract_default or global_mode or "brain_first"),
        )
        if requested not in {"brain_first", "live_first", "brain_only", "live_only"}:
            requested = "brain_first"

        if requested == "brain_only" and not supports_brain:
            return ReadSourceDecision(
                requested_mode=requested,
                effective_mode=requested,
                source="unresolved",
                blocking=True,
                issue_code="read_source_unresolved",
                reason=f"{tool_name} does not support brain source.",
            )
        if requested == "live_only" and not supports_live:
            return ReadSourceDecision(
                requested_mode=requested,
                effective_mode=requested,
                source="unresolved",
                blocking=True,
                issue_code="read_source_unresolved",
                reason=f"{tool_name} does not support live source.",
            )

        if requested == "brain_only":
            return ReadSourceDecision(
                requested_mode=requested,
                effective_mode=requested,
                source="brain",
                reason="brain_only requested",
            )
        if requested == "live_only":
            return ReadSourceDecision(
                requested_mode=requested,
                effective_mode=requested,
                source="live",
                reason="live_only requested",
            )

        if requested == "live_first":
            if supports_live:
                return ReadSourceDecision(
                    requested_mode=requested,
                    effective_mode=requested,
                    source="live",
                    reason="live_first requested",
                )
            if supports_brain:
                return ReadSourceDecision(
                    requested_mode=requested,
                    effective_mode="brain_only",
                    source="brain",
                    reason="live unsupported; downgraded to brain",
                )
            return ReadSourceDecision(
                requested_mode=requested,
                effective_mode=requested,
                source="unresolved",
                blocking=True,
                issue_code="read_source_unresolved",
                reason=f"{tool_name} supports neither brain nor live read source.",
            )

        # brain_first
        if supports_brain:
            return ReadSourceDecision(
                requested_mode=requested,
                effective_mode=requested,
                source="brain",
                reason="brain_first default",
            )
        if supports_live:
            return ReadSourceDecision(
                requested_mode=requested,
                effective_mode="live_only",
                source="live",
                reason="brain unsupported; downgraded to live",
            )
        return ReadSourceDecision(
            requested_mode=requested,
            effective_mode=requested,
            source="unresolved",
            blocking=True,
            issue_code="read_source_unresolved",
            reason=f"{tool_name} supports neither brain nor live read source.",
        )

    def _requested_mode_from_context(
        self,
        *,
        args: Dict[str, Any],
        execution_context: Dict[str, Any],
        fallback: str,
    ) -> str:
        explicit_mode = str(execution_context.get("read_source_mode") or "").strip().lower()
        if explicit_mode in {"brain_first", "live_first", "brain_only", "live_only"}:
            return explicit_mode

        freshness = str(args.get("freshness") or "").strip().lower()
        if freshness in {"latest", "latest_requested", "live"}:
            return "live_first"

        message = str(
            execution_context.get("latest_user_message")
            or execution_context.get("user_message")
            or ""
        ).strip().lower()
        if "latest from jira" in message or "live from jira" in message or "live jira" in message:
            return "live_only"
        if "latest" in message and "jira" in message:
            return "live_first"
        if "from brain" in message or "brain only" in message:
            return "brain_only"

        return fallback

