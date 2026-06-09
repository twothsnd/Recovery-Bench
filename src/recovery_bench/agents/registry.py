from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..config import AgentConfig, ModelConfig
from ..plugins import build_agent_from_import_path
from ..types import AgentAdapter
from .smoke import ProgressSmokeAgent

AgentBuilder = Callable[[ModelConfig, AgentConfig], AgentAdapter]
DependencyProbe = Callable[[], tuple[bool, str]]


@dataclass(slots=True)
class AgentFactory:
    name: str
    create: AgentBuilder
    implemented: bool = True
    reason: str = ""
    probe: DependencyProbe | None = None


class AgentRegistry:
    """Registry for agent backends."""

    def __init__(self) -> None:
        self._factories: dict[str, AgentFactory] = {}

    def register(
        self,
        name: str,
        factory: AgentBuilder,
        *,
        probe: DependencyProbe | None = None,
        reason: str = "",
    ) -> None:
        self._factories[name] = AgentFactory(
            name=name,
            create=factory,
            probe=probe,
            reason=reason,
        )

    def register_planned(self, name: str, reason: str) -> None:
        self._factories[name] = AgentFactory(
            name=name,
            create=lambda _model, _agent: _raise_unavailable(name, reason),
            implemented=False,
            reason=reason,
        )

    def available(self, *, include_planned: bool = True) -> list[str]:
        if include_planned:
            return sorted(self._factories)
        return sorted(name for name, factory in self._factories.items() if factory.implemented)

    def known(self) -> list[str]:
        return sorted(self._factories)

    def describe(self) -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = []
        for name, factory in sorted(self._factories.items()):
            if not factory.implemented:
                rows.append((name, "planned", factory.reason))
                continue
            if factory.probe is None:
                rows.append((name, "available", factory.reason))
                continue
            available, reason = factory.probe()
            rows.append((name, "available" if available else "unavailable", reason or factory.reason))
        return rows

    def build(
        self,
        name: str,
        model_config: ModelConfig | None = None,
        agent_config: AgentConfig | None = None,
    ) -> AgentAdapter:
        model_config = model_config or ModelConfig(name="unknown", provider="unknown")
        agent_config = agent_config or AgentConfig(name=name)
        if agent_config.import_path:
            return build_agent_from_import_path(agent_config.import_path, model_config, agent_config)
        try:
            factory = self._factories[name]
        except KeyError as exc:
            raise KeyError(f"Unknown agent: {name}") from exc
        return factory.create(model_config, agent_config)


def default_agent_registry() -> AgentRegistry:
    registry = AgentRegistry()
    registry.register("progress-smoke-agent", lambda _model, _agent: ProgressSmokeAgent())
    return registry


def _raise_unavailable(name: str, reason: str) -> AgentAdapter:
    raise NotImplementedError(f"Agent adapter '{name}' is not implemented yet. {reason}")
