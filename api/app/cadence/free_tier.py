"""Free tier reminder implementation - Simple notifications"""
from typing import Dict, Any, Optional
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.security import encryption_service
from app.cadence.task_fetcher import fetch_tasks_for_project
from app.cadence.member_matcher import match_tasks_to_members, get_member_by_email
from app.cadence.slack_identity import resolve_slack_user_id_for_member
from app.integrations.slack.client import SlackClient
from app.cadence.formatters import format_free_tier_message
from app.cadence.logger import log_reminder_sent, log_reminder_failed


async def process_free_tier_project(
    db: AsyncIOMotorDatabase,
    project: Dict[str, Any],
    correlation_id: Optional[str] = None
) -> int:
    """
    Process a single free tier project - send simple notifications

    Args:
        db: MongoDB database instance
        project: Project document
        correlation_id: Unique ID for tracking this cron run

    Returns:
        Number of reminders sent successfully
    """
    collection_name = project.get("project_id")  # Format: proj-XXXX-YYYY
    project_name = project.get("project_name", "Unknown")
    reminders_sent = 0

    print(f"Processing free tier project: {project_name} (Collection: {collection_name})")

    # Step 1: Get Slack access token
    slack_integration = project.get("integrations", {}).get("slack", {})
    encrypted_token = slack_integration.get("access_token")

    if not encrypted_token:
        print(f"  No Slack token for project {collection_name}")
        return 0

    try:
        slack_token = encryption_service.decrypt(encrypted_token)
    except Exception as e:
        print(f"  Failed to decrypt Slack token: {str(e)}")
        return 0

    # Step 2: Fetch all tasks for this project (1 DB query)
    tasks = await fetch_tasks_for_project(db, collection_name)

    if not tasks:
        print(f"  No pending tasks for project {collection_name}")
        return 0

    print(f"  Found {len(tasks)} pending tasks")

    # Step 3: Get active members
    members = project.get("members", [])
    active_members = [m for m in members if m.get("status") == "active"]
    cadence_policy = dict(project.get("_cadence_schedule_spec") or {})
    if bool(cadence_policy.get("ignore_followup_for_owner", False)):
        owner_user_id = str(project.get("user_id") or "").strip()
        if owner_user_id:
            active_members = [
                m for m in active_members
                if str(m.get("user_id") or "").strip() != owner_user_id
            ]

    if not active_members:
        print(f"  No active members for project {collection_name}")
        return 0

    # Step 4: Match tasks to members (in-memory grouping)
    member_tasks = match_tasks_to_members(tasks, active_members)

    if not member_tasks:
        print(f"  No tasks matched to members for project {collection_name}")
        return 0

    print(f"  Tasks matched to {len(member_tasks)} members")

    # Step 5: Initialize Slack client
    slack_client = SlackClient(slack_token)

    # Step 6: Send reminders to each member
    for member_email, tasks_list in member_tasks.items():
        try:
            # Get member details
            member = get_member_by_email(active_members, member_email)
            if not member:
                continue

            member_name = member.get("name", member_email.split("@")[0])

            # Resolve Slack user ID (email-primary with optional cached fallback)
            slack_user_id = await resolve_slack_user_id_for_member(
                db=db,
                project=project,
                member=member,
                slack_client=slack_client,
            )

            if not slack_user_id:
                log_reminder_failed(
                    collection_name,
                    member_email,
                    "Slack user not found",
                    correlation_id
                )
                continue

            # Format message
            message = format_free_tier_message(tasks_list, member_name)

            # Send message
            success = await slack_client.send_message(slack_user_id, text=message)

            if success:
                reminders_sent += 1
                log_reminder_sent(
                    collection_name,
                    member_email,
                    len(tasks_list),
                    "free",
                    correlation_id
                )
            else:
                log_reminder_failed(
                    collection_name,
                    member_email,
                    "Failed to send Slack message",
                    correlation_id
                )

        except Exception as e:
            log_reminder_failed(
                collection_name,
                member_email,
                str(e),
                correlation_id
            )

    return reminders_sent
