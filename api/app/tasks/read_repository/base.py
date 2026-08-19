"""Base contracts for provider-scoped task read repositories."""

from __future__ import annotations

from typing import Optional, Protocol

from app.tasks.schemas import ListTasksFilters, TaskResponseDTO


class TaskReadRepository(Protocol):
    """Provider-specific task-read operations."""

    provider: str

    async def list_tasks(
        self,
        *,
        project_id: str,
        filters: Optional[ListTasksFilters] = None,
    ) -> list[TaskResponseDTO]:
        """List tasks for a project."""

    async def search_tasks(
        self,
        *,
        project_id: str,
        query: str,
        limit: int = 50,
    ) -> list[TaskResponseDTO]:
        """Search tasks for a project."""

    async def get_task(
        self,
        *,
        project_id: str,
        external_id: str,
    ) -> Optional[TaskResponseDTO]:
        """Fetch one task by provider external id."""

