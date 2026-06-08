from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

from .agents.registry import default_agent_registry
from .config import (
    AgentConfig,
    BenchmarkConfig,
    ExperimentSpec,
    ModelConfig,
    load_experiment_spec,
    merge_spec_overrides,
)
from .conformance import format_conformance_report, run_basic_benchmark_conformance
from .experiment import DEFAULT_K_VALUES, ExperimentSuite, write_result_bundle
from .io import to_jsonable
from .registry import BenchmarkRegistry, default_benchmark_registry
from .types import AgentAdapter, BenchmarkAdapter, ProtocolMode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="recovery-bench")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list-benchmarks", help="List registered benchmark adapters")
    subparsers.add_parser("list-agents", help="List registered agent adapters")

    check_parser = subparsers.add_parser("check-benchmark", help="Run basic conformance checks for one benchmark adapter")
    check_parser.add_argument("--config", type=Path, default=None)
    check_parser.add_argument("--benchmark", default=None)
    check_parser.add_argument("--benchmark-import-path", default=None)
    check_parser.add_argument("--task-id", default=None)
    check_parser.add_argument("--json", action="store_true")

    run_parser = subparsers.add_parser("run", help="Run Recovery@k or Retry@k experiments")
    _add_common_experiment_args(run_parser)
    run_parser.add_argument("--mode", choices=[mode.value for mode in ProtocolMode], required=True)
    run_parser.add_argument("--k", type=int, default=None)

    suite_parser = subparsers.add_parser("suite", help="Run Success@1 plus Retry/Recovery for k values")
    _add_common_experiment_args(suite_parser)
    suite_parser.add_argument("--k", type=int, action="append", default=None)
    return parser


def _add_common_experiment_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--benchmark", default=None)
    parser.add_argument("--benchmark-import-path", default=None)
    parser.add_argument("--agent", default=None)
    parser.add_argument("--agent-import-path", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--task-id", action="append", default=None)


def _build_registry() -> BenchmarkRegistry:
    return default_benchmark_registry()


def _resolve_benchmark(
    registry: BenchmarkRegistry,
    benchmark_name: str,
    config: BenchmarkConfig,
    task_ids: tuple[str, ...],
) -> BenchmarkAdapter:
    if config.import_path:
        try:
            return registry.build(benchmark_name, config=config, task_ids=task_ids)
        except (ImportError, AttributeError, TypeError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
    if benchmark_name not in registry.known():
        raise SystemExit(
            f"Unknown benchmark '{benchmark_name}'. Use benchmark.import_path or --benchmark-import-path "
            f"for external adapters. Known: {registry.known()}"
        )
    try:
        return registry.build(benchmark_name, config=config, task_ids=task_ids)
    except NotImplementedError as exc:
        raise SystemExit(str(exc)) from exc


def _resolve_agent(
    agent_name: str,
    model_config: ModelConfig,
    agent_config: AgentConfig,
) -> AgentAdapter:
    registry = default_agent_registry()
    if agent_config.import_path:
        try:
            return registry.build(agent_name, model_config=model_config, agent_config=agent_config)
        except (ImportError, AttributeError, TypeError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
    if agent_name not in registry.known():
        raise SystemExit(
            f"Unknown agent '{agent_name}'. Use agent.import_path or --agent-import-path "
            f"for external adapters. Known: {registry.known()}"
        )
    try:
        return registry.build(agent_name, model_config=model_config, agent_config=agent_config)
    except (NotImplementedError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    benchmark_registry = _build_registry()
    agent_registry = default_agent_registry()

    if args.command == "list-benchmarks":
        for benchmark, status, reason in benchmark_registry.describe():
            suffix = f" - {reason}" if reason else ""
            print(f"{benchmark}\t{status}{suffix}")
        return

    if args.command == "list-agents":
        for agent, status, reason in agent_registry.describe():
            suffix = f" - {reason}" if reason else ""
            print(f"{agent}\t{status}{suffix}")
        return

    if args.command == "check-benchmark":
        benchmark_config, task_ids, task_id = _resolve_benchmark_check_args(args)
        benchmark = _resolve_benchmark(
            benchmark_registry,
            benchmark_config.name,
            benchmark_config,
            task_ids,
        )
        try:
            report = run_basic_benchmark_conformance(benchmark, task_id=task_id)
        finally:
            close = getattr(benchmark, "close", None)
            if callable(close):
                close()
        if args.json:
            print(json.dumps(to_jsonable(report), ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print(format_conformance_report(report))
        raise SystemExit(0 if report.passed else 1)

    if args.command in {"run", "suite"}:
        spec = _resolve_spec(args)
        config = spec.to_config()
        benchmark = _resolve_benchmark(
            benchmark_registry,
            spec.benchmark_name,
            config.benchmark,
            spec.task_ids,
        )
        agent = _resolve_agent(spec.agent_name, config.model, config.agent)
        from .protocol import ProtocolRunner

        runner = ProtocolRunner(benchmark=benchmark, agent=agent, config=config)
        if args.command == "suite":
            suite = ExperimentSuite(runner)
            results = suite.run(k_values=spec.k_values, incremental_output_dir=spec.output_dir)
            suite.write_reports(spec.output_dir, results)
            return

        k = spec.k_values[0]
        results = runner.run_all(ProtocolMode(args.mode), k=k)
        write_result_bundle(spec.output_dir, runner, results)


def _resolve_spec(args: argparse.Namespace) -> ExperimentSpec:
    try:
        base = load_experiment_spec(args.config) if args.config else None
        k_values = _resolve_k_values(args, base)
        return merge_spec_overrides(
            base,
            benchmark_name=args.benchmark,
            benchmark_import_path=args.benchmark_import_path,
            agent_name=args.agent,
            agent_import_path=args.agent_import_path,
            model_name=args.model,
            provider=args.provider,
            k_values=k_values,
            task_ids=tuple(args.task_id) if args.task_id is not None else None,
            output_dir=args.output_dir,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def _resolve_benchmark_check_args(args: argparse.Namespace) -> tuple[BenchmarkConfig, tuple[str, ...], str | None]:
    try:
        if args.config:
            spec = load_experiment_spec(args.config)
            config = spec.to_config()
            benchmark_config = config.benchmark
            task_ids = config.task_ids
        else:
            if not args.benchmark:
                raise ValueError("check-benchmark requires --config or --benchmark")
            benchmark_config = BenchmarkConfig(name=str(args.benchmark), import_path=args.benchmark_import_path)
            task_ids = ()

        if args.benchmark:
            benchmark_config = replace(benchmark_config, name=str(args.benchmark))
        if args.benchmark_import_path:
            benchmark_config = replace(benchmark_config, import_path=str(args.benchmark_import_path))

        task_id = str(args.task_id) if args.task_id else (task_ids[0] if task_ids else None)
        build_task_ids = (task_id,) if task_id else task_ids
        return benchmark_config, build_task_ids, task_id
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def _resolve_k_values(args: argparse.Namespace, base: ExperimentSpec | None) -> tuple[int, ...] | None:
    if args.command == "suite":
        if args.k:
            return tuple(args.k)
        if base is None:
            return DEFAULT_K_VALUES
        return None

    if args.k is not None:
        return (args.k,)
    if base is not None:
        return (max(base.k_values),)
    return (3,)


if __name__ == "__main__":
    main()
