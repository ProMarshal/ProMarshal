"""
Model Configuration for Charter AI Tasks
Provides task-based model selection from environment variables.
"""

from dataclasses import dataclass
from typing import Literal
from app.core.config import settings


@dataclass
class ModelConfig:
    """Configuration for a specific model."""
    provider: str  # "anthropic" or "openai"
    model: str
    
    @property
    def is_anthropic(self) -> bool:
        return self.provider.lower() == "anthropic"
    
    @property
    def is_openai(self) -> bool:
        return self.provider.lower() == "openai"


# Task types for model selection
TaskType = Literal["conversation", "reasoning", "extraction", "inference"]


def get_model_for_task(task: TaskType) -> ModelConfig:
    """
    Get the appropriate model configuration for a given task.
    
    Args:
        task: The type of task to get model for
            - conversation: Brainstorm, chat, general interaction
            - reasoning: Pre-finalize check, conflict detection, completeness
            - extraction: Document extraction, content parsing
            - inference: Cross-topic inference, pattern matching
    
    Returns:
        ModelConfig with provider and model name
    """
    if task == "conversation":
        return ModelConfig(
            provider=settings.pm_conversation_provider,
            model=settings.pm_conversation_model
        )
    elif task == "reasoning":
        return ModelConfig(
            provider=settings.pm_reasoning_provider,
            model=settings.pm_reasoning_model
        )
    elif task == "extraction":
        return ModelConfig(
            provider=settings.pm_conversation_provider,
            model=settings.pm_conversation_model
        )
    elif task == "inference":
        return ModelConfig(
            provider=settings.pm_conversation_provider,
            model=settings.pm_conversation_model
        )
    else:
        # Default to conversation model
        return ModelConfig(
            provider=settings.pm_conversation_provider,
            model=settings.pm_conversation_model
        )


def get_all_model_configs() -> dict:
    """Get all model configurations for debugging/display."""
    return {
        "conversation": {
            "provider": settings.pm_conversation_provider,
            "model": settings.pm_conversation_model,
            "purpose": "Brainstorm, chat, general interaction"
        },
        "reasoning": {
            "provider": settings.pm_reasoning_provider,
            "model": settings.pm_reasoning_model,
            "purpose": "Pre-finalize check, conflict detection"
        },
        "extraction": {
            "provider": settings.pm_conversation_provider,
            "model": settings.pm_conversation_model,
            "purpose": "Document extraction, content parsing"
        },
        "inference": {
            "provider": settings.pm_conversation_provider,
            "model": settings.pm_conversation_model,
            "purpose": "Cross-topic inference, pattern matching"
        }
    }
