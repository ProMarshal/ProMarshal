"""MongoDB storage service for Slack messages"""

from typing import List, Dict
from datetime import datetime
from bson import ObjectId

from app.core.database import get_database


class SlackMessageStorage:
    """Handles storing and managing Slack messages in MongoDB"""

    @staticmethod
    async def store_hourly_messages(
        project_id: str,
        workspace_id: str,
        hour: datetime,
        channels_data: List[Dict],
        dm_messages: List[Dict]
    ) -> str:
        """
        Store or update messages for a project hour.
        Uses upsert to avoid duplicates.

        Args:
            project_id: MongoDB project ID
            workspace_id: Slack team/workspace ID
            hour: Start of the hour (datetime)
            channels_data: List of channel objects with messages
            dm_messages: List of DM messages

        Returns:
            Document ID (string)
        """
        db = get_database()

        # Prepare document
        document = {
            "project_id": ObjectId(project_id),
            "workspace_id": workspace_id,
            "hour": hour,
            "channels": channels_data,
            "dm_messages": dm_messages,
            "updated_at": datetime.utcnow()
        }

        # Upsert (update if exists, insert if not)
        result = await db.slack_messages.update_one(
            {
                "project_id": ObjectId(project_id),
                "hour": hour
            },
            {
                "$set": document,
                "$setOnInsert": {"created_at": datetime.utcnow()}
            },
            upsert=True
        )

        # Get document ID
        if result.upserted_id:
            return str(result.upserted_id)
        else:
            # Document was updated, find it
            doc = await db.slack_messages.find_one({
                "project_id": ObjectId(project_id),
                "hour": hour
            })
            return str(doc["_id"]) if doc else None

    @staticmethod
    async def get_project_messages(
        project_id: str,
        hours_back: int = 24
    ) -> List[Dict]:
        """
        Retrieve stored messages for a project.

        Args:
            project_id: MongoDB project ID
            hours_back: Number of hours to look back

        Returns:
            List of message documents
        """
        from datetime import timedelta

        db = get_database()
        cutoff = datetime.utcnow() - timedelta(hours=hours_back)

        cursor = db.slack_messages.find({
            "project_id": ObjectId(project_id),
            "hour": {"$gte": cutoff}
        }).sort("hour", -1)

        documents = await cursor.to_list(length=None)

        # Convert ObjectId to string for JSON serialization
        for doc in documents:
            doc["_id"] = str(doc["_id"])
            doc["project_id"] = str(doc["project_id"])

        return documents

    @staticmethod
    async def delete_channel_from_documents(
        channel_id: str,
        older_than: datetime
    ) -> int:
        """
        Remove channel objects from documents older than specified time.

        Args:
            channel_id: Slack channel ID to remove
            older_than: Delete from documents older than this datetime

        Returns:
            Count of modified documents
        """
        db = get_database()

        result = await db.slack_messages.update_many(
            {
                "channels.channel_id": channel_id,
                "hour": {"$lt": older_than}
            },
            {
                "$pull": {
                    "channels": {"channel_id": channel_id}
                }
            }
        )

        return result.modified_count

    @staticmethod
    async def delete_empty_documents() -> int:
        """
        Delete documents where both channels and dm_messages are empty.

        Returns:
            Count of deleted documents
        """
        db = get_database()

        result = await db.slack_messages.delete_many({
            "channels": [],
            "dm_messages": []
        })

        return result.deleted_count

    @staticmethod
    async def get_message_stats(project_id: str) -> Dict:
        """
        Get statistics about stored messages for a project.

        Args:
            project_id: MongoDB project ID

        Returns:
            Dict with stats (total_documents, total_channels, total_messages, etc.)
        """
        db = get_database()

        pipeline = [
            {"$match": {"project_id": ObjectId(project_id)}},
            {
                "$group": {
                    "_id": None,
                    "total_documents": {"$sum": 1},
                    "total_channels": {"$sum": {"$size": "$channels"}},
                    "total_dm_messages": {"$sum": {"$size": "$dm_messages"}},
                    "oldest_hour": {"$min": "$hour"},
                    "newest_hour": {"$max": "$hour"}
                }
            }
        ]

        result = await db.slack_messages.aggregate(pipeline).to_list(length=1)

        if not result:
            return {
                "total_documents": 0,
                "total_channels": 0,
                "total_dm_messages": 0,
                "oldest_hour": None,
                "newest_hour": None
            }

        stats = result[0]
        stats.pop("_id", None)

        return stats

    @staticmethod
    async def cleanup_old_messages(days_old: int = 7) -> Dict:
        """
        Clean up messages older than specified days.
        Deletes individual channel objects, then removes empty documents.

        Args:
            days_old: Delete messages older than this many days

        Returns:
            Dict with cleanup stats
        """
        from datetime import timedelta

        db = get_database()
        cutoff = datetime.utcnow() - timedelta(days=days_old)

        # Get all unique channel IDs
        pipeline = [
            {"$match": {"hour": {"$lt": cutoff}}},
            {"$unwind": "$channels"},
            {"$group": {"_id": "$channels.channel_id"}}
        ]

        channels = await db.slack_messages.aggregate(pipeline).to_list(length=None)

        # Delete each channel's old data
        channels_deleted = 0
        for channel in channels:
            count = await SlackMessageStorage.delete_channel_from_documents(
                channel_id=channel["_id"],
                older_than=cutoff
            )
            channels_deleted += count

        # Clean up empty documents
        docs_deleted = await SlackMessageStorage.delete_empty_documents()

        return {
            "cutoff_date": cutoff.isoformat(),
            "channels_modified": channels_deleted,
            "empty_documents_deleted": docs_deleted
        }
