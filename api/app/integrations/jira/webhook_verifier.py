"""Jira webhook verifier implementation."""

from __future__ import annotations

import hmac
from typing import Mapping

from fastapi import HTTPException, Request, status

from app.core.database import get_database
from app.integrations.base.webhook_verifier import BaseWebhookVerifier


class JiraWebhookVerifier(BaseWebhookVerifier):
    integration_name = "jira"

    def is_configured(self) -> bool:
        return True

    async def verify(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        request: Request | None = None,
    ) -> None:
        if request is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing jira request context",
            )

        path_project_id = str((request.path_params or {}).get("project_id") or "").strip()
        if not path_project_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing jira project_id path param",
            )

        provided_token = str((request.query_params or {}).get("token") or "").strip()
        if not provided_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing jira webhook token",
            )

        db = get_database()
        project = await db.projects.find_one(
            {
                "project_id": path_project_id,
                "integrations.jira.provider": "jira",
            },
            {
                "integrations.jira.webhook_token": 1,
            },
        )

        stored_token = str(
            ((((project or {}).get("integrations") or {}).get("jira") or {}).get("webhook_token") or "")
        ).strip()
        if not stored_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing jira webhook token configuration",
            )

        if not hmac.compare_digest(provided_token, stored_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid jira webhook token",
            )
