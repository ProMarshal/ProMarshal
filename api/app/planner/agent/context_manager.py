"""
Context Manager
Aggregates context from existing service methods for tool use.
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

from app.core.database import get_planner_discussion_collection


@dataclass
class AgentContext:
    """Aggregated context for agent tools."""
    project_id: str
    topic: str
    project_type: str
    project_name: str
    
    # Chat history for current topic
    chat_history: List[Dict] = field(default_factory=list)
    
    # Finalized content from previous topics
    previous_topics: Dict[str, List[str]] = field(default_factory=dict)
    
    # Current draft items (extracted or discussed)
    current_draft: List[str] = field(default_factory=list)
    
    # Session mode
    mode: str = "brainstorm"  # "brainstorm" or "document"
    
    # Web search results if any
    web_search_results: Optional[str] = None


class ContextManager:
    """
    Thin wrapper that aggregates context from service methods.
    Does not duplicate storage - delegates to service.py.
    """
    
    def __init__(self, service):
        """
        Initialize with service instance.
        
        Args:
            service: PlannerService instance
        """
        self.service = service
    
    async def get_full_context(
        self,
        project_id: str,
        topic: str,
        project_type: str = "Software / Product Development",
        project_name: str = ""
    ) -> AgentContext:
        """
        Aggregate all context needed for agent operations.
        
        Args:
            project_id: Project ID
            topic: Current topic being worked on
            project_type: Project type for prompt building
            project_name: Project name for display
        
        Returns:
            AgentContext with all aggregated data
        """
        # Get chat history
        chat_history = await self.service.get_chat_history(project_id, topic)
        
        # Get previous topics content
        previous_topics = await self.service.get_previous_context(project_id, topic)
        
        # Get current draft items from entities (draft or finalized)
        stage_entities = await self.service.get_stage_entities(project_id, topic)
        draft_list = stage_entities.get("items", [])
        
        # Get discussion to determine mode
        discussion = await self.service.get_active_discussion(project_id, topic)
        mode = "brainstorm"
        if discussion and discussion.get("entry_mode"):
            mode = discussion.get("entry_mode")
        
        return AgentContext(
            project_id=project_id,
            topic=topic,
            project_type=project_type,
            project_name=project_name,
            chat_history=chat_history,
            previous_topics=previous_topics,
            current_draft=draft_list,
            mode=mode
        )
    
    async def update_draft(
        self,
        project_id: str,
        topic: str,
        items: List[str]
    ) -> None:
        """
        Update draft items (before finalization).
        Stores in discussion document for persistence.
        """
        await self.service.save_entity_draft(project_id, topic, items)
    
    async def get_draft(
        self,
        project_id: str,
        topic: str
    ) -> List[str]:
        """Get current draft items."""
        stage_entities = await self.service.get_stage_entities(project_id, topic)
        return stage_entities.get("items", [])
    
    async def set_entry_mode(
        self,
        project_id: str,
        topic: str,
        mode: str
    ) -> None:
        """Set the entry mode (brainstorm or document) for tracking."""
        collection = get_planner_discussion_collection(project_id)
        await collection.update_one(
            {"project_id": project_id, "stage": topic, "status": {"$in": ["active", "in_progress"]}},
            {"$set": {"entry_mode": mode}},
            upsert=False
        )

