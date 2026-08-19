"""
Web Search Tool
Performs web searches using Serper API when users mention references or URLs.
"""

import re
import httpx
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from app.core.config import settings


@dataclass
class SearchResult:
    """Individual search result."""
    title: str
    link: str
    snippet: str
    position: int


@dataclass
class WebSearchResult:
    """Complete web search response."""
    query: str
    results: List[SearchResult]
    answer_box: Optional[str] = None
    knowledge_graph: Optional[Dict] = None


class WebSearchTool:
    """
    Web Search Tool using Serper API.
    
    Triggers:
    - User provides URL: "Check out this PRD: https://..."
    - User mentions reference: "Like Notion's project tracking"
    - User asks about examples: "What do other PM tools do?"
    """
    
    name = "web_search"
    description = "Search the web for reference examples, competitor features, or best practices"
    
    # URL detection pattern
    URL_PATTERN = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')
    
    # Reference keywords that trigger search
    REFERENCE_KEYWORDS = [
        "like", "similar to", "such as", "for example",
        "check out", "look at", "reference", "example of",
        "how does", "what does", "how do other"
    ]
    
    def __init__(self):
        self.api_key = settings.serper_api_key
        self.base_url = "https://google.serper.dev/search"
        self.timeout = 10.0
    
    def can_handle(self, context: Dict) -> bool:
        """
        Determine if web search should be triggered.
        
        Args:
            context: Dict containing 'message' key with user's message
        
        Returns:
            True if message contains URL or reference keywords
        """
        message = context.get("message", "").lower()
        
        # Check for URLs
        if self.URL_PATTERN.search(context.get("message", "")):
            return True
        
        # Check for reference keywords
        for keyword in self.REFERENCE_KEYWORDS:
            if keyword in message:
                return True
        
        return False
    
    def extract_search_query(self, message: str) -> str:
        """
        Extract the most relevant search query from user message.
        
        Args:
            message: User's message text
        
        Returns:
            Extracted search query
        """
        # If URL found, search for context about that URL/domain
        urls = self.URL_PATTERN.findall(message)
        if urls:
            # Extract domain for context search
            url = urls[0]
            # Try to get meaningful context from URL
            return f"what is {url}"
        
        # Extract reference context
        message_lower = message.lower()
        
        # Pattern: "like [PRODUCT/COMPANY]"
        like_patterns = [
            r"like\s+([a-zA-Z0-9\s]+?)(?:'s|'s|\s+has|\s+does|\s+for|\s+project|$)",
            r"similar to\s+([a-zA-Z0-9\s]+?)(?:'s|'s|\s+has|\s+does|\s+for|$)",
        ]
        
        for pattern in like_patterns:
            match = re.search(pattern, message_lower)
            if match:
                product = match.group(1).strip()
                # Add context for PM-related search
                return f"{product} project management features"
        
        # Fallback: use the whole message (cleaned)
        # Remove common filler words
        cleaned = re.sub(r'\b(i want|i need|can you|please|the)\b', '', message_lower)
        return cleaned.strip()[:100]  # Limit query length
    
    async def execute(
        self,
        query: Optional[str] = None,
        message: Optional[str] = None,
        num_results: int = 5
    ) -> Dict[str, Any]:
        """
        Execute web search.
        
        Args:
            query: Direct search query (optional)
            message: User message to extract query from (optional)
            num_results: Number of results to return
        
        Returns:
            Dict with success status and search results
        """
        if not self.api_key:
            return {
                "success": False,
                "error": "Serper API key not configured",
                "data": None
            }
        
        # Determine search query
        search_query = query or (self.extract_search_query(message) if message else None)
        
        if not search_query:
            return {
                "success": False,
                "error": "No search query provided",
                "data": None
            }
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.base_url,
                    headers={
                        "X-API-KEY": self.api_key,
                        "Content-Type": "application/json"
                    },
                    json={
                        "q": search_query,
                        "num": num_results
                    }
                )
                
                if response.status_code != 200:
                    return {
                        "success": False,
                        "error": f"Search API error: {response.status_code}",
                        "data": None
                    }
                
                data = response.json()
                
                # Parse results
                results = []
                for i, item in enumerate(data.get("organic", [])[:num_results]):
                    results.append(SearchResult(
                        title=item.get("title", ""),
                        link=item.get("link", ""),
                        snippet=item.get("snippet", ""),
                        position=i + 1
                    ))
                
                search_result = WebSearchResult(
                    query=search_query,
                    results=results,
                    answer_box=data.get("answerBox", {}).get("snippet") if data.get("answerBox") else None,
                    knowledge_graph=data.get("knowledgeGraph")
                )
                
                return {
                    "success": True,
                    "error": None,
                    "data": search_result
                }
                
        except httpx.TimeoutException:
            return {
                "success": False,
                "error": "Search request timed out",
                "data": None
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Search error: {str(e)}",
                "data": None
            }
    
    def format_for_context(self, search_result: WebSearchResult) -> str:
        """
        Format search results for inclusion in LLM prompt context.
        
        Args:
            search_result: WebSearchResult from execute()
        
        Returns:
            Formatted string for prompt injection
        """
        if not search_result or not search_result.results:
            return ""
        
        parts = [f"### Web Search Results for: \"{search_result.query}\""]
        
        # Include answer box if available
        if search_result.answer_box:
            parts.append(f"\n**Quick Answer:** {search_result.answer_box}")
        
        # Include knowledge graph summary if available
        if search_result.knowledge_graph:
            kg = search_result.knowledge_graph
            if kg.get("description"):
                parts.append(f"\n**About:** {kg.get('description')}")
        
        # Include top results
        parts.append("\n**Top Results:**")
        for result in search_result.results[:3]:
            parts.append(f"- **{result.title}**: {result.snippet}")
        
        return "\n".join(parts)
