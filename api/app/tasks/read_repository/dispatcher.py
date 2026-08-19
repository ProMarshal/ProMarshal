"""Task read repository dispatcher with active-provider routing policy."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.database import get_database
from app.domain.capabilities.materialization import build_capability_context
from app.tasks.read_repository.base import TaskReadRepository
from app.tasks.read_repository.providers.jira_repository import JiraTaskReadRepository
from app.tasks.read_repository.providers.linear_repository import LinearTaskReadRepository
from app.tasks.schemas import ListTasksFilters, TaskResponseDTO


class TaskReadClarificationRequired(ValueError):
    """Raised when task reads need explicit provider selection."""

    def __init__(
        self,
        *,
        reason_code: str,
        message: str,
        candidate_providers: Optional[list[str]] = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = str(reason_code or "").strip().lower() or "clarification_required"
        self.candidate_providers = list(candidate_providers or [])


class TaskReadDispatcher:
    """Route read operations to provider-scoped repositories."""

    def __init__(self) -> None:
        self._repositories: dict[str, TaskReadRepository] = {}

    @staticmethod
    def _normalize_provider(value: Optional[str]) -> Optional[str]:
        normalized = str(value or "").strip().lower()
        return normalized or None

    def register(self, provider: str, repository: TaskReadRepository) -> None:
        normalized_provider = self._normalize_provider(provider)
        if not normalized_provider:
            raise ValueError("Provider is required for task read repository registration")
        self._repositories[normalized_provider] = repository

    async def list_tasks(
        self,
        *,
        project_id: str,
        filters: Optional[ListTasksFilters] = None,
        provider_hint: Optional[str] = None,
        project_context: Optional[Dict[str, Any]] = None,
    ) -> list[TaskResponseDTO]:
        resolved_provider = await self._resolve_provider(
            project_id=project_id,
            provider_hint=provider_hint,
            project_context=project_context,
        )
        repository = self._repositories[resolved_provider]
        return await repository.list_tasks(project_id=project_id, filters=filters)

    async def search_tasks(
        self,
        *,
        project_id: str,
        query: str,
        limit: int = 50,
        provider_hint: Optional[str] = None,
        project_context: Optional[Dict[str, Any]] = None,
    ) -> list[TaskResponseDTO]:
        resolved_provider = await self._resolve_provider(
            project_id=project_id,
            provider_hint=provider_hint,
            project_context=project_context,
        )
        repository = self._repositories[resolved_provider]
        return await repository.search_tasks(project_id=project_id, query=query, limit=limit)

    async def get_task(
        self,
        *,
        project_id: str,
        external_id: str,
        provider_hint: Optional[str] = None,
        project_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[TaskResponseDTO]:
        resolved_provider = await self._resolve_provider(
            project_id=project_id,
            provider_hint=provider_hint,
            project_context=project_context,
        )
        repository = self._repositories[resolved_provider]
        return await repository.get_task(project_id=project_id, external_id=external_id)

    async def _resolve_provider(
        self,
        *,
        project_id: str,
        provider_hint: Optional[str],
        project_context: Optional[Dict[str, Any]],
    ) -> str:
        hinted_provider = self._normalize_provider(provider_hint)
        if hinted_provider:
            repository = self._repositories.get(hinted_provider)
            if repository is None:
                raise ValueError(f"Task read provider `{hinted_provider}` is not registered")
            return hinted_provider

        context = await self._resolve_project_context(
            project_id=project_id,
            project_context=project_context,
        )
        if context:
            capability_context = build_capability_context(project_context=context)
            active_provider = self._normalize_provider(capability_context.active_provider)
            if active_provider:
                repository = self._repositories.get(active_provider)
                if repository is None:
                    raise ValueError(
                        f"Active provider `{active_provider}` has no task-read repository registration"
                    )
                return active_provider

            connected = list(capability_context.connected_pm_providers)
            if len(connected) > 1:
                raise TaskReadClarificationRequired(
                    reason_code="ambiguous_provider",
                    message=(
                        "Multiple connected providers are available for task reads. "
                        "Specify provider or set active_provider."
                    ),
                    candidate_providers=connected,
                )
            if len(connected) == 1:
                single_provider = self._normalize_provider(connected[0])
                if single_provider and single_provider in self._repositories:
                    return single_provider
                raise ValueError(
                    f"Connected provider `{single_provider}` has no task-read repository registration"
                )

        if "jira" in self._repositories:
            return "jira"
        if self._repositories:
            return sorted(self._repositories.keys())[0]
        raise ValueError("No task-read repositories are registered")

    async def _resolve_project_context(
        self,
        *,
        project_id: str,
        project_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if isinstance(project_context, dict):
            return project_context

        db = get_database()
        if db is None:
            return {}

        project_doc = await db.projects.find_one(
            {"project_id": str(project_id or "").strip()},
            {
                "project_id": 1,
                "integrations": 1,
                "active_provider": 1,
            },
        )
        return project_doc or {}


task_read_dispatcher = TaskReadDispatcher()
task_read_dispatcher.register("jira", JiraTaskReadRepository())
task_read_dispatcher.register("linear", LinearTaskReadRepository())

