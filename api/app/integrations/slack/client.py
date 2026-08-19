"""Slack API client for sending messages"""
import httpx
from typing import Dict, Any, Optional
from slack_sdk.web.async_client import AsyncWebClient
from app.integrations.common.errors import (
    IntegrationAuthError,
    IntegrationError,
    IntegrationRateLimitError,
    IntegrationRequestError,
    IntegrationTransportError,
)
from app.integrations.common.retry import (
    RetryPolicy,
    execute_with_retry,
    parse_retry_after_seconds,
)
from app.integrations.slack.delivery import chat_post_message_with_retry, views_open_with_retry


class SlackClient:
    """Wrapper for Slack API calls"""

    def __init__(self, access_token: str):
        """
        Initialize Slack client

        Args:
            access_token: Slack bot access token
        """
        self.access_token = access_token
        self.base_url = "https://slack.com/api"
        self.web_client = AsyncWebClient(token=access_token)

    async def find_user_by_email(self, email: str) -> Optional[str]:
        """
        Find Slack user ID by email address

        Args:
            email: User's email address

        Returns:
            Slack user ID or None if not found
        """
        async def _request_lookup() -> Optional[str]:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{self.base_url}/users.lookupByEmail",
                        params={"email": email},
                        headers={"Authorization": f"Bearer {self.access_token}"}
                    )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise IntegrationTransportError(
                    f"Slack transport error during email lookup: {exc}",
                    integration="slack",
                    operation="find_user_by_email",
                    details={"email": email},
                ) from exc

            if response.status_code == 429:
                retry_after = parse_retry_after_seconds(
                    response.headers.get("Retry-After"),
                    fallback_seconds=1.0,
                )
                raise IntegrationRateLimitError(
                    "Slack rate limited users.lookupByEmail",
                    integration="slack",
                    operation="find_user_by_email",
                    retry_after_seconds=retry_after,
                    details={"email": email},
                )
            if response.status_code in (401, 403):
                raise IntegrationAuthError(
                    "Slack auth failed users.lookupByEmail",
                    integration="slack",
                    operation="find_user_by_email",
                    http_status=response.status_code,
                    details={"email": email},
                )
            if response.status_code >= 500:
                raise IntegrationRequestError(
                    "Slack server error users.lookupByEmail",
                    integration="slack",
                    operation="find_user_by_email",
                    retryable=True,
                    http_status=response.status_code,
                    details={"email": email},
                )
            if response.status_code >= 400:
                raise IntegrationRequestError(
                    "Slack request failed users.lookupByEmail",
                    integration="slack",
                    operation="find_user_by_email",
                    retryable=False,
                    http_status=response.status_code,
                    details={"email": email},
                )

            data = response.json()
            if data.get("ok"):
                return data.get("user", {}).get("id")

            error_code = data.get("error")
            if error_code == "users_not_found":
                return None
            if error_code == "ratelimited":
                raise IntegrationRateLimitError(
                    "Slack API rate limited users.lookupByEmail",
                    integration="slack",
                    operation="find_user_by_email",
                    retry_after_seconds=1.0,
                    details={"email": email, "error_code": error_code},
                )
            if error_code in {"invalid_auth", "not_authed", "account_inactive", "token_revoked"}:
                raise IntegrationAuthError(
                    f"Slack auth error users.lookupByEmail: {error_code}",
                    integration="slack",
                    operation="find_user_by_email",
                    details={"email": email, "error_code": error_code},
                )
            if error_code in {"internal_error", "fatal_error", "request_timeout"}:
                raise IntegrationRequestError(
                    f"Slack transient error users.lookupByEmail: {error_code}",
                    integration="slack",
                    operation="find_user_by_email",
                    retryable=True,
                    details={"email": email, "error_code": error_code},
                )
            print(f"Slack API error finding user {email}: {error_code}")
            return None

        def _on_retry(attempt_number: int, max_attempts: int, delay_seconds: float, exc: Exception) -> None:
            print(
                f"Retry Slack email lookup for {email} in {delay_seconds:.1f}s "
                f"(attempt {attempt_number + 1}/{max_attempts}): {exc}"
            )

        try:
            return await execute_with_retry(
                "slack.find_user_by_email",
                _request_lookup,
                policy=RetryPolicy(max_attempts=3),
                on_retry=_on_retry,
            )
        except IntegrationError as e:
            print(f"Error finding Slack user {email}: {e.error_code}")
            return None
        except Exception as e:
            print(f"Error finding Slack user {email}: {str(e)}")
            return None

    async def get_user_email(self, user_id: str) -> Optional[str]:
        """
        Get email address from Slack user ID

        Args:
            user_id: Slack user ID

        Returns:
            User's email address or None if not found
        """
        async def _request_user_info() -> Optional[str]:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{self.base_url}/users.info",
                        params={"user": user_id},
                        headers={"Authorization": f"Bearer {self.access_token}"}
                    )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise IntegrationTransportError(
                    f"Slack transport error during user info lookup: {exc}",
                    integration="slack",
                    operation="get_user_email",
                    details={"user_id": user_id},
                ) from exc

            if response.status_code == 429:
                retry_after = parse_retry_after_seconds(
                    response.headers.get("Retry-After"),
                    fallback_seconds=1.0,
                )
                raise IntegrationRateLimitError(
                    "Slack rate limited users.info",
                    integration="slack",
                    operation="get_user_email",
                    retry_after_seconds=retry_after,
                    details={"user_id": user_id},
                )
            if response.status_code in (401, 403):
                raise IntegrationAuthError(
                    "Slack auth failed users.info",
                    integration="slack",
                    operation="get_user_email",
                    http_status=response.status_code,
                    details={"user_id": user_id},
                )
            if response.status_code >= 500:
                raise IntegrationRequestError(
                    "Slack server error users.info",
                    integration="slack",
                    operation="get_user_email",
                    retryable=True,
                    http_status=response.status_code,
                    details={"user_id": user_id},
                )
            if response.status_code >= 400:
                raise IntegrationRequestError(
                    "Slack request failed users.info",
                    integration="slack",
                    operation="get_user_email",
                    retryable=False,
                    http_status=response.status_code,
                    details={"user_id": user_id},
                )

            data = response.json()
            if data.get("ok"):
                return data.get("user", {}).get("profile", {}).get("email")

            error_code = data.get("error")
            if error_code in {"ratelimited"}:
                raise IntegrationRateLimitError(
                    "Slack API rate limited users.info",
                    integration="slack",
                    operation="get_user_email",
                    retry_after_seconds=1.0,
                    details={"user_id": user_id, "error_code": error_code},
                )
            if error_code in {"invalid_auth", "not_authed", "account_inactive", "token_revoked"}:
                raise IntegrationAuthError(
                    f"Slack auth error users.info: {error_code}",
                    integration="slack",
                    operation="get_user_email",
                    details={"user_id": user_id, "error_code": error_code},
                )
            if error_code in {"internal_error", "fatal_error", "request_timeout"}:
                raise IntegrationRequestError(
                    f"Slack transient error users.info: {error_code}",
                    integration="slack",
                    operation="get_user_email",
                    retryable=True,
                    details={"user_id": user_id, "error_code": error_code},
                )
            print(f"Slack API error getting user info {user_id}: {error_code}")
            return None

        def _on_retry(attempt_number: int, max_attempts: int, delay_seconds: float, exc: Exception) -> None:
            print(
                f"Retry Slack user info for {user_id} in {delay_seconds:.1f}s "
                f"(attempt {attempt_number + 1}/{max_attempts}): {exc}"
            )

        try:
            return await execute_with_retry(
                "slack.get_user_email",
                _request_user_info,
                policy=RetryPolicy(max_attempts=3),
                on_retry=_on_retry,
            )
        except IntegrationError as e:
            print(f"Error getting Slack user email {user_id}: {e.error_code}")
            return None
        except Exception as e:
            print(f"Error getting Slack user email {user_id}: {str(e)}")
            return None

    async def send_message(
        self,
        user_id: str,
        text: Optional[str] = None,
        blocks: Optional[list] = None
    ) -> bool:
        """
        Send a direct message to a user

        Args:
            user_id: Slack user ID
            text: Plain text message (for free tier)
            blocks: Slack blocks (for paid tier with buttons)

        Returns:
            True if successful, False otherwise
        """
        try:
            payload = {
                "channel": user_id
            }

            if blocks:
                payload["blocks"] = blocks
                payload["text"] = "Task Reminder"  # Fallback text
            else:
                payload["text"] = text

            await chat_post_message_with_retry(
                slack_client=self.web_client,
                payload=payload,
                operation_name="slack.send_message",
            )
            return True

        except Exception as e:
            print(f"Error sending Slack message: {str(e)}")
            return False

    async def open_modal(self, trigger_id: str, view: Dict[str, Any]) -> bool:
        """
        Open a modal dialog (paid tier only)

        Args:
            trigger_id: Trigger ID from interaction
            view: Modal view structure

        Returns:
            True if successful, False otherwise
        """
        try:
            await views_open_with_retry(
                slack_client=self.web_client,
                payload={
                    "trigger_id": trigger_id,
                    "view": view,
                },
                operation_name="slack.open_modal",
            )
            return True

        except Exception as e:
            print(f"Error opening Slack modal: {str(e)}")
            return False
