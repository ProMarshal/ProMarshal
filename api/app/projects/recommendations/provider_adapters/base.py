from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict

from app.projects.recommendations.normalized_task_model import NormalizedTask


@dataclass(frozen=True)
class ProviderCapabilities:
    has_reviews: bool = False
    has_sprints: bool = False
    has_dependencies: bool = False


class TaskProviderAdapter(ABC):
    provider_name: str = "unknown"

    @property
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities:
        raise NotImplementedError

    @abstractmethod
    def normalize_task(self, raw_task: Dict[str, Any]) -> NormalizedTask:
        raise NotImplementedError
