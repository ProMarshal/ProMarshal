"""Shared Slack project resolver for multi-project workspaces."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Dict, List, Optional

from app.core.redis_queue import get_redis_connection
from app.core.security import encryption_service
from app.integrations.channel_index import (
    clear_active_project,
    read_active_project,
    write_active_project,
)
from app.integrations.slack.client import SlackClient


DEFAULT_PROJECT_KEY_PREFIX = "cortex:default_project"
CHANNEL_CONTEXT_PROVIDER_SLACK = "slack"


@dataclass
class SlackProjectResolution:
    """Resolution output for a Slack ingress context."""

    ok: bool
    reason: str
    project: Optional[Dict[str, Any]] = None
    member: Optional[Dict[str, Any]] = None
    user_email: Optional[str] = None
    identity_resolution_path: Optional[str] = None
    slack_access_token: Optional[str] = None
    candidates: List[str] = field(default_factory=list)
    candidate_projects: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class SlackMemberProjectList:
    """Member-visible projects and active DM context for workspace switching UX."""

    ok: bool
    reason: str
    projects: List[Dict[str, str]] = field(default_factory=list)
    active_project_id: Optional[str] = None
    user_email: Optional[str] = None
    identity_resolution_path: Optional[str] = None


def _is_dm_channel(channel_id: Optional[str]) -> bool:
    return bool(channel_id and channel_id.startswith("D"))


def _default_project_key(team_id: str, slack_user_id: str) -> str:
    return f"{DEFAULT_PROJECT_KEY_PREFIX}:{team_id}:{slack_user_id}"


def _project_name(project: Dict[str, Any]) -> str:
    return str(
        project.get("project_name")
        or project.get("name")
        or project.get("project_id")
        or "Project"
    ).strip()


def _project_candidate(project: Dict[str, Any]) -> Optional[Dict[str, str]]:
    project_id = str(project.get("project_id") or "").strip()
    if not project_id:
        return None
    return {
        "project_id": project_id,
        "project_name": _project_name(project),
    }


def _active_member_by_email(project: Dict[str, Any], email: str) -> Optional[Dict[str, Any]]:
    email_lower = email.lower()
    for member in project.get("members", []):
        if member.get("status") != "active":
            continue
        if (member.get("email") or "").lower() == email_lower:
            return member
    return None


def _active_member_by_slack_user_id(project: Dict[str, Any], slack_user_id: str) -> Optional[Dict[str, Any]]:
    normalized = str(slack_user_id or "").strip()
    if not normalized:
        return None
    for member in project.get("members", []):
        if member.get("status") != "active":
            continue
        integration_ids = member.get("integration_ids") or {}
        if str(integration_ids.get("slack_user_id") or "").strip() == normalized:
            return member
    return None


def _member_email(member: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(member, dict):
        return None
    email = str(member.get("email") or "").strip().lower()
    return email or None


def _selected_channel_ids(project: Dict[str, Any]) -> List[str]:
    slack = project.get("integrations", {}).get("slack", {})
    selected = slack.get("selected_channels") or []
    channel_ids: List[str] = []
    for item in selected:
        if isinstance(item, dict):
            value = item.get("channel_id")
            if value:
                channel_ids.append(str(value))
    return channel_ids


def _log_resolution_metrics(
    *,
    scope: str,
    team_id: str,
    slack_user_id: str,
    reason: str,
    duration_ms: float,
    candidate_count: int = 0,
    member_match_count: int = 0,
    identity_path: Optional[str] = None,
    slack_lookup_called: bool = False,
    is_dm: Optional[bool] = None,
) -> None:
    print(
        "Slack resolver metrics: "
        f"scope={scope} team_id={team_id} user={slack_user_id} reason={reason} "
        f"duration_ms={duration_ms:.1f} candidates={candidate_count} "
        f"member_matches={member_match_count} identity_path={identity_path or 'n/a'} "
        f"slack_lookup_called={slack_lookup_called} "
        f"is_dm={is_dm if is_dm is not None else 'n/a'}"
    )


async def _get_slack_user_email(
    *,
    candidates: List[Dict[str, Any]],
    slack_user_id: str,
    slack_access_token: Optional[str],
) -> tuple[Optional[str], bool]:
    token = slack_access_token
    lookup_called = False
    if not token and candidates:
        encrypted = (
            candidates[0]
            .get("integrations", {})
            .get("slack", {})
            .get("access_token")
        )
        if encrypted:
            token = encryption_service.decrypt(encrypted)

    if not token:
        return None, lookup_called

    lookup_called = True
    slack_client = SlackClient(token)
    return await slack_client.get_user_email(slack_user_id), lookup_called


async def _cache_member_slack_user_id(
    *,
    db,
    project: Dict[str, Any],
    member: Dict[str, Any],
    slack_user_id: str,
) -> None:
    project_db_id = project.get("_id")
    member_email = _member_email(member)
    normalized_user_id = str(slack_user_id or "").strip()
    if not project_db_id or not member_email or not normalized_user_id:
        return

    integration_ids = member.get("integration_ids") or {}
    if str(integration_ids.get("slack_user_id") or "").strip() == normalized_user_id:
        return

    try:
        await db.projects.update_one(
            {"_id": project_db_id, "members.email": member_email},
            {"$set": {"members.$.integration_ids.slack_user_id": normalized_user_id}},
        )
    except Exception as exc:
        print(f"Slack resolver: failed to cache slack_user_id for {member_email}: {exc}")


async def _find_projects_with_active_member(
    *,
    db,
    team_id: str,
    project_ids: List[str],
    member_email: Optional[str] = None,
    slack_user_id: Optional[str] = None,
) -> List[tuple[Dict[str, Any], Dict[str, Any]]]:
    normalized_project_ids = [str(item).strip() for item in project_ids if str(item).strip()]
    if not normalized_project_ids:
        return []

    elem_match: Dict[str, Any] = {"status": "active"}
    normalized_email = str(member_email or "").strip().lower()
    normalized_slack_user_id = str(slack_user_id or "").strip()
    if normalized_email:
        elem_match["email"] = normalized_email
    elif normalized_slack_user_id:
        elem_match["integration_ids.slack_user_id"] = normalized_slack_user_id
    else:
        return []

    query = {
        "integrations.slack.team_id": team_id,
        "integrations.slack.status": "connected",
        "project_id": {"$in": normalized_project_ids},
        "members": {"$elemMatch": elem_match},
    }
    matched_projects = await db.projects.find(query).to_list(length=len(normalized_project_ids))
    matched_projects.sort(key=lambda p: str(p.get("project_id", "")))

    matches: List[tuple[Dict[str, Any], Dict[str, Any]]] = []
    for project in matched_projects:
        member = None
        if normalized_email:
            member = _active_member_by_email(project, normalized_email)
        else:
            member = _active_member_by_slack_user_id(project, normalized_slack_user_id)
        if member:
            matches.append((project, member))
    return matches


def _merge_member_matches_by_project(
    *,
    primary_matches: List[tuple[Dict[str, Any], Dict[str, Any]]],
    secondary_matches: List[tuple[Dict[str, Any], Dict[str, Any]]],
) -> List[tuple[Dict[str, Any], Dict[str, Any]]]:
    """Merge member matches by project_id, keeping primary source precedence."""
    merged: Dict[str, tuple[Dict[str, Any], Dict[str, Any]]] = {}
    for project, member in primary_matches:
        project_id = str(project.get("project_id") or "").strip()
        if project_id:
            merged[project_id] = (project, member)
    for project, member in secondary_matches:
        project_id = str(project.get("project_id") or "").strip()
        if project_id and project_id not in merged:
            merged[project_id] = (project, member)
    items = list(merged.values())
    items.sort(key=lambda pair: str((pair[0] or {}).get("project_id") or ""))
    return items


async def _resolve_member_matches(
    *,
    db,
    team_id: str,
    project_ids: List[str],
    candidates: List[Dict[str, Any]],
    slack_user_id: str,
    slack_access_token: Optional[str],
) -> tuple[List[tuple[Dict[str, Any], Dict[str, Any]]], Optional[str], bool]:
    """
    Resolve active member matches with strict Slack ID first, then optional email fallback.

    Returns:
        (member_matches, identity_resolution_path, slack_lookup_called)
    """
    slack_id_matches = await _find_projects_with_active_member(
        db=db,
        team_id=team_id,
        project_ids=project_ids,
        slack_user_id=slack_user_id,
    )
    member_matches = list(slack_id_matches)
    if member_matches:
        return member_matches, "slack_id_only", False

    resolved_email: Optional[str] = None
    slack_lookup_called = False
    try:
        resolved_email, slack_lookup_called = await _get_slack_user_email(
            candidates=candidates,
            slack_user_id=slack_user_id,
            slack_access_token=slack_access_token,
        )
    except Exception as exc:
        print(f"Slack resolver: fallback email lookup failed for user={slack_user_id}: {exc}")
        resolved_email = None
        slack_lookup_called = False

    if not resolved_email:
        return [], None, slack_lookup_called

    email_matches = await _find_projects_with_active_member(
        db=db,
        team_id=team_id,
        project_ids=project_ids,
        member_email=resolved_email,
    )
    member_matches = _merge_member_matches_by_project(
        primary_matches=list(email_matches),
        secondary_matches=[],
    )
    for project, member in member_matches:
        await _cache_member_slack_user_id(
            db=db,
            project=project,
            member=member,
            slack_user_id=slack_user_id,
        )
    if member_matches:
        return member_matches, "slack_id_fallback_email", slack_lookup_called
    return [], None, slack_lookup_called


def _write_default_project_id(team_id: str, slack_user_id: str, project_id: str) -> None:
    redis_conn = get_redis_connection()
    if not redis_conn:
        return
    try:
        redis_conn.set(_default_project_key(team_id, slack_user_id), project_id)
    except Exception as e:
        print(f"Slack resolver: failed persisting default project key: {str(e)}")


def _clear_default_project_id(team_id: str, slack_user_id: str) -> None:
    redis_conn = get_redis_connection()
    if not redis_conn:
        return
    try:
        redis_conn.delete(_default_project_key(team_id, slack_user_id))
    except Exception as e:
        print(f"Slack resolver: failed clearing default project key: {str(e)}")


async def read_slack_active_project_from_user_context(
    *,
    db,
    team_id: str,
    slack_user_id: str,
    user_email: Optional[str],
) -> Optional[str]:
    normalized_team = str(team_id or "").strip()
    normalized_user = str(slack_user_id or "").strip()
    if not normalized_team or not normalized_user:
        return None

    try:
        doc = await read_active_project(
            db=db,
            provider=CHANNEL_CONTEXT_PROVIDER_SLACK,
            workspace_id=normalized_team,
            external_user_id=normalized_user,
        )
        active_project_id = str((doc or {}).get("active_project_id") or "").strip()
        if not active_project_id:
            print(
                "channel_index_read_miss: "
                f"provider=slack workspace_id={normalized_team} external_user_id={normalized_user}"
            )
            return None
        return active_project_id
    except Exception as exc:
        print(f"Slack resolver: failed reading channel_index active project: {exc}")
        return None


async def write_slack_active_project_to_user_context(
    *,
    db,
    team_id: str,
    slack_user_id: str,
    user_email: Optional[str],
    project_id: str,
    project_name: Optional[str] = None,
    updated_by: str = "auto_resolution",
) -> bool:
    normalized_team = str(team_id or "").strip()
    normalized_user = str(slack_user_id or "").strip()
    normalized_project_id = str(project_id or "").strip()
    if not normalized_team or not normalized_user or not normalized_project_id:
        return False

    try:
        wrote = await write_active_project(
            db=db,
            provider=CHANNEL_CONTEXT_PROVIDER_SLACK,
            workspace_id=normalized_team,
            external_user_id=normalized_user,
            project_id=normalized_project_id,
            project_name=project_name,
            updated_by=updated_by,
        )
        print(
            "channel_index_write_ok: "
            f"provider=slack workspace_id={normalized_team} external_user_id={normalized_user} "
            f"project_id={normalized_project_id} updated_by={updated_by}"
        )
        return bool(wrote)
    except Exception as exc:
        print(
            "channel_index_write_fail: "
            f"provider=slack workspace_id={normalized_team} external_user_id={normalized_user} "
            f"project_id={normalized_project_id} error={exc}"
        )
        return False


async def clear_slack_active_project_user_context(
    *,
    db,
    team_id: str,
    slack_user_id: str,
    user_email: Optional[str],
) -> None:
    normalized_team = str(team_id or "").strip()
    normalized_user = str(slack_user_id or "").strip()
    if not normalized_team or not normalized_user:
        return
    try:
        deleted_count = await clear_active_project(
            db=db,
            provider=CHANNEL_CONTEXT_PROVIDER_SLACK,
            workspace_id=normalized_team,
            external_user_id=normalized_user,
        )
        if deleted_count:
            print(
                "channel_index_clear_ok: "
                f"provider=slack workspace_id={normalized_team} external_user_id={normalized_user} "
                f"deleted_count={deleted_count}"
            )
    except Exception as exc:
        print(f"Slack resolver: failed clearing channel_index active project: {exc}")


async def persist_slack_active_project(
    *,
    db,
    team_id: str,
    slack_user_id: str,
    user_email: Optional[str],
    project_id: str,
    project_name: Optional[str] = None,
    updated_by: str = "user_selection",
) -> bool:
    normalized_project_id = str(project_id or "").strip()
    if not normalized_project_id:
        return False
    wrote = await write_slack_active_project_to_user_context(
        db=db,
        team_id=team_id,
        slack_user_id=slack_user_id,
        user_email=user_email,
        project_id=normalized_project_id,
        project_name=project_name,
        updated_by=updated_by,
    )
    _write_default_project_id(team_id, slack_user_id, normalized_project_id)
    print(
        "Slack resolver: active project persistence "
        f"team_id={team_id} slack_user_id={slack_user_id} project_id={normalized_project_id} "
        f"db_ok={wrote}"
    )
    return wrote


async def list_slack_member_projects(
    *,
    db,
    team_id: str,
    slack_user_id: str,
    slack_access_token: Optional[str] = None,
) -> SlackMemberProjectList:
    """
    List projects in a workspace where the Slack user is an active project member.

    Uses strict slack_user_id matching against active project memberships.
    Returns active project context from DB with Redis compatibility fallback.
    """
    started_at = perf_counter()
    candidates = await db.projects.find(
        {
            "integrations.slack.team_id": team_id,
            "integrations.slack.status": "connected",
        }
    ).to_list(length=200)
    candidate_count = len(candidates)
    if not candidates:
        _log_resolution_metrics(
            scope="list_member_projects",
            team_id=team_id,
            slack_user_id=slack_user_id,
            reason="no_project_for_team",
            duration_ms=(perf_counter() - started_at) * 1000.0,
        )
        return SlackMemberProjectList(ok=False, reason="no_project_for_team")

    candidates.sort(key=lambda p: str(p.get("project_id", "")))
    candidate_project_ids = [
        str(project.get("project_id") or "").strip()
        for project in candidates
        if str(project.get("project_id") or "").strip()
    ]
    member_matches, identity_resolution_path, slack_lookup_called = await _resolve_member_matches(
        db=db,
        team_id=team_id,
        project_ids=candidate_project_ids,
        candidates=candidates,
        slack_user_id=slack_user_id,
        slack_access_token=slack_access_token,
    )

    if not member_matches:
        _log_resolution_metrics(
            scope="list_member_projects",
            team_id=team_id,
            slack_user_id=slack_user_id,
            reason="user_not_member",
            duration_ms=(perf_counter() - started_at) * 1000.0,
            candidate_count=candidate_count,
            identity_path=identity_resolution_path,
            slack_lookup_called=slack_lookup_called,
        )
        return SlackMemberProjectList(
            ok=False,
            reason="user_not_member",
            user_email=None,
            identity_resolution_path=identity_resolution_path,
        )

    dedup: Dict[str, Dict[str, str]] = {}
    fallback_email: Optional[str] = None
    for project, member in member_matches:
        item = _project_candidate(project)
        if item:
            dedup[str(item.get("project_id") or "").strip()] = item
        if not fallback_email:
            fallback_email = _member_email(member)

    projects = [item for item in dedup.values() if str(item.get("project_id") or "").strip()]
    projects.sort(
        key=lambda item: (
            str(item.get("project_name") or "").strip().lower(),
            str(item.get("project_id") or "").strip().lower(),
        )
    )

    active_project_id = await read_slack_active_project_from_user_context(
        db=db,
        team_id=team_id,
        slack_user_id=slack_user_id,
        user_email=fallback_email,
    )

    valid_project_ids = {str(item.get("project_id") or "").strip() for item in projects}
    if active_project_id and active_project_id not in valid_project_ids:
        _clear_default_project_id(team_id, slack_user_id)
        await clear_slack_active_project_user_context(
            db=db,
            team_id=team_id,
            slack_user_id=slack_user_id,
            user_email=fallback_email,
        )
        active_project_id = None

    _log_resolution_metrics(
        scope="list_member_projects",
        team_id=team_id,
        slack_user_id=slack_user_id,
        reason="ok",
        duration_ms=(perf_counter() - started_at) * 1000.0,
        candidate_count=candidate_count,
        member_match_count=len(member_matches),
        identity_path=identity_resolution_path,
        slack_lookup_called=slack_lookup_called,
        is_dm=True,
    )
    return SlackMemberProjectList(
        ok=True,
        reason="ok",
        projects=projects,
        active_project_id=active_project_id,
        user_email=fallback_email,
        identity_resolution_path=identity_resolution_path,
    )


async def resolve_slack_project(
    *,
    db,
    team_id: str,
    slack_user_id: str,
    channel_id: Optional[str],
    slack_access_token: Optional[str] = None,
) -> SlackProjectResolution:
    """
    Resolve project context for Slack ingress using deterministic multi-project chain:
    1) team_id candidates
    2) channel mapping narrowing (non-DM)
    3) active membership narrowing (strict Slack ID match)
    4) DM disambiguation with persisted active project (channel_index authoritative)
    """
    started_at = perf_counter()
    is_dm = _is_dm_channel(channel_id)

    # Fast path for DMs: use persisted active project first when available.
    if is_dm:
        persisted_project_id = await read_slack_active_project_from_user_context(
            db=db,
            team_id=team_id,
            slack_user_id=slack_user_id,
            user_email=None,
        )

        if persisted_project_id:
            active_project = await db.projects.find_one(
                {
                    "project_id": str(persisted_project_id),
                    "integrations.slack.team_id": team_id,
                    "integrations.slack.status": "connected",
                }
            )

            if active_project:
                active_member = _active_member_by_slack_user_id(active_project, slack_user_id)
                active_user_email: Optional[str] = _member_email(active_member)
                identity_path: Optional[str] = "slack_id_only" if active_member else None

                if active_member:
                    resolved_email = _member_email(active_member) or active_user_email
                    _write_default_project_id(team_id, slack_user_id, str(persisted_project_id))
                    if resolved_email:
                        await write_slack_active_project_to_user_context(
                            db=db,
                            team_id=team_id,
                            slack_user_id=slack_user_id,
                            user_email=resolved_email,
                            project_id=str(persisted_project_id),
                            project_name=_project_name(active_project),
                            updated_by="resolved_via_persisted_active_project",
                        )
                    encrypted_token = (
                        ((active_project.get("integrations") or {}).get("slack") or {}).get("access_token")
                    )
                    _log_resolution_metrics(
                        scope="resolve_project",
                        team_id=team_id,
                        slack_user_id=slack_user_id,
                        reason="resolved_via_default_project_fast_path",
                        duration_ms=(perf_counter() - started_at) * 1000.0,
                        candidate_count=1,
                        member_match_count=1,
                        identity_path=identity_path,
                        slack_lookup_called=False,
                        is_dm=True,
                    )
                    return SlackProjectResolution(
                        ok=True,
                        reason="resolved_via_default_project",
                        project=active_project,
                        member=active_member,
                        user_email=resolved_email,
                        identity_resolution_path=identity_path,
                        slack_access_token=encryption_service.decrypt(encrypted_token) if encrypted_token else None,
                    )

            # Persisted key is stale or no longer valid for this user.
            _clear_default_project_id(team_id, slack_user_id)
            await clear_slack_active_project_user_context(
                db=db,
                team_id=team_id,
                slack_user_id=slack_user_id,
                user_email=None,
            )

    candidates = await db.projects.find(
        {
            "integrations.slack.team_id": team_id,
            "integrations.slack.status": "connected",
        }
    ).to_list(length=200)
    candidate_count = len(candidates)

    if not candidates:
        _log_resolution_metrics(
            scope="resolve_project",
            team_id=team_id,
            slack_user_id=slack_user_id,
            reason="no_project_for_team",
            duration_ms=(perf_counter() - started_at) * 1000.0,
            is_dm=is_dm,
        )
        return SlackProjectResolution(
            ok=False,
            reason="no_project_for_team",
        )

    # Deterministic ordering for stable tie-breaking and diagnostics.
    candidates.sort(key=lambda p: str(p.get("project_id", "")))

    narrowed = candidates

    if not is_dm and channel_id:
        mapped = [p for p in candidates if channel_id in _selected_channel_ids(p)]
        if mapped:
            narrowed = mapped
        else:
            # Compatibility path: if nothing is configured yet, keep candidates.
            any_mapping_configured = any(_selected_channel_ids(p) for p in candidates)
            if any_mapping_configured:
                _log_resolution_metrics(
                    scope="resolve_project",
                    team_id=team_id,
                    slack_user_id=slack_user_id,
                    reason="channel_not_mapped",
                    duration_ms=(perf_counter() - started_at) * 1000.0,
                    candidate_count=candidate_count,
                    is_dm=is_dm,
                )
                return SlackProjectResolution(
                    ok=False,
                    reason="channel_not_mapped",
                    candidates=[p.get("project_id") for p in candidates if p.get("project_id")],
                    candidate_projects=[
                        item for item in (_project_candidate(p) for p in candidates) if item
                    ],
                )

    narrowed_project_ids = [
        str(project.get("project_id") or "").strip()
        for project in narrowed
        if str(project.get("project_id") or "").strip()
    ]
    member_matches, identity_resolution_path, slack_lookup_called = await _resolve_member_matches(
        db=db,
        team_id=team_id,
        project_ids=narrowed_project_ids,
        candidates=narrowed,
        slack_user_id=slack_user_id,
        slack_access_token=slack_access_token,
    )

    if not member_matches:
        _log_resolution_metrics(
            scope="resolve_project",
            team_id=team_id,
            slack_user_id=slack_user_id,
            reason="user_not_member",
            duration_ms=(perf_counter() - started_at) * 1000.0,
            candidate_count=candidate_count,
            identity_path=identity_resolution_path,
            slack_lookup_called=slack_lookup_called,
            is_dm=is_dm,
        )
        return SlackProjectResolution(
            ok=False,
            reason="user_not_member",
            user_email=None,
            identity_resolution_path=identity_resolution_path,
            candidates=[p.get("project_id") for p in narrowed if p.get("project_id")],
            candidate_projects=[
                item for item in (_project_candidate(p) for p in narrowed) if item
            ],
        )

    if len(member_matches) == 1:
        project, member = member_matches[0]
        resolved_email = _member_email(member)
        project_id = project.get("project_id")
        if is_dm and project_id:
            _write_default_project_id(team_id, slack_user_id, project_id)
            await write_slack_active_project_to_user_context(
                db=db,
                team_id=team_id,
                slack_user_id=slack_user_id,
                user_email=resolved_email,
                project_id=project_id,
                project_name=_project_name(project),
                updated_by="auto_unique_dm_resolution",
            )
        encrypted_token = project.get("integrations", {}).get("slack", {}).get("access_token")
        _log_resolution_metrics(
            scope="resolve_project",
            team_id=team_id,
            slack_user_id=slack_user_id,
            reason="resolved",
            duration_ms=(perf_counter() - started_at) * 1000.0,
            candidate_count=candidate_count,
            member_match_count=1,
            identity_path=identity_resolution_path,
            slack_lookup_called=slack_lookup_called,
            is_dm=is_dm,
        )
        return SlackProjectResolution(
            ok=True,
            reason="resolved",
            project=project,
            member=member,
            user_email=resolved_email,
            identity_resolution_path=identity_resolution_path,
            slack_access_token=encryption_service.decrypt(encrypted_token) if encrypted_token else None,
        )

    # DM ambiguity flow: try persisted active project from channel_index.
    if is_dm:
        context_email: Optional[str] = None
        if not context_email:
            for _project, member in member_matches:
                context_email = _member_email(member)
                if context_email:
                    break

        persisted_project_id = await read_slack_active_project_from_user_context(
            db=db,
            team_id=team_id,
            slack_user_id=slack_user_id,
            user_email=context_email,
        )
        if persisted_project_id:
            for project, member in member_matches:
                if project.get("project_id") == persisted_project_id:
                    resolved_email = _member_email(member)
                    encrypted_token = project.get("integrations", {}).get("slack", {}).get("access_token")
                    await write_slack_active_project_to_user_context(
                        db=db,
                        team_id=team_id,
                        slack_user_id=slack_user_id,
                        user_email=resolved_email,
                        project_id=str(persisted_project_id),
                        project_name=_project_name(project),
                        updated_by="resolved_via_persisted_active_project",
                    )
                    _log_resolution_metrics(
                        scope="resolve_project",
                        team_id=team_id,
                        slack_user_id=slack_user_id,
                        reason="resolved_via_default_project",
                        duration_ms=(perf_counter() - started_at) * 1000.0,
                        candidate_count=candidate_count,
                        member_match_count=len(member_matches),
                        identity_path=identity_resolution_path,
                        slack_lookup_called=slack_lookup_called,
                        is_dm=is_dm,
                    )
                    return SlackProjectResolution(
                        ok=True,
                        reason="resolved_via_default_project",
                        project=project,
                        member=member,
                        user_email=resolved_email,
                        identity_resolution_path=identity_resolution_path,
                        slack_access_token=encryption_service.decrypt(encrypted_token) if encrypted_token else None,
                    )
            # Persisted key is stale or no longer valid for this user in current membership.
            _clear_default_project_id(team_id, slack_user_id)
            await clear_slack_active_project_user_context(
                db=db,
                team_id=team_id,
                slack_user_id=slack_user_id,
                user_email=context_email,
            )

    candidate_projects = [
        item for item in (_project_candidate(project) for project, _m in member_matches) if item
    ]
    fallback_email: Optional[str] = None
    if not fallback_email:
        for _project, member in member_matches:
            fallback_email = _member_email(member)
            if fallback_email:
                break
    fallback_token = None
    for project, _member in member_matches:
        encrypted_token = ((project.get("integrations") or {}).get("slack") or {}).get("access_token")
        if encrypted_token:
            try:
                fallback_token = encryption_service.decrypt(encrypted_token)
            except Exception:
                fallback_token = None
            if fallback_token:
                break

    _log_resolution_metrics(
        scope="resolve_project",
        team_id=team_id,
        slack_user_id=slack_user_id,
        reason="ambiguous_project",
        duration_ms=(perf_counter() - started_at) * 1000.0,
        candidate_count=candidate_count,
        member_match_count=len(member_matches),
        identity_path=identity_resolution_path,
        slack_lookup_called=slack_lookup_called,
        is_dm=is_dm,
    )
    return SlackProjectResolution(
        ok=False,
        reason="ambiguous_project",
        user_email=fallback_email,
        identity_resolution_path=identity_resolution_path,
        candidates=[p.get("project_id") for p, _m in member_matches if p.get("project_id")],
        candidate_projects=candidate_projects,
        slack_access_token=fallback_token,
    )
