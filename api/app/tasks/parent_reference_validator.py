"""Shared parent-reference validation helpers for work-item parent mutations."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.database import get_brain_collection
from app.core.security import encryption_service
from app.integrations.jira.oauth import jira_oauth_service


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_parent_key(value: Any) -> str:
    return _normalize_text(value).upper()


def _extract_provider_type_from_jira_issue(issue: Dict[str, Any]) -> str:
    fields = issue.get("fields") if isinstance(issue, dict) else {}
    issue_type = fields.get("issuetype") if isinstance(fields, dict) else {}
    return _normalize_text((issue_type or {}).get("name")) or "Task"


def build_parent_reference_not_available_message(parent_external_id: str) -> str:
    key = _normalize_parent_key(parent_external_id)
    return (
        f"Parent `{key}` is not available in this project. "
        "Please provide a valid parent work item key."
    )


async def resolve_parent_reference(
    *,
    project: Dict[str, Any],
    parent_external_id: str,
) -> Dict[str, Any]:
    """
    Resolve parent reference with Brain-first lookup and Jira-live fallback.

    Returns:
      {
        "exists": bool,
        "parent_external_id": str,
        "source": "brain" | "jira_live" | "unverified",
        "provider_type": str,
        "work_item_type": Optional[str],
        "status": Optional[str],
        "project_key": Optional[str],
        "reason": Optional[str],
      }
    """
    parent_key = _normalize_parent_key(parent_external_id)
    project_id = _normalize_text(project.get("project_id"))
    if not parent_key or not project_id:
        return {
            "exists": False,
            "parent_external_id": parent_key,
            "source": "unverified",
            "reason": "missing_input",
        }

    collection = get_brain_collection(project_id)
    brain_doc = await collection.find_one(
        {"entity_type": "work_item", "external_id": parent_key},
        {
            "_id": 0,
            "external_id": 1,
            "provider_type": 1,
            "work_item_type": 1,
            "status": 1,
            "integration_type": 1,
            "provider": 1,
        },
    )
    if isinstance(brain_doc, dict):
        return {
            "exists": True,
            "parent_external_id": parent_key,
            "source": "brain",
            "provider_type": _normalize_text(brain_doc.get("provider_type")) or "Task",
            "work_item_type": _normalize_text(brain_doc.get("work_item_type")) or None,
            "status": _normalize_text(brain_doc.get("status")) or None,
        }

    jira_integration = ((project.get("integrations") or {}).get("jira") or {})
    cloud_id = _normalize_text(jira_integration.get("cloud_id"))
    if not cloud_id:
        return {
            "exists": False,
            "parent_external_id": parent_key,
            "source": "unverified",
            "reason": "parent_not_found",
        }

    encrypted_access_token = _normalize_text(jira_integration.get("access_token"))
    if not encrypted_access_token:
        return {
            "exists": False,
            "parent_external_id": parent_key,
            "source": "unverified",
            "reason": "jira_token_missing",
        }

    try:
        access_token = encryption_service.decrypt(encrypted_access_token)
    except Exception:
        return {
            "exists": False,
            "parent_external_id": parent_key,
            "source": "unverified",
            "reason": "jira_token_unreadable",
        }

    try:
        issue = await jira_oauth_service.get_issue(
            access_token=access_token,
            cloud_id=cloud_id,
            issue_key=parent_key,
        )
    except Exception:
        return {
            "exists": False,
            "parent_external_id": parent_key,
            "source": "unverified",
            "reason": "parent_not_found",
        }

    fields = issue.get("fields") if isinstance(issue, dict) else {}
    project_key = _normalize_text(
        ((fields.get("project") or {}).get("key") if isinstance(fields, dict) else "")
    ) or None
    return {
        "exists": True,
        "parent_external_id": parent_key,
        "source": "jira_live",
        "provider_type": _extract_provider_type_from_jira_issue(issue),
        "work_item_type": None,
        "status": _normalize_text(((fields or {}).get("status") or {}).get("name")) or None,
        "project_key": project_key,
    }

