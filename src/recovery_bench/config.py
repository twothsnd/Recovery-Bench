from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import tomllib


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    """Static configuration for one benchmark integration."""

    name: str
    import_path: str | None = None
    dataset_path: Path | None = None
    eval_path: Path | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModelConfig:
    """Opaque model metadata forwarded to agent adapters."""

    name: str
    provider: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentConfig:
    """Agent adapter configuration."""

    name: str
    import_path: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExperimentConfig:
    """Top-level experiment settings."""

    benchmark: BenchmarkConfig
    model: ModelConfig
    agent: AgentConfig = field(default_factory=lambda: AgentConfig(name=""))
    k_values: tuple[int, ...] = (1, 2, 3)
    task_ids: tuple[str, ...] = ()
    output_dir: Path = Path("runs")
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ExperimentSpec:
    """CLI-facing experiment specification."""

    benchmark_name: str
    agent_name: str
    model_name: str
    provider: str
    benchmark_import_path: str | None = None
    agent_import_path: str | None = None
    k_values: tuple[int, ...] = (1, 2, 3)
    task_ids: tuple[str, ...] = ()
    output_dir: Path = Path("runs")
    benchmark_options: dict[str, Any] = field(default_factory=dict)
    agent_options: dict[str, Any] = field(default_factory=dict)
    model_options: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)

    def to_config(self) -> ExperimentConfig:
        return ExperimentConfig(
            benchmark=BenchmarkConfig(
                name=self.benchmark_name,
                import_path=self.benchmark_import_path,
                dataset_path=_optional_path(self.benchmark_options.get("dataset_path")),
                eval_path=_optional_path(self.benchmark_options.get("eval_path")),
                options=_drop_path_options(self.benchmark_options),
            ),
            model=ModelConfig(
                name=self.model_name,
                provider=self.provider,
                options=dict(self.model_options),
            ),
            agent=AgentConfig(
                name=self.agent_name,
                import_path=self.agent_import_path,
                options=dict(self.agent_options),
            ),
            k_values=self.k_values,
            task_ids=self.task_ids,
            output_dir=self.output_dir,
            options=self.options,
        )


def load_experiment_spec(path: Path) -> ExperimentSpec:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    experiment = _table(data, "experiment")
    benchmark = _table(data, "benchmark")
    agent = _table(data, "agent")
    model = _table(data, "model")

    return ExperimentSpec(
        benchmark_name=_required_str(benchmark, "name", "benchmark.name"),
        benchmark_import_path=_optional_str(benchmark.get("import_path"), "benchmark.import_path"),
        agent_name=_required_str(agent, "name", "agent.name"),
        agent_import_path=_optional_str(agent.get("import_path"), "agent.import_path"),
        model_name=_required_str(model, "name", "model.name"),
        provider=_required_str(model, "provider", "model.provider"),
        k_values=_int_tuple(experiment.get("k_values", (1, 2, 3)), "experiment.k_values"),
        task_ids=_str_tuple(experiment.get("task_ids", ()), "experiment.task_ids"),
        output_dir=Path(str(experiment.get("output_dir", "runs"))),
        benchmark_options=dict(benchmark.get("options", {})),
        agent_options=dict(agent.get("options", {})),
        model_options=dict(model.get("options", {})),
        options=dict(experiment.get("options", {})),
    )


def merge_spec_overrides(
    base: ExperimentSpec | None,
    *,
    benchmark_name: str | None = None,
    agent_name: str | None = None,
    model_name: str | None = None,
    provider: str | None = None,
    benchmark_import_path: str | None = None,
    agent_import_path: str | None = None,
    k_values: tuple[int, ...] | None = None,
    task_ids: tuple[str, ...] | None = None,
    output_dir: Path | None = None,
) -> ExperimentSpec:
    if base is None:
        missing = [
            name
            for name, value in {
                "benchmark": benchmark_name,
                "agent": agent_name,
                "model": model_name,
                "provider": provider,
            }.items()
            if value is None
        ]
        if missing:
            raise ValueError(f"Missing required fields without --config: {', '.join(missing)}")
        base = ExperimentSpec(
            benchmark_name=str(benchmark_name),
            agent_name=str(agent_name),
            model_name=str(model_name),
            provider=str(provider),
        )

    return ExperimentSpec(
        benchmark_name=benchmark_name or base.benchmark_name,
        agent_name=agent_name or base.agent_name,
        model_name=model_name or base.model_name,
        provider=provider or base.provider,
        benchmark_import_path=benchmark_import_path or base.benchmark_import_path,
        agent_import_path=agent_import_path or base.agent_import_path,
        k_values=k_values if k_values is not None else base.k_values,
        task_ids=task_ids if task_ids is not None else base.task_ids,
        output_dir=output_dir or base.output_dir,
        benchmark_options=base.benchmark_options,
        agent_options=base.agent_options,
        model_options=base.model_options,
        options=base.options,
    )


def _table(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a table")
    return value


def _required_str(data: dict[str, Any], key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _optional_str(value: Any, label: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value


def _str_tuple(value: Any, label: str) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, list | tuple):
        raise ValueError(f"{label} must be a list of strings")
    if not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a list of strings")
    return tuple(value)


def _int_tuple(value: Any, label: str) -> tuple[int, ...]:
    if not isinstance(value, list | tuple):
        raise ValueError(f"{label} must be a list of integers")
    if not all(isinstance(item, int) for item in value):
        raise ValueError(f"{label} must be a list of integers")
    return tuple(value)


def _optional_path(value: Any) -> Path | None:
    if value in (None, ""):
        return None
    return Path(str(value))


def _drop_path_options(options: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in options.items() if key not in {"dataset_path", "eval_path"}}
