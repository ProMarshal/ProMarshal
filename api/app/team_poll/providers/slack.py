"""Slack provider adapter implementation for team poll."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict

from app.core.config import settings
from app.core.redis_queue import get_redis_connection
from app.integrations.slack.client import SlackClient


class SlackTeamPollAdapter:
    def __init__(self, access_token: str):
        self._client = SlackClient(access_token)

    async def send_prompt(
        self,
        *,
        target_external_user_id: str,
        payload: Dict[str, Any],
        workspace_id: str | None = None,
    ) -> bool:
        return await self._send_with_retry(
            target_external_user_id=target_external_user_id,
            text=str(payload.get("text") or ""),
            blocks=payload.get("blocks"),
            workspace_id=workspace_id,
        )

    async def send_nudge(
        self,
        *,
        target_external_user_id: str,
        text: str,
        workspace_id: str | None = None,
    ) -> bool:
        return await self._send_with_retry(
            target_external_user_id=target_external_user_id,
            text=text,
            blocks=None,
            workspace_id=workspace_id,
        )

    async def send_owner_summary(
        self,
        *,
        owner_external_user_id: str,
        text: str,
        workspace_id: str | None = None,
    ) -> bool:
        return await self._send_with_retry(
            target_external_user_id=owner_external_user_id,
            text=text,
            blocks=None,
            workspace_id=workspace_id,
        )

    async def _send_with_retry(
        self,
        *,
        target_external_user_id: str,
        text: str,
        blocks: Any,
        workspace_id: str | None,
    ) -> bool:
        if not self._is_rate_limited(workspace_id=workspace_id):
            print(
                "team_poll_delivery_rate_limited: "
                f"workspace_id={str(workspace_id or '').strip() or 'n/a'} target={target_external_user_id}"
            )
            return False

        max_retries = max(0, int(getattr(settings, "team_poll_max_retries", 5) or 5))
        backoffs = self._parse_backoff_seconds(getattr(settings, "team_poll_retry_backoff_seconds", "5,15,30,60,120"))
        attempts = 1 + max_retries
        for attempt in range(1, attempts + 1):
            ok = await self._client.send_message(
                target_external_user_id,
                text=text,
                blocks=blocks,
            )
            if ok:
                return True
            if attempt < attempts:
                backoff = backoffs[min(attempt - 1, len(backoffs) - 1)] if backoffs else 1
                await asyncio.sleep(max(1, int(backoff)))
        return False

    def _is_rate_limited(self, *, workspace_id: str | None) -> bool:
        redis_conn = get_redis_connection()
        if redis_conn is None:
            return True
        try:
            now_bucket = datetime.utcnow().strftime("%Y%m%d%H%M")
            global_key = f"team_poll:rl:global:{now_bucket}"
            workspace = str(workspace_id or "").strip()
            workspace_key = f"team_poll:rl:workspace:{workspace}:{now_bucket}" if workspace else None
            global_limit = max(1, int(getattr(settings, "team_poll_global_rate_limit_per_minute", 1000) or 1000))
            workspace_limit = max(
                1,
                int(getattr(settings, "team_poll_workspace_rate_limit_per_minute", 120) or 120),
            )
            global_count = int(redis_conn.incr(global_key))
            redis_conn.expire(global_key, 75)
            if global_count > global_limit:
                return False
            if workspace_key:
                workspace_count = int(redis_conn.incr(workspace_key))
                redis_conn.expire(workspace_key, 75)
                if workspace_count > workspace_limit:
                    return False
            return True
        except Exception:
            return True

    @staticmethod
    def _parse_backoff_seconds(raw: Any) -> list[int]:
        text = str(raw or "").strip()
        if not text:
            return [5, 15, 30, 60, 120]
        values: list[int] = []
        for token in text.split(","):
            token = str(token or "").strip()
            if not token:
                continue
            try:
                value = int(token)
            except Exception:
                continue
            if value > 0:
                values.append(value)
        return values or [5, 15, 30, 60, 120]
