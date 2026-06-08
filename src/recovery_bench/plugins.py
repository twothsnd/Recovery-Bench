from __future__ import annotations

import importlib
import inspect
from dataclasses import asdict, is_dataclass
from typing import Any

from .config import AgentConfig, BenchmarkConfig, ModelConfig
from .types import AgentAdapter, AgentCapabilities, BenchmarkAdapter, BenchmarkCapabilities


def load_object(import_path: str) -> Any:
    """Load an object from ``module:attribute`` or ``module.attribute``."""

    if not import_path:
        raise ValueError("import_path must be a non-empty string")
    module_name, separator, attribute = import_path.partition(":")
    if not separator:
        module_name, separator, attribute = import_path.rpartition(".")
    if not module_name or not attribute:
        raise ValueError(
            "import_path must use 'module:attribute' or 'module.attribute' format"
        )
    module = importlib.import_module(module_name)
    value: Any = module
    for part in attribute.split("."):
        value = getattr(value, part)
    return value


def build_benchmark_from_import_path(
    import_path: str,
    config: BenchmarkConfig,
    task_ids: tuple[str, ...],
) -> BenchmarkAdapter:
    """Instantiate a benchmark adapter from an external factory or class."""

    target = load_object(import_path)
    if hasattr(target, "from_config") and callable(target.from_config):
        return target.from_config(config=config, task_ids=task_ids)
    if inspect.isclass(target):
        return _call_with_supported_kwargs(target, config=config, task_ids=task_ids)
    if callable(target):
        return _call_with_supported_kwargs(target, config=config, task_ids=task_ids)
    raise TypeError(f"Benchmark import path did not resolve to a callable: {import_path}")


def build_agent_from_import_path(
    import_path: str,
    model_config: ModelConfig,
    agent_config: AgentConfig,
) -> AgentAdapter:
    """Instantiate an agent adapter from an external factory or class."""

    target = load_object(import_path)
    if hasattr(target, "from_config") and callable(target.from_config):
        return target.from_config(model_config=model_config, agent_config=agent_config)
    if inspect.isclass(target):
        return _call_with_supported_kwargs(
            target,
            model_config=model_config,
            agent_config=agent_config,
        )
    if callable(target):
        return _call_with_supported_kwargs(
            target,
            model_config=model_config,
            agent_config=agent_config,
        )
    raise TypeError(f"Agent import path did not resolve to a callable: {import_path}")


def benchmark_capabilities(adapter: Any) -> dict[str, Any]:
    return _capabilities_dict(adapter, BenchmarkCapabilities())


def agent_capabilities(adapter: Any) -> dict[str, Any]:
    return _capabilities_dict(adapter, AgentCapabilities())


def _capabilities_dict(adapter: Any, default: Any) -> dict[str, Any]:
    value = getattr(adapter, "capabilities", None)
    if callable(value):
        value = value()
    if value is None:
        value = default
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        merged = asdict(default)
        merged.update(value)
        return merged
    raise TypeError(f"capabilities must be a dataclass, dict, or callable, got {type(value).__name__}")


def _call_with_supported_kwargs(callable_obj: Any, **kwargs: Any) -> Any:
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return callable_obj(**kwargs)
    parameters = signature.parameters
    if any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        return callable_obj(**kwargs)
    supported = {key: value for key, value in kwargs.items() if key in parameters}
    if supported:
        return callable_obj(**supported)
    return callable_obj()
