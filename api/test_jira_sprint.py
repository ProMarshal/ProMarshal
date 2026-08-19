"""Test script to refresh Jira token and check sprint field in API response"""
import asyncio
import os
import sys
from datetime import datetime, timedelta
from motor.motor_asyncio import AsyncIOMotorClient
import httpx

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

from app.core.security import encryption_service
from app.integrations.jira.oauth import jira_oauth_service

async def main():
    # Load environment variables
    env_vars = {}
    with open('.env') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                env_vars[key] = value.strip('"').strip("'")
                os.environ[key] = value.strip('"').strip("'")

    # Connect to MongoDB
    mongodb_uri = env_vars['MONGODB_URI']

    # Database name is in the URI (e.g., mongodb+srv://...@cluster.net/DB_NAME?...)
    # Extract it or use default from env or use the default database from URI
    client = AsyncIOMotorClient(mongodb_uri)
    db = client.get_default_database()  # Gets the database from the URI

    # Find specific project with fresh token (Test 3 / TT project)
    from bson import ObjectId
    project = await db.projects.find_one({"_id": ObjectId("69997acd63987877734fdb65")})

    if not project:
        print("No project with Jira connected")
        return

    jira = project['integrations']['jira']
    cloud_id = jira['cloud_id']
    default_project_key = jira.get('default_project_key')

    print(f"Project: {project['project_name']}")
    print(f"Cloud ID: {cloud_id}")
    print(f"Jira Project: {default_project_key}")

    # Use existing token (already fresh from the data you provided)
    print("\n" + "="*60)
    print("Step 1: Using existing access token")
    print("="*60)

    access_token = encryption_service.decrypt(jira['access_token'])
    expires_at = jira.get('expires_at')

    if expires_at:
        time_until_expiry = (expires_at - datetime.utcnow()).total_seconds()
        print(f"✓ Token expires in: {int(time_until_expiry)} seconds ({int(time_until_expiry/3600)} hours)")

        if time_until_expiry < 300:  # Less than 5 minutes
            print("⚠ Token expires soon, refreshing...")
            refresh_token = encryption_service.decrypt(jira['refresh_token'])
            try:
                token_response = await jira_oauth_service.refresh_access_token(refresh_token)
                access_token = token_response.get("access_token")
                new_refresh_token = token_response.get("refresh_token")
                expires_in = token_response.get("expires_in")

                await db.projects.update_one(
                    {"_id": project["_id"]},
                    {
                        "$set": {
                            "integrations.jira.access_token": encryption_service.encrypt(access_token),
                            "integrations.jira.refresh_token": encryption_service.encrypt(new_refresh_token or refresh_token),
                            "integrations.jira.expires_at": datetime.utcnow() + timedelta(seconds=expires_in)
                        }
                    }
                )
                print("✓ Token refreshed")
            except Exception as e:
                print(f"✗ Refresh failed: {e}")
                client.close()
                return
    else:
        print("✓ Using existing token (no expiry time set)")

    # Test Jira API with sprint field
    print("\n" + "="*60)
    print("Step 2: Testing Jira API - Checking if 'sprint' field exists")
    print("="*60)

    jql = f"project = {default_project_key} ORDER BY created DESC"

    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(
            f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/search/jql",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json"
            },
            json={
                "jql": jql,
                "maxResults": 5,
                "fields": ["summary", "status", "sprint", "issuetype", "assignee", "priority"]
            },
            timeout=30.0
        )

        if response.status_code != 200:
            print(f"✗ API Error: {response.status_code}")
            print(response.text)
            client.close()
            return

        data = response.json()
        issues = data.get("issues", [])

        print(f"\n✓ Fetched {len(issues)} tasks from Jira")
        print("\n" + "="*60)

        for idx, issue in enumerate(issues, 1):
            key = issue.get("key")
            fields = issue.get("fields", {})
            summary = fields.get("summary", "")[:50]
            status = fields.get("status", {}).get("name", "")
            sprint = fields.get("sprint")
            issuetype = fields.get("issuetype", {}).get("name", "")
            priority = fields.get("priority", {}).get("name", "")

            print(f"\n[{idx}] {key} ({issuetype})")
            print(f"    Title: {summary}")
            print(f"    Status: {status}")
            print(f"    Priority: {priority}")
            print(f"    Sprint field exists: {sprint is not None}")

            if sprint:
                if isinstance(sprint, dict):
                    sprint_name = sprint.get('name', 'N/A')
                    sprint_state = sprint.get('state', 'N/A')
                    sprint_id = sprint.get('id', 'N/A')
                    print(f"    ✓ Sprint: {sprint_name}")
                    print(f"    ✓ State: {sprint_state}")
                    print(f"    ✓ Sprint ID: {sprint_id}")
                elif isinstance(sprint, list):
                    print(f"    ✓ Sprints (array with {len(sprint)} items):")
                    for s in sprint:
                        print(f"      - {s.get('name')} (state: {s.get('state')})")
            else:
                print(f"    ✗ Sprint: null (not assigned to any sprint)")

        print("\n" + "="*60)
        print("Summary:")
        print("="*60)
        tasks_with_sprint = sum(1 for issue in issues if issue.get("fields", {}).get("sprint"))
        tasks_without_sprint = len(issues) - tasks_with_sprint
        print(f"Total tasks: {len(issues)}")
        print(f"  - With sprint: {tasks_with_sprint}")
        print(f"  - Without sprint: {tasks_without_sprint}")

        if tasks_with_sprint > 0:
            print("\n✓ RESULT: Jira API DOES return sprint field!")
            print("  You can fetch and store sprint information.")
        else:
            print("\n⚠ RESULT: No tasks have sprint assigned.")
            print("  Either:")
            print("    1. This Jira board doesn't use sprints, OR")
            print("    2. No tasks are currently assigned to sprints")

    client.close()
    print("\nDone!")

if __name__ == "__main__":
    asyncio.run(main())
