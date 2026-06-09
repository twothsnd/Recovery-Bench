import sys
import types
from pathlib import Path

from recovery_bench.agents.registry import AgentRegistry, default_agent_registry
from recovery_bench.config import AgentConfig, BenchmarkConfig, ModelConfig
from recovery_bench.registry import BenchmarkRegistry, default_benchmark_registry


def test_registry_build_passes_config_and_task_ids_to_factory() -> None:
    captured = {}
    registry = BenchmarkRegistry()

    def factory(config: BenchmarkConfig, task_ids: tuple[str, ...]):
        captured["config"] = config
        captured["task_ids"] = task_ids
        return object()

    config = BenchmarkConfig(
        name="custom",
        dataset_path=Path("external/custom/src"),
        options={"domain": "airline"},
    )
    registry.register("custom", factory)

    adapter = registry.build("custom", config=config, task_ids=("task-1",))

    assert adapter is not None
    assert captured["config"] == config
    assert captured["task_ids"] == ("task-1",)


def test_benchmark_registry_can_build_external_import_path(monkeypatch) -> None:
    captured = {}
    module = types.ModuleType("tests_external_benchmark_plugin")

    def build_benchmark(config: BenchmarkConfig, task_ids: tuple[str, ...]):
        captured["config"] = config
        captured["task_ids"] = task_ids
        return {"adapter": "external-benchmark"}

    module.build_benchmark = build_benchmark  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)

    config = BenchmarkConfig(
        name="not-registered",
        import_path="tests_external_benchmark_plugin:build_benchmark",
        options={"domain": "example"},
    )

    adapter = BenchmarkRegistry().build("not-registered", config=config, task_ids=("task-9",))

    assert adapter == {"adapter": "external-benchmark"}
    assert captured["config"] == config
    assert captured["task_ids"] == ("task-9",)


def test_agent_registry_can_build_external_import_path(monkeypatch) -> None:
    captured = {}
    module = types.ModuleType("tests_external_agent_plugin")

    def build_agent(model_config: ModelConfig, agent_config: AgentConfig):
        captured["model_config"] = model_config
        captured["agent_config"] = agent_config
        return types.SimpleNamespace(name="external-agent")

    module.build_agent = build_agent  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, module.__name__, module)

    model_config = ModelConfig(name="example-chat-model", provider="vllm")
    agent_config = AgentConfig(
        name="not-registered-agent",
        import_path="tests_external_agent_plugin:build_agent",
        options={"max_steps": 50},
    )

    adapter = AgentRegistry().build(
        "not-registered-agent",
        model_config=model_config,
        agent_config=agent_config,
    )

    assert adapter.name == "external-agent"
    assert captured["model_config"] == model_config
    assert captured["agent_config"] == agent_config


def test_default_agent_registry_only_contains_smoke_agent() -> None:
    rows = {name: status for name, status, _reason in default_agent_registry().describe()}
    assert rows == {"progress-smoke-agent": "available"}


def test_real_benchmark_names_are_registered_with_dependency_status() -> None:
    rows = {name: status for name, status, _reason in default_benchmark_registry().describe()}
    assert rows == {
        "placeholder": "available",
        "progress-smoke": "available",
    }
