"""Adapter for semantic quality assessment and arg repair."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class QualityAssessorAdapter:
    """Thin adapter over proposal service quality/repair methods."""

    def __init__(self, proposal_service: Any) -> None:
        self.proposal_service = proposal_service

    async def assess(
        self,
        *,
        executor: Any,
        tool_name: str,
        field_name: str,
        current_value: Any,
        policy: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        try:
            return await self.proposal_service.assess_field_quality(
                executor=executor,
                tool_name=tool_name,
                field_name=field_name,
                current_value=current_value,
                policy=policy,
                original_request=str(context.get("user_message") or ""),
                followup_message=str(context.get("latest_user_message") or ""),
                message_context_source=str(context.get("message_context_source") or ""),
                context=context,
            )
        except Exception as exc:
            print(
                "Cortex quality semantic assessor failed: "
                f"tool={tool_name} field={field_name} reason={exc}"
            )
            return None

    async def repair(
        self,
        *,
        executor: Any,
        tool_name: str,
        current_args: Dict[str, Any],
        tool_contracts: List[Dict[str, Any]],
        context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        try:
            repaired_calls = await self.proposal_service.repair_tool_args(
                executor=executor,
                tool_calls=[{"tool_name": tool_name, "args": dict(current_args)}],
                tool_contracts=tool_contracts,
                original_request=str(context.get("user_message") or ""),
                followup_message=str(context.get("latest_user_message") or ""),
                message_context_source=str(context.get("message_context_source") or ""),
                context=context,
            )
        except Exception as exc:
            print(f"Cortex quality repair failed: tool={tool_name} reason={exc}")
            return None
        if not repaired_calls or not isinstance(repaired_calls, list):
            return None
        first = repaired_calls[0] if repaired_calls else {}
        args = first.get("args") if isinstance(first, dict) else None
        return args if isinstance(args, dict) else None

