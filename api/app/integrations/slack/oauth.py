"""Slack OAuth service for authentication and API calls"""

import httpx
from typing import Dict, Any, Optional
from app.core.config import settings


class SlackOAuthService:
    """Service for Slack OAuth flow and API interactions"""

    def __init__(self):
        """Initialize Slack OAuth service"""
        self.client_id = settings.slack_client_id
        self.client_secret = settings.slack_client_secret
        self.oauth_url = "https://slack.com/oauth/v2/authorize"
        self.token_url = "https://slack.com/api/oauth.v2.access"

    def get_authorization_url(self, state: str, redirect_uri: str) -> str:
        """
        Generate Slack OAuth authorization URL

        Args:
            state: State token for CSRF protection
            redirect_uri: Callback URL after authorization

        Returns:
            Full authorization URL
        """
        # Bot scopes for ProMarshal functionality
        scopes = [
            "channels:read",
            "channels:history",
            "groups:read",
            "groups:history",
            "im:read",
            "im:history",
            "im:write",
            "mpim:read",
            "mpim:history",
            "chat:write",
            "users:read",
            "users:read.email",
            "team:read",
            "app_mentions:read",
            "commands",  # Enable slash commands
        ]

        params = {
            "client_id": self.client_id,
            "scope": ",".join(scopes),
            "redirect_uri": redirect_uri,
            "state": state
        }

        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        return f"{self.oauth_url}?{query_string}"

    async def exchange_code_for_token(self, code: str, redirect_uri: str) -> Dict[str, Any]:
        """
        Exchange authorization code for access token

        Args:
            code: Authorization code from Slack
            redirect_uri: Same redirect URI used in authorization

        Returns:
            Response from Slack with access_token and team info

        Raises:
            httpx.HTTPError: If request fails
            ValueError: If Slack returns error
        """
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.token_url,
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri
                }
            )

            response.raise_for_status()
            data = response.json()

            if not data.get("ok"):
                error = data.get("error", "Unknown error")
                raise ValueError(f"Slack OAuth error: {error}")

            return data

    async def get_team_info(self, access_token: str) -> Optional[Dict[str, Any]]:
        """
        Get team information using access token

        Args:
            access_token: Slack access token

        Returns:
            Team information dict or None if error
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://slack.com/api/team.info",
                    headers={"Authorization": f"Bearer {access_token}"}
                )

                response.raise_for_status()
                data = response.json()

                if data.get("ok"):
                    return data.get("team")
                return None
        except Exception as e:
            print(f"Error getting team info: {str(e)}")
            return None


# Singleton instance
slack_oauth_service = SlackOAuthService()
