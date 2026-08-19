"""Neo4j client for graph database operations"""

from typing import Optional, Dict, Any
from neo4j import AsyncGraphDatabase, AsyncDriver
from app.core.config import settings


class Neo4jClient:
    """Async Neo4j client for managing task hierarchy graph"""

    def __init__(self):
        self.driver: Optional[AsyncDriver] = None
        self._connected = False

    async def connect(self) -> bool:
        """
        Connect to Neo4j database.
        Returns True if connected, False if credentials not configured.
        """
        if not settings.neo4j_uri or not settings.neo4j_user:
            print("Neo4j credentials not configured, skipping graph sync")
            return False

        try:
            self.driver = AsyncGraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_user, settings.neo4j_password)
            )
            # Verify connection
            await self.driver.verify_connectivity()
            self._connected = True
            print(f"Connected to Neo4j at {settings.neo4j_uri}")
            return True
        except Exception as e:
            print(f"Failed to connect to Neo4j: {str(e)}")
            self._connected = False
            return False

    async def close(self):
        """Close Neo4j connection"""
        if self.driver:
            await self.driver.close()
            self._connected = False
            print("Neo4j connection closed")

    @property
    def is_connected(self) -> bool:
        """Check if connected to Neo4j"""
        return self._connected and self.driver is not None

    async def merge_project_node(self, project_id: str, project_name: str, entity_type: str = "project"):
        """
        Create or update a Project node using project_id as dynamic label.

        Args:
            project_id: Project ID (e.g., 'proj-1234-5678') used as node label
            project_name: User's project name (e.g., 'Website Redesign')
            entity_type: Entity type from MongoDB (default: "project")
        """
        if not self.is_connected:
            return

        # Use project_id as dynamic label (escaped with backticks for special chars)
        query = f"""
        MERGE (p:`{project_id}` {{entity_type: $entity_type}})
        SET p.name = $name,
            p.label = $label,
            p.entity_type = $entity_type
        """
        try:
            async with self.driver.session() as session:
                await session.run(query, name=project_name, label=project_id, entity_type=entity_type)
        except Exception as e:
            print(f"Error creating Project node {project_id}: {str(e)}")

    async def merge_task_node(self, task: Dict[str, Any], project_id: str, mongo_id: str = None):
        """
        Create or update a task node using project_id as dynamic label.

        Args:
            task: Task document from MongoDB
            project_id: Project ID (e.g., 'proj-1234-5678') used as node label
            mongo_id: MongoDB document _id for delete tracking
        """
        if not self.is_connected:
            return

        external_id = task.get("external_id")
        integration_type = task.get("integration_type", "jira")
        entity_type = task.get("entity_type", "work_item")

        if not external_id:
            return

        # Build task name: {integration_type}-{external_id}
        task_name = f"{integration_type}-{external_id}"

        # Use project_id as dynamic label (escaped with backticks)
        query = f"""
        MERGE (t:`{project_id}` {{name: $name}})
        SET t.entity_type = $entity_type,
            t.label = $label,
            t.title = $title,
            t.status = $status,
            t.work_item_type = $work_item_type,
            t.provider_type = $provider_type,
            t.issue_type = $issue_type,
            t.parent_key = $parent_key,
            t.project_id = $project_id,
            t.mongo_id = $mongo_id
        """

        try:
            async with self.driver.session() as session:
                await session.run(
                    query,
                    name=task_name,
                    entity_type=entity_type,
                    label=project_id,
                    title=task.get("title", ""),
                    status=task.get("status", ""),
                    work_item_type=task.get("work_item_type", "custom"),
                    provider_type=task.get("provider_type", task.get("issue_type", "Task")),
                    issue_type=task.get("issue_type", task.get("provider_type", "Task")),
                    parent_key=task.get("parent_key") or "",
                    project_id=str(task.get("project_id", "")),
                    mongo_id=mongo_id or ""
                )
        except Exception as e:
            print(f"Error creating task node {task_name} in project {project_id}: {str(e)}")

    async def create_relationship(self, from_name: str, to_name: str, project_id: str):
        """
        Create HAS relationship between two nodes within a project.

        Args:
            from_name: Source node name (e.g., 'jira-PRM2')
            to_name: Target node name (e.g., 'jira-PRM28')
            project_id: Project ID for label-scoped matching
        """
        if not self.is_connected:
            return

        # Use project_id as label for scoped matching
        query = f"""
        MATCH (a:`{project_id}` {{name: $from_name}})
        MATCH (b:`{project_id}` {{name: $to_name}})
        MERGE (a)-[:HAS]->(b)
        """

        try:
            async with self.driver.session() as session:
                await session.run(query, from_name=from_name, to_name=to_name)
        except Exception as e:
            print(f"Error creating HAS relationship {from_name} -> {to_name} in project {project_id}: {str(e)}")

    async def reconnect_children(self, parent_name: str, project_id: str, integration_type: str):
        """
        Find orphan children that have parent_key matching this node's external_id
        and create HAS relationships from this node to them.
        This fixes sync order issues where children sync before parents.

        Args:
            parent_name: Parent node name (e.g., 'jira-PRM2')
            project_id: Project ID for label-scoped matching
            integration_type: Integration type (e.g., 'jira') to build child names
        """
        if not self.is_connected:
            return

        # Extract external_id from parent_name (e.g., 'jira-PRM2' -> 'PRM2')
        external_id = parent_name.split("-", 1)[1] if "-" in parent_name else parent_name

        # Use project_id as label for scoped matching
        query = f"""
        MATCH (parent:`{project_id}` {{name: $parent_name}})
        MATCH (child:`{project_id}` {{parent_key: $external_id}})
        WHERE NOT (parent)-[:HAS]->(child)
        MERGE (parent)-[:HAS]->(child)
        """

        try:
            async with self.driver.session() as session:
                await session.run(query, parent_name=parent_name, external_id=external_id)
        except Exception as e:
            print(f"Error reconnecting children for {parent_name} in project {project_id}: {str(e)}")

    async def delete_node(self, task_name: str, project_id: str):
        """
        Delete a node and its relationships by task name.

        Args:
            task_name: Task node name (e.g., 'jira-PRM11')
            project_id: Project ID for label-scoped deletion
        """
        if not self.is_connected:
            return

        # Use project_id as label for scoped deletion
        query = f"""
        MATCH (n:`{project_id}` {{name: $name}})
        DETACH DELETE n
        """

        try:
            async with self.driver.session() as session:
                await session.run(query, name=task_name)
        except Exception as e:
            print(f"Error deleting node {task_name} from project {project_id}: {str(e)}")

    async def delete_node_by_mongo_id(self, mongo_id: str, project_id: str):
        """
        Delete a node and its relationships by MongoDB _id.

        Args:
            mongo_id: MongoDB document _id
            project_id: Project ID for label-scoped deletion
        """
        if not self.is_connected:
            return

        # Use project_id as label for scoped deletion
        query = f"""
        MATCH (n:`{project_id}` {{mongo_id: $mongo_id}})
        DETACH DELETE n
        """

        try:
            async with self.driver.session() as session:
                await session.run(query, mongo_id=mongo_id)
        except Exception as e:
            print(f"Error deleting node by mongo_id {mongo_id} from project {project_id}: {str(e)}")

    async def get_or_create_project_node(self, project_id: str, project_name: str, entity_type: str = "project"):
        """
        Ensure project node exists. Creates if missing, returns if present.

        Args:
            project_id: Project ID (e.g., 'proj-1234-5678') used as node label
            project_name: User's project name (e.g., 'Website Redesign')
            entity_type: Entity type from MongoDB (default: "project")
        """
        await self.merge_project_node(project_id, project_name, entity_type)

    async def delete_project_scope(self, project_id: str) -> int:
        """
        Delete all project-scoped graph nodes/relationships for one project label.

        Returns:
            Number of nodes deleted.
        """
        if not self.is_connected:
            return 0
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            return 0
        count_query = f"""
        MATCH (n:`{normalized_project_id}`)
        RETURN count(n) AS count
        """
        delete_query = f"""
        MATCH (n:`{normalized_project_id}`)
        DETACH DELETE n
        """
        try:
            async with self.driver.session() as session:
                count_result = await session.run(count_query)
                count_row = await count_result.single()
                delete_count = int((count_row or {}).get("count") or 0)
                await session.run(delete_query)
                return delete_count
        except Exception as e:
            print(f"Error deleting project scope graph for {normalized_project_id}: {str(e)}")
            raise


# Singleton instance
neo4j_client = Neo4jClient()
