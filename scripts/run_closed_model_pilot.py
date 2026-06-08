#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from recovery_bench.agents.registry import default_agent_registry
from recovery_bench.config import ExperimentSpec, load_experiment_spec
from recovery_bench.experiment import write_result_bundle
from recovery_bench.protocol import ProtocolRunner
from recovery_bench.registry import default_benchmark_registry
from recovery_bench.types import ProtocolMode


MODEL_CONFIGS = {
    "gpt55": {
        "appworld": Path("configs/appworld.codex_gpt55.toml"),
        "tau-bench": Path("configs/tau_bench.codex_gpt55.toml"),
        "enterpriseops-gym": Path("configs/enterpriseops_gym.codex_gpt55.toml"),
    },
    "sonnet": {
        "appworld": Path("configs/appworld.claude_sonnet.toml"),
        "tau-bench": Path("configs/tau_bench.claude_sonnet.toml"),
        "enterpriseops-gym": Path("configs/enterpriseops_gym.claude_sonnet.toml"),
    },
    "opus": {
        "appworld": Path("configs/appworld.claude_opus.toml"),
        "tau-bench": Path("configs/tau_bench.claude_opus.toml"),
        "enterpriseops-gym": Path("configs/enterpriseops_gym.claude_opus.toml"),
    },
}

PILOT_TASKS = {
    "appworld": "5238afc_1",
    "tau-bench": "0",
    "enterpriseops-gym": "task_20251121_102744_757_7ebc1127_dadb0c94",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run low-cost closed-model Recovery Bench pilots.")
    parser.add_argument("--model", action="append", choices=sorted(MODEL_CONFIGS), default=None)
    parser.add_argument("--benchmark", action="append", choices=sorted(PILOT_TASKS), default=None)
    parser.add_argument("--output-root", type=Path, default=Path("runs/pilot/closed-models"))
    parser.add_argument("--max-steps", type=int, default=1)
    parser.add_argument("--mode", choices=[mode.value for mode in ProtocolMode], default=ProtocolMode.SUCCESS.value)
    parser.add_argument("--k", type=int, default=1)
    args = parser.parse_args()

    models = args.model or sorted(MODEL_CONFIGS)
    benchmarks = args.benchmark or sorted(PILOT_TASKS)
    failures: list[tuple[str, str, str]] = []

    for model_key in models:
        for benchmark_name in benchmarks:
            config_path = MODEL_CONFIGS[model_key][benchmark_name]
            spec = _pilot_spec(
                load_experiment_spec(config_path),
                task_id=PILOT_TASKS[benchmark_name],
                output_dir=args.output_root / benchmark_name / model_key,
                max_steps=args.max_steps,
            )
            try:
                results = _run_one(spec, mode=ProtocolMode(args.mode), k=args.k)
            except Exception as exc:
                failures.append((benchmark_name, model_key, f"{type(exc).__name__}: {exc}"))
                print(f"{benchmark_name}/{model_key}: FAIL {type(exc).__name__}: {exc}", flush=True)
                continue
            statuses = [
                f"{result.protocol}@{result.k}:success={result.success}:attempts={len(result.attempts)}"
                for result in results
            ]
            print(f"{benchmark_name}/{model_key}: {'; '.join(statuses)} -> {spec.output_dir}", flush=True)

    if failures:
        print(f"failures={failures}", flush=True)
        return 1
    return 0


def _pilot_spec(spec: ExperimentSpec, *, task_id: str, output_dir: Path, max_steps: int) -> ExperimentSpec:
    return replace(
        spec,
        task_ids=(task_id,),
        output_dir=output_dir,
        agent_options={**spec.agent_options, "max_steps": max_steps},
        k_values=(1,),
    )


def _run_one(spec: ExperimentSpec, *, mode: ProtocolMode, k: int):
    config = spec.to_config()
    benchmark_registry = default_benchmark_registry()
    agent_registry = default_agent_registry()
    benchmark = benchmark_registry.build(spec.benchmark_name, config=config.benchmark, task_ids=spec.task_ids)
    agent = agent_registry.build(spec.agent_name, model_config=config.model, agent_config=config.agent)
    runner = ProtocolRunner(benchmark=benchmark, agent=agent, config=config)
    results = runner.run_all(mode, k=k)
    write_result_bundle(spec.output_dir, runner, results)
    return results


if __name__ == "__main__":
    raise SystemExit(main())
