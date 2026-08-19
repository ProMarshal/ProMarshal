"""Service for syncing tasks to Neo4j graph database"""

from typing import Dict, Any, List
from app.graph.neo4j_client import neo4j_client
from app.core.database import get_database


class GraphSyncService:
    """Service to sync MongoDB tasks to Neo4j graph"""

    async def ensure_connected(self) -> bool:
        """Ensure Neo4j is connected, attempt connection if not"""
        if not neo4j_client.is_connected:
            return await neo4j_client.connect()
        return True

    async def sync_project(self, project_id: str, project_name: str, entity_type: str = "project"):
        """
        Sync a project node to Neo4j.

        Args:
            project_id: Custom project ID (e.g., 'proj-1234-5678')
            project_name: User's project name (e.g., 'Website Redesign')
            entity_type: Entity type from MongoDB (default: "project")
        """
        if not await self.ensure_connected():
            return

        await neo4j_client.merge_project_node(project_id, project_name, entity_type)

    async def sync_task(self, task: Dict[str, Any], custom_project_id: str, mongo_id: str = None):
        """
        Sync a single task to Neo4j graph.
        Creates node and relationships.

        Args:
            task: Task document from MongoDB
            custom_project_id: Custom project ID (e.g., 'proj-1234-5678') used as label
            mongo_id: MongoDB document _id for delete tracking
        """
        if not await self.ensure_connected():
            return

        external_id = task.get("external_id")
        integration_type = task.get("integration_type", "jira")

        if not external_id:
            return

        # Build task name: {integration_type}-{external_id}
        task_name = f"{integration_type}-{external_id}"

        # Get mongo_id from task if not provided
        task_mongo_id = mongo_id or str(task.get("_id", ""))

        # Ensure project node exists (fetch from MongoDB)
        await self._ensure_project_node(custom_project_id)

        # Create/update task node with project_id as label
        await neo4j_client.merge_task_node(task, custom_project_id, task_mongo_id)

        # Create HAS relationship from parent to child
        parent_key = task.get("parent_key")
        if parent_key:
            # Has parent -> create parent -[HAS]-> child relationship
            parent_name = f"{integration_type}-{parent_key}"
            await neo4j_client.create_relationship(parent_name, task_name, custom_project_id)

        # NO relationship to project node (as per requirements)

        # Reconnect any orphan children waiting for this node
        # (fixes sync order issue: children synced before parent)
        await neo4j_client.reconnect_children(task_name, custom_project_id, integration_type)

    async def _ensure_project_node(self, custom_project_id: str):
        """
        Ensure project node exists in Neo4j.
        Fetches project from main database and creates node + brain collection document.

        Args:
            custom_project_id: Custom project ID (e.g., 'proj-1234-5678')
        """
        from bson import ObjectId
        from datetime import datetime
        from app.core.database import get_brain_collection

        db = get_database()

        # Find project by custom project_id field
        project = await db.projects.find_one({"project_id": custom_project_id})

        if project:
            project_name = project.get("project_name", "Unknown Project")
            entity_type = "project"

            # Create project node in Neo4j
            await neo4j_client.get_or_create_project_node(custom_project_id, project_name, entity_type)

            # Create project document in brain collection for Change Stream
            brain_collection = get_brain_collection(custom_project_id)
            await brain_collection.update_one(
                {"entity_type": "project"},
                {
                    "$set": {
                        "integration_type": "project",
                        "entity_type": entity_type,
                        "name": project_name,
                        "created_at": datetime.utcnow()
                    }
                },
                upsert=True
            )

    async def sync_tasks_batch(self, tasks: List[Dict[str, Any]], custom_project_id: str, project_name: str):
        """
        Sync multiple tasks to Neo4j graph.

        Args:
            tasks: List of task documents from MongoDB
            custom_project_id: Custom project ID (e.g., 'proj-1234-5678')
            project_name: Project name
        """
        if not await self.ensure_connected():
            return

        # First, ensure project node exists
        await self._ensure_project_node(custom_project_id)

        # Sync all tasks
        for task in tasks:
            await self.sync_task(task, custom_project_id)

        print(f"Synced {len(tasks)} tasks to Neo4j graph for project {project_name}")

    async def delete_task(self, external_id: str, custom_project_id: str, integration_type: str = "jira"):
        """
        Delete a task from Neo4j graph by external_id.

        Args:
            external_id: Task external ID (e.g., 'PRM-11')
            custom_project_id: Custom project ID for label-scoped deletion
            integration_type: Integration type (default: 'jira')
        """
        if not await self.ensure_connected():
            return

        task_name = f"{integration_type}-{external_id}"
        await neo4j_client.delete_node(task_name, custom_project_id)

    async def delete_task_by_mongo_id(self, mongo_id: str, custom_project_id: str):
        """
        Delete a task from Neo4j graph by MongoDB _id.

        Args:
            mongo_id: MongoDB document _id
            custom_project_id: Custom project ID for label-scoped deletion
        """
        if not await self.ensure_connected():
            return

        await neo4j_client.delete_node_by_mongo_id(mongo_id, custom_project_id)


# Singleton instance
graph_sync_service = GraphSyncService()
