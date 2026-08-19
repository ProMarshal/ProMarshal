"""
MongoDB Change Streams listener for Neo4j graph sync.

Watches the brain database collections for changes and syncs to Neo4j.

Usage:
    python -m app.graph.db_listener
"""

import asyncio
import signal
from typing import Optional

from app.core.database import connect_to_mongo, get_brain_db, close_mongo_connection
from app.core.config import settings
from app.graph.sync_service import graph_sync_service


class TasksChangeStreamListener:
    """Listens to MongoDB brain database collections changes and syncs to Neo4j"""

    def __init__(self):
        self.running = False
        self._shutdown_event: Optional[asyncio.Event] = None

    async def start(self):
        """Start listening to changes"""
        self.running = True
        self._shutdown_event = asyncio.Event()

        # Connect to MongoDB
        await connect_to_mongo()
        brain_db = get_brain_db()

        print(f"Starting MongoDB Change Stream listener for brain database: {settings.brain_db_name}")

        try:
            # Watch entire brain database with full document on update
            # This watches all collections (project-specific collections)
            async with brain_db.watch(
                full_document="updateLookup"
            ) as stream:
                print("Listening for changes in brain database collections...")

                async for change in stream:
                    if not self.running:
                        break

                    await self._process_change(change)

        except asyncio.CancelledError:
            print("Listener cancelled")
        except Exception as e:
            print(f"Error in change stream: {str(e)}")
            import traceback
            traceback.print_exc()
        finally:
            await close_mongo_connection()
            print("Listener stopped")

    async def stop(self):
        """Stop the listener"""
        print("Stopping listener...")
        self.running = False
        if self._shutdown_event:
            self._shutdown_event.set()

    async def _process_change(self, change: dict):
        """Process a single change event from brain database collections"""
        operation = change.get("operationType")

        try:
            # Extract collection name (which is the custom project_id)
            namespace = change.get("ns", {})
            collection_name = namespace.get("coll", "")

            if not collection_name:
                return

            # The collection name is the custom project_id (e.g., "proj-1234-5678")
            custom_project_id = collection_name

            if operation in ["insert", "update", "replace"]:
                # Get full document
                doc = change.get("fullDocument")
                if doc:
                    entity_type = doc.get("entity_type")
                    integration_type = doc.get("integration_type")

                    # Handle project node
                    if entity_type == "project":
                        project_name = doc.get("name", "Unknown Project")
                        print(f"[{operation.upper()}] Project node: {project_name} (ID: {custom_project_id})")
                        await graph_sync_service.sync_project(custom_project_id, project_name, entity_type)
                        return

                    # Handle Jira tasks
                    if integration_type == "jira":
                        mongo_id = str(doc.get("_id", ""))
                        external_id = doc.get("external_id", "Unknown")

                        print(f"[{operation.upper()}] Task: {external_id} (Project: {custom_project_id})")
                        await graph_sync_service.sync_task(doc, custom_project_id, mongo_id)
                        return

                    # Ignore planner documents and other data

            elif operation == "delete":
                # For delete, we don't have the full document
                # We can only delete by mongo_id
                doc_key = change.get("documentKey", {})
                mongo_id = str(doc_key.get("_id", ""))

                if mongo_id:
                    print(f"[DELETE] Task with mongo_id: {mongo_id} (Project: {custom_project_id})")
                    await graph_sync_service.delete_task_by_mongo_id(mongo_id, custom_project_id)

        except Exception as e:
            print(f"Error processing {operation}: {str(e)}")
            import traceback
            traceback.print_exc()


async def main():
    """Main entry point"""
    listener = TasksChangeStreamListener()

    # Handle shutdown signals
    loop = asyncio.get_event_loop()

    def signal_handler():
        asyncio.create_task(listener.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    await listener.start()


if __name__ == "__main__":
    asyncio.run(main())
