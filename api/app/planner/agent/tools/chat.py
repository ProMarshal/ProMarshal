"""
Chat Tool
Wraps service.chat() with decision rule evaluation for web search.
Now supports structured JSON responses for entity extraction.
"""

import json
import re
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field

from app.planner.agent.tools.web_search import WebSearchTool
from app.planner.agent.decision_rules import evaluate_rules, ToolAction


@dataclass
class ChatResponse:
    """Response from chat tool."""
    message: str
    web_search_used: bool = False
    web_search_query: Optional[str] = None
    draft_updates: Optional[List[str]] = None


@dataclass
class StructuredChatResponse:
    """Structured response with extracted entities."""
    reply: str
    extracted_entities: Dict[str, Any] = field(default_factory=dict)
    still_needed: List[str] = field(default_factory=list)
    web_search_used: bool = False
    web_search_query: Optional[str] = None
    parse_error: Optional[str] = None


class ChatTool:
    """
    Chat Tool for conversational interactions.
    
    Wraps service.chat() and adds:
    - Decision rule evaluation for web search
    - Context injection from web search results
    - Structured JSON parsing for entity extraction
    """
    
    name = "chat"
    description = "Conversational chat with ProMarshal"
    
    def __init__(self, service):
        """
        Initialize with service instance.
        
        Args:
            service: PlannerService instance
        """
        self.service = service
        self.web_search_tool = WebSearchTool()
    
    def can_handle(self, context: Dict) -> bool:
        """Always can handle - default tool."""
        return True
    
    async def execute(
        self,
        project_id: str,
        topic: str,
        message: str,
        context: Optional[Dict] = None
    ) -> ChatResponse:
        """
        Execute chat with potential web search enhancement.
        
        Args:
            project_id: Project ID
            topic: Current topic
            message: User's message
            context: Additional context (web_search_results, etc.)
        
        Returns:
            ChatResponse with assistant message
        """
        web_search_used = False
        web_search_query = None
        
        # Evaluate decision rules
        rule = evaluate_rules({"message": message, "event": ""})
        
        # Check if web search should be triggered
        if rule.action == ToolAction.WEB_SEARCH:
            search_result = await self.web_search_tool.execute(message=message)
            
            if search_result["success"] and search_result["data"]:
                web_search_used = True
                web_search_query = search_result["data"].query
                
                # Format for context injection
                # This will be passed to service.chat via additional context
                web_context = self.web_search_tool.format_for_context(search_result["data"])
                
                # Inject web search results into message context
                if context is None:
                    context = {}
                context["web_reference"] = web_context
        
        # Call service.chat() - this handles save message, LLM call, save response
        response = await self.service.chat(project_id, message, topic)
        
        return ChatResponse(
            message=response,
            web_search_used=web_search_used,
            web_search_query=web_search_query
        )
    
    async def execute_structured(
        self,
        project_id: str,
        topic: str,
        message: str,
        context: Optional[Dict] = None
    ) -> StructuredChatResponse:
        """
        Execute chat expecting structured JSON response.
        Parses LLM response for reply and extracted entities.
        
        Args:
            project_id: Project ID
            topic: Current topic
            message: User's message
            context: Additional context
        
        Returns:
            StructuredChatResponse with parsed entities
        """
        web_search_used = False
        web_search_query = None
        
        # Evaluate decision rules for web search
        rule = evaluate_rules({"message": message, "event": ""})
        if rule.action == ToolAction.WEB_SEARCH:
            search_result = await self.web_search_tool.execute(message=message)
            if search_result["success"] and search_result["data"]:
                web_search_used = True
                web_search_query = search_result["data"].query
        
        # Call service.chat() - returns raw LLM response
        raw_response = await self.service.chat(project_id, message, topic)
        
        # Parse structured response
        parsed = self._parse_structured_response(raw_response)
        parsed.web_search_used = web_search_used
        parsed.web_search_query = web_search_query
        
        return parsed
    
    def _parse_structured_response(self, response: str) -> StructuredChatResponse:
        """
        Parse JSON from LLM response.
        Falls back gracefully if JSON parsing fails.
        
        Args:
            response: Raw LLM response string
        
        Returns:
            StructuredChatResponse with parsed or fallback data
        """
        try:
            # Try to find JSON in the response
            # LLM might include markdown code blocks
            json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
            if json_match:
                json_str = json_match.group(1)
            else:
                # Try parsing the whole response as JSON
                json_str = response.strip()
            
            data = json.loads(json_str)
            
            return StructuredChatResponse(
                reply=data.get("reply", response),
                extracted_entities=data.get("extracted_entities", {}),
                still_needed=data.get("still_needed", [])
            )
        except json.JSONDecodeError as e:
            # Fallback: treat entire response as the reply
            return StructuredChatResponse(
                reply=response,
                extracted_entities={},
                still_needed=[],
                parse_error=str(e)
            )
    
    async def execute_with_gap_prompt(
        self,
        project_id: str,
        topic: str,
        gaps: List[str],
        existing_items: List[str]
    ) -> ChatResponse:
        """
        Start or continue chat with gap-filling prompt.
        
        Used when FinalizeCheck finds missing items.
        
        Args:
            project_id: Project ID
            topic: Current topic
            gaps: List of missing/gap items
            existing_items: Items already captured
        
        Returns:
            ChatResponse with gap-addressing prompt
        """
        # Generate contextual gap prompt
        if len(gaps) == 1:
            gap_prompt = f"I noticed we haven't covered **{gaps[0]}** yet. Can you tell me more about this?"
        elif self._are_related(gaps):
            gap_prompt = f"I noticed these related items are missing: **{', '.join(gaps)}**. They seem connected - can you tell me about them together?"
        else:
            gap_prompt = f"I found a few gaps we should address. Let's start with **{gaps[0]}**. Can you tell me about this?"
        
        # Save as assistant message
        await self.service.save_message(project_id, topic, "assistant", gap_prompt)
        
        return ChatResponse(
            message=gap_prompt,
            web_search_used=False
        )
    
    def _are_related(self, gaps: List[str]) -> bool:
        """
        Check if gaps are likely related.
        Simple heuristic - can be enhanced.
        """
        # Related if 3 or fewer items (likely same category)
        if len(gaps) <= 3:
            return True
        return False

