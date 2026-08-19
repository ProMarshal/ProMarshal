"""Shared DM pending-interaction routing (Team Poll + work-item clarifications + Action Item)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.action_items.pending_handler import handle_pending_action_item_clarification
from app.core.config import settings
from app.core.pending_interactions import (
    list_pending_interactions_by_external_identity,
    resolve_pending_interaction,
)
from app.tasks.pending_provider_type_handler import handle_pending_provider_type_clarification
from app.tasks.pending_parent_handler import handle_pending_work_item_parent_clarification
from app.team_poll.interaction_handler import handle_team_poll_dm
from app.team_poll.session_store import get_session as get_team_poll_session
from app.integrations.slack.context_footer import (
    append_project_context_footer,
    build_project_context_footer,
)

logger = logging.getLogger(__name__)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class PendingRouteContext:
    db: Any
    team_id: str
    channel: str
    slack_user_id: str
    text: str
    thread_ts: Optional[str]
    get_workspace_slack_client: Callable[..., Awaitable[Any]]
    project_resolution: Optional[Any]
    project_slack_client: Optional[Any]


class PendingInteractionHandler:
    """Typed pending-interaction handler contract for shared DM routing."""

    name: str = "base"
    priority: int = 0
    fail_closed: bool = False

    async def handle(self, ctx: PendingRouteContext) -> Dict[str, Any]:
        raise NotImplementedError


class TeamPollPendingHandler(PendingInteractionHandler):
    name = "team_poll"
    priority = 100
    fail_closed = True

    async def handle(self, ctx: PendingRouteContext) -> Dict[str, Any]:
        interactions = await list_pending_interactions_by_external_identity(
            provider="slack",
            workspace_id=ctx.team_id,
            external_user_id=ctx.slack_user_id,
            interaction_type="team_poll",
            limit=2,
        )
        count = len(interactions)
        if count > 1:
            conflict_client = await ctx.get_workspace_slack_client(
                db=ctx.db,
                team_id=ctx.team_id,
            )
            if conflict_client:
                try:
                    await conflict_client.chat_postMessage(
                        channel=ctx.channel,
                        text=(
                            "I found more than one active team poll session for your DM identity. "
                            "Please retry in a minute or ask the project owner to close one poll."
                        ),
                        thread_ts=ctx.thread_ts,
                    )
                except Exception:
                    pass
            return {"handled": True, "route": "team_poll_conflict"}

        if count != 1:
            return {"handled": False}

        interaction = interactions[0]
        context_payload = interaction.get("context_payload") or {}
        session_id = _normalize_text(context_payload.get("session_id"))
        project_id = _normalize_text(context_payload.get("project_id")) or None
        session = None
        if session_id:
            session = await get_team_poll_session(
                ctx.db,
                session_id=session_id,
                project_id=project_id,
            )
        if not isinstance(session, dict):
            interaction_id = _normalize_text(interaction.get("interaction_id"))
            if interaction_id:
                try:
                    await resolve_pending_interaction(
                        interaction_id=interaction_id,
                        terminal_status="stale_target",
                    )
                except Exception:
                    pass
            return {"handled": False, "route": "team_poll_stale_pending_cleanup"}

        poll_client = await ctx.get_workspace_slack_client(
            db=ctx.db,
            team_id=ctx.team_id,
            preferred_project_id=project_id,
        )
        if poll_client:
            handled = await handle_team_poll_dm(
                ctx.db,
                session=session,
                text=ctx.text,
                slack_client=poll_client,
                channel=ctx.channel,
            )
            if handled:
                return {"handled": True, "route": "team_poll_active_session"}
            return {"handled": False, "route": "team_poll_pass_to_cortex"}
        return {"handled": False, "route": "team_poll_missing_client"}


class ActionItemPendingHandler(PendingInteractionHandler):
    name = "action_item"
    priority = 80
    fail_closed = False

    @staticmethod
    def _resolve_actor_user_id(project_resolution: Optional[Any]) -> str:
        project = getattr(project_resolution, "project", None) or {}
        member = getattr(project_resolution, "member", None) or {}
        actor_user_id = _normalize_text(member.get("user_id"))
        if not actor_user_id and _normalize_text(member.get("role")).lower() == "owner":
            actor_user_id = _normalize_text(project.get("user_id"))
        return actor_user_id

    async def handle(self, ctx: PendingRouteContext) -> Dict[str, Any]:
        if ctx.project_resolution is None:
            return {"handled": False}
        project = getattr(ctx.project_resolution, "project", None) or {}
        actor_user_id = self._resolve_actor_user_id(ctx.project_resolution)
        if not actor_user_id:
            return {"handled": False}

        pending_result = await handle_pending_action_item_clarification(
            project=project,
            actor_user_id=actor_user_id,
            text=ctx.text,
        )
        if not bool((pending_result or {}).get("handled")):
            return {"handled": False}

        response_text = _normalize_text((pending_result or {}).get("response_text"))
        if response_text and ctx.project_slack_client:
            try:
                footer = await build_project_context_footer(
                    db=ctx.db,
                    team_id=ctx.team_id,
                    slack_user_id=ctx.slack_user_id,
                    project=project,
                )
                await ctx.project_slack_client.chat_postMessage(
                    channel=ctx.channel,
                    text=append_project_context_footer(response_text, footer),
                    thread_ts=ctx.thread_ts,
                )
            except Exception:
                pass
        return {"handled": True, "route": "action_item_pending_clarification"}


class WorkItemParentPendingHandler(PendingInteractionHandler):
    name = "work_item_parent"
    priority = 90
    fail_closed = False

    @staticmethod
    def _resolve_actor_user_id(project_resolution: Optional[Any]) -> str:
        project = getattr(project_resolution, "project", None) or {}
        member = getattr(project_resolution, "member", None) or {}
        actor_user_id = _normalize_text(member.get("user_id"))
        if not actor_user_id and _normalize_text(member.get("role")).lower() == "owner":
            actor_user_id = _normalize_text(project.get("user_id"))
        return actor_user_id

    async def handle(self, ctx: PendingRouteContext) -> Dict[str, Any]:
        if ctx.project_resolution is None:
            return {"handled": False}
        project = getattr(ctx.project_resolution, "project", None) or {}
        actor_user_id = self._resolve_actor_user_id(ctx.project_resolution)
        if not actor_user_id:
            return {"handled": False}

        pending_result = await handle_pending_work_item_parent_clarification(
            project=project,
            actor_user_id=actor_user_id,
            text=ctx.text,
        )
        if bool((pending_result or {}).get("resume_to_cortex")):
            resume_target_refs = [
                _normalize_text(item).upper()
                for item in (pending_result or {}).get("resume_target_refs") or []
                if _normalize_text(item)
            ]
            return {
                "handled": False,
                "resume_to_cortex": True,
                "resume_message_text": _normalize_text((pending_result or {}).get("resume_message_text")),
                "resume_target_refs": resume_target_refs,
                "route": "work_item_parent_resume_to_cortex",
            }
        if not bool((pending_result or {}).get("handled")):
            return {"handled": False}

        response_text = _normalize_text((pending_result or {}).get("response_text"))
        if response_text and ctx.project_slack_client:
            try:
                footer = await build_project_context_footer(
                    db=ctx.db,
                    team_id=ctx.team_id,
                    slack_user_id=ctx.slack_user_id,
                    project=project,
                )
                await ctx.project_slack_client.chat_postMessage(
                    channel=ctx.channel,
                    text=append_project_context_footer(response_text, footer),
                    thread_ts=ctx.thread_ts,
                )
            except Exception:
                pass
        return {"handled": True, "route": "work_item_parent_pending_clarification"}


class ProviderTypePendingHandler(PendingInteractionHandler):
    name = "provider_type"
    priority = 85
    fail_closed = False

    @staticmethod
    def _resolve_actor_user_id(project_resolution: Optional[Any]) -> str:
        project = getattr(project_resolution, "project", None) or {}
        member = getattr(project_resolution, "member", None) or {}
        actor_user_id = _normalize_text(member.get("user_id"))
        if not actor_user_id and _normalize_text(member.get("role")).lower() == "owner":
            actor_user_id = _normalize_text(project.get("user_id"))
        return actor_user_id

    async def handle(self, ctx: PendingRouteContext) -> Dict[str, Any]:
        if ctx.project_resolution is None:
            return {"handled": False}
        project = getattr(ctx.project_resolution, "project", None) or {}
        actor_user_id = self._resolve_actor_user_id(ctx.project_resolution)
        if not actor_user_id:
            return {"handled": False}

        pending_result = await handle_pending_provider_type_clarification(
            project=project,
            actor_user_id=actor_user_id,
            text=ctx.text,
        )
        if bool((pending_result or {}).get("resume_to_cortex")):
            resume_target_refs = [
                _normalize_text(item).upper()
                for item in (pending_result or {}).get("resume_target_refs") or []
                if _normalize_text(item)
            ]
            return {
                "handled": False,
                "resume_to_cortex": True,
                "resume_message_text": _normalize_text((pending_result or {}).get("resume_message_text")),
                "resume_target_refs": resume_target_refs,
                "route": "provider_type_resume_to_cortex",
            }
        if not bool((pending_result or {}).get("handled")):
            return {"handled": False}

        response_text = _normalize_text((pending_result or {}).get("response_text"))
        if response_text and ctx.project_slack_client:
            try:
                footer = await build_project_context_footer(
                    db=ctx.db,
                    team_id=ctx.team_id,
                    slack_user_id=ctx.slack_user_id,
                    project=project,
                )
                await ctx.project_slack_client.chat_postMessage(
                    channel=ctx.channel,
                    text=append_project_context_footer(response_text, footer),
                    thread_ts=ctx.thread_ts,
                )
            except Exception:
                pass
        return {"handled": True, "route": "provider_type_pending_clarification"}


def _build_pending_handlers(
    *,
    enable_team_poll: bool,
    enable_work_item_parent: bool,
    enable_provider_type: bool,
    enable_action_item: bool,
) -> List[PendingInteractionHandler]:
    handlers: List[PendingInteractionHandler] = []
    if enable_team_poll and bool(getattr(settings, "team_poll_enabled", True)):
        handlers.append(TeamPollPendingHandler())
    if enable_work_item_parent:
        handlers.append(WorkItemParentPendingHandler())
    if enable_provider_type:
        handlers.append(ProviderTypePendingHandler())
    if enable_action_item and bool(getattr(settings, "action_items_enabled", False)):
        handlers.append(ActionItemPendingHandler())
    return sorted(handlers, key=lambda h: int(h.priority), reverse=True)


async def route_pending_dm_interactions(
    db: Any,
    *,
    team_id: str,
    channel: str,
    slack_user_id: str,
    text: str,
    thread_ts: Optional[str],
    get_workspace_slack_client: Callable[..., Awaitable[Any]],
    project_resolution: Optional[Any] = None,
    project_slack_client: Optional[Any] = None,
    enable_team_poll: bool = True,
    enable_work_item_parent: bool = False,
    enable_provider_type: bool = False,
    enable_action_item: bool = True,
) -> Dict[str, Any]:
    """
    Shared pending-interaction routing before Cortex fallback.

    Precedence (high -> low):
    1) Team Poll
    2) Work-item parent clarification
    3) Work-item type clarification
    4) Action Item clarification
    5) fallback (unhandled)
    """

    ctx = PendingRouteContext(
        db=db,
        team_id=_normalize_text(team_id),
        channel=_normalize_text(channel),
        slack_user_id=_normalize_text(slack_user_id),
        text=_normalize_text(text),
        thread_ts=_normalize_text(thread_ts) or None,
        get_workspace_slack_client=get_workspace_slack_client,
        project_resolution=project_resolution,
        project_slack_client=project_slack_client,
    )

    for handler in _build_pending_handlers(
        enable_team_poll=enable_team_poll,
        enable_work_item_parent=enable_work_item_parent,
        enable_provider_type=enable_provider_type,
        enable_action_item=enable_action_item,
    ):
        try:
            result = await handler.handle(ctx)
        except Exception as exc:
            if handler.fail_closed:
                logger.info(
                    "pending_router_route=%s_error fail_closed=true error=%s",
                    handler.name,
                    str(exc),
                )
                return {"handled": True, "route": f"{handler.name}_error"}
            logger.info(
                "pending_router_route=%s_error fail_closed=false error=%s",
                handler.name,
                str(exc),
            )
            continue

        if bool((result or {}).get("handled")):
            route = _normalize_text((result or {}).get("route")) or f"{handler.name}_handled"
            logger.info("pending_router_route=%s", route)
            result["route"] = route
            return result
        if bool((result or {}).get("resume_to_cortex")):
            route = _normalize_text((result or {}).get("route")) or f"{handler.name}_resume_to_cortex"
            logger.info("pending_router_route=%s", route)
            result["route"] = route
            return result

        if _normalize_text((result or {}).get("route")):
            logger.info("pending_router_route=%s", _normalize_text((result or {}).get("route")))

    logger.info("pending_router_route=fallback")
    return {"handled": False}
