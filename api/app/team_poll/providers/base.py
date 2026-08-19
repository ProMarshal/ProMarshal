"""Base provider adapter contract for team poll."""

from __future__ import annotations

from typing import Any, Dict, Optional, Protocol


class TeamPollProviderAdapter(Protocol):
    async def send_prompt(
        self,
        *,
        target_external_user_id: str,
        payload: Dict[str, Any],
        workspace_id: Optional[str] = None,
    ) -> bool:
        ...

    async def send_nudge(
        self,
        *,
        target_external_user_id: str,
        text: str,
        workspace_id: Optional[str] = None,
    ) -> bool:
        ...

    async def send_owner_summary(
        self,
        *,
        owner_external_user_id: str,
        text: str,
        workspace_id: Optional[str] = None,
    ) -> bool:
        ...
