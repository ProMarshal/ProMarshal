"""
Prompt Loader Module for Project Charter
Handles loading and composing prompts from modular markdown files.
"""

import os
from pathlib import Path
from typing import Dict, Optional, List
from functools import lru_cache

from app.core.config import settings
from app.promarshal.prompt_registry import compose_identity

# Base path for prompts directory
PROMPTS_DIR = Path(__file__).parent


@lru_cache(maxsize=50)
def load_prompt(relative_path: str) -> str:
    """
    Load a prompt markdown file from the prompts directory.
    Uses caching to avoid repeated file reads.
    
    Args:
        relative_path: Path relative to prompts/ directory (e.g., "topics/goal.md")
    
    Returns:
        Content of the markdown file as string
    """
    file_path = PROMPTS_DIR / relative_path
    
    if not file_path.exists():
        return ""
    
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def load_base_persona() -> str:
    """Load the base planner persona prompt."""
    legacy = load_prompt("base_persona.md")
    if not bool(getattr(settings, "promarshal_prompt_registry_enabled", False)):
        return legacy
    return compose_identity(
        capability="planner",
        legacy_identity=legacy,
        legacy_overlay="",
    )


def load_topic_prompt(topic_id: str) -> str:
    """Load the prompt for a specific topic."""
    return load_prompt(f"topics/{topic_id}.md")


def load_project_type_prompt(project_type: str) -> str:
    """
    Load the prompt for a project type.
    
    Args:
        project_type: Either "Software / Product Development" or "Implementation"
    """
    # Normalize project type to filename
    if "software" in project_type.lower() or "product" in project_type.lower():
        return load_prompt("project_types/software_dev.md")
    elif "implementation" in project_type.lower():
        return load_prompt("project_types/implementation.md")
    else:
        # Default to software dev
        return load_prompt("project_types/software_dev.md")


def load_mode_prompt(mode: str) -> str:
    """
    Load the prompt for a specific mode.
    
    Args:
        mode: One of "brainstorm", "document_review", "finalize_check"
    """
    valid_modes = ["brainstorm", "document_review", "finalize_check", "revision_initial", "revision_system", "revision_extract"]
    if mode not in valid_modes:
        mode = "brainstorm"
    
    return load_prompt(f"modes/{mode}.md")


def build_prompt(
    topic_id: str,
    project_type: str,
    mode: str = "brainstorm",
    context: Optional[Dict] = None,
    include_knowledge_base: bool = False
) -> str:
    """
    Compose the final prompt from modular components.
    
    Formula: base_persona + project_type + topic + mode + dynamic_context
    
    Args:
        topic_id: The topic being discussed (e.g., "goal", "scope")
        project_type: Project type for terminology/probing differences
        mode: Current mode ("brainstorm", "document_review", "finalize_check")
        context: Dynamic context (previous topics, extracted content, etc.)
        include_knowledge_base: Whether to include PM knowledge base (for Topic 4)
    
    Returns:
        Composed system prompt string
    """
    parts = []
    
    # 1. Base persona
    base = load_base_persona()
    if base:
        parts.append(base)
    
    # 2. Project type context
    project_type_prompt = load_project_type_prompt(project_type)
    if project_type_prompt:
        parts.append("\n---\n")
        parts.append("## Project Type Context")
        parts.append(project_type_prompt)
    
    # 3. Topic-specific guidance
    topic_prompt = load_topic_prompt(topic_id)
    if topic_prompt:
        parts.append("\n---\n")
        parts.append("## Current Topic Guidance")
        parts.append(topic_prompt)
    
    # 4. Mode-specific instructions
    mode_prompt = load_mode_prompt(mode)
    if mode_prompt:
        parts.append("\n---\n")
        parts.append("## Mode Instructions")
        parts.append(mode_prompt)
    
    # 5. Knowledge base injection (PM expertise for each topic)
    if include_knowledge_base:
        kb_content = load_knowledge_base_content(topic_id, project_type)
        if kb_content:
            parts.append("\n---\n")
            parts.append("## Your Experience (PM Knowledge Base)")
            parts.append(kb_content)
    
    # 6. Dynamic context (previous topics, etc.)
    if context:
        parts.append("\n---\n")
        parts.append("## Current Context")
        parts.append(format_context(context))
    
    return "\n".join(parts)


def load_knowledge_base_content(topic_id: str, project_type: str) -> str:
    """
    Load knowledge base content for a topic and project type.
    
    This includes:
    - Topic-specific insights (do's, don'ts, warnings, recovery tips)
    - Project type-specific insights
    - General best practices (selectively, to avoid context overload)
    
    Args:
        topic_id: The current topic (e.g., "goal", "scope")
        project_type: Project type for type-specific insights
    
    Returns:
        Formatted knowledge base content string
    """
    parts = []
    
    # 1. Topic-specific insights (always include if available)
    topic_insights = load_prompt(f"knowledge_base/topic_insights/{topic_id}_insights.md")
    if topic_insights:
        parts.append(topic_insights)
    
    # 2. Project type insights (include relevant section)
    if "software" in project_type.lower() or "product" in project_type.lower():
        type_insights = load_prompt("knowledge_base/project_type_insights/software_dev_insights.md")
    else:
        type_insights = load_prompt("knowledge_base/project_type_insights/implementation_insights.md")
    
    if type_insights:
        parts.append("\n### Project Type Wisdom")
        parts.append(type_insights)
    
    return "\n".join(parts)


def format_context(context: Dict) -> str:
    """
    Format dynamic context for inclusion in prompt.
    
    Args:
        context: Dictionary containing context like previous topics, extracted content
    
    Returns:
        Formatted context string
    """
    parts = []
    
    # Previous topics content
    if "previous_topics" in context:
        parts.append("### Already Captured:")
        for topic_id, content in context["previous_topics"].items():
            if content:
                parts.append(f"\n**{topic_id.title()}:**")
                if isinstance(content, list):
                    for item in content:
                        parts.append(f"- {item}")
                else:
                    parts.append(str(content))
    
    # Extracted content (for document review mode)
    if "extracted_content" in context:
        parts.append("\n### Extracted from Documents:")
        for item in context["extracted_content"]:
            parts.append(f"- {item}")
    
    # Current topic status
    if "current_topic_status" in context:
        parts.append(f"\n### Current Topic Status: {context['current_topic_status']}")
    
    # Project details
    if "project_name" in context:
        parts.append(f"\n### Project: {context['project_name']}")
    
    return "\n".join(parts)


def get_available_topics() -> List[str]:
    """Get list of available topic prompt files."""
    topics_dir = PROMPTS_DIR / "topics"
    if not topics_dir.exists():
        return []
    
    return [f.stem for f in topics_dir.glob("*.md")]


def get_available_modes() -> List[str]:
    """Get list of available mode prompt files."""
    modes_dir = PROMPTS_DIR / "modes"
    if not modes_dir.exists():
        return []
    
    return [f.stem for f in modes_dir.glob("*.md")]


def clear_prompt_cache():
    """Clear the prompt loading cache (useful during development)."""
    load_prompt.cache_clear()
