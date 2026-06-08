"""Agent adapters live here."""

from .registry import AgentRegistry, default_agent_registry
from .smoke import ProgressSmokeAgent

__all__ = ["AgentRegistry", "ProgressSmokeAgent", "default_agent_registry"]

