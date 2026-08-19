"""
Template Loader Tool
Loads charter templates by industry/project type to give users a starting point.
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CharterTemplate:
    """Charter template definition."""
    id: str
    name: str
    project_type: str
    description: str
    topics: Dict[str, List[str]]  # topic_id -> example items


class TemplateLoaderTool:
    """
    Template Loader Tool.
    
    Loads pre-defined charter templates based on:
    - Project type (Software Dev, Implementation)
    - Industry (optional, future enhancement)
    """
    
    name = "template_loader"
    description = "Load charter templates to give users a starting point"
    
    # Base path for templates (relative to this file)
    TEMPLATES_DIR = Path(__file__).parent.parent.parent / "prompt_templates" / "templates"
    
    def __init__(self):
        self._templates_cache: Dict[str, CharterTemplate] = {}
    
    def can_handle(self, context: Dict) -> bool:
        """
        Determine if template loading should be triggered.
        
        Triggers:
        - New project created
        - User requests template
        - Empty charter detected
        """
        triggers = ["project_created", "template_requested", "empty_charter"]
        return context.get("trigger") in triggers
    
    async def execute(
        self,
        project_type: str,
        template_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Load a charter template.
        
        Args:
            project_type: Project type to find matching template
            template_id: Specific template ID (optional)
        
        Returns:
            Dict with success status and template data
        """
        try:
            # Get available templates for project type
            templates = self.get_templates_for_project_type(project_type)
            
            if not templates:
                return {
                    "success": False,
                    "error": f"No templates found for project type: {project_type}",
                    "data": None
                }
            
            # Select template
            if template_id:
                template = next((t for t in templates if t.id == template_id), None)
                if not template:
                    return {
                        "success": False,
                        "error": f"Template not found: {template_id}",
                        "data": None
                    }
            else:
                # Use first matching template as default
                template = templates[0]
            
            return {
                "success": True,
                "error": None,
                "data": template
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": f"Template loader error: {str(e)}",
                "data": None
            }
    
    def get_templates_for_project_type(self, project_type: str) -> List[CharterTemplate]:
        """Get all templates matching a project type."""
        all_templates = self._load_all_templates()
        
        project_type_lower = project_type.lower()
        
        matching = []
        for template in all_templates:
            if template.project_type.lower() in project_type_lower or \
               project_type_lower in template.project_type.lower():
                matching.append(template)
        
        return matching
    
    def _load_all_templates(self) -> List[CharterTemplate]:
        """Load all templates from config."""
        # For now, return built-in templates
        # In future, could load from files in TEMPLATES_DIR
        return [
            self._get_software_dev_template(),
            self._get_implementation_template(),
        ]
    
    def _get_software_dev_template(self) -> CharterTemplate:
        """Built-in Software Development template."""
        return CharterTemplate(
            id="software_dev_basic",
            name="Software Development - Basic",
            project_type="Software / Product Development",
            description="Standard template for software development projects with core topics pre-filled with examples.",
            topics={
                "goal": [
                    "Build a [product type] that helps [target users] to [main benefit]",
                    "Increase [key metric] by [percentage] within [timeframe]",
                    "Replace/modernize [existing system] with improved [capabilities]"
                ],
                "scope": [
                    "IN SCOPE: Core features for MVP including [list features]",
                    "IN SCOPE: Integration with [existing systems]",
                    "OUT OF SCOPE: Advanced analytics (v2)",
                    "OUT OF SCOPE: Mobile app (v2)"
                ],
                "stakeholder": [
                    "Product Owner - [Name] - Decision maker for features and priorities",
                    "Engineering Lead - [Name] - Technical decisions and architecture",
                    "End Users - [User group] - Primary users of the system"
                ],
                "requirement": [
                    "System must support [N] concurrent users",
                    "Response time under [X] seconds for key operations",
                    "Must integrate with [existing system] via [method]",
                    "Must comply with [security/compliance requirement]"
                ],
                "feature": [
                    "User authentication and authorization",
                    "Dashboard with key metrics",
                    "CRUD operations for core entities",
                    "Reporting and export capabilities"
                ],
                "milestone": [
                    "M1: Requirements finalized and signed off",
                    "M2: Design complete and approved",
                    "M3: MVP development complete",
                    "M4: UAT complete, ready for production"
                ]
            }
        )
    
    def _get_implementation_template(self) -> CharterTemplate:
        """Built-in Implementation Project template."""
        return CharterTemplate(
            id="implementation_basic",
            name="Implementation Project - Basic",
            project_type="Implementation",
            description="Standard template for implementation/rollout projects with change management focus.",
            topics={
                "goal": [
                    "Successfully implement [system/process] across [scope/locations]",
                    "Achieve [adoption rate]% user adoption within [timeframe]",
                    "Realize [efficiency/cost] benefits of [percentage] by [date]"
                ],
                "scope": [
                    "IN SCOPE: Rollout to [regions/departments]",
                    "IN SCOPE: Data migration from [source systems]",
                    "IN SCOPE: User training for [N] users",
                    "OUT OF SCOPE: Custom development beyond standard configuration",
                    "OUT OF SCOPE: Integration with [system] (phase 2)"
                ],
                "stakeholder": [
                    "Executive Sponsor - [Name] - Budget and organizational alignment",
                    "Change Manager - [Name] - Training and adoption",
                    "System Administrator - [Name] - Ongoing maintenance",
                    "Department Heads - [List] - Rollout coordination"
                ],
                "assumption": [
                    "Vendor will provide implementation support",
                    "Current data quality is sufficient for migration",
                    "Key users will be available for UAT",
                    "No major organizational changes during rollout"
                ],
                "constraint": [
                    "Must go live before [date] due to [reason]",
                    "Budget limited to [amount]",
                    "Cannot take current system offline for more than [hours]"
                ],
                "risk": [
                    "Data migration issues may delay go-live",
                    "User resistance to new system",
                    "Integration complexity with legacy systems",
                    "Vendor resource availability"
                ],
                "rollout_phase": [
                    "Phase 1: Pilot with [department] - [date]",
                    "Phase 2: Extended pilot to [regions] - [date]",
                    "Phase 3: Full rollout - [date]",
                    "Phase 4: Hypercare and stabilization - [date]"
                ],
                "milestone": [
                    "M1: Environment setup complete",
                    "M2: Data migration validated",
                    "M3: Pilot go-live",
                    "M4: Full rollout complete",
                    "M5: Hypercare period end"
                ]
            }
        )
    
    def format_template_for_display(self, template: CharterTemplate) -> str:
        """Format template for user display."""
        parts = [
            f"## {template.name}",
            f"*{template.description}*\n"
        ]
        
        for topic_id, items in template.topics.items():
            parts.append(f"### {topic_id.title().replace('_', ' ')}")
            for item in items:
                parts.append(f"- {item}")
            parts.append("")
        
        return "\n".join(parts)
    
    def get_topic_examples(self, template: CharterTemplate, topic_id: str) -> List[str]:
        """Get example items for a specific topic from template."""
        return template.topics.get(topic_id, [])
