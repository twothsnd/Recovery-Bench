from pathlib import Path

from recovery_bench.config import load_experiment_spec, merge_spec_overrides


def test_load_experiment_spec_from_toml() -> None:
    spec = load_experiment_spec(Path("configs/progress_smoke.toml"))
    assert spec.benchmark_name == "progress-smoke"
    assert spec.agent_name == "progress-smoke-agent"
    assert spec.model_name == "smoke-model"
    assert spec.provider == "local"
    assert spec.k_values == (1, 2, 3)
    assert spec.task_ids == ("progress-1",)


def test_merge_spec_overrides_replaces_selected_fields() -> None:
    spec = load_experiment_spec(Path("configs/progress_smoke.toml"))
    merged = merge_spec_overrides(
        spec,
        model_name="other-model",
        k_values=(2,),
        output_dir=Path("runs/override"),
    )
    assert merged.benchmark_name == "progress-smoke"
    assert merged.model_name == "other-model"
    assert merged.k_values == (2,)
    assert merged.output_dir == Path("runs/override")


def test_load_experiment_spec_supports_external_import_paths(tmp_path: Path) -> None:
    config_path = tmp_path / "external.toml"
    config_path.write_text(
        """
[experiment]
output_dir = "runs/external"
k_values = [1, 2]
task_ids = ["task-a"]

[benchmark]
name = "external-benchmark"
import_path = "lab_bench.adapters:build_benchmark"

[benchmark.options]
dataset_path = "external/lab-bench"
domain = "desktop"

[agent]
name = "external-agent"
import_path = "lab_agents.example_agent:build_agent"

[agent.options]
max_steps = 50

[model]
name = "example-chat-model"
provider = "vllm"
""",
        encoding="utf-8",
    )

    spec = load_experiment_spec(config_path)
    config = spec.to_config()

    assert spec.benchmark_import_path == "lab_bench.adapters:build_benchmark"
    assert spec.agent_import_path == "lab_agents.example_agent:build_agent"
    assert config.benchmark.import_path == "lab_bench.adapters:build_benchmark"
    assert config.agent.import_path == "lab_agents.example_agent:build_agent"
    assert config.benchmark.dataset_path == Path("external/lab-bench")
    assert config.benchmark.options == {"domain": "desktop"}
    assert config.agent.options == {"max_steps": 50}


def test_merge_spec_overrides_can_set_import_paths_without_registered_names() -> None:
    merged = merge_spec_overrides(
        None,
        benchmark_name="external-benchmark",
        benchmark_import_path="lab_benchmark:build_benchmark",
        agent_name="external-agent",
        agent_import_path="lab_agent:build_agent",
        model_name="external-model",
        provider="local",
    )

    config = merged.to_config()

    assert config.benchmark.name == "external-benchmark"
    assert config.benchmark.import_path == "lab_benchmark:build_benchmark"
    assert config.agent.name == "external-agent"
    assert config.agent.import_path == "lab_agent:build_agent"
