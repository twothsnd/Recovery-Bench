from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .config import BenchmarkConfig
from .adapters.placeholder import build_placeholder_benchmark
from .adapters.smoke import build_progress_smoke_benchmark
from .types import BenchmarkAdapter
from .plugins import build_benchmark_from_import_path

BenchmarkBuilder = Callable[[BenchmarkConfig, tuple[str, ...]], BenchmarkAdapter]
DependencyProbe = Callable[[], tuple[bool, str]]


@dataclass(slots=True)
class BenchmarkFactory:
    """Factory wrapper for lazy adapter construction."""

    name: str
    create: BenchmarkBuilder
    implemented: bool = True
    reason: str = ""
    probe: DependencyProbe | None = None


class BenchmarkRegistry:
    """Registry for benchmark integrations."""

    def __init__(self) -> None:
        self._factories: dict[str, BenchmarkFactory] = {}

    def register(
        self,
        name: str,
        factory: BenchmarkBuilder,
        *,
        probe: DependencyProbe | None = None,
        reason: str = "",
    ) -> None:
        self._factories[name] = BenchmarkFactory(
            name=name,
            create=factory,
            probe=probe,
            reason=reason,
        )

    def register_planned(self, name: str, reason: str) -> None:
        self._factories[name] = BenchmarkFactory(
            name=name,
            create=lambda _config, _task_ids: _raise_unavailable(name, reason),
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
        config: BenchmarkConfig | None = None,
        task_ids: tuple[str, ...] = (),
    ) -> BenchmarkAdapter:
        config = config or BenchmarkConfig(name=name)
        if config.import_path:
            return build_benchmark_from_import_path(config.import_path, config, task_ids)
        try:
            factory = self._factories[name]
        except KeyError as exc:
            raise KeyError(f"Unknown benchmark: {name}") from exc
        return factory.create(config, task_ids)


def default_benchmark_registry() -> BenchmarkRegistry:
    registry = BenchmarkRegistry()
    registry.register("placeholder", lambda _config, _task_ids: build_placeholder_benchmark())
    registry.register("progress-smoke", lambda _config, _task_ids: build_progress_smoke_benchmark())
    return registry


def _raise_unavailable(name: str, reason: str) -> BenchmarkAdapter:
    raise NotImplementedError(f"Benchmark adapter '{name}' is not implemented yet. {reason}")
