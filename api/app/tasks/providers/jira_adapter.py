"""Jira API adapter for task operations"""

import re
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
from app.core.config import settings
from app.core.database import get_brain_collection
from app.core.redis_queue import get_redis_connection
from app.integrations.jira.oauth import jira_oauth_service
from app.core.security import encryption_service
from app.tasks.comment_composer import compose_comment_text
from app.tasks.provider_parent_rules import (
    build_effective_type_rules,
    convert_legacy_parent_rules_to_type_rules,
    resolve_parent_rule_filter,
)
from app.tasks.schemas import CreateTaskDTO, SprintDirective, SprintPlacementResultDTO


class JiraAdapter:
    """Adapter for Jira API operations"""
    _METADATA_ENTITY_TYPE = "work_item_provider_metadata"
    _METADATA_KIND_ALLOWED_CREATE_TYPES = "jira_allowed_create_types"
    _COMMENT_MENTION_RE = re.compile(r"(?<!\w)@([A-Za-z0-9._-]{2,64})")
    _JIRA_ISSUE_TYPE_ALIASES = {
        "task": "Task",
        "work item": "Task",
        "workitem": "Task",
        "story": "Story",
        "epic": "Epic",
        "bug": "Bug",
        "defect": "Bug",
        "subtask": "Sub-task",
        "sub-task": "Sub-task",
        "sub task": "Sub-task",
    }
    _DEFAULT_PROVIDER_TYPE_BY_WORK_ITEM_TYPE = {
        "portfolio": "Epic",
        "standard": "Task",
        "defect": "Bug",
        "subtask": "Sub-task",
        "service": "Task",
        "custom": "Task",
    }

    def __init__(
        self,
        project: dict,
        access_token_override: Optional[str] = None,
    ):
        """
        Initialize Jira adapter with project.

        Args:
            project: MongoDB project document with Jira integration
            access_token_override: Optional pre-resolved token (caller-managed lifecycle)
        """
        self.project = project
        self.jira_integration = project["integrations"]["jira"]
        self.cloud_id = self.jira_integration["cloud_id"]
        self.site_url = self.jira_integration["site_url"]
        self._access_token_override = access_token_override

        # Decrypt tokens
        if access_token_override:
            self.access_token = access_token_override
        else:
            self.access_token = encryption_service.decrypt(
                self.jira_integration["access_token"]
            )
        self.refresh_token = encryption_service.decrypt(
            self.jira_integration["refresh_token"]
        ) if self.jira_integration.get("refresh_token") else None

    async def get_workspace_users(self) -> List[Dict[str, Any]]:
        """
        Get ALL users from Jira workspace.

        Returns:
            List of user dicts with accountId, displayName, emailAddress
        """
        try:
            # Ensure token is valid
            await self._ensure_token_valid()

            # Fetch all workspace users
            users = await jira_oauth_service.get_workspace_users(
                access_token=self.access_token,
                cloud_id=self.cloud_id
            )

            return users

        except Exception as e:
            print(f"Error fetching workspace users: {str(e)}")
            return []

    async def create_task(
        self,
        task_data: CreateTaskDTO,
    ) -> Tuple[Dict[str, Any], Optional[SprintPlacementResultDTO]]:
        """
        Create task in Jira.

        Args:
            task_data: CreateTaskDTO with task details

        Returns:
            Tuple of normalized Jira issue and sprint placement outcome
        """
        try:
            print(f"\n[JiraAdapter] Creating task in Jira...")
            print(f"[JiraAdapter] Title: {task_data.title}")
            print(
                "[JiraAdapter] Assignee Account ID: "
                f"{task_data.assignee_account_id if task_data.assignee_account_id else 'unassigned'}"
            )

            # Ensure token is valid
            print(f"[JiraAdapter] Checking token validity...")
            await self._ensure_token_valid()

            # Get Jira project key (use stored default project)
            jira_project_key = self.jira_integration.get("default_project_key")
            jira_project_name = self.jira_integration.get("default_project_name")

            if not jira_project_key:
                print(f"[JiraAdapter] WARNING: No default project configured, fetching first available...")
                jira_project = await self._get_jira_project()
                if not jira_project:
                    print(f"[JiraAdapter] ERROR: No Jira project found")
                    raise ValueError("No Jira project found")
                jira_project_key = jira_project.get("key")
                jira_project_name = jira_project.get("name")

            print(f"[JiraAdapter] Using Jira project: {jira_project_key} - {jira_project_name}")

            issue_type_name = self._resolve_issue_type_name(
                provider_type=task_data.provider_type,
                work_item_type=task_data.work_item_type,
            )
            issue_type_name = await self._validate_issue_type_allowed_for_create(
                jira_project_key=str(jira_project_key or ""),
                issue_type_name=issue_type_name,
            )
            parent_rule_filter = await self._resolve_parent_rule_for_create(
                issue_type_name=issue_type_name,
                work_item_type=task_data.work_item_type,
            )
            parent_required = bool((parent_rule_filter or {}).get("parent_required"))
            if not parent_required:
                # Conservative safety fallback when metadata resolution is unavailable.
                parent_required = self._canonical_issue_type_token(issue_type_name) == "subtask"
            if parent_required and not str(task_data.parent_external_id or "").strip():
                raise ValueError(
                    f"Jira '{issue_type_name}' create requires parent_external_id (parent issue key)."
                )
            await self._validate_parent_constraints_for_create(
                jira_project_key=str(jira_project_key or ""),
                issue_type_name=issue_type_name,
                parent_external_id=str(task_data.parent_external_id or "").strip() or None,
                parent_rule_filter=parent_rule_filter,
            )

            # Build Jira issue payload
            print(f"[JiraAdapter] Building issue payload...")
            issue_payload = {
                "fields": {
                    "project": {
                        "key": jira_project_key
                    },
                    "summary": task_data.title,
                    "issuetype": {
                        "name": issue_type_name
                    }
                }
            }
            if task_data.parent_external_id:
                issue_payload["fields"]["parent"] = {"key": str(task_data.parent_external_id).strip()}
            if task_data.assignee_account_id:
                issue_payload["fields"]["assignee"] = {
                    "accountId": task_data.assignee_account_id
                }

            # Add description if provided
            if task_data.description:
                issue_payload["fields"]["description"] = {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {
                                    "type": "text",
                                    "text": task_data.description
                                }
                            ]
                        }
                    ]
                }

            # Add priority if provided
            if task_data.priority:
                priority_map = {
                    "low": "Low",
                    "medium": "Medium",
                    "high": "High",
                    "critical": "Highest"
                }
                issue_payload["fields"]["priority"] = {
                    "name": priority_map.get(task_data.priority.lower(), "Medium")
                }

            # Create issue in Jira
            print(f"[JiraAdapter] Calling Jira API to create issue...")
            jira_response = await jira_oauth_service.create_issue(
                access_token=self.access_token,
                cloud_id=self.cloud_id,
                issue_data=issue_payload
            )

            issue_key = jira_response.get("key")
            print(f"[JiraAdapter] Jira issue created: {issue_key}")

            sprint_placement = await self._apply_sprint_placement_after_create(
                issue_key=str(issue_key or ""),
                requested_directive=task_data.sprint_directive,
            )

            # Fetch full issue details
            print(f"[JiraAdapter] Fetching full issue details...")
            full_issue = await jira_oauth_service.get_issue(
                access_token=self.access_token,
                cloud_id=self.cloud_id,
                issue_key=issue_key
            )

            # Normalize to common schema
            print(f"[JiraAdapter] Normalizing task data...")
            from app.integrations.jira.normalizer import JiraNormalizer
            normalizer = JiraNormalizer(
                cloud_id=self.cloud_id,
                site_url=self.site_url
            )
            normalized_task = normalizer.normalize_task(full_issue)

            print(f"[JiraAdapter] Task creation completed: {normalized_task.get('external_id')}")
            return normalized_task, sprint_placement

        except Exception as e:
            print(f"[JiraAdapter] ERROR creating task in Jira: {str(e)}")
            import traceback
            traceback.print_exc()
            raise

    @classmethod
    def _resolve_issue_type_name(
        cls,
        *,
        provider_type: Optional[str],
        work_item_type: Optional[str],
    ) -> str:
        raw_provider_type = str(provider_type or "").strip()
        if raw_provider_type:
            key = re.sub(r"\s+", " ", raw_provider_type.lower().replace("-", " ")).strip()
            return cls._JIRA_ISSUE_TYPE_ALIASES.get(key, raw_provider_type)
        canonical = str(work_item_type or "").strip().lower()
        if canonical:
            return cls._DEFAULT_PROVIDER_TYPE_BY_WORK_ITEM_TYPE.get(canonical, "Task")
        return "Task"

    @classmethod
    def _normalize_issue_type_key(cls, value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().lower().replace("-", " ")).strip()

    @classmethod
    def _canonical_issue_type_token(cls, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())

    @classmethod
    def _find_allowed_issue_type_label(
        cls,
        *,
        allowed_issue_types: List[str],
        issue_type_name: str,
    ) -> Optional[str]:
        requested_token = cls._canonical_issue_type_token(issue_type_name)
        if not requested_token:
            return None
        for candidate in (allowed_issue_types or []):
            candidate_name = str(candidate or "").strip()
            if not candidate_name:
                continue
            if cls._canonical_issue_type_token(candidate_name) == requested_token:
                return candidate_name
        return None

    @classmethod
    def _issue_type_allowed(cls, allowed_issue_types: List[str], issue_type_name: str) -> bool:
        return (
            cls._find_allowed_issue_type_label(
                allowed_issue_types=allowed_issue_types,
                issue_type_name=issue_type_name,
            )
            is not None
        )

    def _provider_metadata_collection(self):
        project_id = str(self.project.get("project_id") or "").strip()
        return get_brain_collection(project_id) if project_id else None

    async def _load_cached_allowed_issue_types(self, *, jira_project_key: str) -> List[str]:
        collection = self._provider_metadata_collection()
        if collection is None:
            return []
        doc = await collection.find_one(
            {
                "entity_type": self._METADATA_ENTITY_TYPE,
                "provider": "jira",
                "metadata_kind": self._METADATA_KIND_ALLOWED_CREATE_TYPES,
                "project_key": str(jira_project_key or "").strip().upper(),
            },
            {"allowed_create_types": 1},
        )
        candidates = (doc or {}).get("allowed_create_types") or []
        values: List[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            name = str(candidate or "").strip()
            key = self._canonical_issue_type_token(name)
            if not key or key in seen:
                continue
            seen.add(key)
            values.append(name)
        return values

    async def _persist_allowed_issue_types_cache(
        self,
        *,
        jira_project_key: str,
        allowed_issue_types: List[str],
        parent_rules: Optional[Dict[str, Any]] = None,
    ) -> None:
        collection = self._provider_metadata_collection()
        if collection is None:
            return
        now = datetime.utcnow()
        normalized_project_key = str(jira_project_key or "").strip().upper()
        deduped: List[str] = []
        seen: set[str] = set()
        for candidate in allowed_issue_types or []:
            name = str(candidate or "").strip()
            key = self._canonical_issue_type_token(name)
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(name)
        effective_rule_bundle = build_effective_type_rules(
            provider="jira",
            parent_rules={},
            type_rules=convert_legacy_parent_rules_to_type_rules(
                parent_rules=dict(parent_rules or {}),
                allowed_create_types=deduped,
            ),
            allowed_create_types=deduped,
        )
        effective_type_rules, _raw_type_rules = effective_rule_bundle
        await collection.update_one(
            {
                "entity_type": self._METADATA_ENTITY_TYPE,
                "provider": "jira",
                "metadata_kind": self._METADATA_KIND_ALLOWED_CREATE_TYPES,
                "project_key": normalized_project_key,
            },
            {
                "$set": {
                    "allowed_create_types": deduped,
                    "type_rules": effective_type_rules,
                    "updated_at": now,
                },
                "$unset": {
                    "type_rules_raw": "",
                    "parent_rules": "",
                    "parent_rules_raw": "",
                    "site_url": "",
                    "cloud_id": "",
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    async def _fetch_and_cache_allowed_issue_types(self, *, jira_project_key: str) -> List[str]:
        metadata = await jira_oauth_service.get_project_create_metadata(
            access_token=self.access_token,
            cloud_id=self.cloud_id,
            project_key=str(jira_project_key or "").strip().upper(),
        )
        allowed_issue_types = list((metadata or {}).get("allowed_create_types") or [])
        parent_rules = dict((metadata or {}).get("parent_rules") or {})
        if allowed_issue_types or parent_rules:
            try:
                await self._persist_allowed_issue_types_cache(
                    jira_project_key=jira_project_key,
                    allowed_issue_types=allowed_issue_types,
                    parent_rules=parent_rules,
                )
            except Exception as persist_error:
                print(
                    "[JiraAdapter] WARNING: Failed to persist Jira create metadata cache: "
                    f"{persist_error}"
                )
        return allowed_issue_types

    async def _validate_issue_type_allowed_for_create(
        self,
        *,
        jira_project_key: str,
        issue_type_name: str,
    ) -> str:
        normalized_project_key = str(jira_project_key or "").strip().upper()
        requested_issue_type = str(issue_type_name or "").strip()
        if not normalized_project_key or not requested_issue_type:
            return requested_issue_type

        cached_allowed_types = await self._load_cached_allowed_issue_types(
            jira_project_key=normalized_project_key
        )
        matched_cached_label = self._find_allowed_issue_type_label(
            allowed_issue_types=cached_allowed_types,
            issue_type_name=requested_issue_type,
        )
        if matched_cached_label:
            return matched_cached_label

        # No-expiry cache strategy: only refresh live when requested type is absent in cache.
        try:
            refreshed_allowed_types = await self._fetch_and_cache_allowed_issue_types(
                jira_project_key=normalized_project_key
            )
        except Exception as refresh_error:
            print(
                "[JiraAdapter] WARNING: Jira create metadata refresh failed; "
                f"continuing create without pre-validation for {requested_issue_type}: {refresh_error}"
            )
            return requested_issue_type

        if not refreshed_allowed_types:
            return requested_issue_type
        matched_refreshed_label = self._find_allowed_issue_type_label(
            allowed_issue_types=refreshed_allowed_types,
            issue_type_name=requested_issue_type,
        )
        if matched_refreshed_label:
            return matched_refreshed_label

        allowed_display = ", ".join(refreshed_allowed_types)
        raise ValueError(
            f"Issue type '{requested_issue_type}' is not available for project "
            f"{normalized_project_key}. Allowed types: {allowed_display}"
        )

    async def preview_issue_type_for_create(
        self,
        *,
        jira_project_key: str,
        provider_type: Optional[str],
        work_item_type: Optional[str],
    ) -> Dict[str, Any]:
        """
        Resolve create-time issue type against allowed project types without writing.

        This is used by higher layers to ask clarifications before issuing a write.
        """
        normalized_project_key = str(jira_project_key or "").strip().upper()
        requested_issue_type = self._resolve_issue_type_name(
            provider_type=provider_type,
            work_item_type=work_item_type,
        )
        if not normalized_project_key or not requested_issue_type:
            return {
                "jira_project_key": normalized_project_key,
                "requested_issue_type": requested_issue_type,
                "matched_issue_type": requested_issue_type,
                "allowed_issue_types": [],
                "cache_source": "none",
            }

        cache_source = "cache"
        cached_allowed_types = await self._load_cached_allowed_issue_types(
            jira_project_key=normalized_project_key
        )
        allowed_issue_types = list(cached_allowed_types or [])
        matched_issue_type = self._find_allowed_issue_type_label(
            allowed_issue_types=allowed_issue_types,
            issue_type_name=requested_issue_type,
        )
        if matched_issue_type:
            return {
                "jira_project_key": normalized_project_key,
                "requested_issue_type": requested_issue_type,
                "matched_issue_type": matched_issue_type,
                "allowed_issue_types": allowed_issue_types,
                "cache_source": cache_source,
            }

        # Refresh on miss to reduce stale-cache false clarifications.
        cache_source = "refreshed"
        try:
            refreshed_allowed_types = await self._fetch_and_cache_allowed_issue_types(
                jira_project_key=normalized_project_key
            )
            if refreshed_allowed_types:
                allowed_issue_types = list(refreshed_allowed_types)
        except Exception as refresh_error:
            print(
                "[JiraAdapter] WARNING: preview issue-type refresh failed: "
                f"project={normalized_project_key} requested={requested_issue_type} error={refresh_error}"
            )
            cache_source = "cache_fallback"

        matched_issue_type = self._find_allowed_issue_type_label(
            allowed_issue_types=allowed_issue_types,
            issue_type_name=requested_issue_type,
        )
        return {
            "jira_project_key": normalized_project_key,
            "requested_issue_type": requested_issue_type,
            "matched_issue_type": matched_issue_type,
            "allowed_issue_types": allowed_issue_types,
            "cache_source": cache_source,
        }

    async def _validate_parent_constraints_for_create(
        self,
        *,
        jira_project_key: str,
        issue_type_name: str,
        parent_external_id: Optional[str],
        parent_rule_filter: Optional[Dict[str, Any]] = None,
    ) -> None:
        issue_type_token = self._canonical_issue_type_token(issue_type_name)
        normalized_project_key = str(jira_project_key or "").strip().upper()
        parent_key = str(parent_external_id or "").strip().upper()

        if not parent_key:
            return

        allowed_parent_provider_types = [
            str(item or "").strip()
            for item in (parent_rule_filter or {}).get("allowed_parent_provider_types") or []
            if str(item or "").strip()
        ]
        parent_allowed = bool(
            (parent_rule_filter or {}).get("parent_allowed")
            or (parent_rule_filter or {}).get("parent_required")
            or allowed_parent_provider_types
        )
        if not parent_allowed:
            raise ValueError(
                f"parent_external_id is not supported for Jira issue type '{issue_type_name}'."
            )

        try:
            parent_issue = await jira_oauth_service.get_issue(
                access_token=self.access_token,
                cloud_id=self.cloud_id,
                issue_key=parent_key,
            )
        except Exception as exc:
            raise ValueError(
                f"Unable to verify parent issue '{parent_key}' for Sub-task create: {exc}"
            ) from exc

        fields = parent_issue.get("fields") if isinstance(parent_issue, dict) else {}
        if not isinstance(fields, dict):
            return

        parent_project_key = str(
            ((fields.get("project") or {}).get("key") if isinstance(fields.get("project"), dict) else "")
            or ""
        ).strip().upper()
        if parent_project_key and normalized_project_key and parent_project_key != normalized_project_key:
            raise ValueError(
                f"Parent issue '{parent_key}' belongs to project {parent_project_key}; "
                f"expected project {normalized_project_key}."
            )

        parent_type = fields.get("issuetype")
        parent_type_name = ""
        parent_is_subtask = False
        if isinstance(parent_type, dict):
            parent_type_name = str(parent_type.get("name") or "").strip()
            parent_is_subtask = bool(parent_type.get("subtask"))
        parent_type_token = self._canonical_issue_type_token(parent_type_name)
        if issue_type_token == "subtask" and (
            parent_is_subtask or parent_type_token == "subtask"
        ):
            raise ValueError(
                f"Parent issue '{parent_key}' is a sub-task and cannot be used as a parent."
            )
        if allowed_parent_provider_types:
            allowed_tokens = {
                self._canonical_issue_type_token(item) for item in allowed_parent_provider_types
            }
            if parent_type_token and parent_type_token not in allowed_tokens:
                allowed_display = ", ".join(allowed_parent_provider_types)
                raise ValueError(
                    f"Parent issue '{parent_key}' has type '{parent_type_name}', which is not "
                    f"allowed for '{issue_type_name}'. Allowed parent types: {allowed_display}."
                )

    async def _resolve_parent_rule_for_create(
        self,
        *,
        issue_type_name: str,
        work_item_type: Optional[str],
    ) -> Dict[str, Any]:
        try:
            return await resolve_parent_rule_filter(
                project=self.project,
                create_payload={
                    "provider": "jira",
                    "provider_type": issue_type_name,
                    "work_item_type": work_item_type,
                },
            )
        except Exception as rule_error:
            print(
                "[JiraAdapter] WARNING: failed to resolve parent-rule filter for create "
                f"(issue_type={issue_type_name}): {rule_error}"
            )
            return {}

    async def mutate_task(
        self,
        task_key: str,
        status: Optional[str] = None,
        transition_id: Optional[str] = None,
        comment: Optional[str] = None,
        description: Optional[str] = None,
        assignee_account_id: Optional[str] = None,
        parent_external_id: Optional[str] = None,
        due_date: Optional[datetime] = None,
        start_date: Optional[datetime] = None,
        clear_due_date: bool = False,
        clear_start_date: bool = False,
        sprint_directive: Optional[SprintDirective] = None,
        updated_by: Optional[str] = None,
        source_label: Optional[str] = None,
        add_status_auto_comment: bool = False,
        api_delay_seconds: float = 0.0,
    ) -> Tuple[Dict[str, Any], bool, bool, Optional[str], bool, bool, bool, bool, bool, Optional[SprintPlacementResultDTO]]:
        """
        Execute task status/comment/description/assignee/parent mutation via one shared Jira adapter path.

        Returns:
            (
                normalized_task,
                status_updated,
                comment_added,
                posted_comment_text,
                description_updated,
                assignee_updated,
                parent_updated,
                due_date_updated,
                start_date_updated,
                sprint_placement,
            )
        """
        import asyncio

        if (
            not status
            and not transition_id
            and not comment
            and not description
            and not assignee_account_id
            and not parent_external_id
            and due_date is None
            and start_date is None
            and not bool(clear_due_date)
            and not bool(clear_start_date)
        ):
            raise ValueError(
                "Task mutation requires status, transition_id, comment, description, assignee, parent, due_date, or start_date"
            )

        if not self._access_token_override:
            await self._ensure_token_valid()

        status_updated = False
        comment_added = False
        posted_comment_text: Optional[str] = None
        description_updated = False
        assignee_updated = False
        parent_updated = False
        due_date_updated = False
        start_date_updated = False

        if transition_id:
            if api_delay_seconds > 0:
                await asyncio.sleep(api_delay_seconds)
            transition_result = await jira_oauth_service.apply_transition_by_id(
                access_token=self.access_token,
                cloud_id=self.cloud_id,
                task_key=task_key,
                transition_id=transition_id,
            )
            status_updated = bool(transition_result.get("status_updated"))
        elif status:
            if api_delay_seconds > 0:
                await asyncio.sleep(api_delay_seconds)
            status_updated = await jira_oauth_service.update_task_status(
                self.access_token,
                self.cloud_id,
                task_key,
                status,
            )

        if assignee_account_id:
            if api_delay_seconds > 0:
                await asyncio.sleep(api_delay_seconds)
            assignee_updated = await jira_oauth_service.update_task_assignee(
                self.access_token,
                self.cloud_id,
                task_key,
                assignee_account_id,
            )

        normalized_parent_external_id = str(parent_external_id or "").strip().upper()
        if normalized_parent_external_id:
            if api_delay_seconds > 0:
                await asyncio.sleep(api_delay_seconds)
            child_issue = await jira_oauth_service.get_issue(
                access_token=self.access_token,
                cloud_id=self.cloud_id,
                issue_key=task_key,
            )
            child_fields = child_issue.get("fields") if isinstance(child_issue, dict) else {}
            child_issue_type = child_fields.get("issuetype") if isinstance(child_fields, dict) else {}
            child_issue_type_name = str((child_issue_type or {}).get("name") or "").strip() or "Task"
            child_project = child_fields.get("project") if isinstance(child_fields, dict) else {}
            child_project_key = str((child_project or {}).get("key") or "").strip().upper()
            if not child_project_key:
                child_project_key = str(self.jira_integration.get("default_project_key") or "").strip().upper()
            parent_rule_filter = await self._resolve_parent_rule_for_create(
                issue_type_name=child_issue_type_name,
                work_item_type=None,
            )
            await self._validate_parent_constraints_for_create(
                jira_project_key=child_project_key,
                issue_type_name=child_issue_type_name,
                parent_external_id=normalized_parent_external_id,
                parent_rule_filter=parent_rule_filter,
            )
            parent_updated = await jira_oauth_service.update_task_parent(
                self.access_token,
                self.cloud_id,
                task_key,
                normalized_parent_external_id,
            )

        normalized_description = (description or "").strip()
        if normalized_description:
            if api_delay_seconds > 0:
                await asyncio.sleep(api_delay_seconds)
            description_updated = await jira_oauth_service.update_task_description(
                self.access_token,
                self.cloud_id,
                task_key,
                normalized_description,
            )

        if due_date is not None or bool(clear_due_date):
            if api_delay_seconds > 0:
                await asyncio.sleep(api_delay_seconds)
            due_date_updated = await jira_oauth_service.update_task_due_date(
                self.access_token,
                self.cloud_id,
                task_key,
                due_date=due_date,
                clear=bool(clear_due_date),
            )

        if start_date is not None or bool(clear_start_date):
            if api_delay_seconds > 0:
                await asyncio.sleep(api_delay_seconds)
            start_date_updated = await jira_oauth_service.update_task_start_date(
                self.access_token,
                self.cloud_id,
                task_key,
                start_date=start_date,
                clear=bool(clear_start_date),
            )

        normalized_comment = compose_comment_text(str(comment or ""))
        should_add_comment = bool(normalized_comment) or (add_status_auto_comment and bool(status))

        if should_add_comment:
            if api_delay_seconds > 0:
                await asyncio.sleep(api_delay_seconds)

            display_source_label = self._display_source_label(source_label)
            source_suffix = f" via {display_source_label}" if display_source_label else ""
            actor_prefix = f"[Updated by {updated_by}{source_suffix}]\n" if updated_by else ""

            if normalized_comment:
                full_comment = f"{actor_prefix}{normalized_comment}" if actor_prefix else normalized_comment
            else:
                actor_line = f" by {updated_by}{source_suffix}" if updated_by else ""
                full_comment = f"Status updated to {status}{actor_line}"
            posted_comment_text = full_comment
            mention_entities = await self._build_comment_mentions(full_comment)

            comment_added = await jira_oauth_service.add_comment_to_task(
                self.access_token,
                self.cloud_id,
                task_key,
                full_comment,
                mention_entities=mention_entities,
            )

        sprint_placement = await self._apply_sprint_placement_after_mutation(
            issue_key=task_key,
            requested_directive=sprint_directive,
            requested_status=status,
            status_updated=status_updated,
        )

        full_issue = await jira_oauth_service.get_issue(
            access_token=self.access_token,
            cloud_id=self.cloud_id,
            issue_key=task_key,
        )

        from app.integrations.jira.normalizer import JiraNormalizer
        normalizer = JiraNormalizer(cloud_id=self.cloud_id, site_url=self.site_url)
        normalized_task = normalizer.normalize_task(full_issue)
        return (
            normalized_task,
            status_updated,
            comment_added,
            posted_comment_text,
            description_updated,
            assignee_updated,
            parent_updated,
            due_date_updated,
            start_date_updated,
            sprint_placement,
        )

    def _display_source_label(self, source_label: Optional[str]) -> Optional[str]:
        """Return user-facing source attribution label for Jira comments."""
        normalized = str(source_label or "").strip()
        if not normalized:
            return None
        return "ProMarshal"

    @staticmethod
    def _normalize_identity_token(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "").strip().lower())

    @staticmethod
    def _normalize_handle(value: Any) -> str:
        return str(value or "").strip().lower().lstrip("@")

    def _member_handle_candidates(self, member: Dict[str, Any]) -> List[str]:
        if not isinstance(member, dict):
            return []
        candidates: List[str] = []
        email = self._normalize_identity_token(member.get("email"))
        name = self._normalize_identity_token(member.get("name"))
        if email:
            candidates.append(email)
            local = email.split("@", 1)[0].strip()
            if local:
                candidates.append(local)
        if name:
            candidates.append(name)
            nospace = name.replace(" ", "")
            if nospace:
                candidates.append(nospace)
            first = name.split(" ", 1)[0].strip()
            if first:
                candidates.append(first)
        return [item for item in dict.fromkeys(candidates) if item]

    def _workspace_user_handle_candidates(self, user: Dict[str, Any]) -> List[str]:
        if not isinstance(user, dict):
            return []
        candidates: List[str] = []
        email = self._normalize_identity_token(user.get("emailAddress"))
        display_name = self._normalize_identity_token(user.get("displayName"))
        if email:
            candidates.append(email)
            local = email.split("@", 1)[0].strip()
            if local:
                candidates.append(local)
        if display_name:
            candidates.append(display_name)
            nospace = display_name.replace(" ", "")
            if nospace:
                candidates.append(nospace)
            first = display_name.split(" ", 1)[0].strip()
            if first:
                candidates.append(first)
        return [item for item in dict.fromkeys(candidates) if item]

    async def _resolve_handle_to_jira_identity(
        self,
        *,
        handle: str,
        workspace_users: List[Dict[str, Any]],
    ) -> Optional[Dict[str, str]]:
        normalized_handle = self._normalize_handle(handle)
        if not normalized_handle:
            return None

        members = [item for item in (self.project.get("members") or []) if isinstance(item, dict)]
        active_members = [
            item
            for item in members
            if str(item.get("status") or "active").strip().lower() in {"", "active"}
        ]
        matched_members = [
            member
            for member in active_members
            if normalized_handle in self._member_handle_candidates(member)
        ]
        if len(matched_members) > 1:
            return None
        if len(matched_members) == 1:
            member = matched_members[0]
            integration_ids = member.get("integration_ids") if isinstance(member.get("integration_ids"), dict) else {}
            account_id = str(integration_ids.get("jira_account_id") or "").strip()
            if account_id:
                display_name = str(member.get("name") or "").strip()
                mention_text = f"@{display_name}" if display_name else f"@{normalized_handle}"
                return {"account_id": account_id, "mention_text": mention_text}

        matched_users = [
            user
            for user in (workspace_users or [])
            if normalized_handle in self._workspace_user_handle_candidates(user)
        ]
        if len(matched_users) != 1:
            return None
        resolved = matched_users[0]
        account_id = str(resolved.get("accountId") or "").strip()
        if not account_id:
            return None
        display_name = str(resolved.get("displayName") or "").strip()
        mention_text = f"@{display_name}" if display_name else f"@{normalized_handle}"
        return {"account_id": account_id, "mention_text": mention_text}

    async def _build_comment_mentions(self, comment_text: str) -> List[Dict[str, Any]]:
        text = str(comment_text or "")
        if not text:
            return []
        matches = list(self._COMMENT_MENTION_RE.finditer(text))
        if not matches:
            return []

        handles = {self._normalize_handle(match.group(1)) for match in matches if self._normalize_handle(match.group(1))}
        if not handles:
            return []
        workspace_users = await self.get_workspace_users()
        resolved_by_handle: Dict[str, Dict[str, str]] = {}
        for handle in handles:
            resolved = await self._resolve_handle_to_jira_identity(
                handle=handle,
                workspace_users=workspace_users,
            )
            if resolved:
                resolved_by_handle[handle] = resolved

        entities: List[Dict[str, Any]] = []
        for match in matches:
            raw_handle = self._normalize_handle(match.group(1))
            resolved = resolved_by_handle.get(raw_handle)
            if not resolved:
                continue
            account_id = str(resolved.get("account_id") or "").strip()
            mention_text = str(resolved.get("mention_text") or "").strip()
            if not account_id:
                continue
            entities.append(
                {
                    "start": int(match.start()),
                    "end": int(match.end()),
                    "account_id": account_id,
                    "text": mention_text or match.group(0),
                }
            )
        return entities

    def _integration_sprint_policy(self) -> str:
        raw = str(self.jira_integration.get("sprint_policy") or "auto").strip().lower()
        if raw in {"auto", "manual", "disabled"}:
            return raw
        return "auto"

    def _default_board_metadata(self) -> Tuple[Optional[int], str, Optional[str]]:
        raw_board_id = self.jira_integration.get("default_board_id")
        board_name = str(self.jira_integration.get("default_board_name") or "").strip() or None
        board_type = str(self.jira_integration.get("default_board_type") or "").strip().lower()
        if board_type not in {"scrum", "kanban", "simple"}:
            board_type = "unknown"

        board_id: Optional[int] = None
        if raw_board_id is not None:
            try:
                board_id = int(raw_board_id)
            except Exception:
                board_id = None
        return board_id, board_type, board_name

    def _is_sprint_capable_board_type(self, board_type: str) -> bool:
        normalized = str(board_type or "").strip().lower()
        # Jira may return "simple" for team-managed scrum boards.
        return normalized in {"scrum", "simple"}

    def _is_auto_sprint_enabled(self) -> bool:
        return bool(getattr(settings, "cortex_jira_auto_sprint_enabled", True))

    def _no_active_sprint_cache_key(self, *, board_id: int) -> str:
        project_id = str(self.project.get("project_id") or "").strip() or "unknown_project"
        cloud_id = str(self.cloud_id or "").strip() or "unknown_cloud"
        return f"jira:no_active_sprint:{project_id}:{cloud_id}:{int(board_id)}"

    def _is_no_active_sprint_cached(self, *, board_id: int) -> bool:
        redis_conn = get_redis_connection()
        if redis_conn is None:
            return False
        try:
            cached = redis_conn.get(self._no_active_sprint_cache_key(board_id=board_id))
            return bool(cached)
        except Exception:
            return False

    def _cache_no_active_sprint(self, *, board_id: int) -> None:
        redis_conn = get_redis_connection()
        if redis_conn is None:
            return
        ttl_seconds = max(
            10, int(getattr(settings, "jira_no_active_sprint_cache_ttl_seconds", 120) or 120)
        )
        try:
            redis_conn.set(
                self._no_active_sprint_cache_key(board_id=board_id),
                b"1",
                ex=ttl_seconds,
            )
        except Exception:
            return

    def _clear_no_active_sprint_cache(self, *, board_id: int) -> None:
        redis_conn = get_redis_connection()
        if redis_conn is None:
            return
        try:
            redis_conn.delete(self._no_active_sprint_cache_key(board_id=board_id))
        except Exception:
            return

    def _resolve_directive_for_create(
        self,
        requested_directive: Optional[SprintDirective],
    ) -> Optional[SprintDirective]:
        if requested_directive in {SprintDirective.ACTIVE_SPRINT, SprintDirective.BACKLOG}:
            return requested_directive
        if not self._is_auto_sprint_enabled():
            return None
        if self._integration_sprint_policy() != "auto":
            return None
        board_id, board_type, _board_name = self._default_board_metadata()
        if (not self._is_sprint_capable_board_type(board_type)) or board_id is None:
            return None
        return SprintDirective.ACTIVE_SPRINT

    def _resolve_directive_for_mutation(
        self,
        *,
        requested_directive: Optional[SprintDirective],
        requested_status: Optional[str],
    ) -> Optional[SprintDirective]:
        if requested_directive in {SprintDirective.ACTIVE_SPRINT, SprintDirective.BACKLOG}:
            return requested_directive
        normalized_status = str(requested_status or "").strip().lower()
        if normalized_status != "in_progress":
            return None
        if not self._is_auto_sprint_enabled():
            return None
        if self._integration_sprint_policy() != "auto":
            return None
        board_id, board_type, _board_name = self._default_board_metadata()
        if (not self._is_sprint_capable_board_type(board_type)) or board_id is None:
            return None
        return SprintDirective.ACTIVE_SPRINT

    async def _apply_sprint_placement_after_create(
        self,
        *,
        issue_key: str,
        requested_directive: Optional[SprintDirective],
    ) -> Optional[SprintPlacementResultDTO]:
        directive = self._resolve_directive_for_create(requested_directive)
        if directive is None:
            return None
        return await self._apply_sprint_directive(
            issue_key=issue_key,
            directive=directive,
            trigger="create",
        )

    async def _apply_sprint_placement_after_mutation(
        self,
        *,
        issue_key: str,
        requested_directive: Optional[SprintDirective],
        requested_status: Optional[str],
        status_updated: bool,
    ) -> Optional[SprintPlacementResultDTO]:
        directive = self._resolve_directive_for_mutation(
            requested_directive=requested_directive,
            requested_status=requested_status,
        )
        if directive is None:
            return None
        if requested_status and not status_updated:
            return SprintPlacementResultDTO(
                attempted=False,
                applied=False,
                directive=directive,
                reason="status_transition_failed",
            )
        return await self._apply_sprint_directive(
            issue_key=issue_key,
            directive=directive,
            trigger="status_update",
        )

    async def _apply_sprint_directive(
        self,
        *,
        issue_key: str,
        directive: SprintDirective,
        trigger: str,
    ) -> SprintPlacementResultDTO:
        board_id, board_type, _board_name = self._default_board_metadata()
        if directive == SprintDirective.BACKLOG:
            print(f"[JiraAdapter] Sprint placement skipped for {issue_key}: explicit backlog directive")
            return SprintPlacementResultDTO(
                attempted=False,
                applied=False,
                directive=directive,
                reason="explicit_backlog",
                board_id=board_id,
                board_type=board_type,
            )

        if directive != SprintDirective.ACTIVE_SPRINT:
            print(f"[JiraAdapter] Sprint placement skipped for {issue_key}: unsupported directive={directive}")
            return SprintPlacementResultDTO(
                attempted=False,
                applied=False,
                directive=directive,
                reason="directive_not_supported",
                board_id=board_id,
                board_type=board_type,
            )

        if not self._is_sprint_capable_board_type(board_type):
            print(
                f"[JiraAdapter] Sprint placement skipped for {issue_key}: board_type={board_type}"
            )
            return SprintPlacementResultDTO(
                attempted=False,
                applied=False,
                directive=directive,
                reason="board_not_scrum",
                board_id=board_id,
                board_type=board_type,
            )

        if board_id is None:
            print(f"[JiraAdapter] Sprint placement skipped for {issue_key}: default board not configured")
            return SprintPlacementResultDTO(
                attempted=False,
                applied=False,
                directive=directive,
                reason="default_board_not_configured",
                board_type=board_type,
            )

        if self._is_no_active_sprint_cached(board_id=board_id):
            print(
                f"[JiraAdapter] Sprint placement skipped for {issue_key}: cached no active sprint on board={board_id}"
            )
            return SprintPlacementResultDTO(
                attempted=False,
                applied=False,
                directive=directive,
                reason="no_active_sprint",
                board_id=board_id,
                board_type=board_type,
            )

        active_sprint = await jira_oauth_service.get_active_sprint_for_board(
            access_token=self.access_token,
            cloud_id=self.cloud_id,
            board_id=board_id,
        )
        sprint_id_raw = active_sprint.get("id") if isinstance(active_sprint, dict) else None
        sprint_name = str((active_sprint or {}).get("name") or "").strip() if isinstance(active_sprint, dict) else None
        sprint_id: Optional[int] = None
        if sprint_id_raw is not None:
            try:
                sprint_id = int(sprint_id_raw)
            except Exception:
                sprint_id = None

        if sprint_id is None:
            self._cache_no_active_sprint(board_id=board_id)
            print(f"[JiraAdapter] Sprint placement skipped for {issue_key}: no active sprint on board={board_id}")
            return SprintPlacementResultDTO(
                attempted=False,
                applied=False,
                directive=directive,
                reason="no_active_sprint",
                board_id=board_id,
                board_type=board_type,
            )

        self._clear_no_active_sprint_cache(board_id=board_id)

        added = await jira_oauth_service.add_issue_to_sprint(
            access_token=self.access_token,
            cloud_id=self.cloud_id,
            sprint_id=sprint_id,
            issue_key=issue_key,
        )
        if added:
            print(
                f"[JiraAdapter] Sprint placement applied for {issue_key}: "
                f"board={board_id} sprint={sprint_id}"
            )
            return SprintPlacementResultDTO(
                attempted=True,
                applied=True,
                directive=directive,
                reason=f"{trigger}_added_to_active_sprint",
                board_id=board_id,
                board_type=board_type,
                sprint_id=sprint_id,
                sprint_name=sprint_name,
            )

        print(
            f"[JiraAdapter] Sprint placement failed for {issue_key}: "
            f"board={board_id} sprint={sprint_id}"
        )
        return SprintPlacementResultDTO(
            attempted=True,
            applied=False,
            directive=directive,
            reason="add_to_sprint_failed",
            board_id=board_id,
            board_type=board_type,
            sprint_id=sprint_id,
            sprint_name=sprint_name,
            error="jira_add_issue_to_sprint_failed",
        )

    async def _get_jira_project(self):
        """
        Get Jira project (uses first available project).
        """
        try:
            import httpx

            response = await httpx.AsyncClient().get(
                f"https://api.atlassian.com/ex/jira/{self.cloud_id}/rest/api/3/project",
                headers={
                    "Authorization": f"Bearer {self.access_token}",
                    "Accept": "application/json"
                },
                timeout=30.0
            )

            if response.status_code == 200:
                projects = response.json()
                if projects:
                    return projects[0]  # Use first project

            return None

        except Exception as e:
            print(f"Error fetching Jira project: {str(e)}")
            return None

    async def ensure_token_valid(self) -> None:
        """Public wrapper for token refresh checks."""
        await self._ensure_token_valid()

    async def _ensure_token_valid(self):
        """
        Check if access token is expired and refresh if needed.
        Updates the project document with new token.
        """
        try:
            if self._access_token_override:
                return

            expires_at = self.jira_integration.get("expires_at")

            # Check if token is expired or about to expire (within 5 minutes)
            if expires_at and datetime.utcnow() >= (expires_at - timedelta(minutes=5)):
                print("Jira token expired, refreshing...")

                # Refresh token
                token_response = await jira_oauth_service.refresh_access_token(
                    self.refresh_token
                )

                # Update tokens
                self.access_token = token_response.get("access_token")
                new_refresh_token = token_response.get("refresh_token")
                expires_in = token_response.get("expires_in")

                # Update project document
                from app.core.database import get_database
                db = get_database()

                await db.projects.update_one(
                    {"project_id": self.project["project_id"]},
                    {
                        "$set": {
                            "integrations.jira.access_token": encryption_service.encrypt(self.access_token),
                            "integrations.jira.refresh_token": encryption_service.encrypt(new_refresh_token) if new_refresh_token else self.refresh_token,
                            "integrations.jira.expires_at": datetime.utcnow() + timedelta(seconds=expires_in) if expires_in else None
                        }
                    }
                )

                print("Jira token refreshed successfully")

        except Exception as e:
            print(f"Error refreshing Jira token: {str(e)}")
            raise
