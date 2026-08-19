"""Cadence runtime parsing adapter registration helpers."""

from __future__ import annotations

from typing import Awaitable, Callable

from app.domain.parsing.models import ParseOutcome
from app.domain.parsing.service import register_runtime_adapter


StatusParser = Callable[[str], Awaitable[ParseOutcome]]
BacklogParser = Callable[[str], Awaitable[ParseOutcome]]


def register_cadence_runtime_adapters(
    *,
    status_parser: StatusParser,
    backlog_parser: BacklogParser,
) -> None:
    """Register Cadence parser adapters into shared runtime registry."""
    register_runtime_adapter("cadence.status_update", status_parser)
    register_runtime_adapter("cadence.backlog_intent", backlog_parser)
