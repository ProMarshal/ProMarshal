"""Cortex runtime parsing adapters for shared ParseOutcome pipeline."""

from __future__ import annotations

from typing import Any, Dict

from app.cortex.domain.normalization import resolve_enum_alias
from app.cortex.domain.tasks.query_intent import STATUS_ALIASES
from app.domain.parsing.models import (
    PARSE_STATUS_INVALID,
    PARSE_STATUS_RESOLVED,
    ParseOutcome,
    clamp_confidence,
)
from app.domain.parsing.service import register_runtime_adapter


async def parse_cortex_write_args(
    *,
    tool_name: str,
    args: Dict[str, Any],
    parser_context: Dict[str, Any] | None = None,
) -> ParseOutcome:
    """
    Parse/normalize write arguments into shared ParseOutcome.

    This adapter is intentionally strict for status-bearing writes and pass-through
    for write tools without parse ambiguity semantics.
    """
    normalized_tool = str(tool_name or "").strip()
    raw_args = dict(args or {})
    if normalized_tool != "jira_update_status":
        return ParseOutcome(
            status=PARSE_STATUS_RESOLVED,
            reason_code="resolved",
            confidence=clamp_confidence(1.0),
            requires_clarification=False,
            normalized_payload={
                "tool_name": normalized_tool,
                "args": raw_args,
                "source": "cortex_write_adapter_passthrough",
                "parser_context": dict(parser_context or {}),
            },
        )

    raw_status = str(raw_args.get("status") or "").strip()
    if not raw_status:
        return ParseOutcome(
            status=PARSE_STATUS_RESOLVED,
            reason_code="resolved",
            confidence=clamp_confidence(1.0),
            requires_clarification=False,
            normalized_payload={
                "tool_name": normalized_tool,
                "args": raw_args,
                "source": "cortex_write_adapter_status_missing",
                "parser_context": dict(parser_context or {}),
            },
        )

    normalized_status = resolve_enum_alias(raw_status, STATUS_ALIASES)
    normalized_args = dict(raw_args)
    normalized_args["status"] = str(normalized_status or raw_status).strip()
    return ParseOutcome(
        status=PARSE_STATUS_RESOLVED,
        reason_code="resolved",
        confidence=clamp_confidence(0.95 if normalized_status else 0.75),
        requires_clarification=False,
        normalized_payload={
            "tool_name": normalized_tool,
            "args": normalized_args,
            "status": str(normalized_status or raw_status).strip(),
            "source": "cortex_write_adapter",
            "parser_context": dict(parser_context or {}),
        },
    )


def register_cortex_runtime_adapters() -> None:
    """Register Cortex runtime adapters into shared parse registry."""
    register_runtime_adapter("cortex.write_args", parse_cortex_write_args)
