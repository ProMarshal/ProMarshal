"""Provider-agnostic transition fetcher for Cadence session creation.

Each supported PM provider implements its own fetch path. Unknown or
unimplemented providers return an empty list so callers degrade gracefully.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger("app.cadence.transition_fetcher")


async def fetch_available_transitions(
    *,
    db: Any = None,
    project: Dict[str, Any],
    task_key: str,
    task_provider: str,
) -> List[Dict[str, str]]:
    """Return available transitions for a task from the PM provider.

    Returns a list of dicts: [{"id": "31", "name": "Done"}, ...]

    On any failure returns [] so callers fall back gracefully.
    The caller (checkin.py) stores these per task in the session doc.
    """
    normalized_provider = str(task_provider or "").strip().lower()
    try:
        if normalized_provider == "jira":
            return await _fetch_jira_transitions(db=db, project=project, task_key=task_key)
        # Linear and other providers: stub — return empty until implemented
        logger.debug(
            "cadence_transition_fetch_skipped provider=%s task=%s reason=not_implemented",
            normalized_provider,
            task_key,
        )
        return []
    except Exception as exc:
        logger.warning(
            "cadence_transition_fetch_failed provider=%s task=%s error=%s",
            normalized_provider,
            task_key,
            exc,
        )
        return []


async def _fetch_jira_transitions(
    *,
    db: Any = None,
    project: Dict[str, Any],
    task_key: str,
) -> List[Dict[str, str]]:
    """Fetch available Jira transitions for a task and return normalized list."""
    from app.core.security import encryption_service
    from app.cadence.action_adapters.providers.jira_task_adapters import _resolve_jira_access_token
    from app.integrations.jira.oauth import jira_oauth_service

    jira_integration = (project.get("integrations") or {}).get("jira") or {}
    encrypted_token = jira_integration.get("access_token")
    cloud_id = str(jira_integration.get("cloud_id") or "").strip()

    if not encrypted_token or not cloud_id:
        logger.debug(
            "cadence_transition_fetch_skipped provider=jira task=%s reason=missing_token_or_cloud_id",
            task_key,
        )
        return []

    project_id = str(project.get("project_id") or "").strip()
    if db is not None and project_id:
        access_token = await _resolve_jira_access_token(
            db,
            project_id=project_id,
            jira_integration=jira_integration,
        )
    else:
        access_token = encryption_service.decrypt(encrypted_token)
    raw_transitions = await jira_oauth_service.get_available_transitions(
        access_token, cloud_id, task_key
    )

    if not raw_transitions and db is not None and project_id:
        encrypted_refresh = jira_integration.get("refresh_token")
        if encrypted_refresh:
            try:
                refresh_token = encryption_service.decrypt(encrypted_refresh)
                token_data = await jira_oauth_service.refresh_access_token(refresh_token)
                refreshed_access = str(token_data.get("access_token") or "").strip()
                if refreshed_access:
                    raw_transitions = await jira_oauth_service.get_available_transitions(
                        refreshed_access,
                        cloud_id,
                        task_key,
                    )
            except Exception as exc:
                logger.warning(
                    "cadence_transition_fetch_refresh_retry_failed provider=jira task=%s error=%s",
                    task_key,
                    exc,
                )

    result: List[Dict[str, str]] = []
    for t in raw_transitions:
        t_id = str(t.get("id") or "").strip()
        t_name = str((t.get("to") or {}).get("name") or "").strip()
        if t_id and t_name:
            result.append({"id": t_id, "name": t_name})

    logger.debug(
        "cadence_transitions_fetched provider=jira task=%s count=%d transitions=%s",
        task_key,
        len(result),
        [r["name"] for r in result],
    )
    return result
