"""Hourly cron job for reading Slack messages"""

from datetime import datetime, timedelta
from typing import List, Dict
from bson import ObjectId

from app.core.database import get_database
from app.core.security import encryption_service
from app.integrations.slack.message_reader import SlackMessageReader
from app.workers.slack_messages.storage_service import SlackMessageStorage


async def run_hourly_slack_reader() -> Dict:
    """
    Main cron job - reads messages from last hour for all connected projects.
    Called by GitHub Actions every hour.

    Returns:
        Dict with execution stats
    """
    db = get_database()
    print(f"Starting hourly Slack message reader at {datetime.utcnow().isoformat()}")

    # Get current hour boundaries
    now = datetime.utcnow()
    hour_start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    hour_end = hour_start + timedelta(hours=1)

    print(f"Reading messages from {hour_start.isoformat()} to {hour_end.isoformat()}")

    # Find all projects with Slack connected
    projects = await db.projects.find({
        "integrations.slack.status": "connected"
    }).to_list(length=None)

    print(f"Found {len(projects)} projects with Slack connected")

    stats = {
        "timestamp": now.isoformat(),
        "hour_start": hour_start.isoformat(),
        "hour_end": hour_end.isoformat(),
        "total_projects": len(projects),
        "successful_projects": 0,
        "failed_projects": 0,
        "errors": []
    }

    # Process each project
    for project in projects:
        project_id = str(project["_id"])
        project_name = project.get("project_name", "Unknown")

        try:
            print(f"Processing project: {project_name} ({project_id})")

            slack_config = project["integrations"]["slack"]
            access_token = encryption_service.decrypt(slack_config["access_token"])
            workspace_id = slack_config["team_id"]
            bot_user_id = slack_config["bot_user_id"]

            # Check if channels are configured for selective monitoring
            selected_channels = slack_config.get("selected_channels", [])
            channels_configured = slack_config.get("channels_configured", False)

            # Fetch channel list based on configuration
            if selected_channels:
                # Only monitor selected channels
                print(f"  Monitoring {len(selected_channels)} selected channels for {project_name}...")
                # Build channels list from selected_channels
                channels = [
                    {"id": ch["channel_id"], "name": ch["channel_name"]}
                    for ch in selected_channels
                ]
                print(f"  Selected channels: {', '.join(['#' + c['name'] for c in channels])}")
            else:
                # Monitor all channels (backward compatibility or not configured)
                print(f"  Fetching all channels for {project_name}...")
                channels = await SlackMessageReader.get_workspace_channels(access_token)
                print(f"  Found {len(channels)} channels: {', '.join(['#' + c['name'] for c in channels])}")

            # Fetch messages from each channel
            channels_data = []
            for channel in channels:
                channel_id = channel["id"]
                channel_name = channel["name"]

                try:
                    messages = await SlackMessageReader.get_channel_history(
                        access_token=access_token,
                        channel_id=channel_id,
                        oldest=hour_start.timestamp(),
                        latest=hour_end.timestamp()
                    )

                    if messages:
                        # Enrich messages with usernames
                        enriched_messages = await SlackMessageReader.enrich_messages_with_usernames(
                            access_token=access_token,
                            messages=messages
                        )

                        channels_data.append({
                            "channel_id": channel_id,
                            "channel_name": channel_name,
                            "channel_type": "public",
                            "message_count": len(enriched_messages),
                            "messages": enriched_messages,
                            "fetched_at": datetime.utcnow()
                        })

                        print(f"    #{channel_name}: {len(messages)} messages")
                    else:
                        print(f"    #{channel_name}: 0 messages")

                except Exception as e:
                    error_msg = str(e)
                    # If not in channel, log it
                    if "not_in_channel" in error_msg:
                        print(f"    #{channel_name}: Bot not invited (skipped)")
                    else:
                        print(f"    #{channel_name}: Error - {error_msg}")

            # Fetch DMs to bot
            print(f"  Fetching DMs sent to bot for {project_name}...")
            dm_messages = await SlackMessageReader.get_dm_history(
                access_token=access_token,
                bot_user_id=bot_user_id,
                oldest=hour_start.timestamp(),
                latest=hour_end.timestamp()
            )

            if dm_messages:
                # Enrich DMs with usernames
                dm_messages = await SlackMessageReader.enrich_messages_with_usernames(
                    access_token=access_token,
                    messages=dm_messages
                )
                print(f"  Found {len(dm_messages)} DM messages sent to bot")
            else:
                print(f"  No DMs sent to bot in this hour")

            # Store in MongoDB
            if channels_data or dm_messages:
                doc_id = await SlackMessageStorage.store_hourly_messages(
                    project_id=project_id,
                    workspace_id=workspace_id,
                    hour=hour_start,
                    channels_data=channels_data,
                    dm_messages=dm_messages
                )
                print(f"  Stored messages for {project_name} (doc_id: {doc_id})")
            else:
                print(f"  No messages found for {project_name}")

            stats["successful_projects"] += 1

        except Exception as e:
            error_msg = str(e)
            print(f"Error processing project {project_name}: {error_msg}")

            # Check if token expired (401 error)
            if "invalid_auth" in error_msg or "token_revoked" in error_msg:
                print(f"  Token expired for {project_name}, marking as expired")

                # Mark Slack integration as expired
                await db.projects.update_one(
                    {"_id": project["_id"]},
                    {
                        "$set": {
                            "integrations.slack.status": "token_expired",
                            "integrations.slack.error": "Token expired or revoked",
                            "integrations.slack.expired_at": datetime.utcnow()
                        }
                    }
                )

            stats["failed_projects"] += 1
            stats["errors"].append({
                "project_id": project_id,
                "project_name": project_name,
                "error": error_msg
            })

    print(f"Hourly Slack message reader completed: {stats['successful_projects']} successful, {stats['failed_projects']} failed")

    return stats


async def fetch_messages_for_project(
    project_id: str,
    hours_back: int = 0
) -> Dict:
    """
    Manually fetch messages for a specific project.
    Used for testing and manual triggers.

    Args:
        project_id: MongoDB project ID
        hours_back: Number of hours back to fetch
            - 0 = Current hour (if now is 3:45 PM, fetch 3:00-4:00 PM)
            - 1 = Previous hour (if now is 3:45 PM, fetch 2:00-3:00 PM)

    Returns:
        Dict with execution stats
    """
    db = get_database()
    print(f"Manually fetching messages for project {project_id} (hours_back={hours_back})")

    # Get project
    project = await db.projects.find_one({"_id": ObjectId(project_id)})
    if not project:
        raise ValueError("Project not found")

    slack_config = project.get("integrations", {}).get("slack", {})
    if slack_config.get("status") != "connected":
        raise ValueError("Slack not connected for this project")

    # Get time range
    now = datetime.utcnow()
    hour_start = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours_back)
    hour_end = hour_start + timedelta(hours=1)

    print(f"Fetching messages from {hour_start.isoformat()} to {hour_end.isoformat()}")

    access_token = encryption_service.decrypt(slack_config["access_token"])
    workspace_id = slack_config["team_id"]
    bot_user_id = slack_config["bot_user_id"]

    # Check if channels are configured for selective monitoring
    selected_channels = slack_config.get("selected_channels", [])

    # Fetch channels based on configuration
    if selected_channels:
        # Only monitor selected channels
        channels = [
            {"id": ch["channel_id"], "name": ch["channel_name"]}
            for ch in selected_channels
        ]
        print(f"Monitoring {len(channels)} selected channels: {', '.join(['#' + c['name'] for c in channels])}")
    else:
        # Monitor all channels (backward compatibility)
        channels = await SlackMessageReader.get_workspace_channels(access_token)
        print(f"Found {len(channels)} channels: {', '.join(['#' + c['name'] for c in channels])}")

    # Fetch messages from each channel
    channels_data = []
    for channel in channels:
        channel_id = channel["id"]
        channel_name = channel["name"]

        try:
            messages = await SlackMessageReader.get_channel_history(
                access_token=access_token,
                channel_id=channel_id,
                oldest=hour_start.timestamp(),
                latest=hour_end.timestamp()
            )

            if messages:
                enriched_messages = await SlackMessageReader.enrich_messages_with_usernames(
                    access_token=access_token,
                    messages=messages
                )

                channels_data.append({
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "channel_type": "public",
                    "message_count": len(enriched_messages),
                    "messages": enriched_messages,
                    "fetched_at": datetime.utcnow()
                })
                print(f"  #{channel_name}: {len(messages)} messages")
            else:
                print(f"  #{channel_name}: 0 messages")
        except Exception as e:
            error_msg = str(e)
            if "not_in_channel" in error_msg:
                print(f"  #{channel_name}: Bot not invited (skipped)")
            else:
                print(f"  #{channel_name}: Error - {error_msg}")

    # Fetch DMs
    print(f"Fetching DMs sent to bot...")
    dm_messages = await SlackMessageReader.get_dm_history(
        access_token=access_token,
        bot_user_id=bot_user_id,
        oldest=hour_start.timestamp(),
        latest=hour_end.timestamp()
    )

    if dm_messages:
        dm_messages = await SlackMessageReader.enrich_messages_with_usernames(
            access_token=access_token,
            messages=dm_messages
        )
        print(f"Found {len(dm_messages)} DM messages")
    else:
        print(f"No DMs sent to bot in this time range")

    # Store in MongoDB
    doc_id = await SlackMessageStorage.store_hourly_messages(
        project_id=project_id,
        workspace_id=workspace_id,
        hour=hour_start,
        channels_data=channels_data,
        dm_messages=dm_messages
    )

    return {
        "project_id": project_id,
        "hour_start": hour_start.isoformat(),
        "hour_end": hour_end.isoformat(),
        "channels_count": len(channels_data),
        "dm_messages_count": len(dm_messages),
        "document_id": doc_id
    }
