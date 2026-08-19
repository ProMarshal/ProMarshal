"""
Finalize Check Tool
Validates extracted content for topic coverage and cross-topic conflicts.
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
import httpx

from app.core.config import settings
from app.planner.topic_registry import (
    TOPICS,
    TOPIC_GROUPS,
    get_topics_for_project_type,
    get_default_topics_for_project_type,
)
from app.planner.prompt_templates.config import get_model_for_task


@dataclass
class TopicCoverage:
    """Coverage status for a single topic."""
    topic_id: str
    topic_name: str
    is_required: bool
    is_covered: bool
    extracted_items: List[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class ConflictItem:
    """Detected conflict between topics."""
    topic_a: str
    topic_b: str
    description: str
    severity: str  # "low", "medium", "high"
    suggestion: str


@dataclass
class FinalizeCheckResult:
    """Complete finalize check result."""
    # Coverage analysis
    total_topics: int
    covered_topics: int
    missing_required: List[str]
    coverage_details: List[TopicCoverage]
    
    # Conflict analysis
    conflicts: List[ConflictItem]
    
    # Quality assessment
    completeness_score: float  # 0-100
    quality_notes: List[str]
    
    # Recommendations
    recommendations: List[str]


class FinalizeCheckTool:
    """
    Finalize Check Tool using reasoning model.
    
    Two modes:
    1. Topic Coverage Check - After document extraction
       - Compare extracted content against topic registry
       - Identify covered topics
       - Flag missing required topics
    
    2. Cross-Topic Conflict Check - Before finalization
       - Detect conflicts (Goal vs Scope inconsistencies)
       - Completeness validation
       - Quality assessment
    """
    
    name = "finalize_check"
    description = "Validate topic coverage and detect cross-topic conflicts"
    
    def __init__(self):
        self.model_config = get_model_for_task("reasoning")
        self.timeout = 60.0
    
    def can_handle(self, context: Dict) -> bool:
        """
        Determine if finalize check should run.
        
        Triggers:
        - Document uploaded and extracted
        - User clicks finalize button
        - Explicit check request
        """
        triggers = ["document_extracted", "finalize_requested", "check_requested"]
        return context.get("trigger") in triggers
    
    async def execute(
        self,
        project_type: str,
        extracted_content: Optional[Dict[str, List[str]]] = None,
        finalized_topics: Optional[Dict[str, List[str]]] = None,
        mode: str = "full"  # "coverage", "conflicts", "full"
    ) -> Dict[str, Any]:
        """
        Execute finalize check.
        
        Args:
            project_type: Project type for topic registry lookup
            extracted_content: Dict of topic_id -> extracted items (from doc)
            finalized_topics: Dict of topic_id -> finalized items (existing)
            mode: "coverage" (after extraction), "conflicts" (before finalize), "full" (both)
        
        Returns:
            Dict with success status and FinalizeCheckResult
        """
        try:
            result = FinalizeCheckResult(
                total_topics=0,
                covered_topics=0,
                missing_required=[],
                coverage_details=[],
                conflicts=[],
                completeness_score=0.0,
                quality_notes=[],
                recommendations=[]
            )
            
            # Get applicable topics for project type
            applicable_topics = get_topics_for_project_type(project_type)
            default_topic_ids = get_default_topics_for_project_type(project_type)
            result.total_topics = len(applicable_topics)
            
            # Merge content sources
            all_content = {}
            if extracted_content:
                all_content.update(extracted_content)
            if finalized_topics:
                for topic_id, items in finalized_topics.items():
                    if topic_id in all_content:
                        all_content[topic_id].extend(items)
                    else:
                        all_content[topic_id] = items
            
            # Run coverage check
            if mode in ["coverage", "full"]:
                await self._check_coverage(
                    result, applicable_topics, default_topic_ids, all_content
                )
            
            # Run conflict check
            if mode in ["conflicts", "full"]:
                await self._check_conflicts(result, all_content, project_type)
            
            # Calculate completeness score
            if result.total_topics > 0:
                required_count = len(default_topic_ids)
                covered_required = len([
                    t for t in result.coverage_details 
                    if t.is_required and t.is_covered
                ])
                result.completeness_score = (covered_required / required_count * 100) if required_count > 0 else 100
            
            # Generate recommendations
            self._generate_recommendations(result)
            
            return {
                "success": True,
                "error": None,
                "data": result
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"Finalize check error: {str(e)}",
                "data": None
            }
    
    async def _check_coverage(
        self,
        result: FinalizeCheckResult,
        applicable_topics: List[Dict],
        default_topic_ids: List[str],
        content: Dict[str, List[str]]
    ):
        """Check which topics are covered by the content."""
        for topic in applicable_topics:
            topic_id = topic["id"]
            topic_name = topic["name"]
            is_required = topic_id in default_topic_ids
            
            # Check if topic has content
            topic_items = content.get(topic_id, [])
            is_covered = len(topic_items) > 0
            
            coverage = TopicCoverage(
                topic_id=topic_id,
                topic_name=topic_name,
                is_required=is_required,
                is_covered=is_covered,
                extracted_items=topic_items,
                confidence=1.0 if is_covered else 0.0
            )
            
            result.coverage_details.append(coverage)
            
            if is_covered:
                result.covered_topics += 1
            elif is_required:
                result.missing_required.append(topic_name)
    
    async def _check_conflicts(
        self,
        result: FinalizeCheckResult,
        content: Dict[str, List[str]],
        project_type: str
    ):
        """
        Check for cross-topic conflicts using reasoning model.
        
        Common conflicts to detect:
        - Goal vs Scope: Goals that can't be achieved with defined scope
        - Schedule vs Resources: Timeline that doesn't match available resources
        - Requirements vs Constraints: Requirements that violate constraints
        """
        # Only check if we have substantial content
        if len(content) < 2:
            return
        
        # Build conflict check prompt
        conflict_prompt = self._build_conflict_check_prompt(content, project_type)
        
        # Call reasoning model
        conflicts_response = await self._call_reasoning_model(conflict_prompt)
        
        if conflicts_response:
            # Parse conflicts from response
            parsed_conflicts = self._parse_conflicts(conflicts_response)
            result.conflicts.extend(parsed_conflicts)
            
            # Add quality notes
            if not parsed_conflicts:
                result.quality_notes.append("No cross-topic conflicts detected")
            else:
                result.quality_notes.append(f"Found {len(parsed_conflicts)} potential conflict(s)")
    
    def _build_conflict_check_prompt(self, content: Dict[str, List[str]], project_type: str) -> str:
        """Build prompt for conflict detection."""
        content_summary = []
        for topic_id, items in content.items():
            if items:
                content_summary.append(f"**{topic_id.title()}:**")
                for item in items[:5]:  # Limit items per topic
                    content_summary.append(f"- {item}")
        
        return f"""Analyze the following project charter content for conflicts and inconsistencies.

Project Type: {project_type}

Charter Content:
{chr(10).join(content_summary)}

Identify any conflicts between topics. For each conflict found, provide:
1. The two topics involved
2. Description of the conflict
3. Severity (low/medium/high)
4. Suggestion to resolve

If no conflicts are found, respond with "NO_CONFLICTS".

Format each conflict as:
CONFLICT: [Topic A] vs [Topic B]
DESCRIPTION: [What's conflicting]
SEVERITY: [low/medium/high]
SUGGESTION: [How to resolve]
"""
    
    async def _call_reasoning_model(self, prompt: str) -> Optional[str]:
        """Call the reasoning model for conflict analysis."""
        try:
            if self.model_config.is_anthropic:
                return await self._call_anthropic(prompt)
            if self.model_config.provider.lower() == "groq":
                return await self._call_groq(prompt)
            return await self._call_openai(prompt)
        except Exception as e:
            print(f"Reasoning model error: {str(e)}")
            return None
    
    async def _call_anthropic(self, prompt: str) -> Optional[str]:
        """Call Anthropic Claude API."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json"
                },
                json={
                    "model": self.model_config.model,
                    "max_tokens": 1000,
                    "messages": [{"role": "user", "content": prompt}]
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                return data["content"][0]["text"]
            return None
    
    async def _call_openai(self, prompt: str) -> Optional[str]:
        """Call OpenAI API."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            is_openai_restricted = self.model_config.model.lower().startswith(("gpt-5", "o3"))
            payload = {
                "model": self.model_config.model,
                "messages": [{"role": "user", "content": prompt}]
            }
            if is_openai_restricted:
                payload["max_completion_tokens"] = 1000
            else:
                payload["temperature"] = 1
                payload["max_tokens"] = 1000

            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "Content-Type": "application/json"
                },
                json=payload
            )
            
            if response.status_code == 200:
                data = response.json()
                message = (data.get("choices") or [{}])[0].get("message") or {}
                content = message.get("content")
                if isinstance(content, list):
                    parts = []
                    for item in content:
                        if isinstance(item, str):
                            parts.append(item)
                        elif isinstance(item, dict):
                            text = item.get("text") or item.get("content")
                            if text:
                                parts.append(text)
                    return "\n".join(parts)
                if isinstance(content, str):
                    return content
            return None

    async def _call_groq(self, prompt: str) -> Optional[str]:
        """Call Groq API."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.groq_api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": self.model_config.model,
                    "max_tokens": 1000,
                    "messages": [{"role": "user", "content": prompt}]
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                message = (data.get("choices") or [{}])[0].get("message") or {}
                content = message.get("content")
                if isinstance(content, list):
                    parts = []
                    for item in content:
                        if isinstance(item, str):
                            parts.append(item)
                        elif isinstance(item, dict):
                            text = item.get("text") or item.get("content")
                            if text:
                                parts.append(text)
                    return "\n".join(parts)
                if isinstance(content, str):
                    return content
            return None
    
    def _parse_conflicts(self, response: str) -> List[ConflictItem]:
        """Parse conflict items from model response."""
        conflicts = []
        
        if "NO_CONFLICTS" in response.upper():
            return conflicts
        
        # Simple parsing - look for CONFLICT: markers
        import re
        conflict_blocks = re.split(r'CONFLICT:', response)
        
        for block in conflict_blocks[1:]:  # Skip first empty split
            lines = block.strip().split('\n')
            if not lines:
                continue
            
            # Parse first line for topics
            topics_line = lines[0].strip()
            topics_match = re.match(r'(.+?)\s+vs\s+(.+)', topics_line, re.IGNORECASE)
            
            if topics_match:
                topic_a = topics_match.group(1).strip()
                topic_b = topics_match.group(2).strip()
                
                description = ""
                severity = "medium"
                suggestion = ""
                
                for line in lines[1:]:
                    if line.startswith("DESCRIPTION:"):
                        description = line.replace("DESCRIPTION:", "").strip()
                    elif line.startswith("SEVERITY:"):
                        severity = line.replace("SEVERITY:", "").strip().lower()
                    elif line.startswith("SUGGESTION:"):
                        suggestion = line.replace("SUGGESTION:", "").strip()
                
                conflicts.append(ConflictItem(
                    topic_a=topic_a,
                    topic_b=topic_b,
                    description=description,
                    severity=severity,
                    suggestion=suggestion
                ))
        
        return conflicts
    
    def _generate_recommendations(self, result: FinalizeCheckResult):
        """Generate recommendations based on check results."""
        # Missing required topics
        if result.missing_required:
            result.recommendations.append(
                f"Complete these required topics: {', '.join(result.missing_required)}"
            )
        
        # Low completeness
        if result.completeness_score < 50:
            result.recommendations.append(
                "Consider adding more detail to covered topics before finalizing"
            )
        
        # High severity conflicts
        high_conflicts = [c for c in result.conflicts if c.severity == "high"]
        if high_conflicts:
            result.recommendations.append(
                f"Resolve {len(high_conflicts)} high-severity conflict(s) before proceeding"
            )
        
        # Good to go
        if not result.missing_required and not high_conflicts and result.completeness_score >= 70:
            result.recommendations.append(
                "Charter looks good! Ready for finalization."
            )
    
    def format_for_user(self, result: FinalizeCheckResult) -> str:
        """Format check results for user display."""
        parts = ["## Charter Validation Results\n"]
        
        # Completeness score
        score_emoji = "🟢" if result.completeness_score >= 70 else "🟡" if result.completeness_score >= 40 else "🔴"
        parts.append(f"**Completeness Score:** {score_emoji} {result.completeness_score:.0f}%\n")
        
        # Coverage summary
        parts.append(f"**Topics Covered:** {result.covered_topics}/{result.total_topics}")
        
        # Missing required
        if result.missing_required:
            parts.append(f"\n⚠️ **Missing Required Topics:** {', '.join(result.missing_required)}")
        
        # Conflicts
        if result.conflicts:
            parts.append(f"\n### Conflicts Detected ({len(result.conflicts)})")
            for conflict in result.conflicts:
                severity_icon = "🔴" if conflict.severity == "high" else "🟡" if conflict.severity == "medium" else "🟢"
                parts.append(f"\n{severity_icon} **{conflict.topic_a} vs {conflict.topic_b}**")
                parts.append(f"   {conflict.description}")
                if conflict.suggestion:
                    parts.append(f"   💡 {conflict.suggestion}")
        
        # Recommendations
        if result.recommendations:
            parts.append("\n### Recommendations")
            for rec in result.recommendations:
                parts.append(f"- {rec}")
        
        return "\n".join(parts)
