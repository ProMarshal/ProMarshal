"""Background workers for Slack command processing"""

import asyncio
import httpx
from typing import Optional
from app.core.database import get_database
from app.core.security import encryption_service
from app.tasks.schemas import CreateTaskDTO
from app.tasks.service import TaskService


def process_create_task(
    project_id: str,
    team_id: str,
    created_by_email: str,
    title: str,
    description: str = "",  # Optional, default to empty string
    assignee_account_id: Optional[str] = None,
    priority: str = "medium",
    user_id: str = None,
    channel_id: str = None  # Channel where command was used
):
    """
    Background worker to process task creation.

    This runs in RQ worker process.
    """
    # Run async function in event loop
    asyncio.run(process_create_task_async(
        project_id=project_id,
        team_id=team_id,
        created_by_email=created_by_email,
        title=title,
        description=description,
        assignee_account_id=assignee_account_id,
        priority=priority,
        user_id=user_id,
        channel_id=channel_id
    ))


async def process_create_task_async(
    project_id: str,
    team_id: str,
    created_by_email: str,
    title: str,
    description: str = "",  # Optional, default to empty string
    assignee_account_id: Optional[str] = None,
    priority: str = "medium",
    user_id: str = None,
    channel_id: str = None  # Channel where command was used
):
    """
    Async implementation of task creation worker.
    """
    try:
        print("\n=== WORKER: CREATE TASK STARTED ===")
        print(f"[Worker] Project ID: {project_id}")
        print(f"[Worker] Title: {title}")
        print(f"[Worker] Assignee Account ID: {assignee_account_id}")
        print(f"[Worker] Priority: {priority}")
        print(f"[Worker] Created by: {created_by_email}")

        # Use existing DB connection (initialized on worker startup)
        db = get_database()

        if db is None:
            print(f"[Worker] ERROR: Database not initialized! Attempting to connect...")
            from app.core.database import ensure_mongo_ready
            await ensure_mongo_ready()
            db = get_database()

            if db is None:
                print(f"[Worker] ERROR: Failed to initialize database connection")
                raise RuntimeError("Database connection failed")

        # Get project (already validated before queuing, but fetch for task creation)
        print(f"[Worker] Fetching project from database...")
        project = await db.projects.find_one({"project_id": project_id})

        print(f"[Worker] Project: {project.get('project_name')}")

        # Create task via Task Service
        print(f"[Worker] Creating CreateTaskDTO...")
        task_data = CreateTaskDTO(
            title=title,
            description=description,
            assignee_account_id=assignee_account_id,
            priority=priority,
            project_id=project_id
        )

        print(f"[Worker] Calling TaskService.create_task()...")
        result = await TaskService.create_task(
            task_data=task_data,
            project=project,
            created_by=created_by_email
        )

        print(f"[Worker] SUCCESS: Task {result.external_id} created in Jira")
        print(f"[Worker] Task URL: {result.url}")

        # Send confirmation to channel (or DM if no channel)
        response_channel = channel_id if channel_id else user_id
        print(f"[Worker] Sending confirmation to channel/user: {response_channel}")

        slack_token = encryption_service.decrypt(
            project["integrations"]["slack"]["access_token"]
        )

        assignee_text = result.assignee_name if result.assignee_name else "Unassigned"
        priority_text = result.priority.capitalize() if result.priority else "Medium"

        confirmation_text = (
            f"*Task created successfully!*\n\n"
            f"*[{result.external_id}] {result.title}*\n"
            f"Assigned to: {assignee_text}\n"
            f"Priority: {priority_text}\n\n"
            f"<{result.url}|View in Jira>"
        )

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://slack.com/api/chat.postMessage",
                json={
                    "channel": response_channel,  # Send to channel where command was used
                    "text": confirmation_text
                },
                headers={
                    "Authorization": f"Bearer {slack_token}",
                    "Content-Type": "application/json"
                },
                timeout=30.0
            )

            if response.status_code == 200:
                data = response.json()
                if data.get("ok"):
                    print(f"[Worker] Confirmation message sent to {response_channel}")
                else:
                    print(f"[Worker] Failed to send message: {data.get('error')}")
            else:
                print(f"[Worker] Failed to send message: {response.status_code}")

    except Exception as e:
        print(f"[Worker] Error processing task creation: {str(e)}")
        import traceback
        traceback.print_exc()
        raise
