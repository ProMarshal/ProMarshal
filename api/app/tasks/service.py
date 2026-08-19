"""Interface-agnostic task operations service"""

from typing import Any, Dict, List, Optional

from app.tasks.schemas import (
    TaskResponseDTO,
    TaskAssigneeDTO,
    ListTasksFilters,
    CreateTaskDTO,
    UpdateTaskDTO,
    TaskCreateResultDTO,
    TaskMutationResultDTO,
)
from app.tasks.providers.jira_adapter import JiraAdapter
from app.tasks.read_repository.dispatcher import task_read_dispatcher
from app.tasks.brain_sync import BrainSync
from app.integrations.slack.assignment_notifications import (
    send_work_item_assignment_notification,
)
from app.projects.recommendations.recommendation_orchestrator import mark_project_task_dirty
from app.core.config import settings


class TaskService:
    """Platform-agnostic task operations"""

    @staticmethod
    def _mark_recommendation_dirty_fallback(*, project_id: str, task_key: Optional[str]) -> None:
        clean_project = str(project_id or "").strip()
        clean_task = str(task_key or "").strip()
        if not clean_project or not clean_task:
            return
        try:
            mark_project_task_dirty(clean_project, clean_task)
        except Exception as mark_error:
            print(
                "[TaskService] Recommendation dirty fallback mark failed "
                f"for {clean_project}:{clean_task}: {mark_error}"
            )

    @staticmethod
    def _derive_is_closed(payload: Dict[str, Any]) -> bool:
        raw_flag = payload.get("is_closed")
        if isinstance(raw_flag, bool):
            return raw_flag
        return str(payload.get("status") or "").strip().lower() == "done"

    @staticmethod
    def _build_assignee(payload: Dict[str, Any]) -> Optional[TaskAssigneeDTO]:
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

    @staticmethod
    def _to_task_response_dto(jira_task: Dict[str, Any]) -> TaskResponseDTO:
        """Convert normalized Jira task payload to response DTO."""
        return TaskResponseDTO(
            external_id=jira_task["external_id"],
            provider=str(
                jira_task.get("provider")
                or jira_task.get("integration_type")
                or "jira"
            ).strip().lower() or "jira",
            title=jira_task["title"],
            status=jira_task["status"],
            status_raw=(str(jira_task.get("status_raw") or "").strip() or None),
            status_display=(
                str(jira_task.get("status_display") or jira_task.get("status_raw") or "").strip()
                or None
            ),
            assignee=TaskService._build_assignee(jira_task),
            is_closed=TaskService._derive_is_closed(jira_task),
            assignee_email=jira_task.get("assignee_email"),
            assignee_name=jira_task.get("assignee_name"),
            priority=jira_task.get("priority"),
            provider_type=(
                str(jira_task.get("provider_type") or jira_task.get("issue_type") or "").strip()
                or None
            ),
            due_date=jira_task.get("due_date"),
            start_date=jira_task.get("start_date"),
            url=jira_task["url"],
            source=str(jira_task.get("source") or "jira").strip().lower() or "jira",
            schema_version=str(jira_task.get("schema_version") or "1.0").strip() or "1.0",
            created_at=jira_task["created_at"],
            updated_at=jira_task["updated_at"],
        )

    @staticmethod
    async def create_task(
        task_data: CreateTaskDTO,
        project: Dict[str, Any],
        created_by: str
    ) -> TaskResponseDTO:
        """
        Create a task (interface-agnostic).
        Can be called from: Slack, Web UI, API, CLI, Teams, Discord, etc.

        Args:
            task_data: Task creation data
            project: MongoDB project document
            created_by: Email of user creating task

        Returns:
            TaskResponseDTO with created task details
        """
        print(f"\n[TaskService] Creating task: {task_data.title}")
        print(f"[TaskService] Project: {project.get('project_name')} ({project['project_id']})")

        result = await TaskService.create_task_with_sync_status(
            task_data=task_data,
            project=project,
            created_by=created_by,
        )

        # Keep original method contract for existing call sites.
        print(f"[TaskService] Task creation completed successfully")
        return result.task

    @staticmethod
    async def create_task_with_sync_status(
        task_data: CreateTaskDTO,
        project: Dict[str, Any],
        created_by: str,
    ) -> TaskCreateResultDTO:
        """Create task via Jira-first path and return Brain sync outcome."""
        # 1. Create task in Jira
        print(f"[TaskService] Step 1: Creating task in Jira...")
        jira_adapter = JiraAdapter(project)
        jira_task, sprint_placement = await jira_adapter.create_task(task_data)
        print(f"[TaskService] Jira task created: {jira_task.get('external_id')}")

        # 2. Sync to brain database (best effort with webhook compensation expectation)
        print(f"[TaskService] Step 2: Syncing to brain database...")
        brain_sync_succeeded = True
        synced_task = jira_task
        previous_task = None
        try:
            synced_task, previous_task = await BrainSync.sync_task_to_brain(
                project_id=project["project_id"],
                jira_task=jira_task
            )
            print(f"[TaskService] Brain sync completed")
        except Exception as sync_error:
            brain_sync_succeeded = False
            print(f"[TaskService] Brain sync failed for {jira_task.get('external_id')}: {sync_error}")
            print(
                "[TaskService] Jira write committed; awaiting webhook compensation "
                "or subsequent sync."
            )
            TaskService._mark_recommendation_dirty_fallback(
                project_id=project.get("project_id", ""),
                task_key=str(jira_task.get("external_id") or "").strip() or None,
            )
        if brain_sync_succeeded:
            try:
                await send_work_item_assignment_notification(
                    db=None,
                    project=project,
                    task=synced_task,
                    old_task=previous_task,
                    actor_hint=created_by,
                )
            except Exception as exc:
                print(
                    "[TaskService] Work-item assignment notification failed "
                    f"for {jira_task.get('external_id')}: {exc}"
                )

        return TaskCreateResultDTO(
            task=TaskService._to_task_response_dto(jira_task),
            brain_sync_succeeded=brain_sync_succeeded,
            sprint_placement=sprint_placement,
        )

    @staticmethod
    async def mutate_task(
        task_data: UpdateTaskDTO,
        project: Dict[str, Any],
        updated_by: Optional[str] = None,
        source_label: Optional[str] = None,
        access_token_override: Optional[str] = None,
        add_status_auto_comment: bool = False,
        api_delay_seconds: float = 0.0,
    ) -> TaskMutationResultDTO:
        """
        Mutate a task through the shared path (status/comment/description/assignee/parent), then sync Brain.

        Jira is the write source-of-truth. Brain sync is best-effort with
        explicit logging for webhook-based compensation.
        """
        if (
            not task_data.status
            and not task_data.transition_id
            and not task_data.comment
            and not task_data.description
            and not task_data.assignee_account_id
            and not task_data.parent_external_id
            and task_data.due_date is None
            and task_data.start_date is None
            and not bool(task_data.clear_due_date)
            and not bool(task_data.clear_start_date)
        ):
            raise ValueError(
                "At least one mutation is required: status, transition_id, comment, description, assignee, parent, due_date, or start_date"
            )

        print(f"\n[TaskService] Mutating task: {task_data.task_id}")
        print(f"[TaskService] Project: {project.get('project_name')} ({project['project_id']})")

        jira_adapter = JiraAdapter(project, access_token_override=access_token_override)
        (
            jira_task,
            status_updated,
            comment_added,
            posted_comment_text,
            description_updated,
            assignee_updated,
            parent_updated,
            due_date_updated,
            start_date_updated,
            sprint_placement,
        ) = await jira_adapter.mutate_task(
            task_key=task_data.task_id,
            status=task_data.status,
            transition_id=task_data.transition_id,
            comment=task_data.comment,
            description=task_data.description,
            assignee_account_id=task_data.assignee_account_id,
            parent_external_id=task_data.parent_external_id,
            due_date=task_data.due_date,
            start_date=task_data.start_date,
            clear_due_date=bool(task_data.clear_due_date),
            clear_start_date=bool(task_data.clear_start_date),
            sprint_directive=task_data.sprint_directive,
            updated_by=updated_by,
            source_label=source_label,
            add_status_auto_comment=add_status_auto_comment,
            api_delay_seconds=api_delay_seconds,
        )

        brain_sync_succeeded = True
        synced_task = jira_task
        previous_task = None
        try:
            synced_task, previous_task = await BrainSync.sync_task_to_brain(
                project_id=project["project_id"],
                jira_task=jira_task,
            )
            print(f"[TaskService] Brain sync completed for {task_data.task_id}")
        except Exception as sync_error:
            brain_sync_succeeded = False
            print(f"[TaskService] Brain sync failed for {task_data.task_id}: {sync_error}")
            print(
                "[TaskService] Jira write committed; awaiting webhook compensation "
                "or subsequent sync."
            )
            TaskService._mark_recommendation_dirty_fallback(
                project_id=project.get("project_id", ""),
                task_key=str(jira_task.get("external_id") or task_data.task_id or "").strip() or None,
            )

        if (
            settings.action_items_enabled
            and settings.action_items_auto_detect_enabled
            and str(source_label or "").strip().lower() == "promarshal"
            and bool(comment_added)
            and str(task_data.comment or "").strip()
            and str(posted_comment_text or "").strip()
        ):
            try:
                from app.action_items.extraction import (
                    enqueue_from_task_comment_mutation,
                    resolve_actor_user_id_from_project,
                )

                actor_user_id = resolve_actor_user_id_from_project(project, updated_by)
                enqueue_result = enqueue_from_task_comment_mutation(
                    project_id=str(project.get("project_id") or "").strip(),
                    task_key=str(jira_task.get("external_id") or task_data.task_id or "").strip(),
                    raw_comment_text=str(task_data.comment or "").strip(),
                    posted_comment_text=str(posted_comment_text or "").strip(),
                    actor_user_id=actor_user_id,
                    updated_by_user_id=str(updated_by or "").strip(),
                    provider="jira",
                    source="task_service_mutate_task",
                    mutation_timestamp=str(jira_task.get("updated_at") or "").strip(),
                )
                print(
                    "[TaskService] Action item extraction enqueue result: "
                    f"task={task_data.task_id} actor_user_id={actor_user_id or 'none'} "
                    f"updated_by={updated_by or 'none'} queued={bool(enqueue_result.get('queued'))} "
                    f"job_id={enqueue_result.get('job_id') or 'none'} reason={enqueue_result.get('reason') or 'n/a'}"
                )
            except Exception as extraction_error:
                print(
                    f"[TaskService] Action item extraction enqueue failed for "
                    f"{task_data.task_id}: {extraction_error}"
                )
        if brain_sync_succeeded:
            try:
                await send_work_item_assignment_notification(
                    db=None,
                    project=project,
                    task=synced_task,
                    old_task=previous_task,
                    actor_hint=updated_by,
                )
            except Exception as exc:
                print(
                    "[TaskService] Work-item assignment notification failed "
                    f"for {task_data.task_id}: {exc}"
                )

        return TaskMutationResultDTO(
            task=TaskService._to_task_response_dto(jira_task),
            status_updated=status_updated,
            comment_added=comment_added,
            posted_comment_text=posted_comment_text,
            description_updated=description_updated,
            assignee_updated=assignee_updated,
            parent_updated=parent_updated,
            due_date_updated=due_date_updated,
            start_date_updated=start_date_updated,
            brain_sync_succeeded=brain_sync_succeeded,
            sprint_placement=sprint_placement,
        )

    @staticmethod
    async def list_tasks(
        project_id: str,
        filters: Optional[ListTasksFilters] = None,
        *,
        provider_hint: Optional[str] = None,
        project_context: Optional[Dict[str, Any]] = None,
    ) -> List[TaskResponseDTO]:
        """
        List tasks (interface-agnostic).
        Can be called from: Slack, Web UI, API, CLI, Teams, Discord, etc.

        Read freshness policy:
        - Default shared read path uses Brain as the query source.
        - Write paths remain Jira-first and sync back to Brain.
        - Jira live reads are reserved for explicit freshness-demanded paths.

        Args:
            project_id: Custom project_id (proj-XXXX-YYYY)
            filters: Optional filters (assignee, status, limit)

        Returns:
            List of TaskResponseDTO
        """
        resolved_filters = filters or ListTasksFilters()
        return await task_read_dispatcher.list_tasks(
            project_id=project_id,
            filters=resolved_filters,
            provider_hint=provider_hint,
            project_context=project_context,
        )
