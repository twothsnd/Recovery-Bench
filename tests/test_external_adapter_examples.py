from pathlib import Path

from recovery_bench.agents.registry import default_agent_registry
from recovery_bench.config import load_experiment_spec
from recovery_bench.experiment import ExperimentSuite
from recovery_bench.protocol import ProtocolRunner
from recovery_bench.registry import default_benchmark_registry


def test_external_minimal_adapter_runs_without_core_registry_changes() -> None:
    spec = load_experiment_spec(Path("configs/external_minimal_adapter.example.toml"))
    config = spec.to_config()
    benchmark = default_benchmark_registry().build(spec.benchmark_name, config.benchmark, spec.task_ids)
    agent = default_agent_registry().build(spec.agent_name, config.model, config.agent)

    results = ExperimentSuite(ProtocolRunner(benchmark=benchmark, agent=agent, config=config)).run(k_values=(1, 2))
    by_protocol = {(result.protocol, result.k): result for result in results}

    assert by_protocol[("success", 1)].success is False
    assert by_protocol[("retry", 2)].success is False
    assert by_protocol[("recovery", 2)].success is True
    assert by_protocol[("recovery", 2)].metadata["benchmark_capabilities"]["strict_recovery"] is True
