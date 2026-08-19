"""Linear-backed task read repository over Brain work-item snapshots."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Dict, List, Optional

from app.core.database import get_brain_collection
from app.tasks.schemas import ListTasksFilters, TaskAssigneeDTO, TaskResponseDTO


class LinearTaskReadRepository:
    """Linear read repository using project-scoped Brain collection."""

    provider = "linear"

    @staticmethod
    def _derive_is_closed(payload: Dict[str, Any]) -> bool:
        raw_flag = payload.get("is_closed")
        if isinstance(raw_flag, bool):
            return raw_flag
        return str(payload.get("status") or "").strip().lower() == "done"

    @classmethod
    def _build_assignee(cls, payload: Dict[str, Any]) -> Optional[TaskAssigneeDTO]:
        email = str(payload.get("assignee_email") or "").strip() or None
        name = str(payload.get("assignee_name") or "").strip() or None
        account_id = str(payload.get("assignee_account_id") or "").strip() or None
        nested = payload.get("assignee")
        if isinstance(nested, dict):
            email = str(nested.get("email") or "").strip() or email
            name = str(nested.get("name") or "").strip() or name
            account_id = str(nested.get("account_id") or "").strip() or account_id
        if not email and not name and not account_id:
            return None
        return TaskAssigneeDTO(email=email, name=name, account_id=account_id)

    @classmethod
    def _to_task_response_dto(cls, payload: Dict[str, Any]) -> TaskResponseDTO:
        return TaskResponseDTO(
            external_id=payload.get("external_id", ""),
            provider=str(
                payload.get("provider")
                or payload.get("integration_type")
                or cls.provider
            ).strip().lower() or cls.provider,
            title=payload.get("title", ""),
            status=payload.get("status", ""),
            description=payload.get("description") or None,
            assignee=cls._build_assignee(payload),
            is_closed=cls._derive_is_closed(payload),
            assignee_email=payload.get("assignee_email", ""),
            assignee_name=payload.get("assignee_name", ""),
            priority=payload.get("priority"),
            provider_type=(
                str(payload.get("provider_type") or payload.get("issue_type") or "").strip()
                or None
            ),
            due_date=payload.get("due_date"),
            start_date=payload.get("start_date"),
            url=payload.get("url", ""),
            source=str(payload.get("source") or cls.provider).strip().lower() or cls.provider,
            schema_version=str(payload.get("schema_version") or "1.0").strip() or "1.0",
            created_at=payload.get("created_at", datetime.now()),
            updated_at=payload.get("updated_at", datetime.now()),
        )

    @classmethod
    def _build_list_query(cls, filters: ListTasksFilters) -> Dict[str, Any]:
        query: Dict[str, Any] = {"integration_type": cls.provider}
        and_clauses: List[Dict[str, Any]] = []

        assignee_user_id = str(
            filters.assignee_user_id or filters.assignee_account_id or ""
        ).strip()
        assignee_email = str(filters.assignee_email or "").strip()
        assignee_name_exact = str(filters.assignee_name_exact or "").strip()

        assignee_or_clauses: List[Dict[str, Any]] = []
        if assignee_user_id:
            assignee_or_clauses.extend(
                [
                    {"assignee_user_id": assignee_user_id},
                    {"assignee_account_id": assignee_user_id},
                    {"assignee.account_id": assignee_user_id},
                    {"assignee.user_id": assignee_user_id},
                ]
            )
        elif assignee_email:
            assignee_or_clauses.extend(
                [
                    {"assignee_email": assignee_email},
                    {"assignee.email": assignee_email},
                ]
            )
        elif assignee_name_exact:
            escaped = re.escape(assignee_name_exact)
            assignee_or_clauses.extend(
                [
                    {"assignee_name": {"$regex": f"^{escaped}$", "$options": "i"}},
                    {"assignee.name": {"$regex": f"^{escaped}$", "$options": "i"}},
                ]
            )
        if assignee_or_clauses:
            and_clauses.append({"$or": assignee_or_clauses})

        if filters.status_filter:
            and_clauses.append({"status": {"$in": filters.status_filter}})

        due_date_mode = str(filters.due_date_mode or "").strip().lower()
        if due_date_mode == "missing":
            and_clauses.append(
                {
                    "$or": [
                        {"due_date": None},
                        {"due_date": ""},
                        {"due_date": {"$exists": False}},
                    ]
                }
            )
        elif due_date_mode == "present":
            and_clauses.append(
                {
                    "$and": [
                        {"due_date": {"$exists": True}},
                        {"due_date": {"$ne": None}},
                        {"due_date": {"$ne": ""}},
                    ]
                }
            )

        if filters.is_closed is not None:
            closed_value = bool(filters.is_closed)
            if closed_value:
                and_clauses.append(
                    {
                        "$or": [
                            {"is_closed": True},
                            {
                                "$and": [
                                    {"is_closed": {"$exists": False}},
                                    {"status": "done"},
                                ]
                            },
                        ]
                    }
                )
            else:
                and_clauses.append(
                    {
                        "$or": [
                            {"is_closed": False},
                            {
                                "$and": [
                                    {"is_closed": {"$exists": False}},
                                    {"status": {"$ne": "done"}},
                                ]
                            },
                        ]
                    }
                )

        if and_clauses:
            query["$and"] = and_clauses
        return query

    async def list_tasks(
        self,
        *,
        project_id: str,
        filters: Optional[ListTasksFilters] = None,
    ) -> list[TaskResponseDTO]:
        resolved_filters = filters or ListTasksFilters()
        brain_collection = get_brain_collection(project_id)
        query = self._build_list_query(resolved_filters)
        cursor = brain_collection.find(query).sort("updated_at", -1).limit(resolved_filters.limit)
        tasks = await cursor.to_list(length=resolved_filters.limit)
        return [self._to_task_response_dto(task) for task in tasks]

    async def search_tasks(
        self,
        *,
        project_id: str,
        query: str,
        limit: int = 50,
    ) -> list[TaskResponseDTO]:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return []
        regex = {"$regex": normalized_query, "$options": "i"}
        brain_collection = get_brain_collection(project_id)
        cursor = (
            brain_collection.find(
                {
                    "integration_type": self.provider,
                    "$or": [
                        {"external_id": regex},
                        {"title": regex},
                        {"status": regex},
                    ],
                }
            )
            .sort("updated_at", -1)
            .limit(limit)
        )
        tasks = await cursor.to_list(length=limit)
        return [self._to_task_response_dto(task) for task in tasks]

    async def get_task(
        self,
        *,
        project_id: str,
        external_id: str,
    ) -> Optional[TaskResponseDTO]:
        normalized_external_id = str(external_id or "").strip()
        if not normalized_external_id:
            return None
        brain_collection = get_brain_collection(project_id)
        task = await brain_collection.find_one(
            {
                "integration_type": self.provider,
                "external_id": normalized_external_id,
            }
        )
        if not isinstance(task, dict):
            return None
        return self._to_task_response_dto(task)
