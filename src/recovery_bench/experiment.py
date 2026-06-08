from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from .io import dump_json
from .plugins import agent_capabilities, benchmark_capabilities
from .protocol import ProtocolRunner
from .reporting import (
    aggregate_main_table,
    aggregate_results,
    write_aggregate_csv,
    write_main_csv,
    write_main_report,
    write_report,
)
from .types import BenchmarkResult, ProtocolMode


DEFAULT_K_VALUES = (1, 2, 3)


def write_result_bundle(output_dir: Path, runner: ProtocolRunner, results: list[BenchmarkResult]) -> None:
    """Write artifacts, aggregate tables, CSVs, and run manifest."""

    runner.artifacts = list(results)
    runner.save_results(output_dir / "artifacts")
    aggregate_rows = aggregate_results(results)
    main_rows = aggregate_main_table(results)
    write_report(output_dir / "summary.md", aggregate_rows)
    write_main_report(output_dir / "main.md", main_rows)
    write_aggregate_csv(output_dir / "summary.csv", aggregate_rows)
    write_main_csv(output_dir / "main.csv", main_rows)
    dump_json(output_dir / "manifest.json", build_manifest(runner, results))


def build_manifest(runner: ProtocolRunner, results: list[BenchmarkResult]) -> dict:
    config = runner.config
    return {
        "benchmark": {
            "name": config.benchmark.name,
            "import_path": config.benchmark.import_path,
            "dataset_path": config.benchmark.dataset_path,
            "eval_path": config.benchmark.eval_path,
            "options": config.benchmark.options,
            "capabilities": benchmark_capabilities(runner.benchmark),
        },
        "agent": {
            "name": runner.agent.name,
            "config_name": config.agent.name,
            "import_path": config.agent.import_path,
            "options": config.agent.options,
            "capabilities": agent_capabilities(runner.agent),
        },
        "model": {
            "name": config.model.name,
            "provider": config.model.provider,
            "options": config.model.options,
        },
        "k_values": config.k_values,
        "task_ids": config.task_ids,
        "output_dir": config.output_dir,
        "results": [
            {
                "task_id": result.task_id,
                "protocol": result.protocol,
                "k": result.k,
                "success": result.success,
                "attempts": len(result.attempts),
            }
            for result in results
        ],
    }


@dataclass(slots=True)
class ExperimentSuite:
    """Run the standard Recovery@k comparison suite."""

    runner: ProtocolRunner

    def run(
        self,
        *,
        k_values: tuple[int, ...] = DEFAULT_K_VALUES,
        incremental_output_dir: Path | None = None,
    ) -> list[BenchmarkResult]:
        normalized = tuple(sorted(set(k_values)))
        if not normalized:
            raise ValueError("k_values must not be empty")
        if normalized[0] < 1:
            raise ValueError("k_values must all be >= 1")

        max_k = max(normalized)
        if incremental_output_dir is not None:
            return self._run_incremental(k_values=normalized, max_k=max_k, output_dir=incremental_output_dir)

        max_results = self.runner.run_comparison_all(k=max_k)
        return self._expand_k_values(max_results, normalized)

    def _run_incremental(
        self,
        *,
        k_values: tuple[int, ...],
        max_k: int,
        output_dir: Path,
    ) -> list[BenchmarkResult]:
        results: list[BenchmarkResult] = []
        task_ids = self.runner.config.task_ids or tuple(self.runner.benchmark.list_tasks())
        for task_id in task_ids:
            try:
                task_results = self.runner.run_comparison_task(task_id, k=max_k)
                results.extend(self._expand_k_values(task_results, k_values))
                write_result_bundle(output_dir, self.runner, results)
            finally:
                self.runner._close_benchmark()
        self.runner.artifacts = list(results)
        return results

    @staticmethod
    def _expand_k_values(
        max_results: tuple[BenchmarkResult, ...] | list[BenchmarkResult],
        k_values: tuple[int, ...],
    ) -> list[BenchmarkResult]:
        results: list[BenchmarkResult] = []
        for result in max_results:
            if result.protocol == ProtocolMode.SUCCESS.value:
                if 1 in k_values:
                    results.append(result)
                continue
            for k in k_values:
                if k == 1:
                    continue
                results.append(derive_result_at_k(result, k))
        return results

    def write_reports(self, output_dir: Path, results: list[BenchmarkResult]) -> None:
        write_result_bundle(output_dir, self.runner, results)


def derive_results_at_k(results: list[BenchmarkResult], k: int) -> list[BenchmarkResult]:
    return [derive_result_at_k(result, k) for result in results]


def derive_result_at_k(result: BenchmarkResult, k: int) -> BenchmarkResult:
    if k < 1:
        raise ValueError("k must be >= 1")
    if k > result.k:
        raise ValueError(f"Cannot derive {result.protocol}@{k} from {result.protocol}@{result.k}")

    attempts = tuple(attempt for attempt in result.attempts if attempt.attempt_index <= k)
    success = any(attempt.outcome is not None and attempt.outcome.success for attempt in attempts)
    metadata = dict(result.metadata)
    if k != result.k:
        metadata["derived_from_k"] = result.k
    return replace(result, attempts=attempts, success=success, k=k, metadata=metadata)
