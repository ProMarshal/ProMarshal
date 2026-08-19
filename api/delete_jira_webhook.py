"""
Delete old Jira webhooks that don't have project_id in URL

This script:
1. Reads project from MongoDB
2. Decrypts Jira access token (and refreshes if needed)
3. Lists all webhooks
4. Deletes webhooks matching the old URL pattern
"""

import asyncio
import sys
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from datetime import datetime, timezone
import httpx

# Import from app modules
from app.core.config import settings
from app.core.security import encryption_service
from app.integrations.jira.oauth import jira_oauth_service


async def get_project_jira_credentials(project_id: str):
    """
    Get Jira credentials from MongoDB project
    
    Args:
        project_id: MongoDB project ID
        
    Returns:
        Tuple of (access_token, cloud_id) or (None, None) if not found
    """
    # Connect to MongoDB
    client = AsyncIOMotorClient(settings.mongodb_uri)
    db = client.get_database()
    
    try:
        # Find project
        project = await db.projects.find_one({"_id": ObjectId(project_id)})
        
        if not project:
            print(f"❌ Project {project_id} not found")
            return None, None
            
        # Check if Jira is connected
        jira_integration = project.get("integrations", {}).get("jira", {})
        if jira_integration.get("status") != "connected":
            print(f"❌ Jira not connected for this project")
            return None, None
            
        # Get encrypted tokens
        encrypted_access_token = jira_integration.get("access_token")
        encrypted_refresh_token = jira_integration.get("refresh_token")
        cloud_id = jira_integration.get("cloud_id")
        expires_at = jira_integration.get("expires_at")
        
        if not encrypted_access_token or not cloud_id:
            print(f"❌ Missing Jira credentials in project")
            return None, None
            
        # Decrypt access token
        access_token = encryption_service.decrypt(encrypted_access_token)
        
        # Check if token is expired
        if expires_at:
            # Handle different datetime formats from MongoDB
            if isinstance(expires_at, str):
                expires_at = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
            elif isinstance(expires_at, datetime):
                # If naive datetime, make it timezone-aware (assume UTC)
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
            
            if datetime.now(timezone.utc) >= expires_at:
                print("⚠️  Access token expired, refreshing...")
                
                if not encrypted_refresh_token:
                    print("❌ No refresh token available")
                    return None, None
                    
                # Decrypt refresh token
                refresh_token = encryption_service.decrypt(encrypted_refresh_token)
                
                # Refresh the token
                try:
                    token_response = await jira_oauth_service.refresh_access_token(refresh_token)
                    access_token = token_response.get("access_token")
                    new_refresh_token = token_response.get("refresh_token")
                    expires_in = token_response.get("expires_in", 3600)
                    
                    # Update in database
                    new_expires_at = datetime.now(timezone.utc).timestamp() + expires_in
                    
                    await db.projects.update_one(
                        {"_id": ObjectId(project_id)},
                        {
                            "$set": {
                                "integrations.jira.access_token": encryption_service.encrypt(access_token),
                                "integrations.jira.refresh_token": encryption_service.encrypt(new_refresh_token),
                                "integrations.jira.expires_at": datetime.fromtimestamp(new_expires_at, tz=timezone.utc)
                            }
                        }
                    )
                    
                    print("✅ Token refreshed successfully")
                    
                except Exception as e:
                    print(f"❌ Failed to refresh token: {str(e)}")
                    return None, None
        
        return access_token, cloud_id
        
    finally:
        client.close()


async def list_webhooks(access_token: str, cloud_id: str):
    """
    List all webhooks registered with Jira
    
    Args:
        access_token: Jira access token
        cloud_id: Jira cloud ID
        
    Returns:
        List of webhooks
    """
    api_url = "https://api.atlassian.com"
    all_webhooks = []
    start_at = 0
    max_results = 100
    
    async with httpx.AsyncClient() as client:
        while True:
            response = await client.get(
                f"{api_url}/ex/jira/{cloud_id}/rest/api/3/webhook",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json"
                },
                params={
                    "startAt": start_at,
                    "maxResults": max_results
                },
                timeout=30
            )
            
            response.raise_for_status()
            data = response.json()
            
            values = data.get("values", []) or data.get("webhooks", [])
            all_webhooks.extend(values)
            
            is_last = data.get("isLast", True)
            if is_last or len(values) == 0:
                break
                
            start_at += max_results
    
    return all_webhooks


async def delete_webhooks(access_token: str, cloud_id: str, webhook_ids: list):
    """
    Delete webhooks by IDs
    
    Args:
        access_token: Jira access token
        cloud_id: Jira cloud ID
        webhook_ids: List of webhook IDs to delete
    """
    api_url = "https://api.atlassian.com"
    
    async with httpx.AsyncClient() as client:
        response = await client.request(
            method="DELETE",
            url=f"{api_url}/ex/jira/{cloud_id}/rest/api/3/webhook",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            json={"webhookIds": webhook_ids},
            timeout=30
        )
        
        response.raise_for_status()
        print("✅ Webhooks deleted successfully")


async def main():
    """Main function"""
    print("\n" + "="*60)
    print("Jira Webhook Deletion Script")
    print("="*60 + "\n")
    
    # Get project ID from command line
    if len(sys.argv) < 2:
        print("Usage: python delete_jira_webhook.py <project_id>")
        print("\nExample: python delete_jira_webhook.py 69660e1099bfb74205667354")
        sys.exit(1)
    
    project_id = sys.argv[1]
    
    print(f"📋 Project ID: {project_id}\n")
    
    # Get credentials from MongoDB
    print("🔍 Reading Jira credentials from MongoDB...")
    access_token, cloud_id = await get_project_jira_credentials(project_id)
    
    if not access_token or not cloud_id:
        print("\n❌ Failed to get Jira credentials")
        sys.exit(1)
    
    print(f"✅ Got credentials for cloud_id: {cloud_id}\n")
    
    # List all webhooks
    print("🔍 Listing all webhooks...")
    webhooks = await list_webhooks(access_token, cloud_id)
    
    if not webhooks:
        print("✅ No webhooks found")
        return
    
    print(f"Found {len(webhooks)} webhook(s):\n")
    
    # Find webhooks to delete (old URL pattern without project_id)
    old_pattern = "/api/jira/webhooks/task-updated"
    to_delete = []
    
    for webhook in webhooks:
        webhook_id = webhook.get("id")
        url = webhook.get("url", "")
        
        print(f"  • ID: {webhook_id}")
        print(f"    URL: {url}")
        
        if url.endswith(old_pattern):
            to_delete.append(webhook_id)
            print(f"    ⚠️  MARKED FOR DELETION (old pattern)")
        
        print()
    
    # Delete old webhooks
    if not to_delete:
        print("✅ No old webhooks found. All webhooks are using the new URL pattern.")
        return
    
    print(f"\n⚠️  Found {len(to_delete)} webhook(s) with old URL pattern")
    print(f"Webhook IDs to delete: {to_delete}\n")
    
    # Confirm deletion
    confirm = input("Delete these webhooks? (yes/no): ").strip().lower()
    
    if confirm != "yes":
        print("\n❌ Deletion cancelled")
        return
    
    print("\n🗑️  Deleting webhooks...")
    await delete_webhooks(access_token, cloud_id, to_delete)
    
    print("\n" + "="*60)
    print("✅ Done! You can now reconnect Jira to register the new webhook URL.")
    print("="*60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
