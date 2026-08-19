"""Jira-specific runtime helpers for Cortex tool adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.integrations.jira.oauth import jira_oauth_service
from app.tasks.providers.jira_adapter import JiraAdapter


@dataclass(frozen=True)
class AssigneeResolutionResult:
    """Resolution output for name/email -> Jira account lookup."""

    account_id: Optional[str]
    resolved_email: Optional[str] = None
    resolved_name: Optional[str] = None
    matched_via: Optional[str] = None
    ambiguous: bool = False
    candidates: Optional[List[str]] = None
    reason: Optional[str] = None
    project_member_matched: bool = False
    project_member_email: Optional[str] = None
    project_member_name: Optional[str] = None
    jira_mapping_present: bool = False
    mapping_backfill_attempted: bool = False
    mapping_backfill_succeeded: bool = False
    mapping_conflict: bool = False
    mapping_existing_account_id: Optional[str] = None
    mapping_candidate_account_id: Optional[str] = None


@dataclass(frozen=True)
class JiraMappingSyncResult:
    """Detailed mapping sync status for safe backfill handling."""

    attempted: bool
    succeeded: bool
    conflict: bool = False
    existing_account_id: Optional[str] = None
    candidate_account_id: Optional[str] = None
    reason: Optional[str] = None


def _normalize_person_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _candidate_label(*, name: str, email: str) -> str:
    normalized_name = str(name or "").strip()
    normalized_email = str(email or "").strip().lower()
    if normalized_name and normalized_email:
        return f"{normalized_name} <{normalized_email}>"
    return normalized_name or normalized_email


def _match_project_member(
    *,
    members: List[Dict[str, Any]],
    hint: str,
) -> tuple[Optional[Dict[str, Any]], Optional[str], List[str]]:
    exact_email: List[Dict[str, Any]] = []
    exact_name: List[Dict[str, Any]] = []
    partial: List[Dict[str, Any]] = []

    for member in members:
        if not isinstance(member, dict):
            continue
        if str(member.get("status") or "").strip().lower() != "active":
            continue
        email = _normalize_person_text(member.get("email"))
        name = _normalize_person_text(member.get("name"))
        if email and email == hint:
            exact_email.append(member)
            continue
        if name and name == hint:
            exact_name.append(member)
            continue
        if hint and ((email and hint in email) or (name and hint in name)):
            partial.append(member)

    def labels(items: List[Dict[str, Any]]) -> List[str]:
        deduped: List[str] = []
        for member in items:
            label = _candidate_label(
                name=str(member.get("name") or ""),
                email=str(member.get("email") or ""),
            )
            if label and label not in deduped:
                deduped.append(label)
        return deduped[:5]

    if len(exact_email) == 1:
        return exact_email[0], "member_email_exact", []
    if len(exact_email) > 1:
        return None, None, labels(exact_email)

    if len(exact_name) == 1:
        return exact_name[0], "member_name_exact", []
    if len(exact_name) > 1:
        return None, None, labels(exact_name)

    if len(partial) == 1:
        return partial[0], "member_partial", []
    if len(partial) > 1:
        return None, None, labels(partial)

    return None, None, []


def _match_workspace_user(
    *,
    users: List[Dict[str, Any]],
    hint: str,
) -> tuple[Optional[Dict[str, Any]], Optional[str], List[str]]:
    exact_email: List[Dict[str, Any]] = []
    exact_name: List[Dict[str, Any]] = []
    partial: List[Dict[str, Any]] = []

    for user in users:
        if not isinstance(user, dict):
            continue
        email = _normalize_person_text(user.get("emailAddress"))
        name = _normalize_person_text(user.get("displayName"))
        if email and email == hint:
            exact_email.append(user)
            continue
        if name and name == hint:
            exact_name.append(user)
            continue
        if hint and ((email and hint in email) or (name and hint in name)):
            partial.append(user)

    def labels(items: List[Dict[str, Any]]) -> List[str]:
        deduped: List[str] = []
        for user in items:
            label = _candidate_label(
                name=str(user.get("displayName") or ""),
                email=str(user.get("emailAddress") or ""),
            )
            if label and label not in deduped:
                deduped.append(label)
        return deduped[:5]

    if len(exact_email) == 1:
        return exact_email[0], "jira_email_exact", []
    if len(exact_email) > 1:
        return None, None, labels(exact_email)

    if len(exact_name) == 1:
        return exact_name[0], "jira_name_exact", []
    if len(exact_name) > 1:
        return None, None, labels(exact_name)

    if len(partial) == 1:
        return partial[0], "jira_partial", []
    if len(partial) > 1:
        return None, None, labels(partial)

    return None, None, []


def _find_workspace_user_by_email(
    *,
    users: List[Dict[str, Any]],
    email: str,
) -> Optional[Dict[str, Any]]:
    normalized_email = _normalize_person_text(email)
    if not normalized_email:
        return None
    for user in users:
        if not isinstance(user, dict):
            continue
        if _normalize_person_text(user.get("emailAddress")) == normalized_email:
            return user
    return None


async def _persist_member_jira_mapping(
    *,
    adapter: JiraAdapter,
    project: Dict[str, Any],
    member_email: str,
    jira_account_id: str,
) -> JiraMappingSyncResult:
    try:
        from app.core.database import get_database
    except Exception:
        return JiraMappingSyncResult(
            attempted=True,
            succeeded=False,
            reason="database_unavailable",
            candidate_account_id=str(jira_account_id or "").strip() or None,
        )

    db = get_database()
    if db is None:
        return JiraMappingSyncResult(
            attempted=True,
            succeeded=False,
            reason="database_unavailable",
            candidate_account_id=str(jira_account_id or "").strip() or None,
        )

    normalized_email = str(member_email or "").strip().lower()
    normalized_account_id = str(jira_account_id or "").strip()
    if not normalized_email or not normalized_account_id:
        return JiraMappingSyncResult(
            attempted=True,
            succeeded=False,
            reason="invalid_mapping_input",
            candidate_account_id=normalized_account_id or None,
        )

    raw_project_id = str(project.get("_id") or "").strip() or str(project.get("project_id") or "").strip()
    sync_result = await jira_oauth_service.sync_single_member_jira_id_detailed(
        db=db,
        project_id=raw_project_id,
        member_email=normalized_email,
        access_token=str(adapter.access_token or ""),
        cloud_id=str(adapter.cloud_id or ""),
        jira_account_id=normalized_account_id,
        allow_overwrite=False,
    )

    return JiraMappingSyncResult(
        attempted=True,
        succeeded=bool(sync_result.get("ok")),
        conflict=bool(sync_result.get("conflict")),
        existing_account_id=str(sync_result.get("existing_account_id") or "").strip() or None,
        candidate_account_id=str(sync_result.get("jira_account_id") or normalized_account_id).strip() or None,
        reason=str(sync_result.get("reason") or "").strip() or None,
    )


async def resolve_assignee_identity(
    project: Dict[str, Any],
    *,
    assignee_hint: str,
) -> AssigneeResolutionResult:
    """
    Resolve assignee by email or name into Jira account id.

    Matching order:
    1) Active project member cache (email/name exact, then partial)
    2) Jira workspace users (email/name exact, then partial)
    3) If member matched but Jira ID was missing, backfill mapping into project record
    """
    hint = _normalize_person_text(assignee_hint)
    if not hint:
        return AssigneeResolutionResult(
            account_id=None,
            reason="empty_assignee_hint",
        )

    members = [item for item in (project.get("members") or []) if isinstance(item, dict)]
    member_match, member_via, member_candidates = _match_project_member(
        members=members,
        hint=hint,
    )
    if member_candidates:
        return AssigneeResolutionResult(
            account_id=None,
            ambiguous=True,
            candidates=member_candidates,
            reason="ambiguous_project_member",
        )

    member_email: Optional[str] = None
    member_name: Optional[str] = None
    if member_match:
        member_email = str(member_match.get("email") or "").strip().lower() or None
        member_name = str(member_match.get("name") or "").strip() or None
        integration_ids = member_match.get("integration_ids") or {}
        jira_account_id = str(integration_ids.get("jira_account_id") or "").strip()
        if jira_account_id:
            return AssigneeResolutionResult(
                account_id=jira_account_id,
                resolved_email=member_email,
                resolved_name=member_name,
                matched_via=member_via,
                project_member_matched=True,
                project_member_email=member_email,
                project_member_name=member_name,
                jira_mapping_present=True,
            )

    try:
        adapter = JiraAdapter(project)
        users = await adapter.get_workspace_users()
    except Exception as exc:
        print(f"Cortex Jira assignee lookup failed for {hint}: {exc}")
        return AssigneeResolutionResult(
            account_id=None,
            reason="jira_workspace_lookup_failed",
            project_member_matched=bool(member_match),
            project_member_email=member_email,
            project_member_name=member_name,
            jira_mapping_present=False,
        )

    if not isinstance(users, list):
        users = []

    # Prefer exact email match for already-resolved project member; this makes
    # member->Jira mapping deterministic and allows automatic backfill.
    if member_match and member_email:
        workspace_exact = _find_workspace_user_by_email(users=users, email=member_email)
        if workspace_exact:
            account_id = str(workspace_exact.get("accountId") or "").strip()
            if account_id:
                mapping_sync = await _persist_member_jira_mapping(
                    adapter=adapter,
                    project=project,
                    member_email=member_email,
                    jira_account_id=account_id,
                )
                if mapping_sync.succeeded:
                    print(
                        f"Cortex Jira mapping backfill succeeded: "
                        f"{member_email} -> {account_id}"
                    )
                else:
                    print(
                        "Cortex Jira mapping backfill skipped/failed: "
                        f"{member_email} -> {account_id} "
                        f"(reason={mapping_sync.reason}, conflict={mapping_sync.conflict})"
                    )
                return AssigneeResolutionResult(
                    account_id=account_id,
                    resolved_email=member_email,
                    resolved_name=member_name
                    or str(workspace_exact.get("displayName") or "").strip()
                    or None,
                    matched_via="jira_email_exact_backfill",
                    project_member_matched=True,
                    project_member_email=member_email,
                    project_member_name=member_name,
                    jira_mapping_present=True,
                    reason="jira_mapping_conflict" if mapping_sync.conflict else None,
                    mapping_backfill_attempted=True,
                    mapping_backfill_succeeded=mapping_sync.succeeded,
                    mapping_conflict=mapping_sync.conflict,
                    mapping_existing_account_id=mapping_sync.existing_account_id,
                    mapping_candidate_account_id=mapping_sync.candidate_account_id,
                )

    workspace_match, workspace_via, workspace_candidates = _match_workspace_user(
        users=users,
        hint=hint,
    )
    if workspace_candidates:
        return AssigneeResolutionResult(
            account_id=None,
            ambiguous=True,
            candidates=workspace_candidates,
            reason="ambiguous_jira_user",
            project_member_matched=bool(member_match),
            project_member_email=member_email,
            project_member_name=member_name,
            jira_mapping_present=False,
        )

    if workspace_match:
        account_id = str(workspace_match.get("accountId") or "").strip()
        if account_id:
            resolved_email = (
                str(workspace_match.get("emailAddress") or "").strip().lower() or None
            )
            resolved_name = str(workspace_match.get("displayName") or "").strip() or None

            mapping_attempted = False
            mapping_succeeded = False
            mapping_conflict = False
            existing_account_id: Optional[str] = None
            candidate_account_id: Optional[str] = None
            if member_match and member_email:
                mapping_attempted = True
                mapping_sync = await _persist_member_jira_mapping(
                    adapter=adapter,
                    project=project,
                    member_email=member_email,
                    jira_account_id=account_id,
                )
                mapping_succeeded = mapping_sync.succeeded
                mapping_conflict = mapping_sync.conflict
                existing_account_id = mapping_sync.existing_account_id
                candidate_account_id = mapping_sync.candidate_account_id
                if mapping_succeeded:
                    print(
                        f"Cortex Jira mapping backfill succeeded: "
                        f"{member_email} -> {account_id}"
                    )
                else:
                    print(
                        "Cortex Jira mapping backfill skipped/failed: "
                        f"{member_email} -> {account_id} "
                        f"(reason={mapping_sync.reason}, conflict={mapping_sync.conflict})"
                    )

            return AssigneeResolutionResult(
                account_id=account_id,
                resolved_email=resolved_email or member_email,
                resolved_name=resolved_name or member_name,
                matched_via=workspace_via,
                project_member_matched=bool(member_match),
                project_member_email=member_email,
                project_member_name=member_name,
                jira_mapping_present=True,
                reason="jira_mapping_conflict" if mapping_conflict else None,
                mapping_backfill_attempted=mapping_attempted,
                mapping_backfill_succeeded=mapping_succeeded,
                mapping_conflict=mapping_conflict,
                mapping_existing_account_id=existing_account_id,
                mapping_candidate_account_id=candidate_account_id,
            )

    if member_match:
        return AssigneeResolutionResult(
            account_id=None,
            reason="project_member_missing_jira_mapping",
            project_member_matched=True,
            project_member_email=member_email,
            project_member_name=member_name,
            jira_mapping_present=False,
        )

    return AssigneeResolutionResult(
        account_id=None,
        reason="not_found",
        project_member_matched=False,
        jira_mapping_present=False,
    )


async def resolve_assignee_account_id(
    project: Dict[str, Any],
    *,
    assignee_email: str,
) -> Optional[str]:
    """
    Resolve Jira account id for assignee email.

    Policy:
    - Prefer project member cache (`members[].integration_ids.jira_account_id`)
    - Fallback to Jira workspace user lookup
    """
    result = await resolve_assignee_identity(
        project,
        assignee_hint=str(assignee_email or ""),
    )
    return result.account_id
