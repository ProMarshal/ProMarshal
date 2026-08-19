"""
Topic Registry for Project Charter
Defines all topics, groups, project type mappings, and cross-topic dependencies.
"""

from typing import Dict, List, Literal, Optional
from enum import Enum


class TopicStatus(str, Enum):
    """Status of a topic in a project"""
    DEFAULT = "default"      # Auto-included for project type
    OPTIONAL = "optional"    # Can be added by user
    NOT_APPLICABLE = "na"    # Not available for project type


class ProjectType(str, Enum):
    """Supported project types"""
    SOFTWARE_PRODUCT = "Software / Product Development"
    IMPLEMENTATION = "Implementation"


# =============================================================================
# TOPIC GROUPS (5 Universal Groups)
# =============================================================================

TOPIC_GROUPS = [
    {
        "id": "vision_boundaries",
        "name": "Vision & Boundaries",
        "display_order": 1,
        "description": "Define project goals and scope boundaries"
    },
    {
        "id": "people",
        "name": "People",
        "display_order": 2,
        "description": "Stakeholders, ownership, and responsibilities"
    },
    {
        "id": "context_risks",
        "name": "Context & Risks",
        "display_order": 3,
        "description": "Assumptions, constraints, risks, and dependencies"
    },
    {
        "id": "planning_resources",
        "name": "Planning & Resources",
        "display_order": 4,
        "description": "Schedule, resource allocation, and budget"
    },
    {
        "id": "deliverables",
        "name": "Deliverables",
        "display_order": 5,
        "description": "Requirements, features, milestones, and outputs"
    }
]


# =============================================================================
# TOPIC DEFINITIONS (17 Topics)
# =============================================================================

TOPICS = [
    # Vision & Boundaries
    {
        "id": "goal",
        "name": "Goal",
        "group_id": "vision_boundaries",
        "description": "Project objectives and success criteria",
        "display_order": 1,
        "storage_mode": "holistic"
    },
    {
        "id": "scope",
        "name": "Scope",
        "group_id": "vision_boundaries",
        "description": "Project boundaries - what's in and out",
        "display_order": 2,
        "storage_mode": "embedded"
    },
    
    # People
    {
        "id": "stakeholder",
        "name": "Stakeholder",
        "group_id": "people",
        "description": "Key people affected by or influencing the project",
        "display_order": 3,
        "storage_mode": "collection"
    },
    {
        "id": "owner",
        "name": "Owner",
        "group_id": "people",
        "description": "Project ownership and accountability",
        "display_order": 4,
        "storage_mode": "embedded"
    },
    {
        "id": "raci",
        "name": "RACI",
        "group_id": "people",
        "description": "Responsibility assignment matrix",
        "display_order": 5,
        "storage_mode": "embedded"
    },
    
    # Context & Risks
    {
        "id": "assumption",
        "name": "Assumption",
        "group_id": "context_risks",
        "description": "Conditions assumed to be true",
        "display_order": 6,
        "storage_mode": "embedded"
    },
    {
        "id": "constraint",
        "name": "Constraint",
        "group_id": "context_risks",
        "description": "Limitations and restrictions",
        "display_order": 7,
        "storage_mode": "embedded"
    },
    {
        "id": "risk",
        "name": "Risk",
        "group_id": "context_risks",
        "description": "Potential issues and mitigation strategies",
        "display_order": 8,
        "storage_mode": "collection"
    },
    {
        "id": "dependency",
        "name": "Dependency",
        "group_id": "context_risks",
        "description": "External and internal dependencies",
        "display_order": 9,
        "storage_mode": "embedded"
    },
    
    # Planning & Resources
    {
        "id": "schedule",
        "name": "Schedule",
        "group_id": "planning_resources",
        "description": "Timeline and key dates",
        "display_order": 10,
        "storage_mode": "embedded"
    },
    {
        "id": "resource_allocation",
        "name": "Resource Allocation",
        "group_id": "planning_resources",
        "description": "Team assignments and resource planning",
        "display_order": 11,
        "storage_mode": "embedded"
    },
    {
        "id": "budget",
        "name": "Budget",
        "group_id": "planning_resources",
        "description": "Cost estimates and financial constraints",
        "display_order": 12,
        "storage_mode": "embedded"
    },
    
    # Deliverables
    {
        "id": "requirement",
        "name": "Requirement",
        "group_id": "deliverables",
        "description": "Functional and non-functional requirements",
        "display_order": 13,
        "storage_mode": "collection"
    },
    {
        "id": "feature",
        "name": "Feature",
        "group_id": "deliverables",
        "description": "Product features and capabilities",
        "display_order": 14,
        "storage_mode": "collection"
    },
    {
        "id": "milestone",
        "name": "Milestone",
        "group_id": "deliverables",
        "description": "Key achievement points",
        "display_order": 15,
        "storage_mode": "collection"
    },
    {
        "id": "deliverable",
        "name": "Deliverable",
        "group_id": "deliverables",
        "description": "Tangible outputs and artifacts",
        "display_order": 16,
        "storage_mode": "collection"
    },
    {
        "id": "rollout_phase",
        "name": "Rollout Phase",
        "group_id": "deliverables",
        "description": "Deployment phases and go-live planning",
        "display_order": 17,
        "storage_mode": "embedded"
    }
]


# =============================================================================
# PROJECT TYPE → TOPIC STATUS MAPPING
# =============================================================================

PROJECT_TYPE_TOPIC_STATUS: Dict[str, Dict[str, TopicStatus]] = {
    ProjectType.SOFTWARE_PRODUCT.value: {
        "goal": TopicStatus.DEFAULT,
        "scope": TopicStatus.DEFAULT,
        "stakeholder": TopicStatus.OPTIONAL,
        "owner": TopicStatus.DEFAULT,
        "raci": TopicStatus.OPTIONAL,
        "assumption": TopicStatus.OPTIONAL,
        "constraint": TopicStatus.OPTIONAL,
        "risk": TopicStatus.OPTIONAL,
        "dependency": TopicStatus.OPTIONAL,
        "schedule": TopicStatus.OPTIONAL,
        "resource_allocation": TopicStatus.OPTIONAL,
        "budget": TopicStatus.OPTIONAL,
        "requirement": TopicStatus.DEFAULT,
        "feature": TopicStatus.DEFAULT,
        "milestone": TopicStatus.OPTIONAL,
        "deliverable": TopicStatus.OPTIONAL,
        "rollout_phase": TopicStatus.NOT_APPLICABLE
    },
    ProjectType.IMPLEMENTATION.value: {
        "goal": TopicStatus.DEFAULT,
        "scope": TopicStatus.DEFAULT,
        "stakeholder": TopicStatus.DEFAULT,
        "owner": TopicStatus.DEFAULT,
        "raci": TopicStatus.DEFAULT,
        "assumption": TopicStatus.DEFAULT,
        "constraint": TopicStatus.DEFAULT,
        "risk": TopicStatus.DEFAULT,
        "dependency": TopicStatus.DEFAULT,
        "schedule": TopicStatus.DEFAULT,
        "resource_allocation": TopicStatus.DEFAULT,
        "budget": TopicStatus.OPTIONAL,
        "requirement": TopicStatus.OPTIONAL,
        "feature": TopicStatus.NOT_APPLICABLE,
        "milestone": TopicStatus.DEFAULT,
        "deliverable": TopicStatus.DEFAULT,
        "rollout_phase": TopicStatus.OPTIONAL
    }
}


# =============================================================================
# CROSS-TOPIC DEPENDENCIES
# Key: topic_id, Value: list of topic_ids that should be completed first
# Used for informational banners, not blocking navigation
# =============================================================================

TOPIC_DEPENDENCIES: Dict[str, List[str]] = {
    "scope": ["goal"],
    "stakeholder": ["goal"],
    "owner": ["stakeholder"],
    "raci": ["stakeholder", "owner"],
    "assumption": ["goal", "scope"],
    "constraint": ["goal", "scope"],
    "risk": ["goal", "scope", "assumption", "constraint"],
    "dependency": ["goal", "scope"],
    "schedule": ["scope", "milestone"],
    "resource_allocation": ["scope", "stakeholder"],
    "budget": ["scope", "resource_allocation"],
    "requirement": ["goal", "scope"],
    "feature": ["requirement"],
    "milestone": ["goal", "scope"],
    "deliverable": ["scope", "requirement"],
    "rollout_phase": ["milestone", "deliverable"]
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_topics_for_project_type(project_type: str) -> List[dict]:
    """
    Get all applicable topics for a project type with their status.
    Excludes topics marked as NOT_APPLICABLE.
    """
    if project_type not in PROJECT_TYPE_TOPIC_STATUS:
        # Default to Software/Product Development if unknown type
        project_type = ProjectType.SOFTWARE_PRODUCT.value
    
    type_mapping = PROJECT_TYPE_TOPIC_STATUS[project_type]
    result = []
    
    for topic in TOPICS:
        status = type_mapping.get(topic["id"], TopicStatus.NOT_APPLICABLE)
        if status != TopicStatus.NOT_APPLICABLE:
            result.append({
                **topic,
                "status": status.value,
                "dependencies": TOPIC_DEPENDENCIES.get(topic["id"], [])
            })
    
    return result


def get_default_topics_for_project_type(project_type: str) -> List[str]:
    """Get list of topic IDs that are default for a project type."""
    if project_type not in PROJECT_TYPE_TOPIC_STATUS:
        project_type = ProjectType.SOFTWARE_PRODUCT.value
    
    type_mapping = PROJECT_TYPE_TOPIC_STATUS[project_type]
    return [
        topic_id 
        for topic_id, status in type_mapping.items() 
        if status == TopicStatus.DEFAULT
    ]


def get_optional_topics_for_project_type(project_type: str) -> List[str]:
    """Get list of topic IDs that are optional for a project type."""
    if project_type not in PROJECT_TYPE_TOPIC_STATUS:
        project_type = ProjectType.SOFTWARE_PRODUCT.value
    
    type_mapping = PROJECT_TYPE_TOPIC_STATUS[project_type]
    return [
        topic_id 
        for topic_id, status in type_mapping.items() 
        if status == TopicStatus.OPTIONAL
    ]


def get_topic_by_id(topic_id: str) -> Optional[dict]:
    """Get topic definition by ID."""
    for topic in TOPICS:
        if topic["id"] == topic_id:
            return topic
    return None


def get_group_by_id(group_id: str) -> Optional[dict]:
    """Get group definition by ID."""
    for group in TOPIC_GROUPS:
        if group["id"] == group_id:
            return group
    return None


def get_topics_by_group(group_id: str) -> List[dict]:
    """Get all topics in a group."""
    return [topic for topic in TOPICS if topic["group_id"] == group_id]


def get_topic_dependencies(topic_id: str) -> List[str]:
    """Get list of topic IDs that should be completed before this topic."""
    return TOPIC_DEPENDENCIES.get(topic_id, [])


def get_storage_mode(topic_id: str) -> str:
    """Return storage mode for a topic: holistic, embedded, or collection."""
    topic = get_topic_by_id(topic_id)
    if not topic:
        return "embedded"
    return topic.get("storage_mode", "embedded")


def get_full_registry() -> dict:
    """Get the complete topic registry with groups and topics."""
    return {
        "groups": TOPIC_GROUPS,
        "topics": TOPICS,
        "project_types": [pt.value for pt in ProjectType],
        "dependencies": TOPIC_DEPENDENCIES
    }


def get_ordered_default_topics(project_type: str) -> List[dict]:
    """
    Get default topics for a project type, ordered by display_order.
    Used for progressive topic loading during brainstorm.
    
    Args:
        project_type: Project type string
    
    Returns:
        List of topic dicts sorted by display_order
    """
    if project_type not in PROJECT_TYPE_TOPIC_STATUS:
        project_type = ProjectType.SOFTWARE_PRODUCT.value
    
    type_mapping = PROJECT_TYPE_TOPIC_STATUS[project_type]
    
    # Filter to default topics only
    default_topics = []
    for topic in TOPICS:
        status = type_mapping.get(topic["id"], TopicStatus.NOT_APPLICABLE)
        if status == TopicStatus.DEFAULT:
            default_topics.append({
                **topic,
                "dependencies": TOPIC_DEPENDENCIES.get(topic["id"], [])
            })
    
    # Sort by display_order
    return sorted(default_topics, key=lambda t: t["display_order"])

