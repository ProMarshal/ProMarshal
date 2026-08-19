"""
Agent Decision Rules
Defines patterns and rules for automatic tool selection and routing.
"""

import re
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum


class ToolAction(Enum):
    """Available tool actions."""
    CHAT = "chat"
    WEB_SEARCH = "web_search"
    DOCUMENT_EXTRACT = "document_extract"
    FINALIZE_CHECK = "finalize_check"
    TEMPLATE_LOAD = "template_load"


class ModelType(Enum):
    """Model types for different tasks."""
    CONVERSATION = "conversation"
    REASONING = "reasoning"
    EXTRACTION = "extraction"
    INFERENCE = "inference"


@dataclass
class DecisionRule:
    """Single decision rule definition."""
    name: str
    trigger_type: str  # "pattern", "keyword", "event"
    trigger_value: Any  # regex pattern, keyword list, or event name
    action: ToolAction
    model_type: ModelType
    priority: int = 0  # Higher = checked first
    description: str = ""


# URL detection pattern
URL_PATTERN = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')

# Reference keywords that suggest web search
REFERENCE_KEYWORDS = [
    "like", "similar to", "such as", "for example",
    "check out", "look at", "reference", "example of",
    "how does", "what does", "how do other", "competitor"
]

# Template request keywords
TEMPLATE_KEYWORDS = [
    "template", "example charter", "sample", "starting point",
    "give me a", "show me a", "standard format"
]

# Finalize keywords
FINALIZE_KEYWORDS = [
    "finalize", "complete", "done with", "ready to finish",
    "looks good", "approve", "confirm"
]


# Decision Rules Configuration
AGENT_RULES: List[DecisionRule] = [
    # URL Detection -> Web Search
    DecisionRule(
        name="url_detected",
        trigger_type="pattern",
        trigger_value=URL_PATTERN,
        action=ToolAction.WEB_SEARCH,
        model_type=ModelType.CONVERSATION,
        priority=100,
        description="User provided a URL - fetch and analyze"
    ),
    
    # Reference Keywords -> Web Search
    DecisionRule(
        name="reference_mentioned",
        trigger_type="keyword",
        trigger_value=REFERENCE_KEYWORDS,
        action=ToolAction.WEB_SEARCH,
        model_type=ModelType.CONVERSATION,
        priority=90,
        description="User mentioned a reference product/example"
    ),
    
    # Document Upload -> Extract + Check
    DecisionRule(
        name="document_uploaded",
        trigger_type="event",
        trigger_value="file_upload",
        action=ToolAction.DOCUMENT_EXTRACT,
        model_type=ModelType.EXTRACTION,
        priority=100,
        description="Document uploaded - extract content"
    ),
    
    # Finalize Request -> Reasoning Check
    DecisionRule(
        name="finalize_requested",
        trigger_type="keyword",
        trigger_value=FINALIZE_KEYWORDS,
        action=ToolAction.FINALIZE_CHECK,
        model_type=ModelType.REASONING,
        priority=85,
        description="User wants to finalize - run validation"
    ),
    
    # Template Request -> Load Template
    DecisionRule(
        name="template_requested",
        trigger_type="keyword",
        trigger_value=TEMPLATE_KEYWORDS,
        action=ToolAction.TEMPLATE_LOAD,
        model_type=ModelType.CONVERSATION,
        priority=80,
        description="User wants a template/example"
    ),
    
    # Default -> Chat
    DecisionRule(
        name="default_chat",
        trigger_type="event",
        trigger_value="default",
        action=ToolAction.CHAT,
        model_type=ModelType.CONVERSATION,
        priority=0,
        description="Default action - conversational chat"
    ),
]


def evaluate_rules(context: Dict) -> DecisionRule:
    """
    Evaluate all rules against context and return the matching rule.
    
    Args:
        context: Dict containing:
            - message: User's message text
            - event: Event type (e.g., "file_upload", "finalize_click")
            - topic: Current topic
            - project_type: Current project type
    
    Returns:
        Matching DecisionRule with highest priority
    """
    message = context.get("message", "").lower()
    event = context.get("event", "")
    
    # Sort rules by priority (highest first)
    sorted_rules = sorted(AGENT_RULES, key=lambda r: r.priority, reverse=True)
    
    for rule in sorted_rules:
        if _matches_rule(rule, message, event, context):
            return rule
    
    # Return default chat rule
    return sorted_rules[-1]  # Lowest priority is default


def _matches_rule(rule: DecisionRule, message: str, event: str, context: Dict) -> bool:
    """Check if a rule matches the given context."""
    
    if rule.trigger_type == "pattern":
        # Regex pattern match
        pattern = rule.trigger_value
        if pattern.search(context.get("message", "")):  # Use original case for URLs
            return True
    
    elif rule.trigger_type == "keyword":
        # Keyword list match
        keywords = rule.trigger_value
        for keyword in keywords:
            if keyword in message:
                return True
    
    elif rule.trigger_type == "event":
        # Event match
        if rule.trigger_value == event:
            return True
        if rule.trigger_value == "default" and rule.priority == 0:
            return True  # Default fallback
    
    return False


def get_model_type_for_action(action: ToolAction) -> ModelType:
    """Get the recommended model type for an action."""
    action_model_map = {
        ToolAction.CHAT: ModelType.CONVERSATION,
        ToolAction.WEB_SEARCH: ModelType.CONVERSATION,
        ToolAction.DOCUMENT_EXTRACT: ModelType.EXTRACTION,
        ToolAction.FINALIZE_CHECK: ModelType.REASONING,
        ToolAction.TEMPLATE_LOAD: ModelType.CONVERSATION,
    }
    return action_model_map.get(action, ModelType.CONVERSATION)


def get_rules_summary() -> List[Dict]:
    """Get summary of all rules for debugging/display."""
    return [
        {
            "name": rule.name,
            "action": rule.action.value,
            "model": rule.model_type.value,
            "priority": rule.priority,
            "description": rule.description
        }
        for rule in AGENT_RULES
    ]
