"""Slack message reader for fetching channel history and DMs"""

import httpx
from typing import List, Dict, Optional
from datetime import datetime


class SlackMessageReader:
    """Reads messages from Slack channels and DMs using REST API"""

    BASE_URL = "https://slack.com/api"

    @staticmethod
    async def get_workspace_channels(access_token: str) -> List[Dict]:
        """
        List all public channels in workspace.

        Args:
            access_token: Slack bot access token

        Returns:
            List of channel objects with id, name, etc.
        """
        channels = []
        cursor = None

        async with httpx.AsyncClient() as client:
            while True:
                params = {
                    "types": "public_channel",
                    "exclude_archived": "true",
                    "limit": 200
                }
                if cursor:
                    params["cursor"] = cursor

                response = await client.get(
                    f"{SlackMessageReader.BASE_URL}/conversations.list",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params=params,
                    timeout=30.0
                )

                data = response.json()

                if not data.get("ok"):
                    error = data.get("error", "unknown_error")
                    raise Exception(f"Slack API error: {error}")

                channels.extend(data.get("channels", []))

                # Check for pagination
                cursor = data.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

        return channels

    @staticmethod
    async def get_channel_history(
        access_token: str,
        channel_id: str,
        oldest: float,
        latest: float
    ) -> List[Dict]:
        """
        Read messages from a channel within time range.

        Args:
            access_token: Slack bot access token
            channel_id: Slack channel ID (e.g., C123ABC)
            oldest: Unix timestamp for start of time range
            latest: Unix timestamp for end of time range

        Returns:
            List of message objects
        """
        messages = []
        cursor = None

        async with httpx.AsyncClient() as client:
            while True:
                params = {
                    "channel": channel_id,
                    "oldest": str(oldest),
                    "latest": str(latest),
                    "limit": 200
                }
                if cursor:
                    params["cursor"] = cursor

                response = await client.get(
                    f"{SlackMessageReader.BASE_URL}/conversations.history",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params=params,
                    timeout=30.0
                )

                data = response.json()

                if not data.get("ok"):
                    error = data.get("error", "unknown_error")
                    # If bot not in channel, return empty list
                    if error == "not_in_channel":
                        return []
                    raise Exception(f"Slack API error: {error}")

                messages.extend(data.get("messages", []))

                # Check for pagination
                cursor = data.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

        return messages

    @staticmethod
    async def get_dm_channels(access_token: str, bot_user_id: str) -> List[str]:
        """
        Get list of DM channels where bot is a member.

        Args:
            access_token: Slack bot access token
            bot_user_id: Bot's Slack user ID

        Returns:
            List of DM channel IDs
        """
        dm_channels = []
        cursor = None

        async with httpx.AsyncClient() as client:
            while True:
                params = {
                    "types": "im",
                    "limit": 200
                }
                if cursor:
                    params["cursor"] = cursor

                response = await client.get(
                    f"{SlackMessageReader.BASE_URL}/conversations.list",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params=params,
                    timeout=30.0
                )

                data = response.json()

                if not data.get("ok"):
                    error = data.get("error", "unknown_error")
                    raise Exception(f"Slack API error: {error}")

                # Only include DM channels where bot is a member
                channels = data.get("channels", [])
                for channel in channels:
                    if channel.get("is_im"):
                        dm_channels.append(channel.get("id"))

                # Check for pagination
                cursor = data.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break

        return dm_channels

    @staticmethod
    async def get_dm_history(
        access_token: str,
        bot_user_id: str,
        oldest: float,
        latest: float
    ) -> List[Dict]:
        """
        Read DMs sent to bot within time range.

        Args:
            access_token: Slack bot access token
            bot_user_id: Bot's Slack user ID
            oldest: Unix timestamp for start of time range
            latest: Unix timestamp for end of time range

        Returns:
            List of DM message objects with user info
        """
        all_dm_messages = []

        # Get all DM channels
        dm_channels = await SlackMessageReader.get_dm_channels(access_token, bot_user_id)

        # Fetch messages from each DM channel
        async with httpx.AsyncClient() as client:
            for dm_channel_id in dm_channels:
                cursor = None

                while True:
                    params = {
                        "channel": dm_channel_id,
                        "oldest": str(oldest),
                        "latest": str(latest),
                        "limit": 200
                    }
                    if cursor:
                        params["cursor"] = cursor

                    response = await client.get(
                        f"{SlackMessageReader.BASE_URL}/conversations.history",
                        headers={"Authorization": f"Bearer {access_token}"},
                        params=params,
                        timeout=30.0
                    )

                    data = response.json()

                    if not data.get("ok"):
                        # Skip this DM channel if error
                        break

                    messages = data.get("messages", [])

                    # Filter out bot's own messages - only keep messages TO the bot
                    for message in messages:
                        if message.get("user") != bot_user_id:
                            all_dm_messages.append(message)

                    # Check for pagination
                    cursor = data.get("response_metadata", {}).get("next_cursor")
                    if not cursor:
                        break

        return all_dm_messages

    @staticmethod
    async def get_user_info(access_token: str, user_id: str) -> Optional[Dict]:
        """
        Get user information by Slack user ID.

        Args:
            access_token: Slack bot access token
            user_id: Slack user ID

        Returns:
            User info dict or None if error
        """
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{SlackMessageReader.BASE_URL}/users.info",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"user": user_id},
                timeout=30.0
            )

            data = response.json()

            if not data.get("ok"):
                return None

            return data.get("user")

    @staticmethod
    async def enrich_messages_with_usernames(
        access_token: str,
        messages: List[Dict]
    ) -> List[Dict]:
        """
        Add user_name field to messages by fetching user info.

        Args:
            access_token: Slack bot access token
            messages: List of message objects

        Returns:
            Messages with user_name field added
        """
        # Get unique user IDs
        user_ids = set()
        for message in messages:
            user_id = message.get("user")
            if user_id:
                user_ids.add(user_id)

        # Fetch user info (with caching to avoid duplicate calls)
        user_cache = {}
        for user_id in user_ids:
            user_info = await SlackMessageReader.get_user_info(access_token, user_id)
            if user_info:
                user_cache[user_id] = user_info.get("real_name") or user_info.get("name") or "Unknown"
            else:
                user_cache[user_id] = "Unknown"

        # Add user_name to messages
        enriched_messages = []
        for message in messages:
            enriched = message.copy()
            user_id = message.get("user")
            if user_id:
                enriched["user_name"] = user_cache.get(user_id, "Unknown")
            else:
                enriched["user_name"] = "Unknown"
            enriched_messages.append(enriched)

        return enriched_messages
