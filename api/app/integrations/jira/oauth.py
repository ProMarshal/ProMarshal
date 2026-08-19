"""Jira OAuth service for authentication and API calls"""

import httpx
import re
from datetime import datetime, timezone
from urllib.parse import urlencode, quote
from typing import Dict, Any, Optional, List
from app.core.config import settings
from app.integrations.common.errors import (
    IntegrationAuthError,
    IntegrationError,
    IntegrationRateLimitError,
    IntegrationRequestError,
    IntegrationTransportError,
)
from app.integrations.common.retry import (
    parse_retry_after_seconds,
)
from app.integrations.common.non_idempotent import execute_non_idempotent_write_with_verify


class JiraOAuthService:
    """Service for Jira OAuth 2.0 (3LO) flow and API interactions"""

    def __init__(self):
        """Initialize Jira OAuth service"""
        self.client_id = settings.jira_client_id
        self.client_secret = settings.jira_client_secret
        self.oauth_url = "https://auth.atlassian.com/authorize"
        self.token_url = "https://auth.atlassian.com/oauth/token"
        self.api_url = "https://api.atlassian.com"

    def get_authorization_url(self, state: str, redirect_uri: str) -> str:
        """
        Generate Jira OAuth authorization URL

        Args:
            state: State token for CSRF protection
            redirect_uri: Callback URL after authorization

        Returns:
            Full authorization URL
        """
        scopes = [
            "read:jira-work",
            "read:jira-user",
            "write:jira-work",
            "read:email-address:jira",  # Required for robust user identity mapping
            "read:project:jira",  # Required by board APIs with projectKeyOrId
            "read:board-scope:jira-software",  # Required to list boards/backlogs
            "read:sprint:jira-software",  # Required to discover active sprint
            "write:sprint:jira-software",  # Required to place issues into sprint
            "manage:jira-webhook",  # Required for webhook registration
            "offline_access"
        ]

        params = {
            "audience": "api.atlassian.com",
            "client_id": self.client_id,
            "scope": " ".join(scopes),
            "redirect_uri": redirect_uri,
            "state": state,
            "response_type": "code",
            "prompt": "consent"
        }

        query_string = urlencode(params, quote_via=quote)
        return f"{self.oauth_url}?{query_string}"

    async def exchange_code_for_token(self, code: str, redirect_uri: str) -> Dict[str, Any]:
        """
        Exchange authorization code for access token

        Args:
            code: Authorization code from Jira
            redirect_uri: Same redirect URI used in authorization

        Returns:
            Response from Atlassian with access_token, refresh_token, etc.

        Raises:
            ValueError: If token exchange fails
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.token_url,
                json={
                    "grant_type": "authorization_code",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri
                },
                headers={"Content-Type": "application/json"}
            )

            if response.status_code != 200:
                error_data = response.json() if response.content else {}
                error = error_data.get("error_description", error_data.get("error", "Unknown error"))
                raise ValueError(f"Jira OAuth error: {error}")

            return response.json()

    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """
        Refresh an expired access token

        Args:
            refresh_token: Refresh token from initial auth

        Returns:
            New access token and refresh token

        Raises:
            ValueError: If refresh fails
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.token_url,
                json={
                    "grant_type": "refresh_token",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "refresh_token": refresh_token
                },
                headers={"Content-Type": "application/json"}
            )

            if response.status_code != 200:
                error_data = response.json() if response.content else {}
                error = error_data.get("error_description", error_data.get("error", "Unknown error"))
                raise ValueError(f"Jira token refresh error: {error}")

            return response.json()

    async def get_accessible_resources(self, access_token: str) -> List[Dict[str, Any]]:
        """
        Get list of Jira sites the user has access to

        Args:
            access_token: Jira access token

        Returns:
            List of accessible resources (Jira sites)
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url}/oauth/token/accessible-resources",
                    headers={"Authorization": f"Bearer {access_token}"}
                )

                response.raise_for_status()
                return response.json()
        except Exception as e:
            print(f"Error getting accessible resources: {str(e)}")
            return []

    async def get_current_user(self, access_token: str, cloud_id: str) -> Optional[Dict[str, Any]]:
        """
        Get current user information from Jira

        Args:
            access_token: Jira access token
            cloud_id: Jira Cloud ID

        Returns:
            User information dict or None if error
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/myself",
                    headers={"Authorization": f"Bearer {access_token}"}
                )

                response.raise_for_status()
                return response.json()
        except Exception as e:
            print(f"Error getting current user: {str(e)}")
            return None

    async def fetch_user_tasks(
        self,
        access_token: str,
        cloud_id: str,
        account_id: str,
        statuses: List[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch Jira tasks assigned to a user using the new /search/jql endpoint

        Args:
            access_token: Jira access token
            cloud_id: Jira Cloud ID
            account_id: User's Jira account ID
            statuses: List of statuses to filter (default: To Do, In Progress)

        Returns:
            List of Jira issues
        """
        if statuses is None:
            statuses = ["To Do", "In Progress"]

        status_str = ", ".join([f'"{s}"' for s in statuses])
        jql = f'assignee = "{account_id}" AND status IN ({status_str}) ORDER BY status ASC, updated DESC'

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/search/jql",
                    json={
                        "jql": jql,
                        "maxResults": 50,
                        "fields": ["summary", "status", "priority", "issuetype", "project", "updated", "key", "assignee", "comment", "parent", "statuscategorychangedate"]
                    },
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json"
                    }
                )

                if response.status_code == 401:
                    raise ValueError("Token expired")

                response.raise_for_status()
                data = response.json()
                return data.get("issues", [])

        except httpx.HTTPStatusError as e:
            print(f"Error fetching Jira tasks: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            print(f"Error fetching Jira tasks: {str(e)}")
            raise

    async def fetch_all_workspace_tasks(
        self,
        access_token: str,
        cloud_id: str
    ) -> List[Dict[str, Any]]:
        """
        Fetch ALL tasks from Jira workspace with pagination

        Args:
            access_token: Jira access token
            cloud_id: Jira Cloud ID

        Returns:
            List of all Jira issues
        """
        all_issues = []
        next_page_token = None

        try:
            async with httpx.AsyncClient() as client:
                while True:
                    # Build request body
                    request_body = {
                        "jql": "project is not EMPTY ORDER BY updated DESC",
                        "maxResults": 1000,
                        "fields": ["summary", "status", "priority", "issuetype", "project", "updated", "key", "assignee", "duedate", "comment", "parent", "statuscategorychangedate", "customfield_10020"]
                    }

                    # Add nextPageToken for subsequent requests
                    if next_page_token:
                        request_body["nextPageToken"] = next_page_token

                    response = await client.post(
                        f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/search/jql",
                        json=request_body,
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "Content-Type": "application/json",
                            "Accept": "application/json"
                        },
                        timeout=30.0
                    )

                    if response.status_code == 401:
                        raise ValueError("Token expired")

                    if response.status_code != 200:
                        print(f"Task fetch error: {response.text}")
                        response.raise_for_status()

                    data = response.json()
                    issues = data.get("issues", [])
                    all_issues.extend(issues)

                    print(f"Fetched {len(issues)} issues (total: {len(all_issues)})")

                    # Check for next page
                    next_page_token = data.get("nextPageToken")
                    if not next_page_token:
                        break  # No more pages

                print(f"Total issues fetched: {len(all_issues)}")
                return all_issues

        except Exception as e:
            print(f"Error fetching workspace tasks: {str(e)}")
            raise

    async def search_issues(
        self,
        access_token: str,
        cloud_id: str,
        jql: str,
        max_results: int = 1000,
        sprint_field_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Search Jira issues using JQL with pagination

        Args:
            access_token: Jira access token
            cloud_id: Jira Cloud ID
            jql: JQL query string (e.g., "project = PRM")
            max_results: Maximum results per page
            sprint_field_id: Optional sprint custom field ID (e.g., "customfield_10020")

        Returns:
            List of matching Jira issues
        """
        all_issues = []
        next_page_token = None

        try:
            # Build fields list dynamically
            fields = ["summary", "status", "priority", "issuetype", "project", "updated", "key", "assignee", "duedate", "comment", "parent", "statuscategorychangedate"]

            # Add sprint field if provided, otherwise try common defaults
            if sprint_field_id:
                fields.append(sprint_field_id)
            else:
                # Fallback: try common sprint field IDs
                fields.extend(["customfield_10020", "sprint"])

            async with httpx.AsyncClient() as client:
                while True:
                    request_body = {
                        "jql": jql,
                        "maxResults": max_results,
                        "fields": fields
                    }

                    if next_page_token:
                        request_body["nextPageToken"] = next_page_token

                    response = await client.post(
                        f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/search/jql",
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "Accept": "application/json",
                            "Content-Type": "application/json"
                        },
                        json=request_body,
                        timeout=30.0
                    )

                    if response.status_code != 200:
                        print(f"Error response: {response.status_code} - {response.text}")
                        raise Exception(f"Failed to search issues: {response.status_code}")

                    data = response.json()
                    issues = data.get("issues", [])
                    all_issues.extend(issues)

                    next_page_token = data.get("nextPageToken")
                    if not next_page_token:
                        break

                print(f"JQL search returned {len(all_issues)} issues")
                return all_issues

        except Exception as e:
            print(f"Error searching issues: {str(e)}")
            raise

    async def fetch_project_statuses(
        self,
        access_token: str,
        cloud_id: str,
        project_key_or_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Fetch project workflow statuses from Jira.

        Returns flattened status list with optional statusCategory metadata.
        """
        normalized_project = str(project_key_or_id or "").strip()
        if not normalized_project:
            return []
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/project/{normalized_project}/statuses",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                    },
                    timeout=30.0,
                )
                if response.status_code != 200:
                    print(
                        "[JiraOAuth] Failed to fetch project statuses: "
                        f"project={normalized_project} status={response.status_code}"
                    )
                    return []
                payload = response.json()
                if not isinstance(payload, list):
                    return []
                flattened: List[Dict[str, Any]] = []
                for item in payload:
                    if not isinstance(item, dict):
                        continue
                    statuses = item.get("statuses")
                    if not isinstance(statuses, list):
                        continue
                    for status in statuses:
                        if not isinstance(status, dict):
                            continue
                        name = str(status.get("name") or "").strip()
                        if not name:
                            continue
                        flattened.append(status)
                return flattened
        except Exception as exc:
            print(
                "[JiraOAuth] Error fetching project statuses: "
                f"project={normalized_project} error={exc}"
            )
            return []

    async def detect_sprint_field_id(
        self,
        access_token: str,
        cloud_id: str
    ) -> Optional[str]:
        """
        Auto-detect the Sprint custom field ID for this Jira instance.

        The Sprint field ID varies across Jira instances (e.g., customfield_10020).
        This method queries the Jira API to find the correct field ID.

        Args:
            access_token: Jira access token
            cloud_id: Jira Cloud ID

        Returns:
            Sprint field ID (e.g., "customfield_10020") or None if not found
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/field",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json"
                    },
                    timeout=30.0
                )

                if response.status_code != 200:
                    print(f"Failed to fetch fields: {response.status_code}")
                    return None

                fields = response.json()

                # Find sprint field by name and schema type
                for field in fields:
                    field_name = field.get("name", "").lower()
                    field_schema = field.get("schema", {})
                    custom_type = field_schema.get("custom", "")

                    # Match sprint field by name OR schema custom type
                    if (field_name == "sprint" or
                        "sprint" in custom_type or
                        "gh-sprint" in custom_type):
                        field_id = field.get("id")
                        print(f"[Sprint Field Detection] Found: {field_id} (name: {field.get('name')})")
                        return field_id

                print("[Sprint Field Detection] No sprint field found")
                return None

        except Exception as e:
            print(f"Error detecting sprint field: {str(e)}")
            return None

    async def detect_start_date_field_id(
        self,
        access_token: str,
        cloud_id: str,
    ) -> Optional[str]:
        """
        Auto-detect Start Date custom field ID for this Jira instance.

        Returns:
            Field ID (for example ``customfield_10015``) or ``None`` when not found.
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/field",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                    },
                    timeout=30.0,
                )
                if response.status_code != 200:
                    print(f"Failed to fetch fields for start date detection: {response.status_code}")
                    return None
                fields = response.json()
                if not isinstance(fields, list):
                    return None
                for field in fields:
                    if not isinstance(field, dict):
                        continue
                    field_id = str(field.get("id") or "").strip()
                    if not field_id.startswith("customfield_"):
                        continue
                    field_name = str(field.get("name") or "").strip().lower()
                    schema = field.get("schema") if isinstance(field.get("schema"), dict) else {}
                    field_type = str(schema.get("type") or "").strip().lower()
                    custom_type = str(schema.get("custom") or "").strip().lower()
                    if field_name == "start date" and field_type == "date":
                        return field_id
                    if "startdate" in custom_type and field_type == "date":
                        return field_id
                return None
        except Exception as exc:
            print(f"Error detecting start date field: {exc}")
            return None

    async def register_webhook(
        self,
        access_token: str,
        cloud_id: str,
        webhook_url: str
    ) -> Dict[str, Any]:
        """
        TEST METHOD: Register webhook with Jira

        Args:
            access_token: Jira access token
            cloud_id: Jira Cloud ID
            webhook_url: Our backend URL to receive webhooks

        Returns:
            Webhook registration response
        """
        try:
            async with httpx.AsyncClient() as client:
                # POST /rest/api/3/webhook - Register webhook
                response = await client.post(
                    f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/webhook",
                    json={
                        "url": webhook_url,
                        "webhooks": [
                            {
                                "jqlFilter": "issuetype != ''",
                                "events": [
                                    "jira:issue_created",
                                    "jira:issue_updated",
                                    "jira:issue_deleted"
                                ]
                            }
                        ]
                    },
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json"
                    }
                )

                print(f"Webhook registration status: {response.status_code}")

                if response.status_code == 401:
                    raise ValueError("Token expired")

                if response.status_code != 200 and response.status_code != 201:
                    print(f"Webhook error: {response.text}")

                response.raise_for_status()
                result = response.json()
                print(f"Webhook registered successfully")
                return result

        except Exception as e:
            print(f"Error registering webhook: {str(e)}")
            raise

    async def delete_webhooks_by_url(
        self,
        *,
        access_token: str,
        cloud_id: str,
        webhook_url: str,
    ) -> int:
        """
        Best-effort delete Jira webhooks that exactly match the given webhook URL.

        Returns:
            Number of webhook IDs requested for deletion.
        """
        normalized_url = str(webhook_url or "").strip()
        if not normalized_url:
            return 0

        async with httpx.AsyncClient() as client:
            collected: list[str] = []
            start_at = 0
            max_results = 100

            while True:
                response = await client.get(
                    f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/webhook",
                    params={"startAt": start_at, "maxResults": max_results},
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                    },
                    timeout=30.0,
                )
                if response.status_code == 401:
                    raise ValueError("Token expired")
                response.raise_for_status()
                payload = response.json() if response.content else {}
                values = payload.get("values") or payload.get("webhooks") or []
                if not isinstance(values, list):
                    values = []

                for row in values:
                    if not isinstance(row, dict):
                        continue
                    wid = str(row.get("id") or "").strip()
                    row_url = str(row.get("url") or "").strip()
                    if wid and row_url == normalized_url:
                        collected.append(wid)

                is_last = bool(payload.get("isLast", True))
                if is_last or not values:
                    break
                start_at += max_results

            if not collected:
                return 0

            delete_response = await client.delete(
                f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/webhook",
                json={"webhookIds": collected},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
            if delete_response.status_code == 401:
                raise ValueError("Token expired")
            delete_response.raise_for_status()
            return len(collected)

    async def search_user_by_email(
        self,
        access_token: str,
        cloud_id: str,
        email: str
    ) -> Optional[str]:
        """
        Search Jira user by email and return their account ID

        Args:
            access_token: Jira access token
            cloud_id: Jira Cloud ID
            email: User's email address

        Returns:
            Jira account ID if found, None otherwise
        """
        try:
            async with httpx.AsyncClient() as client:
                # Search user by email
                response = await client.get(
                    f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/user/search",
                    params={"query": email},
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json"
                    }
                )

                if response.status_code == 401:
                    raise ValueError("Token expired")

                response.raise_for_status()
                users = response.json()

                # Return first matching user's accountId
                if users and len(users) > 0:
                    # Match by email if available, otherwise first result
                    for user in users:
                        user_email = user.get("emailAddress", "").lower()
                        if user_email == email.lower():
                            return user.get("accountId")

                    # If no exact email match, return first result
                    return users[0].get("accountId")

                return None

        except Exception as e:
            print(f"Error searching Jira user by email {email}: {str(e)}")
            return None

    async def sync_single_member_jira_id(
        self,
        db,
        project_id: str,
        member_email: str,
        access_token: str,
        cloud_id: str
    ) -> bool:
        """
        Sync a single member's Jira account ID

        Args:
            db: Database instance
            project_id: Project ID
            member_email: Member's email address
            access_token: Jira access token
            cloud_id: Jira Cloud ID

        Returns:
            True if synced successfully, False otherwise
        """
        result = await self.sync_single_member_jira_id_detailed(
            db=db,
            project_id=project_id,
            member_email=member_email,
            access_token=access_token,
            cloud_id=cloud_id,
            jira_account_id=None,
            allow_overwrite=False,
        )
        return bool(result.get("ok"))

    async def sync_single_member_jira_id_detailed(
        self,
        *,
        db,
        project_id: str,
        member_email: str,
        access_token: str,
        cloud_id: str,
        jira_account_id: Optional[str] = None,
        allow_overwrite: bool = False,
    ) -> Dict[str, Any]:
        """
        Sync a single member's Jira account ID with conflict-safe behavior.

        Behavior:
        - Does not overwrite an existing different mapping unless allow_overwrite=True.
        - Supports project lookup by Mongo _id or custom project_id.
        - Returns detailed status for caller-side insight messaging.
        """
        try:
            from bson import ObjectId
        except Exception:
            ObjectId = None  # type: ignore[assignment]

        try:
            normalized_email = str(member_email or "").strip().lower()
            normalized_project_id = str(project_id or "").strip()
            normalized_account_id = str(jira_account_id or "").strip()

            if not normalized_email:
                return {
                    "ok": False,
                    "updated": False,
                    "conflict": False,
                    "reason": "missing_member_email",
                }

            # Resolve account id if caller did not provide it.
            if not normalized_account_id:
                normalized_account_id = str(
                    await self.search_user_by_email(access_token, cloud_id, normalized_email) or ""
                ).strip()

            if not normalized_account_id:
                print(f"Jira user not found for email: {normalized_email}")
                return {
                    "ok": False,
                    "updated": False,
                    "conflict": False,
                    "reason": "jira_user_not_found",
                    "member_email": normalized_email,
                }

            project_filters: List[Dict[str, Any]] = []
            if normalized_project_id:
                project_filters.append({"project_id": normalized_project_id})
                if ObjectId is not None:
                    try:
                        project_filters.append({"_id": ObjectId(normalized_project_id)})
                    except Exception:
                        pass

            if not project_filters:
                return {
                    "ok": False,
                    "updated": False,
                    "conflict": False,
                    "reason": "missing_project_identifier",
                    "member_email": normalized_email,
                    "jira_account_id": normalized_account_id,
                }

            email_filter = {
                "$regex": f"^{re.escape(normalized_email)}$",
                "$options": "i",
            }

            for base_filter in project_filters:
                selector = dict(base_filter)
                selector["members"] = {"$elemMatch": {"email": email_filter}}

                doc = await db.projects.find_one(selector, {"members.$": 1})
                if not doc:
                    continue

                member = None
                members = doc.get("members") or []
                if isinstance(members, list) and members:
                    member = members[0]
                existing_mapping = ""
                if isinstance(member, dict):
                    existing_mapping = str(
                        ((member.get("integration_ids") or {}).get("jira_account_id") or "")
                    ).strip()

                if (
                    existing_mapping
                    and existing_mapping != normalized_account_id
                    and not allow_overwrite
                ):
                    print(
                        "Jira mapping conflict detected: "
                        f"email={normalized_email} existing={existing_mapping} "
                        f"candidate={normalized_account_id}"
                    )
                    return {
                        "ok": False,
                        "updated": False,
                        "conflict": True,
                        "reason": "mapping_conflict",
                        "member_email": normalized_email,
                        "existing_account_id": existing_mapping,
                        "jira_account_id": normalized_account_id,
                    }

                update_filter = dict(base_filter)
                update_filter["members.email"] = email_filter
                result = await db.projects.update_one(
                    update_filter,
                    {
                        "$set": {
                            "members.$.integration_ids.jira_account_id": normalized_account_id
                        }
                    },
                )

                if result.modified_count > 0:
                    print(
                        f"Synced Jira account ID for {normalized_email}: {normalized_account_id}"
                    )
                    return {
                        "ok": True,
                        "updated": True,
                        "conflict": False,
                        "reason": "mapping_updated",
                        "member_email": normalized_email,
                        "jira_account_id": normalized_account_id,
                    }

                if result.matched_count > 0:
                    return {
                        "ok": True,
                        "updated": False,
                        "conflict": False,
                        "reason": "mapping_already_current",
                        "member_email": normalized_email,
                        "jira_account_id": normalized_account_id,
                    }

            print(f"Failed to update member mapping for {normalized_email}")
            return {
                "ok": False,
                "updated": False,
                "conflict": False,
                "reason": "member_or_project_not_found",
                "member_email": normalized_email,
                "jira_account_id": normalized_account_id,
            }

        except Exception as e:
            print(f"Error syncing Jira ID for {member_email}: {str(e)}")
            return {
                "ok": False,
                "updated": False,
                "conflict": False,
                "reason": "sync_exception",
                "error": str(e),
                "member_email": str(member_email or "").strip().lower(),
                "jira_account_id": str(jira_account_id or "").strip() or None,
            }

    def _status_mapping_for_target(self) -> Dict[str, List[str]]:
        return {
            "todo": ["To Do", "Backlog", "Open"],
            "in_progress": ["In Progress", "In Development"],
            "in_review": ["In Review", "Review", "Ready for Review", "In QA", "QA"],
            "done": ["Done", "Closed", "Complete", "Resolved"],
        }

    @staticmethod
    def _extract_adf_plain_text(node: Any) -> str:
        if isinstance(node, list):
            return "".join(JiraOAuthService._extract_adf_plain_text(item) for item in node)
        if not isinstance(node, dict):
            return ""
        node_type = str(node.get("type") or "").strip().lower()
        if node_type == "text":
            return str(node.get("text") or "")
        if node_type == "mention":
            attrs = node.get("attrs") if isinstance(node.get("attrs"), dict) else {}
            mention_text = str(attrs.get("text") or "").strip()
            if mention_text:
                return mention_text
            mention_id = str(attrs.get("id") or "").strip()
            return f"@{mention_id}" if mention_id else ""
        return JiraOAuthService._extract_adf_plain_text(node.get("content"))

    @staticmethod
    def _build_comment_adf_body(
        *,
        comment_text: str,
        mention_entities: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        text = str(comment_text or "")
        entities: List[Dict[str, Any]] = []
        for item in (mention_entities or []):
            if not isinstance(item, dict):
                continue
            start = item.get("start")
            end = item.get("end")
            account_id = str(item.get("account_id") or "").strip()
            mention_text = str(item.get("text") or "").strip()
            if not isinstance(start, int) or not isinstance(end, int):
                continue
            if start < 0 or end <= start or start >= len(text):
                continue
            if not account_id:
                continue
            entities.append(
                {
                    "start": start,
                    "end": min(end, len(text)),
                    "account_id": account_id,
                    "text": mention_text or text[start:min(end, len(text))],
                }
            )
        entities.sort(key=lambda row: int(row["start"]))

        non_overlapping: List[Dict[str, Any]] = []
        cursor = 0
        for item in entities:
            start = int(item["start"])
            end = int(item["end"])
            if start < cursor:
                continue
            non_overlapping.append(item)
            cursor = end

        content_nodes: List[Dict[str, Any]] = []
        cursor = 0
        for item in non_overlapping:
            start = int(item["start"])
            end = int(item["end"])
            if start > cursor:
                segment = text[cursor:start]
                if segment:
                    content_nodes.append({"type": "text", "text": segment})
            content_nodes.append(
                {
                    "type": "mention",
                    "attrs": {
                        "id": str(item["account_id"]),
                        "text": str(item["text"]),
                    },
                }
            )
            cursor = end
        if cursor < len(text):
            tail = text[cursor:]
            if tail:
                content_nodes.append({"type": "text", "text": tail})
        if not content_nodes:
            content_nodes = [{"type": "text", "text": text}]

        return {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": content_nodes,
                    }
                ],
            }
        }

    @staticmethod
    def _normalize_whitespace(value: str) -> str:
        return " ".join(str(value or "").split())

    @staticmethod
    def _parse_jira_datetime(raw_value: Any) -> Optional[datetime]:
        text = str(raw_value or "").strip()
        if not text:
            return None
        text = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            try:
                parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%f%z")
            except ValueError:
                return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    async def _verify_comment_added(
        self,
        *,
        access_token: str,
        cloud_id: str,
        task_key: str,
        expected_comment_text: str,
        not_before: Optional[datetime],
    ) -> bool:
        issue = await self.get_issue(access_token, cloud_id, task_key)
        fields = issue.get("fields") if isinstance(issue, dict) else {}
        comment_container = fields.get("comment") if isinstance(fields, dict) else {}
        comments = comment_container.get("comments") if isinstance(comment_container, dict) else []
        if not isinstance(comments, list):
            return False

        expected = self._normalize_whitespace(expected_comment_text)
        if not expected:
            return False

        floor_time = not_before.astimezone(timezone.utc) if isinstance(not_before, datetime) else None
        if floor_time:
            floor_time = floor_time.replace(microsecond=0)

        for comment in reversed(comments):
            if not isinstance(comment, dict):
                continue
            if floor_time is not None:
                created_at = self._parse_jira_datetime(comment.get("created"))
                if created_at is None or created_at.replace(microsecond=0) < floor_time:
                    continue
            plain_text = self._normalize_whitespace(
                self._extract_adf_plain_text(comment.get("body"))
            )
            if plain_text == expected:
                return True
        return False

    async def _verify_task_assignee(
        self,
        *,
        access_token: str,
        cloud_id: str,
        task_key: str,
        expected_account_id: str,
    ) -> bool:
        issue = await self.get_issue(access_token, cloud_id, task_key)
        fields = issue.get("fields") if isinstance(issue, dict) else {}
        assignee = fields.get("assignee") if isinstance(fields, dict) else {}
        current = str((assignee or {}).get("accountId") or "").strip()
        return bool(current) and current == str(expected_account_id or "").strip()

    async def _verify_task_status(
        self,
        *,
        access_token: str,
        cloud_id: str,
        task_key: str,
        target_status: str,
    ) -> bool:
        expected_names = self._status_mapping_for_target().get(str(target_status or "").strip().lower(), [])
        if not expected_names:
            return False
        issue = await self.get_issue(access_token, cloud_id, task_key)
        fields = issue.get("fields") if isinstance(issue, dict) else {}
        status = fields.get("status") if isinstance(fields, dict) else {}
        status_name = str((status or {}).get("name") or "").strip()
        return status_name in expected_names

    async def _verify_task_description(
        self,
        *,
        access_token: str,
        cloud_id: str,
        task_key: str,
        expected_description_text: str,
    ) -> bool:
        expected = self._normalize_whitespace(expected_description_text)
        if not expected:
            return False
        issue = await self.get_issue(access_token, cloud_id, task_key)
        fields = issue.get("fields") if isinstance(issue, dict) else {}
        description = fields.get("description") if isinstance(fields, dict) else {}
        current = self._normalize_whitespace(self._extract_adf_plain_text(description))
        return bool(current) and current == expected

    async def _verify_task_parent(
        self,
        *,
        access_token: str,
        cloud_id: str,
        task_key: str,
        expected_parent_key: str,
    ) -> bool:
        issue = await self.get_issue(access_token, cloud_id, task_key)
        fields = issue.get("fields") if isinstance(issue, dict) else {}
        parent = fields.get("parent") if isinstance(fields, dict) else {}
        current_parent_key = str((parent or {}).get("key") or "").strip().upper()
        return bool(current_parent_key) and current_parent_key == str(expected_parent_key or "").strip().upper()

    @staticmethod
    def _format_jira_date_value(raw_value: Optional[datetime]) -> Optional[str]:
        if raw_value is None:
            return None
        if isinstance(raw_value, datetime):
            if raw_value.tzinfo is not None:
                raw_value = raw_value.astimezone(timezone.utc).replace(tzinfo=None)
            return raw_value.strftime("%Y-%m-%d")
        return None

    @staticmethod
    def _extract_start_date_from_fields(fields: Dict[str, Any]) -> Optional[str]:
        if not isinstance(fields, dict):
            return None
        for key in (
            "customfield_10015",
            "customfield_10016",
            "customfield_10017",
            "customfield_10018",
            "customfield_10019",
            "customfield_10021",
            "customfield_10022",
        ):
            value = fields.get(key)
            normalized = str(value or "").strip()
            if normalized:
                return normalized
        return None

    async def _verify_task_due_date(
        self,
        *,
        access_token: str,
        cloud_id: str,
        task_key: str,
        expected_due_date: Optional[str],
    ) -> bool:
        issue = await self.get_issue(access_token, cloud_id, task_key)
        fields = issue.get("fields") if isinstance(issue, dict) else {}
        current_due_date = str((fields or {}).get("duedate") or "").strip() if isinstance(fields, dict) else ""
        expected = str(expected_due_date or "").strip()
        if not expected:
            return not current_due_date
        return current_due_date == expected

    async def _verify_task_start_date(
        self,
        *,
        access_token: str,
        cloud_id: str,
        task_key: str,
        expected_start_date: Optional[str],
    ) -> bool:
        issue = await self.get_issue(access_token, cloud_id, task_key)
        fields = issue.get("fields") if isinstance(issue, dict) else {}
        current_start_date = self._extract_start_date_from_fields(fields if isinstance(fields, dict) else {})
        expected = str(expected_start_date or "").strip()
        if not expected:
            return not str(current_start_date or "").strip()
        return str(current_start_date or "").strip() == expected

    async def _verify_issue_in_sprint(
        self,
        *,
        access_token: str,
        cloud_id: str,
        sprint_id: int,
        issue_key: str,
    ) -> bool:
        normalized_issue_key = str(issue_key or "").strip().upper()
        if not normalized_issue_key:
            return False
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url}/ex/jira/{cloud_id}/rest/agile/1.0/sprint/{int(sprint_id)}/issue",
                    params={"jql": f'key = "{normalized_issue_key}"', "maxResults": 1, "fields": "key"},
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                    },
                    timeout=20.0,
                )
        except Exception as exc:
            print(
                f"Jira verify-first sprint check failed for {normalized_issue_key} in sprint {sprint_id}: {exc}"
            )
            return False

        if response.status_code != 200:
            return False
        data = response.json() if response.content else {}
        issues = data.get("issues") if isinstance(data, dict) else None
        if not isinstance(issues, list):
            return False
        for issue in issues:
            key = str((issue or {}).get("key") or "").strip().upper()
            if key == normalized_issue_key:
                return True
        return False

    async def add_comment_to_task(
        self,
        access_token: str,
        cloud_id: str,
        task_key: str,
        comment_text: str,
        max_retries: int = 3,
        mention_entities: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """
        Add a comment to a Jira task

        Args:
            access_token: Jira access token
            cloud_id: Jira Cloud ID
            task_key: Task key (e.g., "PRM-28")
            comment_text: Comment text to add
            max_retries: Maximum number of retries on failure

        Returns:
            True if successful, False otherwise
        """
        write_started_at = datetime.now(timezone.utc)

        # Build ADF (Atlassian Document Format) comment body.
        comment_body = self._build_comment_adf_body(
            comment_text=comment_text,
            mention_entities=mention_entities,
        )

        async def _send_comment_request() -> None:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/issue/{task_key}/comment",
                        json=comment_body,
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "Content-Type": "application/json",
                            "Accept": "application/json"
                        },
                        timeout=10.0
                    )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise IntegrationTransportError(
                    f"Transport error while adding comment to {task_key}: {exc}",
                    integration="jira",
                    operation="add_comment_to_task",
                    details={"task_key": task_key},
                ) from exc

            if response.status_code == 201:
                return

            error_msg = response.text[:200] if response.text else "No error message"
            details = {"task_key": task_key, "error": error_msg}
            if response.status_code == 429:
                retry_after = parse_retry_after_seconds(
                    response.headers.get("Retry-After"),
                    fallback_seconds=1.0,
                )
                raise IntegrationRateLimitError(
                    f"Rate limited while adding comment to {task_key}",
                    integration="jira",
                    operation="add_comment_to_task",
                    retry_after_seconds=retry_after,
                    details=details,
                )
            if response.status_code in (401, 403):
                raise IntegrationAuthError(
                    f"Auth failed while adding comment to {task_key}",
                    integration="jira",
                    operation="add_comment_to_task",
                    http_status=response.status_code,
                    details=details,
                )
            if response.status_code >= 500:
                raise IntegrationRequestError(
                    f"Server error while adding comment to {task_key}",
                    integration="jira",
                    operation="add_comment_to_task",
                    retryable=True,
                    http_status=response.status_code,
                    details=details,
                )
            raise IntegrationRequestError(
                f"Failed to add comment to {task_key}",
                integration="jira",
                operation="add_comment_to_task",
                retryable=False,
                http_status=response.status_code,
                details=details,
            )

        def _on_retry(attempt_number: int, max_attempts: int, delay_seconds: float, exc: Exception) -> None:
            print(
                f"Retrying add comment for {task_key} in {delay_seconds:.1f}s "
                f"(attempt {attempt_number + 1}/{max_attempts}): {exc}"
            )

        try:
            await execute_non_idempotent_write_with_verify(
                operation_name="add_comment_to_task",
                write_operation=_send_comment_request,
                verify_operation=lambda: self._verify_comment_added(
                    access_token=access_token,
                    cloud_id=cloud_id,
                    task_key=task_key,
                    expected_comment_text=comment_text,
                    not_before=write_started_at,
                ),
                max_retries=max_retries,
                integration="jira",
                on_retry=_on_retry,
            )
            print(f"Comment added to {task_key}")
            return True
        except IntegrationError as exc:
            status_text = f"HTTP {exc.http_status}" if exc.http_status else "no_status"
            print(f"Failed to add comment: {status_text}")
            print(f"   Error code: {exc.error_code}")
            print(f"   Task: {task_key}, Comment: '{comment_text[:50]}...'")
            return False
        except Exception as exc:
            print(f"Unexpected error adding comment to {task_key}: {exc}")
            return False

    async def get_available_transitions(
        self,
        access_token: str,
        cloud_id: str,
        task_key: str
    ) -> list:
        """
        Get available status transitions for a task

        Args:
            access_token: Jira access token
            cloud_id: Jira Cloud ID
            task_key: Task key (e.g., "PRM-28")

        Returns:
            List of available transitions
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/issue/{task_key}/transitions",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json"
                    },
                    timeout=10.0
                )

                if response.status_code == 200:
                    data = response.json()
                    return data.get("transitions", [])
                else:
                    print(f"Failed to get transitions: {response.status_code}")
                    return []

        except Exception as e:
            print(f"Error getting transitions: {str(e)}")
            return []

    async def apply_transition_by_id(
        self,
        access_token: str,
        cloud_id: str,
        task_key: str,
        transition_id: str,
    ) -> dict:
        """Apply a Jira transition directly by transition ID.

        Used by the Cadence adapter when available_transitions are pre-fetched
        at session creation and stored per task.

        Returns dict with keys: ok, status_updated, transition_id.
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/issue/{task_key}/transitions",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    json={"transition": {"id": transition_id}},
                    timeout=10.0,
                )
                if response.status_code in (200, 204):
                    return {"ok": True, "status_updated": True, "transition_id": transition_id}
                print(
                    f"apply_transition_by_id failed: task={task_key} "
                    f"transition_id={transition_id} status={response.status_code}"
                )
                return {"ok": False, "status_updated": False, "transition_id": transition_id}
        except Exception as exc:
            print(f"apply_transition_by_id error: task={task_key} error={exc}")
            return {"ok": False, "status_updated": False, "transition_id": transition_id}

    async def update_task_assignee(
        self,
        access_token: str,
        cloud_id: str,
        task_key: str,
        assignee_account_id: str,
        max_retries: int = 3,
    ) -> bool:
        """
        Update task assignee in Jira.

        Args:
            access_token: Jira access token
            cloud_id: Jira Cloud ID
            task_key: Task key (e.g., "PRM-28")
            assignee_account_id: Jira account ID for the assignee
            max_retries: Maximum number of retries on failure

        Returns:
            True if successful, False otherwise
        """
        normalized_account_id = str(assignee_account_id or "").strip()
        if not normalized_account_id:
            print(f"Missing assignee account id for task {task_key}")
            return False

        payload = {"accountId": normalized_account_id}

        async def _send_assignee_request() -> None:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.put(
                        f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/issue/{task_key}/assignee",
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                        },
                        timeout=10.0,
                    )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise IntegrationTransportError(
                    f"Transport error while updating assignee for {task_key}: {exc}",
                    integration="jira",
                    operation="update_task_assignee",
                    details={"task_key": task_key, "assignee_account_id": normalized_account_id},
                ) from exc

            details = {
                "task_key": task_key,
                "assignee_account_id": normalized_account_id,
                "error": response.text[:200] if response.text else "",
            }
            if response.status_code == 204:
                return
            if response.status_code == 429:
                retry_after = parse_retry_after_seconds(
                    response.headers.get("Retry-After"),
                    fallback_seconds=1.0,
                )
                raise IntegrationRateLimitError(
                    f"Rate limited while updating assignee for {task_key}",
                    integration="jira",
                    operation="update_task_assignee",
                    retry_after_seconds=retry_after,
                    details=details,
                )
            if response.status_code in (401, 403):
                raise IntegrationAuthError(
                    f"Auth failed while updating assignee for {task_key}",
                    integration="jira",
                    operation="update_task_assignee",
                    http_status=response.status_code,
                    details=details,
                )
            if response.status_code >= 500:
                raise IntegrationRequestError(
                    f"Server error while updating assignee for {task_key}",
                    integration="jira",
                    operation="update_task_assignee",
                    retryable=True,
                    http_status=response.status_code,
                    details=details,
                )
            raise IntegrationRequestError(
                f"Failed to update assignee for {task_key}",
                integration="jira",
                operation="update_task_assignee",
                retryable=False,
                http_status=response.status_code,
                details=details,
            )

        def _on_retry(attempt_number: int, max_attempts: int, delay_seconds: float, exc: Exception) -> None:
            print(
                f"Retrying assignee update for {task_key} in {delay_seconds:.1f}s "
                f"(attempt {attempt_number + 1}/{max_attempts}): {exc}"
            )

        try:
            await execute_non_idempotent_write_with_verify(
                operation_name="update_task_assignee",
                write_operation=_send_assignee_request,
                verify_operation=lambda: self._verify_task_assignee(
                    access_token=access_token,
                    cloud_id=cloud_id,
                    task_key=task_key,
                    expected_account_id=normalized_account_id,
                ),
                max_retries=max_retries,
                integration="jira",
                on_retry=_on_retry,
            )
            print(f"Assignee updated for {task_key}")
            return True
        except IntegrationError as exc:
            status_text = f"HTTP {exc.http_status}" if exc.http_status else "no_status"
            print(f"Failed to update assignee: {status_text} [{exc.error_code}]")
            return False
        except Exception as exc:
            print(f"Unexpected error updating assignee for {task_key}: {exc}")
            return False

    async def update_task_parent(
        self,
        access_token: str,
        cloud_id: str,
        task_key: str,
        parent_key: str,
        max_retries: int = 3,
    ) -> bool:
        """Update task parent in Jira."""
        normalized_parent_key = str(parent_key or "").strip().upper()
        if not normalized_parent_key:
            print(f"Missing parent key for task {task_key}")
            return False

        payload = {"fields": {"parent": {"key": normalized_parent_key}}}

        async def _send_parent_request() -> None:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.put(
                        f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/issue/{task_key}",
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                        },
                        timeout=10.0,
                    )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise IntegrationTransportError(
                    f"Transport error while updating parent for {task_key}: {exc}",
                    integration="jira",
                    operation="update_task_parent",
                    details={"task_key": task_key, "parent_key": normalized_parent_key},
                ) from exc

            details = {
                "task_key": task_key,
                "parent_key": normalized_parent_key,
                "error": response.text[:200] if response.text else "",
            }
            if response.status_code == 204:
                return
            if response.status_code == 429:
                retry_after = parse_retry_after_seconds(
                    response.headers.get("Retry-After"),
                    fallback_seconds=1.0,
                )
                raise IntegrationRateLimitError(
                    f"Rate limited while updating parent for {task_key}",
                    integration="jira",
                    operation="update_task_parent",
                    retry_after_seconds=retry_after,
                    details=details,
                )
            if response.status_code in (401, 403):
                raise IntegrationAuthError(
                    f"Auth failed while updating parent for {task_key}",
                    integration="jira",
                    operation="update_task_parent",
                    http_status=response.status_code,
                    details=details,
                )
            if response.status_code >= 500:
                raise IntegrationRequestError(
                    f"Server error while updating parent for {task_key}",
                    integration="jira",
                    operation="update_task_parent",
                    retryable=True,
                    http_status=response.status_code,
                    details=details,
                )
            raise IntegrationRequestError(
                f"Failed to update parent for {task_key}",
                integration="jira",
                operation="update_task_parent",
                retryable=False,
                http_status=response.status_code,
                details=details,
            )

        def _on_retry(attempt_number: int, max_attempts: int, delay_seconds: float, exc: Exception) -> None:
            print(
                f"Retrying parent update for {task_key} in {delay_seconds:.1f}s "
                f"(attempt {attempt_number + 1}/{max_attempts}): {exc}"
            )

        try:
            await execute_non_idempotent_write_with_verify(
                operation_name="update_task_parent",
                write_operation=_send_parent_request,
                verify_operation=lambda: self._verify_task_parent(
                    access_token=access_token,
                    cloud_id=cloud_id,
                    task_key=task_key,
                    expected_parent_key=normalized_parent_key,
                ),
                max_retries=max_retries,
                integration="jira",
                on_retry=_on_retry,
            )
            print(f"Parent updated for {task_key} -> {normalized_parent_key}")
            return True
        except IntegrationError as exc:
            status_text = f"HTTP {exc.http_status}" if exc.http_status else "no_status"
            print(f"Failed to update parent: {status_text} [{exc.error_code}]")
            return False
        except Exception as exc:
            print(f"Unexpected error updating parent for {task_key}: {exc}")
            return False

    async def update_task_status(
        self,
        access_token: str,
        cloud_id: str,
        task_key: str,
        target_status: str,
        max_retries: int = 3
    ) -> bool:
        """
        Update task status in Jira (transition to new status)

        Args:
            access_token: Jira access token
            cloud_id: Jira Cloud ID
            task_key: Task key (e.g., "PRM-28")
            target_status: Target normalized status ("todo", "in_progress", "in_review", "done")
            max_retries: Maximum number of retries on failure

        Returns:
            True if successful, False otherwise
        """
        # Map normalized status to common Jira status names
        status_mapping = self._status_mapping_for_target()

        target_names = status_mapping.get(target_status, [])
        # Allow custom Jira workflow statuses by trying exact target label when
        # canonical mapping does not contain the requested token.
        if not target_names:
            clean_target = str(target_status or "").strip()
            if clean_target:
                target_names = [clean_target]
            else:
                print(f"Unsupported target status '{target_status}' for {task_key}")
                return False

        async def _execute_transition() -> None:
            transitions = await self.get_available_transitions(access_token, cloud_id, task_key)
            if not transitions:
                raise IntegrationRequestError(
                    f"No transitions available for {task_key}",
                    integration="jira",
                    operation="update_task_status",
                    retryable=False,
                    details={"task_key": task_key, "target_status": target_status},
                )

            transition_id = None
            matched_transition_name = None
            for transition in transitions:
                transition_name = transition.get("to", {}).get("name", "")
                if any(str(transition_name).strip().lower() == str(name).strip().lower() for name in target_names):
                    transition_id = transition.get("id")
                    matched_transition_name = transition_name
                    break

            if not transition_id:
                available = [t.get("to", {}).get("name") for t in transitions]
                raise IntegrationRequestError(
                    f"No matching transition for status '{target_status}' in {task_key}",
                    integration="jira",
                    operation="update_task_status",
                    retryable=False,
                    details={"task_key": task_key, "target_status": target_status, "available": available},
                )

            print(f"Found transition: {matched_transition_name} (ID: {transition_id})")

            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/issue/{task_key}/transitions",
                        json={
                            "transition": {
                                "id": transition_id
                            }
                        },
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "Content-Type": "application/json",
                            "Accept": "application/json"
                        },
                        timeout=10.0
                    )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise IntegrationTransportError(
                    f"Transport error while updating status for {task_key}: {exc}",
                    integration="jira",
                    operation="update_task_status",
                    details={"task_key": task_key, "target_status": target_status},
                ) from exc

            details = {
                "task_key": task_key,
                "target_status": target_status,
                "error": response.text[:200] if response.text else "",
            }
            if response.status_code == 204:
                return
            if response.status_code == 429:
                retry_after = parse_retry_after_seconds(
                    response.headers.get("Retry-After"),
                    fallback_seconds=1.0,
                )
                raise IntegrationRateLimitError(
                    f"Rate limited while updating status for {task_key}",
                    integration="jira",
                    operation="update_task_status",
                    retry_after_seconds=retry_after,
                    details=details,
                )
            if response.status_code in (401, 403):
                raise IntegrationAuthError(
                    f"Auth failed while updating status for {task_key}",
                    integration="jira",
                    operation="update_task_status",
                    http_status=response.status_code,
                    details=details,
                )
            if response.status_code >= 500:
                raise IntegrationRequestError(
                    f"Server error while updating status for {task_key}",
                    integration="jira",
                    operation="update_task_status",
                    retryable=True,
                    http_status=response.status_code,
                    details=details,
                )
            raise IntegrationRequestError(
                f"Failed to update status for {task_key}",
                integration="jira",
                operation="update_task_status",
                retryable=False,
                http_status=response.status_code,
                details=details,
            )

        def _on_retry(attempt_number: int, max_attempts: int, delay_seconds: float, exc: Exception) -> None:
            print(
                f"Retrying status update for {task_key} in {delay_seconds:.1f}s "
                f"(attempt {attempt_number + 1}/{max_attempts}): {exc}"
            )

        try:
            await execute_non_idempotent_write_with_verify(
                operation_name="update_task_status",
                write_operation=_execute_transition,
                verify_operation=lambda: self._verify_task_status(
                    access_token=access_token,
                    cloud_id=cloud_id,
                    task_key=task_key,
                    target_status=target_status,
                ),
                max_retries=max_retries,
                integration="jira",
                on_retry=_on_retry,
            )
            print(f"Status updated for {task_key} to {target_status}")
            return True
        except IntegrationError as exc:
            if exc.details.get("available"):
                print(f"Available: {exc.details.get('available')}")
            status_text = f"HTTP {exc.http_status}" if exc.http_status else "no_status"
            print(f"Failed to update status: {status_text} [{exc.error_code}]")
            return False
        except Exception as exc:
            print(f"Unexpected error updating status for {task_key}: {exc}")
            return False

    async def update_task_description(
        self,
        access_token: str,
        cloud_id: str,
        task_key: str,
        description_text: str,
        max_retries: int = 3,
    ) -> bool:
        """
        Update task description in Jira.

        Args:
            access_token: Jira access token
            cloud_id: Jira Cloud ID
            task_key: Task key (e.g., "PRM-28")
            description_text: New description text
            max_retries: Maximum number of retries on failure

        Returns:
            True if successful, False otherwise
        """
        normalized_description = str(description_text or "").strip()
        if not normalized_description:
            print(f"Missing description text for task {task_key}")
            return False

        payload = {
            "fields": {
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {
                                    "type": "text",
                                    "text": normalized_description,
                                }
                            ],
                        }
                    ],
                }
            }
        }

        async def _send_description_request() -> None:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.put(
                        f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/issue/{task_key}",
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                        },
                        timeout=10.0,
                    )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise IntegrationTransportError(
                    f"Transport error while updating description for {task_key}: {exc}",
                    integration="jira",
                    operation="update_task_description",
                    details={"task_key": task_key},
                ) from exc

            details = {
                "task_key": task_key,
                "error": response.text[:200] if response.text else "",
            }
            if response.status_code == 204:
                return
            if response.status_code == 429:
                retry_after = parse_retry_after_seconds(
                    response.headers.get("Retry-After"),
                    fallback_seconds=1.0,
                )
                raise IntegrationRateLimitError(
                    f"Rate limited while updating description for {task_key}",
                    integration="jira",
                    operation="update_task_description",
                    retry_after_seconds=retry_after,
                    details=details,
                )
            if response.status_code in (401, 403):
                raise IntegrationAuthError(
                    f"Auth failed while updating description for {task_key}",
                    integration="jira",
                    operation="update_task_description",
                    http_status=response.status_code,
                    details=details,
                )
            if response.status_code >= 500:
                raise IntegrationRequestError(
                    f"Server error while updating description for {task_key}",
                    integration="jira",
                    operation="update_task_description",
                    retryable=True,
                    http_status=response.status_code,
                    details=details,
                )
            raise IntegrationRequestError(
                f"Failed to update description for {task_key}",
                integration="jira",
                operation="update_task_description",
                retryable=False,
                http_status=response.status_code,
                details=details,
            )

        def _on_retry(attempt_number: int, max_attempts: int, delay_seconds: float, exc: Exception) -> None:
            print(
                f"Retrying description update for {task_key} in {delay_seconds:.1f}s "
                f"(attempt {attempt_number + 1}/{max_attempts}): {exc}"
            )

        try:
            await execute_non_idempotent_write_with_verify(
                operation_name="update_task_description",
                write_operation=_send_description_request,
                verify_operation=lambda: self._verify_task_description(
                    access_token=access_token,
                    cloud_id=cloud_id,
                    task_key=task_key,
                    expected_description_text=normalized_description,
                ),
                max_retries=max_retries,
                integration="jira",
                on_retry=_on_retry,
            )
            print(f"Description updated for {task_key}")
            return True
        except IntegrationError as exc:
            status_text = f"HTTP {exc.http_status}" if exc.http_status else "no_status"
            print(f"Failed to update description: {status_text} [{exc.error_code}]")
            return False
        except Exception as exc:
            print(f"Unexpected error updating description for {task_key}: {exc}")
            return False

    async def update_task_due_date(
        self,
        access_token: str,
        cloud_id: str,
        task_key: str,
        *,
        due_date: Optional[datetime] = None,
        clear: bool = False,
        max_retries: int = 3,
    ) -> bool:
        """
        Update or clear Jira due date.
        """
        expected_due_date = None if clear else self._format_jira_date_value(due_date)
        if not clear and not expected_due_date:
            print(f"Missing due_date for task {task_key}")
            return False

        payload = {
            "fields": {
                "duedate": None if clear else expected_due_date,
            }
        }

        async def _send_due_date_request() -> None:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.put(
                        f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/issue/{task_key}",
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                        },
                        timeout=10.0,
                    )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise IntegrationTransportError(
                    f"Transport error while updating due date for {task_key}: {exc}",
                    integration="jira",
                    operation="update_task_due_date",
                    details={"task_key": task_key},
                ) from exc

            details = {
                "task_key": task_key,
                "due_date": expected_due_date,
                "clear": bool(clear),
                "error": response.text[:200] if response.text else "",
            }
            if response.status_code == 204:
                return
            if response.status_code == 429:
                retry_after = parse_retry_after_seconds(
                    response.headers.get("Retry-After"),
                    fallback_seconds=1.0,
                )
                raise IntegrationRateLimitError(
                    f"Rate limited while updating due date for {task_key}",
                    integration="jira",
                    operation="update_task_due_date",
                    retry_after_seconds=retry_after,
                    details=details,
                )
            if response.status_code in (401, 403):
                raise IntegrationAuthError(
                    f"Auth failed while updating due date for {task_key}",
                    integration="jira",
                    operation="update_task_due_date",
                    http_status=response.status_code,
                    details=details,
                )
            if response.status_code >= 500:
                raise IntegrationRequestError(
                    f"Server error while updating due date for {task_key}",
                    integration="jira",
                    operation="update_task_due_date",
                    retryable=True,
                    http_status=response.status_code,
                    details=details,
                )
            raise IntegrationRequestError(
                f"Failed to update due date for {task_key}",
                integration="jira",
                operation="update_task_due_date",
                retryable=False,
                http_status=response.status_code,
                details=details,
            )

        try:
            await execute_non_idempotent_write_with_verify(
                operation_name="update_task_due_date",
                write_operation=_send_due_date_request,
                verify_operation=lambda: self._verify_task_due_date(
                    access_token=access_token,
                    cloud_id=cloud_id,
                    task_key=task_key,
                    expected_due_date=expected_due_date,
                ),
                max_retries=max_retries,
                integration="jira",
            )
            print(f"Due date updated for {task_key} to {expected_due_date or 'EMPTY'}")
            return True
        except IntegrationError as exc:
            status_text = f"HTTP {exc.http_status}" if exc.http_status else "no_status"
            print(f"Failed to update due date: {status_text} [{exc.error_code}]")
            return False
        except Exception as exc:
            print(f"Unexpected error updating due date for {task_key}: {exc}")
            return False

    async def update_task_start_date(
        self,
        access_token: str,
        cloud_id: str,
        task_key: str,
        *,
        start_date: Optional[datetime] = None,
        clear: bool = False,
        max_retries: int = 3,
    ) -> bool:
        """
        Update or clear Jira start date custom field.
        """
        expected_start_date = None if clear else self._format_jira_date_value(start_date)
        if not clear and not expected_start_date:
            print(f"Missing start_date for task {task_key}")
            return False

        start_field_id = await self.detect_start_date_field_id(access_token=access_token, cloud_id=cloud_id)
        field_candidates = [start_field_id] if start_field_id else []
        field_candidates.extend(
            [
                "customfield_10015",
                "customfield_10016",
                "customfield_10017",
                "customfield_10018",
                "customfield_10019",
                "customfield_10021",
                "customfield_10022",
            ]
        )
        # De-duplicate while preserving order.
        ordered_candidates = [value for value in dict.fromkeys(field_candidates) if value]
        if not ordered_candidates:
            print(f"No start date field candidates available for {task_key}")
            return False

        last_error: Optional[str] = None
        for field_id in ordered_candidates:
            payload = {"fields": {field_id: None if clear else expected_start_date}}

            async def _send_start_date_request() -> None:
                try:
                    async with httpx.AsyncClient() as client:
                        response = await client.put(
                            f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/issue/{task_key}",
                            json=payload,
                            headers={
                                "Authorization": f"Bearer {access_token}",
                                "Content-Type": "application/json",
                                "Accept": "application/json",
                            },
                            timeout=10.0,
                        )
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    raise IntegrationTransportError(
                        f"Transport error while updating start date for {task_key}: {exc}",
                        integration="jira",
                        operation="update_task_start_date",
                        details={"task_key": task_key, "field_id": field_id},
                    ) from exc

                details = {
                    "task_key": task_key,
                    "field_id": field_id,
                    "start_date": expected_start_date,
                    "clear": bool(clear),
                    "error": response.text[:200] if response.text else "",
                }
                if response.status_code == 204:
                    return
                if response.status_code == 429:
                    retry_after = parse_retry_after_seconds(
                        response.headers.get("Retry-After"),
                        fallback_seconds=1.0,
                    )
                    raise IntegrationRateLimitError(
                        f"Rate limited while updating start date for {task_key}",
                        integration="jira",
                        operation="update_task_start_date",
                        retry_after_seconds=retry_after,
                        details=details,
                    )
                if response.status_code in (401, 403):
                    raise IntegrationAuthError(
                        f"Auth failed while updating start date for {task_key}",
                        integration="jira",
                        operation="update_task_start_date",
                        http_status=response.status_code,
                        details=details,
                    )
                if response.status_code == 400 and "customfield" in str(response.text or "").lower():
                    raise IntegrationRequestError(
                        f"Start date field unsupported for {task_key}",
                        integration="jira",
                        operation="update_task_start_date",
                        retryable=False,
                        http_status=response.status_code,
                        details=details,
                    )
                if response.status_code >= 500:
                    raise IntegrationRequestError(
                        f"Server error while updating start date for {task_key}",
                        integration="jira",
                        operation="update_task_start_date",
                        retryable=True,
                        http_status=response.status_code,
                        details=details,
                    )
                raise IntegrationRequestError(
                    f"Failed to update start date for {task_key}",
                    integration="jira",
                    operation="update_task_start_date",
                    retryable=False,
                    http_status=response.status_code,
                    details=details,
                )

            try:
                await execute_non_idempotent_write_with_verify(
                    operation_name="update_task_start_date",
                    write_operation=_send_start_date_request,
                    verify_operation=lambda: self._verify_task_start_date(
                        access_token=access_token,
                        cloud_id=cloud_id,
                        task_key=task_key,
                        expected_start_date=expected_start_date,
                    ),
                    max_retries=max_retries,
                    integration="jira",
                )
                print(
                    f"Start date updated for {task_key} to {expected_start_date or 'EMPTY'} via {field_id}"
                )
                return True
            except IntegrationError as exc:
                # Continue trying other field candidates for unsupported field configs.
                last_error = f"{exc.error_code}:{exc.http_status or 'no_status'}"
                if exc.http_status == 400:
                    continue
                return False
            except Exception as exc:
                last_error = str(exc)
                continue

        print(
            f"Failed to update start date for {task_key}: no candidate field accepted "
            f"(last_error={last_error or 'unknown'})"
        )
        return False

    async def get_workspace_users(
        self,
        access_token: str,
        cloud_id: str
    ) -> List[Dict[str, Any]]:
        """
        Get all HUMAN users from Jira workspace (excludes bots and service accounts).

        Args:
            access_token: Jira access token
            cloud_id: Jira Cloud ID

        Returns:
            List of user dicts with accountId, displayName, emailAddress
        """
        try:
            all_users = []
            start_at = 0
            max_results = 100

            async with httpx.AsyncClient() as client:
                while True:
                    response = await client.get(
                        f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/users/search",
                        params={
                            "startAt": start_at,
                            "maxResults": max_results
                        },
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "Accept": "application/json"
                        },
                        timeout=30.0
                    )

                    if response.status_code != 200:
                        print(f"Error fetching users: {response.status_code} - {response.text}")
                        break

                    users = response.json()
                    if not users:
                        break

                    # Filter only human users (exclude bots and service accounts)
                    human_users = [
                        user for user in users
                        if user.get("accountType") == "atlassian"
                    ]
                    all_users.extend(human_users)

                    # If we got fewer results than max, we've reached the end
                    if len(users) < max_results:
                        break

                    start_at += max_results

            print(f"Fetched {len(all_users)} human users (filtered out bots)")
            return all_users

        except Exception as e:
            print(f"Error fetching workspace users: {str(e)}")
            return []

    async def get_boards_for_project(
        self,
        access_token: str,
        cloud_id: str,
        project_key: str,
    ) -> List[Dict[str, Any]]:
        """
        Fetch Jira boards associated with a project key.

        Returns boards as {id, name, type} where type is typically scrum/kanban.
        """
        normalized_project_key = str(project_key or "").strip()
        if not normalized_project_key:
            return []

        boards: List[Dict[str, Any]] = []
        start_at = 0
        max_results = 50

        try:
            async with httpx.AsyncClient() as client:
                while True:
                    response = await client.get(
                        f"{self.api_url}/ex/jira/{cloud_id}/rest/agile/1.0/board",
                        params={
                            "projectKeyOrId": normalized_project_key,
                            "startAt": start_at,
                            "maxResults": max_results,
                        },
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "Accept": "application/json",
                        },
                        timeout=20.0,
                    )
                    if response.status_code != 200:
                        response_excerpt = ""
                        try:
                            response_excerpt = (response.text or "")[:300]
                        except Exception:
                            response_excerpt = ""
                        print(
                            "Failed to fetch Jira boards: "
                            f"status={response.status_code} project={normalized_project_key} body={response_excerpt!r}"
                        )
                        return []

                    data = response.json() if response.content else {}
                    values = data.get("values") if isinstance(data, dict) else None
                    if not isinstance(values, list):
                        values = []
                    for board in values:
                        if not isinstance(board, dict):
                            continue
                        board_id_raw = board.get("id")
                        board_name = str(board.get("name") or "").strip()
                        board_type = str(board.get("type") or "").strip().lower()
                        try:
                            board_id = int(board_id_raw)
                        except Exception:
                            continue
                        if not board_name:
                            board_name = f"Board {board_id}"
                        if not board_type:
                            board_type = "unknown"
                        boards.append(
                            {
                                "id": board_id,
                                "name": board_name,
                                "type": board_type,
                            }
                        )

                    is_last = bool(data.get("isLast", True)) if isinstance(data, dict) else True
                    if is_last:
                        break
                    start_at += max_results
        except Exception as exc:
            print(f"Error fetching Jira boards for project {normalized_project_key}: {exc}")
            return []

        boards.sort(key=lambda item: (str(item.get("type") or ""), str(item.get("name") or ""), int(item.get("id") or 0)))
        return boards

    async def get_active_sprint_for_board(
        self,
        access_token: str,
        cloud_id: str,
        board_id: int,
    ) -> Optional[Dict[str, Any]]:
        """
        Get one active sprint for a board.

        If multiple active sprints are returned, the sprint with the highest id is selected.
        """
        try:
            normalized_board_id = int(board_id)
        except Exception:
            return None

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url}/ex/jira/{cloud_id}/rest/agile/1.0/board/{normalized_board_id}/sprint",
                    params={"state": "active", "maxResults": 50},
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                    },
                    timeout=20.0,
                )
                if response.status_code != 200:
                    print(
                        "Failed to fetch active sprint: "
                        f"status={response.status_code} board_id={normalized_board_id}"
                    )
                    return None
                data = response.json() if response.content else {}
                values = data.get("values") if isinstance(data, dict) else None
                if not isinstance(values, list) or not values:
                    return None
                values = [item for item in values if isinstance(item, dict)]
                if not values:
                    return None
                values.sort(key=lambda item: int(item.get("id") or 0), reverse=True)
                return values[0]
        except Exception as exc:
            print(f"Error fetching active sprint for board {board_id}: {exc}")
            return None

    async def add_issue_to_sprint(
        self,
        access_token: str,
        cloud_id: str,
        sprint_id: int,
        issue_key: str,
        max_retries: int = 3,
    ) -> bool:
        """
        Add an issue to a Jira sprint.

        Returns True when issue is added (or already present), False otherwise.
        """
        try:
            normalized_sprint_id = int(sprint_id)
        except Exception:
            print(f"Invalid sprint id for add_issue_to_sprint: {sprint_id}")
            return False
        normalized_issue_key = str(issue_key or "").strip()
        if not normalized_issue_key:
            return False

        payload = {"issues": [normalized_issue_key]}

        async def _send_add_to_sprint() -> None:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{self.api_url}/ex/jira/{cloud_id}/rest/agile/1.0/sprint/{normalized_sprint_id}/issue",
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "Content-Type": "application/json",
                            "Accept": "application/json",
                        },
                        timeout=20.0,
                    )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise IntegrationTransportError(
                    f"Transport error while adding {normalized_issue_key} to sprint {normalized_sprint_id}: {exc}",
                    integration="jira",
                    operation="add_issue_to_sprint",
                    details={"issue_key": normalized_issue_key, "sprint_id": normalized_sprint_id},
                ) from exc

            details = {
                "issue_key": normalized_issue_key,
                "sprint_id": normalized_sprint_id,
                "error": response.text[:300] if response.text else "",
            }
            if response.status_code in (200, 201, 204):
                return
            if response.status_code == 429:
                retry_after = parse_retry_after_seconds(
                    response.headers.get("Retry-After"),
                    fallback_seconds=1.0,
                )
                raise IntegrationRateLimitError(
                    f"Rate limited while adding {normalized_issue_key} to sprint {normalized_sprint_id}",
                    integration="jira",
                    operation="add_issue_to_sprint",
                    retry_after_seconds=retry_after,
                    details=details,
                )
            if response.status_code in (401, 403):
                raise IntegrationAuthError(
                    f"Auth failed while adding {normalized_issue_key} to sprint {normalized_sprint_id}",
                    integration="jira",
                    operation="add_issue_to_sprint",
                    http_status=response.status_code,
                    details=details,
                )
            if response.status_code >= 500:
                raise IntegrationRequestError(
                    f"Server error while adding {normalized_issue_key} to sprint {normalized_sprint_id}",
                    integration="jira",
                    operation="add_issue_to_sprint",
                    retryable=True,
                    http_status=response.status_code,
                    details=details,
                )

            # Non-retryable 4xx path.
            body = str(response.text or "").lower()
            if response.status_code == 400 and "already" in body:
                return
            raise IntegrationRequestError(
                f"Failed to add {normalized_issue_key} to sprint {normalized_sprint_id}",
                integration="jira",
                operation="add_issue_to_sprint",
                retryable=False,
                http_status=response.status_code,
                details=details,
            )

        def _on_retry(attempt_number: int, max_attempts: int, delay_seconds: float, exc: Exception) -> None:
            print(
                f"Retrying sprint add for {normalized_issue_key} in {delay_seconds:.1f}s "
                f"(attempt {attempt_number + 1}/{max_attempts}): {exc}"
            )

        try:
            await execute_non_idempotent_write_with_verify(
                operation_name="add_issue_to_sprint",
                write_operation=_send_add_to_sprint,
                verify_operation=lambda: self._verify_issue_in_sprint(
                    access_token=access_token,
                    cloud_id=cloud_id,
                    sprint_id=normalized_sprint_id,
                    issue_key=normalized_issue_key,
                ),
                max_retries=max_retries,
                integration="jira",
                on_retry=_on_retry,
            )
            print(f"Added {normalized_issue_key} to sprint {normalized_sprint_id}")
            return True
        except IntegrationError as exc:
            status_text = f"HTTP {exc.http_status}" if exc.http_status else "no_status"
            print(f"Failed to add issue to sprint: {status_text} [{exc.error_code}]")
            return False
        except Exception as exc:
            print(f"Unexpected error adding issue to sprint: {exc}")
            return False

    async def create_issue(
        self,
        access_token: str,
        cloud_id: str,
        issue_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create a new issue in Jira.

        Args:
            access_token: Jira access token
            cloud_id: Jira Cloud ID
            issue_data: Issue creation payload

        Returns:
            Created issue response
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/issue",
                    json=issue_data,
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json"
                    },
                    timeout=30.0
                )

                if response.status_code not in [200, 201]:
                    error_text = response.text
                    print(f"Error creating issue: {response.status_code} - {error_text}")
                    response.raise_for_status()

                return response.json()

        except Exception as e:
            print(f"Error creating Jira issue: {str(e)}")
            raise

    @staticmethod
    def _normalize_issue_type_token(value: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())

    async def get_project_create_metadata(
        self,
        *,
        access_token: str,
        cloud_id: str,
        project_key: str,
    ) -> Dict[str, Any]:
        """
        Return Jira create metadata for the given project.

        Extracts:
        - ``allowed_create_types`` from ``projects[].issuetypes[].name``
        - ``parent_rules`` from ``projects[].issuetypes[].fields.parent`` (when present)
        """
        normalized_project_key = str(project_key or "").strip().upper()
        if not normalized_project_key:
            return {"allowed_create_types": [], "parent_rules": {}}

        try:
            async with httpx.AsyncClient() as client:
                payload: Dict[str, Any] = {}
                last_error: Optional[Exception] = None
                for expand_value in ("projects.issuetypes.fields", "projects.issuetypes"):
                    response = await client.get(
                        f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/issue/createmeta",
                        params={
                            "projectKeys": normalized_project_key,
                            "expand": expand_value,
                        },
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "Accept": "application/json",
                        },
                        timeout=30.0,
                    )
                    if response.status_code == 200:
                        payload = response.json() or {}
                        break
                    print(
                        "Error fetching Jira create metadata "
                        f"for {normalized_project_key} (expand={expand_value}): "
                        f"{response.status_code} - {response.text}"
                    )
                    try:
                        response.raise_for_status()
                    except Exception as exc:
                        last_error = exc
                        # Fallback for metadata shape support differences.
                        if response.status_code in {400, 404} and expand_value == "projects.issuetypes.fields":
                            continue
                        raise
                if not payload and last_error:
                    raise last_error

                projects = payload.get("projects") or []
                issue_types: List[str] = []
                parent_rules: Dict[str, Dict[str, Any]] = {}
                seen: set[str] = set()
                for project in projects:
                    for issue_type in (project or {}).get("issuetypes") or []:
                        name = str((issue_type or {}).get("name") or "").strip()
                        normalized_name = self._normalize_issue_type_token(name)
                        if not name or not normalized_name or normalized_name in seen:
                            continue
                        seen.add(normalized_name)
                        issue_types.append(name)
                        child_token = self._normalize_issue_type_token(name)
                        if not child_token:
                            continue
                        fields = (issue_type or {}).get("fields") or {}
                        parent_field = fields.get("parent") if isinstance(fields, dict) else None
                        allowed_parent_types: List[str] = []
                        allowed_seen: set[str] = set()
                        if isinstance(parent_field, dict):
                            for allowed in (parent_field.get("allowedValues") or []):
                                allowed_name = str((allowed or {}).get("name") or "").strip()
                                if not allowed_name and isinstance(allowed, dict):
                                    issue_type_ref = allowed.get("issueType")
                                    if isinstance(issue_type_ref, dict):
                                        allowed_name = str(issue_type_ref.get("name") or "").strip()
                                allowed_token = self._normalize_issue_type_token(allowed_name)
                                if not allowed_name or not allowed_token or allowed_token in allowed_seen:
                                    continue
                                allowed_seen.add(allowed_token)
                                allowed_parent_types.append(allowed_name)
                        parent_rules[child_token] = {
                            "provider_type": name,
                            "parent_required": bool(
                                (issue_type or {}).get("subtask")
                                or (isinstance(parent_field, dict) and parent_field.get("required"))
                            ),
                            "allowed_parent_provider_types": allowed_parent_types,
                        }
                return {
                    "allowed_create_types": issue_types,
                    "parent_rules": parent_rules,
                }
        except Exception as exc:
            print(
                f"Error fetching Jira project create issue types for {normalized_project_key}: {exc}"
            )
            raise

    async def get_project_create_issue_types(
        self,
        *,
        access_token: str,
        cloud_id: str,
        project_key: str,
    ) -> List[str]:
        """Backward-compatible helper that returns only allowed create issue type names."""
        metadata = await self.get_project_create_metadata(
            access_token=access_token,
            cloud_id=cloud_id,
            project_key=project_key,
        )
        return list((metadata or {}).get("allowed_create_types") or [])

    async def get_issue(
        self,
        access_token: str,
        cloud_id: str,
        issue_key: str
    ) -> Dict[str, Any]:
        """
        Get full issue details from Jira.

        Args:
            access_token: Jira access token
            cloud_id: Jira Cloud ID
            issue_key: Issue key (e.g., "PRM-123")

        Returns:
            Issue details
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.api_url}/ex/jira/{cloud_id}/rest/api/3/issue/{issue_key}",
                    params={
                        "fields": (
                            "summary,status,priority,issuetype,project,updated,created,assignee,duedate,comment,parent,description,"
                            "customfield_10015,customfield_10016,customfield_10017,customfield_10018,customfield_10019,customfield_10021,customfield_10022"
                        )
                    },
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json"
                    },
                    timeout=30.0
                )

                if response.status_code != 200:
                    print(f"Error fetching issue: {response.status_code} - {response.text}")
                    response.raise_for_status()

                return response.json()

        except Exception as e:
            print(f"Error fetching Jira issue: {str(e)}")
            raise


async def get_fresh_jira_access_token(
    *,
    db,
    project: dict,
) -> str:
    """Return a valid Jira access token for the project, refreshing if needed.

    Checks ``integrations.jira.expires_at`` and proactively refreshes when the
    token has expired or is within 5 minutes of expiry.  The new tokens are
    persisted back to the project document so subsequent callers also benefit.

    Args:
        db:      Motor async database instance.
        project: Full project document from MongoDB.

    Returns:
        Decrypted, valid access token string.

    Raises:
        ValueError: If the stored tokens cannot be decrypted or if refresh fails.
    """
    from datetime import datetime, timedelta
    from app.core.security import encryption_service

    jira_cfg = ((project.get("integrations") or {}).get("jira") or {})
    encrypted_access = jira_cfg.get("access_token")
    encrypted_refresh = jira_cfg.get("refresh_token")
    expires_at = jira_cfg.get("expires_at")
    cloud_id = jira_cfg.get("cloud_id") or ""

    if not encrypted_access:
        raise ValueError("jira_access_token_missing")

    access_token = encryption_service.decrypt(encrypted_access)

    # Decide whether we need to refresh.
    needs_refresh = False
    if expires_at is None:
        # No expiry info stored — refresh defensively so we get an updated
        # expires_at written back for future calls.
        needs_refresh = True
    elif datetime.utcnow() >= (expires_at - timedelta(minutes=5)):
        needs_refresh = True

    if needs_refresh and encrypted_refresh:
        try:
            refresh_token = encryption_service.decrypt(encrypted_refresh)
            token_response = await jira_oauth_service.refresh_access_token(refresh_token)
            access_token = token_response.get("access_token", access_token)
            new_refresh_token = token_response.get("refresh_token")
            expires_in = token_response.get("expires_in")

            update_fields: dict = {
                "integrations.jira.access_token": encryption_service.encrypt(access_token),
                "updated_at": datetime.utcnow(),
            }
            if new_refresh_token:
                update_fields["integrations.jira.refresh_token"] = encryption_service.encrypt(new_refresh_token)
            if expires_in:
                update_fields["integrations.jira.expires_at"] = datetime.utcnow() + timedelta(seconds=expires_in)

            await db.projects.update_one(
                {"_id": project["_id"]},
                {"$set": update_fields},
            )
            print(
                "jira_token_refreshed: "
                f"project_id={project.get('project_id')} cloud_id={cloud_id}"
            )
        except Exception as exc:
            print(
                "jira_token_refresh_failed (using existing token): "
                f"project_id={project.get('project_id')} error={exc}"
            )
            # Fall through with the existing access_token — the API call
            # downstream will raise a proper error if it is truly expired.

    return access_token


# Singleton instance
jira_oauth_service = JiraOAuthService()
