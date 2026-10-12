"""Declarative agent factory: AgentSpec (YAML) + prompt.md -> MAF Agent."""
from src.factory.agent_factory import AgentFactory
from src.factory.loader import SpecLoader, SpecValidationError
from src.factory.spec import AgentSpec

__all__ = ["AgentFactory", "AgentSpec", "SpecLoader", "SpecValidationError"]
