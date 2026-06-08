from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import BenchmarkConfig
from .adapters.appworld import (
    appworld_dependency_status,
    build_appworld_benchmark,
)
from .adapters.enterpriseops_gym import (
    build_enterpriseops_gym_benchmark,
    enterpriseops_gym_dependency_status,
)
from .adapters.osworld import (
    build_osworld_benchmark,
    osworld_dependency_status,
)
from .adapters.placeholder import build_placeholder_benchmark
from .adapters.smoke import build_progress_smoke_benchmark
from .adapters.tau_bench import (
    build_tau_bench_benchmark,
    tau_bench_dependency_status,
)
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
    registry.register(
        "appworld",
        _build_appworld_from_config,
        probe=appworld_dependency_status,
        reason="Requires the appworld package or benchmark.options.source_path.",
    )
    registry.register(
        "tau-bench",
        _build_tau_bench_from_config,
        probe=tau_bench_dependency_status,
        reason="Requires tau2-bench with the gym extra or benchmark.options.source_path.",
    )
    registry.register_planned(
        "clawsbench",
        "First-wave target. Official source currently exposes website/trajectory placeholders; executable tasks/environments are not released yet.",
    )
    registry.register(
        "enterpriseops-gym",
        _build_enterpriseops_gym_from_config,
        probe=enterpriseops_gym_dependency_status,
        reason="Requires official EnterpriseOps-Gym source, task configs, and running MCP servers.",
    )
    registry.register(
        "osworld",
        _build_osworld_from_config,
        probe=osworld_dependency_status,
        reason="Requires official OSWorld source and a configured DesktopEnv provider.",
    )
    return registry


def _raise_unavailable(name: str, reason: str) -> BenchmarkAdapter:
    raise NotImplementedError(f"Benchmark adapter '{name}' is not implemented yet. {reason}")


def _build_appworld_from_config(
    config: BenchmarkConfig,
    task_ids: tuple[str, ...],
) -> BenchmarkAdapter:
    options = dict(config.options)
    dataset_name = str(options.pop("dataset_name", "test_challenge"))
    experiment_name = str(options.pop("experiment_name", "recovery-bench"))
    source_path = _path_option(options.pop("source_path", None))
    root = _path_option(options.pop("root", None)) or _path_option(options.pop("data_root", None))
    dataset_path = config.dataset_path
    if source_path is None and dataset_path is not None and _looks_like_python_source_tree(dataset_path, "appworld"):
        source_path = dataset_path
    elif root is None:
        root = dataset_path
    return build_appworld_benchmark(
        root=root,
        source_path=source_path,
        dataset_name=dataset_name,
        experiment_name=experiment_name,
        task_ids=task_ids,
        options=options,
    )


def _build_tau_bench_from_config(
    config: BenchmarkConfig,
    task_ids: tuple[str, ...],
) -> BenchmarkAdapter:
    options = dict(config.options)
    domain = str(options.pop("domain", "airline"))
    solo_mode = bool(options.pop("solo_mode", False))
    source_path = _path_option(options.pop("source_path", None)) or config.dataset_path
    return build_tau_bench_benchmark(
        source_path=source_path,
        domain=domain,
        task_ids=task_ids,
        solo_mode=solo_mode,
        options=options,
    )


def _build_enterpriseops_gym_from_config(
    config: BenchmarkConfig,
    task_ids: tuple[str, ...],
) -> BenchmarkAdapter:
    options = dict(config.options)
    source_path = _path_option(options.pop("source_path", None)) or config.dataset_path
    configs_folder = _path_option(options.pop("configs_folder", None))
    domain = str(options.pop("domain", "teams"))
    mode = str(options.pop("mode", "oracle"))
    hf_dataset = str(options.pop("hf_dataset", "ServiceNow-AI/EnterpriseOps-Gym"))
    return build_enterpriseops_gym_benchmark(
        source_path=source_path,
        configs_folder=configs_folder,
        domain=domain,
        mode=mode,
        hf_dataset=hf_dataset,
        task_ids=task_ids,
        options=options,
    )


def _build_osworld_from_config(
    config: BenchmarkConfig,
    task_ids: tuple[str, ...],
) -> BenchmarkAdapter:
    options = dict(config.options)
    source_path = _path_option(options.pop("source_path", None)) or config.dataset_path
    test_all_meta_path = _path_option(options.pop("test_all_meta_path", None))
    domain = str(options.pop("domain", "all"))
    return build_osworld_benchmark(
        source_path=source_path,
        test_all_meta_path=test_all_meta_path,
        domain=domain,
        task_ids=task_ids,
        options=options,
    )


def _path_option(value: object) -> Path | None:
    if value in (None, ""):
        return None
    return Path(str(value))


def _looks_like_python_source_tree(path: Path, package_name: str) -> bool:
    return (
        (path / package_name).exists()
        or (path / "src" / package_name).exists()
        or (path / "pyproject.toml").exists()
    )
