from app.projects.recommendations.provider_adapters.base import ProviderCapabilities, TaskProviderAdapter
from app.projects.recommendations.provider_adapters.jira import JiraTaskAdapter

__all__ = [
    "ProviderCapabilities",
    "TaskProviderAdapter",
    "JiraTaskAdapter",
]
