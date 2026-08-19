"""
Charter Orchestrator
Main entry point for all agent interactions in the Project Charter.
Routes requests to appropriate tools based on mode and context.
"""

from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass, field
from enum import Enum
import io

from app.planner.agent.context_manager import ContextManager, AgentContext
from app.planner.agent.tools.chat import ChatTool
from app.planner.agent.tools.web_search import WebSearchTool
from app.planner.agent.tools.finalize_check import FinalizeCheckTool
from app.planner.agent.tools.template_loader import TemplateLoaderTool
from app.planner.agent.decision_rules import evaluate_rules, ToolAction
from app.planner.document_processor import DocumentProcessor
from app.planner.prompt_templates import build_prompt
from app.planner.prompt_templates.config import get_model_for_task
from app.planner.topic_registry import get_ordered_default_topics
from app.core.database import get_brain_collection


class AgentMode(Enum):
    """Entry modes for charter agent."""
    BRAINSTORM = "brainstorm"
    DOCUMENT = "document"


class AgentAction(Enum):
    """Actions the orchestrator can return."""
    CONTINUE_CHAT = "continue_chat"       # Continue conversation
    SHOW_DRAFT = "show_draft"             # Show draft panel
    READY_TO_FINALIZE = "ready_to_finalize"  # Ready for finalization
    FINALIZED = "finalized"               # Successfully finalized
    ERROR = "error"                       # Error occurred


@dataclass
class AgentResponse:
    """Standard response from orchestrator."""
    action: AgentAction
    message: Optional[str] = None
    draft: List[str] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)
    validation_result: Optional[Dict] = None
    error: Optional[str] = None
    metadata: Dict = field(default_factory=dict)


class CharterOrchestrator:
    """
    Main orchestrator for Project Charter agent.
    
    Entry Points:
    - start_session(): Initialize brainstorm or document mode
    - process_chat(): Handle chat messages
    - process_document(): Handle document upload
    - process_finalize(): Handle finalization request
    """
    
    def __init__(self, service):
        """
        Initialize orchestrator with service instance.
        
        Args:
            service: PlannerService instance
        """
        self.service = service
        self.context_manager = ContextManager(service)
        self.chat_tool = ChatTool(service)
        self.web_search_tool = WebSearchTool()
        self.finalize_check_tool = FinalizeCheckTool()
        self.template_loader = TemplateLoaderTool()
    
    async def start_session(
        self,
        project_id: str,
        topic: str,
        mode: AgentMode,
        project_type: str = "Software / Product Development",
        project_name: str = ""
    ) -> AgentResponse:
        """
        Start a new agent session.
        
        Args:
            project_id: Project ID
            topic: Topic to work on (e.g., "goal", "scope")
            mode: BRAINSTORM or DOCUMENT
            project_type: Project type for prompts
            project_name: Project name for display
        
        Returns:
            AgentResponse with initial message or action
        """
        # Ensure discussion exists
        discussion = await self.service.get_active_discussion(project_id, topic)
        if not discussion:
            discussion = await self.service.create_discussion(project_id, topic)

        # Set entry mode for tracking
        await self.context_manager.set_entry_mode(project_id, topic, mode.value)

        is_new = not discussion or not discussion.get("messages")
        
        if mode == AgentMode.BRAINSTORM:
            if is_new:
                # Fast start: avoid heavy context and LLM calls
                initial_message = await self.service.ensure_initial_message(project_id, topic)
                return AgentResponse(
                    action=AgentAction.CONTINUE_CHAT,
                    message=initial_message,
                    draft=[],
                    metadata={"mode": mode.value}
                )

            # In-progress: client will fetch history, avoid heavy context
            return AgentResponse(
                action=AgentAction.CONTINUE_CHAT,
                message="",
                draft=[],
                metadata={"mode": mode.value}
            )
        
        else:  # DOCUMENT mode
            return AgentResponse(
                action=AgentAction.CONTINUE_CHAT,
                message="Hey, what do you have in mind, lets brainstorm.",
                draft=[],
                metadata={"mode": mode.value, "awaiting_upload": True}
            )
    
    async def start_topic_brainstorm(
        self,
        project_id: str,
        topic: str,
        existing_content: List[str],
        project_type: str = "Software / Product Development",
        project_name: str = ""
    ) -> AgentResponse:
        """
        Start brainstorm session for a specific topic with existing extracted content.
        Used when user clicks [Brainstorm] on an entity card after document extraction.
        
        Args:
            project_id: Project ID
            topic: Topic to brainstorm (e.g., "scope")
            existing_content: Already extracted content to refine
            project_type: Project type for prompts
            project_name: Project name
        
        Returns:
            AgentResponse with initial brainstorm message
        """
        # Build prompt for this specific topic
        system_prompt = build_prompt(
            topic_id=topic,
            project_type=project_type,
            mode="brainstorm",
            context={
                "existing_draft": existing_content,
                "refine_mode": True,
                "project_name": project_name
            },
            include_knowledge_base=True
        )
        
        # Create initial message acknowledging existing content
        if existing_content:
            items_text = "\n".join([f"• {item}" for item in existing_content[:5]])
            initial_message = f"I see you've already captured some {topic} items:\n\n{items_text}\n\nWould you like to refine any of these, or add more?"
        else:
            initial_message = f"Let's brainstorm the {topic} for your project. What would you like to include?"
        
        # Store context for subsequent chat
        await self.context_manager.set_entry_mode(project_id, topic, "brainstorm_refine")
        
        return AgentResponse(
            action=AgentAction.CONTINUE_CHAT,
            message=initial_message,
            draft=existing_content,
            metadata={
                "mode": "brainstorm_refine",
                "topic": topic,
                "refining_extracted": True
            }
        )
    
    async def process_chat(
        self,
        project_id: str,
        topic: str,
        message: str,
        project_type: str = "Software / Product Development"
    ) -> AgentResponse:
        """
        Process a chat message with dynamic entity extraction.
        
        Uses structured JSON responses from LLM to extract entities
        as they are detected during natural conversation.
        
        Args:
            project_id: Project ID
            topic: Current topic
            message: User's message
            project_type: Project type for prompts
        
        Returns:
            AgentResponse with assistant reply and extracted entities
        """
        # Execute chat tool with structured response parsing
        chat_response = await self.chat_tool.execute_structured(
            project_id, topic, message
        )
        
        # If entities were extracted, save them
        draft_items = []
        if chat_response.extracted_entities:
            draft_items = await self._save_extracted_entities(
                project_id, chat_response.extracted_entities
            )
        
        # Get current draft (all entity items for this project)
        current_draft = await self._get_all_draft_items(project_id)
        
        return AgentResponse(
            action=AgentAction.CONTINUE_CHAT,
            message=chat_response.reply,
            draft=current_draft,
            metadata={
                "web_search_used": chat_response.web_search_used,
                "web_search_query": chat_response.web_search_query,
                "extracted_this_turn": list(chat_response.extracted_entities.keys()),
                "still_needed": chat_response.still_needed,
                "parse_error": chat_response.parse_error
            }
        )
    
    async def process_document(
        self,
        project_id: str,
        topic: str,
        file_content: bytes,
        filename: str,
        project_type: str = "Software / Product Development"
    ) -> AgentResponse:
        """
        Process an uploaded document.
        
        Flow:
        1. Extract text from document
        2. Use LLM to extract topic-relevant content
        3. Run FinalizeCheck
        4. Return draft with gaps (if any)
        
        Args:
            project_id: Project ID
            topic: Current topic
            file_content: Document bytes
            filename: Original filename
            project_type: Project type
        
        Returns:
            AgentResponse with extracted content and validation
        """
        # 1. Extract text from document
        file_io = io.BytesIO(file_content)
        extracted_text = DocumentProcessor.extract_text(file_io, filename)
        
        # 2. Use LLM to extract topic-relevant content
        extracted_items = await self._extract_topic_content(
            extracted_text, topic, project_type
        )
        
        # 3. Store as draft
        await self.context_manager.update_draft(project_id, topic, extracted_items)
        
        # 4. Run FinalizeCheck (auto after extraction)
        check_result = await self.finalize_check_tool.execute(
            project_type=project_type,
            extracted_content={topic: extracted_items},
            mode="coverage"
        )
        
        if check_result["success"]:
            result_data = check_result["data"]
            
            if result_data.missing_required:
                # Has gaps - trigger gap-filling chat
                gap_response = await self.chat_tool.execute_with_gap_prompt(
                    project_id, topic, result_data.missing_required, extracted_items
                )
                
                return AgentResponse(
                    action=AgentAction.SHOW_DRAFT,
                    message=gap_response.message,
                    draft=extracted_items,
                    gaps=result_data.missing_required,
                    validation_result={
                        "completeness_score": result_data.completeness_score,
                        "recommendations": result_data.recommendations
                    }
                )
            else:
                # No gaps - ready to finalize
                return AgentResponse(
                    action=AgentAction.READY_TO_FINALIZE,
                    message="Great! I've extracted the content and everything looks complete. Ready to finalize?",
                    draft=extracted_items,
                    validation_result={
                        "completeness_score": result_data.completeness_score,
                        "recommendations": result_data.recommendations
                    }
                )
        else:
            # Check failed - still show extracted content
            return AgentResponse(
                action=AgentAction.SHOW_DRAFT,
                message="I've extracted the content. Please review and let me know if anything is missing.",
                draft=extracted_items,
                error=check_result.get("error")
            )
    
    async def process_document_extraction(
        self,
        project_id: str,
        file_ids: List[str],
        project_type: str = "Software / Product Development",
        project_name: str = ""
    ) -> Dict[str, Any]:
        """
        Process document extraction for all active topics in one pass.
        
        Uses the same extraction logic as planner service but routed
        through the agent for a unified pipeline.
        """
        return await self.service.extract_from_documents(project_id, file_ids)

    async def process_finalize(
        self,
        project_id: str,
        topic: str,
        project_type: str = "Software / Product Development"
    ) -> AgentResponse:
        """
        Process finalization request.
        
        Flow:
        1. Run FinalizeCheck (coverage + conflicts)
        2. If gaps → return to chat with prompts
        3. If clean → finalize and return success
        
        Args:
            project_id: Project ID
            topic: Current topic
            project_type: Project type
        
        Returns:
            AgentResponse with finalization result
        """
        # Get current content
        context = await self.context_manager.get_full_context(
            project_id, topic, project_type
        )
        
        # Get draft or existing content from entities
        stage_entities = await self.service.get_stage_entities(project_id, topic)
        draft = stage_entities.get("items", [])
        out_of_scope = stage_entities.get("out_of_scope", []) if topic == "scope" else []
        
        # Run full FinalizeCheck (coverage + conflicts)
        check_result = await self.finalize_check_tool.execute(
            project_type=project_type,
            extracted_content={topic: draft},
            finalized_topics=context.previous_topics,
            mode="full"
        )
        
        if check_result["success"]:
            result_data = check_result["data"]
            
            # Check for high-severity issues
            high_conflicts = [c for c in result_data.conflicts if c.severity == "high"]
            
            if result_data.missing_required or high_conflicts:
                # Has issues - return to chat
                issues = result_data.missing_required + [c.description for c in high_conflicts]
                
                gap_response = await self.chat_tool.execute_with_gap_prompt(
                    project_id, topic, issues[:3], draft  # Limit to 3 issues at a time
                )
                
                return AgentResponse(
                    action=AgentAction.CONTINUE_CHAT,
                    message=gap_response.message,
                    draft=draft,
                    gaps=result_data.missing_required,
                    validation_result={
                        "completeness_score": result_data.completeness_score,
                        "conflicts": [{"description": c.description, "severity": c.severity} for c in result_data.conflicts],
                        "recommendations": result_data.recommendations
                    }
                )
            else:
                # Clean - finalize
                await self.service.finalize_stage(project_id, topic, draft, out_of_scope)
                
                return AgentResponse(
                    action=AgentAction.FINALIZED,
                    message="Successfully finalized!",
                    draft=draft,
                    validation_result={
                        "completeness_score": result_data.completeness_score,
                        "quality_notes": result_data.quality_notes
                    }
                )
        else:
            return AgentResponse(
                action=AgentAction.ERROR,
                error=check_result.get("error", "Validation failed"),
                draft=draft
            )
    
    async def get_draft(
        self,
        project_id: str,
        topic: str
    ) -> AgentResponse:
        """Get current draft state."""
        draft = await self.context_manager.get_draft(project_id, topic)
        
        return AgentResponse(
            action=AgentAction.SHOW_DRAFT,
            draft=draft
        )
    
    async def _extract_topic_content(
        self,
        text: str,
        topic: str,
        project_type: str
    ) -> List[str]:
        """
        Use LLM to extract topic-relevant content from document text.
        
        Args:
            text: Extracted document text
            topic: Topic to extract for
            project_type: Project type for context
        
        Returns:
            List of extracted items
        """
        # Build extraction prompt
        prompt = f"""Extract all {topic}-related items from the following document text.

Document Text:
{text[:8000]}  # Limit to avoid token overflow

Instructions:
1. Identify all items related to "{topic}"
2. Format each as a clear, actionable bullet point
3. Return ONLY the extracted items, one per line
4. If no {topic} items found, return "NONE_FOUND"

Extracted {topic.title()} items:"""
        
        # Get extraction model
        model_config = get_model_for_task("extraction")
        
        # Call LLM (using service's LLM calling)
        messages = [{"role": "user", "content": prompt}]
        response = await self.service._call_llm(
            messages,
            max_tokens=1000,
            model_config=model_config
        )
        
        if not response or "NONE_FOUND" in response:
            return []
        
        # Parse response into list
        items = []
        for line in response.strip().split("\n"):
            line = line.strip()
            if line and not line.startswith("#"):
                # Remove bullet markers
                line = line.lstrip("•-*").strip()
                if line:
                    items.append(line)
        
        return items
    
    async def get_current_focus_topic(
        self,
        project_id: str,
        project_type: str = "Software / Product Development"
    ) -> str:
        """
        Determine which topic to focus on based on extraction progress.
        Uses registry display_order for priority, returns first unfinalized default topic.
        
        Args:
            project_id: Project ID to check entity status
            project_type: Project type for filtering default topics
        
        Returns:
            Topic ID to focus on (e.g., "goal", "scope")
        """
        ordered_topics = get_ordered_default_topics(project_type)
        
        if not ordered_topics:
            return "goal"  # Fallback
        
        collection = get_brain_collection(project_id)
        
        for topic in ordered_topics:
            topic_id = topic["id"]
            # Check if this topic has a finalized entity
            entity = await collection.find_one({
                "entity_type": topic_id,
                "status": "finalized"
            })
            if not entity:
                return topic_id  # First unfinalized = current focus
        
        # All done, stay on last topic
        return ordered_topics[-1]["id"]

    
    async def _save_extracted_entities(
        self,
        project_id: str,
        extracted: Dict[str, Any]
    ) -> List[str]:
        """
        Save extracted entities to MongoDB by type.
        
        Entity types:
        - goal: Holistic (single content field)
        - scope_items: Embedded items array (in-scope)
        - out_of_scope_items: Embedded items array (out of scope)
        - any other topic: stored as draft_content on discussion doc
        """
        saved_items = []
        
        for entity_type, value in extracted.items():
            if entity_type == "goal" and value:
                await self.service.save_entity_draft(project_id, "goal", [value])
                saved_items.append(f"[Goal] {value}")
                
            elif entity_type == "scope_items" and value:
                await self.service.save_entity_draft(project_id, "scope", value)
                for item in value:
                    saved_items.append(f"[Scope] {item}")

            elif entity_type == "out_of_scope_items" and value:
                await self.service.save_entity_draft(project_id, "scope", [], value)
                for item in value:
                    saved_items.append(f"[Out of Scope] {item}")
                    
            elif value:
                # Generic topic list: store on discussion doc as draft_content
                items = value if isinstance(value, list) else [value]
                await self.service.save_entity_draft(project_id, entity_type, items)
                for item in items:
                    saved_items.append(f"[{entity_type.title()}] {item}")
        
        return saved_items
    
    async def _get_all_draft_items(self, project_id: str) -> List[str]:
        """
        Get all draft entity items for display in draft panel.
        
        Args:
            project_id: Project ID
        
        Returns:
            List of formatted draft items
        """
        items = []
        topic_data = await self.service.get_project_topics(project_id)
        active_topics = topic_data.get("active_topics", [])

        for topic in active_topics:
            topic_id = topic.get("topic_id")
            if not topic_id:
                continue
            stage_entities = await self.service.get_stage_entities(project_id, topic_id)
            if stage_entities.get("status") != "draft":
                continue
            for item in stage_entities.get("items", []) or []:
                label = "Out of Scope" if topic_id == "scope" and item in (stage_entities.get("out_of_scope") or []) else topic_id.title()
                items.append(f"[{label}] {item}")

        return items

