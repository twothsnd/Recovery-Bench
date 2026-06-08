#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from recovery_bench.agents.registry import default_agent_registry
from recovery_bench.config import ExperimentSpec, load_experiment_spec
from recovery_bench.errors import FatalRunError
from recovery_bench.experiment import derive_result_at_k, write_result_bundle
from recovery_bench.protocol import ProtocolRunner
from recovery_bench.registry import default_benchmark_registry
from recovery_bench.types import BenchmarkResult, ProtocolMode


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
    "sonnet46": {
        "appworld": Path("configs/appworld.claude_sonnet46.toml"),
        "tau-bench": Path("configs/tau_bench.claude_sonnet46.toml"),
        "enterpriseops-gym": Path("configs/enterpriseops_gym.claude_sonnet46.toml"),
    },
    "opus": {
        "appworld": Path("configs/appworld.claude_opus.toml"),
        "tau-bench": Path("configs/tau_bench.claude_opus.toml"),
        "enterpriseops-gym": Path("configs/enterpriseops_gym.claude_opus.toml"),
    },
    "opus46": {
        "appworld": Path("configs/appworld.claude_opus46.toml"),
        "tau-bench": Path("configs/tau_bench.claude_opus46.toml"),
        "enterpriseops-gym": Path("configs/enterpriseops_gym.claude_opus46.toml"),
    },
}


@dataclass(frozen=True, slots=True)
class SuiteEntry:
    benchmark_name: str
    label: str
    benchmark_options: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TaskSelection:
    entry: SuiteEntry
    task_ids: tuple[str, ...]
    available_count: int


APPWORLD_FULL_DATASETS = ("train", "dev", "test_normal", "test_challenge")
APPWORLD_OFFICIAL_DATASETS = ("test_normal", "test_challenge")
TAU_BENCH_FULL_TASK_SETS = (
    ("airline", "airline", "airline"),
    ("retail", "retail", "retail"),
    ("telecom", "telecom", "telecom"),
    ("telecom-workflow", "telecom-workflow", "telecom-workflow"),
    ("banking_knowledge", "banking_knowledge", "banking_knowledge"),
    ("telecom_full", "telecom", "telecom_full"),
    ("telecom_small", "telecom", "telecom_small"),
)
TAU_BENCH_OFFICIAL_TASK_SETS = (
    ("airline", "airline", "airline"),
    ("retail", "retail", "retail"),
    ("telecom", "telecom", "telecom"),
    ("banking_knowledge", "banking_knowledge", "banking_knowledge"),
)
ENTERPRISEOPS_DOMAINS = ("calendar", "csm", "drive", "email", "hr", "hybrid", "itsm", "teams")
ENTERPRISEOPS_OFFICIAL_MODES = ("oracle",)
ENTERPRISEOPS_FULL_MODES = ("oracle", "plus_5_tools", "plus_10_tools", "plus_15_tools")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run closed-model Recovery@k batches.")
    parser.add_argument("--model", action="append", choices=sorted(MODEL_CONFIGS), default=None)
    parser.add_argument("--benchmark", action="append", choices=sorted(next(iter(MODEL_CONFIGS.values()))), default=None)
    parser.add_argument(
        "--task-count",
        default=None,
        help="Optional pilot limit. Omit, or pass 'all'/'full', to run every task.",
    )
    parser.add_argument(
        "--benchmark-task-count",
        type=int,
        default=None,
        help=(
            "Optional total task cap per benchmark. For expanded suites this is allocated "
            "proportionally across split/domain entries and sampled deterministically."
        ),
    )
    parser.add_argument(
        "--sample-seed",
        default="recovery-bench-quick100-v1",
        help="Stable seed used for deterministic benchmark-level sampling.",
    )
    parser.add_argument("--output-root", type=Path, default=Path("runs/closed-model-batch"))
    parser.add_argument("--k", type=int, action="append", default=None)
    parser.add_argument("--max-steps", type=int, default=None, help="Optional agent max_steps override.")
    parser.add_argument(
        "--suite",
        choices=("configured", "official", "full"),
        default="configured",
        help=(
            "Task matrix to run. 'configured' preserves each config file's single split/domain; "
            "'official' runs standard local eval sets; 'full' runs every downloaded local task set/split."
        ),
    )
    parser.add_argument("--dry-run", action="store_true", help="Print selected jobs and task counts without running.")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip a model/subset combination when its output bundle already exists.",
    )
    args = parser.parse_args()
    if args.task_count is not None and args.benchmark_task_count is not None:
        parser.error("--task-count and --benchmark-task-count are mutually exclusive")
    if args.benchmark_task_count is not None and args.benchmark_task_count < 1:
        parser.error("--benchmark-task-count must be >= 1")

    models = args.model or sorted(MODEL_CONFIGS)
    benchmarks = args.benchmark or sorted(next(iter(MODEL_CONFIGS.values())))
    k_values = tuple(args.k or [1, 2, 3])
    report_k_values = tuple(k for k in sorted(set(k_values)) if k > 1)
    failures: list[tuple[str, str, str]] = []

    print(
        f"batch_start models={models} benchmarks={benchmarks} suite={args.suite} "
        f"task_count={args.task_count or 'all'} benchmark_task_count={args.benchmark_task_count or 'all'} "
        f"sample_seed={args.sample_seed!r} k_values={k_values}",
        flush=True,
    )
    for benchmark_name in benchmarks:
        base_spec = load_experiment_spec(MODEL_CONFIGS[models[0]][benchmark_name])
        selections = _suite_task_selections(
            benchmark_name,
            args.suite,
            base_spec,
            task_count=_parse_task_count(args.task_count),
            benchmark_task_count=args.benchmark_task_count,
            sample_seed=args.sample_seed,
        )
        for selection in selections:
            entry = selection.entry
            task_ids = selection.task_ids
            preview = task_ids[:5]
            suffix = "..." if len(task_ids) > len(preview) else ""
            print(
                f"selected_tasks benchmark={benchmark_name} suite_entry={entry.label} "
                f"count={len(task_ids)} available={selection.available_count} "
                f"preview={preview}{suffix}",
                flush=True,
            )
            if args.dry_run:
                continue
            for model_key in models:
                config_path = MODEL_CONFIGS[model_key][benchmark_name]
                spec = _batch_spec(
                    _entry_spec(load_experiment_spec(config_path), entry),
                    task_ids=task_ids,
                    output_dir=_output_dir(args.output_root, benchmark_name, entry, model_key, args.suite),
                    k_values=k_values,
                    max_steps=args.max_steps,
                )
                if args.skip_existing and _output_complete(
                    spec.output_dir,
                    spec.task_ids,
                    report_k_values,
                    expected_agent_options=spec.agent_options,
                ):
                    print(
                        f"combo_skip benchmark={benchmark_name} suite_entry={entry.label} "
                        f"model={model_key} output={spec.output_dir}",
                        flush=True,
                    )
                    continue
                try:
                    results = _run_one(spec, k_values=k_values, suite_entry=entry.label)
                except FatalRunError as exc:
                    print(
                        f"fatal_exit benchmark={benchmark_name} suite_entry={entry.label} "
                        f"model={model_key} error={type(exc).__name__}: {exc}",
                        flush=True,
                    )
                    return 2
                except Exception as exc:
                    failures.append((f"{benchmark_name}/{entry.label}", model_key, f"{type(exc).__name__}: {exc}"))
                    print(
                        f"combo_fail benchmark={benchmark_name} suite_entry={entry.label} "
                        f"model={model_key} error={type(exc).__name__}: {exc}",
                        flush=True,
                    )
                    continue
                successes = sum(1 for result in results if result.success)
                print(
                    f"combo_done benchmark={benchmark_name} suite_entry={entry.label} model={model_key} "
                    f"results={len(results)} successes={successes} output={spec.output_dir}",
                    flush=True,
                )

    print(f"batch_done failures={failures}", flush=True)
    return 1 if failures else 0


def _parse_task_count(value: str | None) -> int | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"", "all", "full"}:
        return None
    task_count = int(normalized)
    if task_count < 1:
        raise ValueError("--task-count must be >= 1, or use 'all'/'full'")
    return task_count


def _suite_entries(benchmark_name: str, suite: str) -> tuple[SuiteEntry, ...]:
    if suite == "configured":
        return (SuiteEntry(benchmark_name=benchmark_name, label="configured", benchmark_options={}),)
    if benchmark_name == "appworld":
        datasets = APPWORLD_OFFICIAL_DATASETS if suite == "official" else APPWORLD_FULL_DATASETS
        return tuple(
            SuiteEntry(benchmark_name=benchmark_name, label=dataset, benchmark_options={"dataset_name": dataset})
            for dataset in datasets
        )
    if benchmark_name == "tau-bench":
        task_sets = TAU_BENCH_OFFICIAL_TASK_SETS if suite == "official" else TAU_BENCH_FULL_TASK_SETS
        return tuple(
            SuiteEntry(
                benchmark_name=benchmark_name,
                label=label,
                benchmark_options={"domain": domain, "task_set_name": task_set_name},
            )
            for label, domain, task_set_name in task_sets
        )
    if benchmark_name == "enterpriseops-gym":
        modes = ENTERPRISEOPS_OFFICIAL_MODES if suite == "official" else ENTERPRISEOPS_FULL_MODES
        return tuple(
            SuiteEntry(
                benchmark_name=benchmark_name,
                label=f"{mode}/{domain}",
                benchmark_options={
                    "mode": mode,
                    "domain": domain,
                    "configs_folder": f"external/enterpriseops-gym/tasks/{mode}/{domain}",
                },
            )
            for mode in modes
            for domain in ENTERPRISEOPS_DOMAINS
        )
    raise ValueError(f"Unknown benchmark for suite expansion: {benchmark_name}")


def _suite_task_selections(
    benchmark_name: str,
    suite: str,
    base_spec: ExperimentSpec,
    *,
    task_count: int | None,
    benchmark_task_count: int | None,
    sample_seed: str,
) -> tuple[TaskSelection, ...]:
    entries = _suite_entries(benchmark_name, suite)
    inventories = tuple(
        (entry, _list_task_ids(_entry_spec(base_spec, entry)))
        for entry in entries
    )
    if benchmark_task_count is None:
        return tuple(
            TaskSelection(
                entry=entry,
                task_ids=task_ids if task_count is None else task_ids[:task_count],
                available_count=len(task_ids),
            )
            for entry, task_ids in inventories
        )

    allocations = _allocate_sample_counts(
        tuple((entry.label, len(task_ids)) for entry, task_ids in inventories),
        benchmark_task_count,
    )
    selections = []
    for (entry, task_ids), count in zip(inventories, allocations, strict=True):
        if count < 1:
            continue
        selections.append(
            TaskSelection(
                entry=entry,
                task_ids=_stable_sample_task_ids(
                    task_ids,
                    count,
                    sample_seed=sample_seed,
                    benchmark_name=benchmark_name,
                    suite_entry=entry.label,
                ),
                available_count=len(task_ids),
            )
        )
    return tuple(selections)


def _allocate_sample_counts(
    entries: tuple[tuple[str, int], ...],
    requested_total: int,
) -> tuple[int, ...]:
    if requested_total < 1:
        raise ValueError("requested_total must be >= 1")
    available_total = sum(count for _label, count in entries)
    if available_total < 1:
        raise RuntimeError("No tasks available for benchmark sampling")
    target_total = min(requested_total, available_total)
    raw_allocations = [
        (index, label, count, (count * target_total) / available_total)
        for index, (label, count) in enumerate(entries)
    ]
    allocations = [min(count, int(raw)) for _index, _label, count, raw in raw_allocations]
    remaining = target_total - sum(allocations)
    for index, _label, count, _raw in sorted(
        raw_allocations,
        key=lambda item: (-(item[3] - int(item[3])), item[1]),
    ):
        if remaining <= 0:
            break
        if allocations[index] >= count:
            continue
        allocations[index] += 1
        remaining -= 1
    return tuple(allocations)


def _entry_spec(spec: ExperimentSpec, entry: SuiteEntry) -> ExperimentSpec:
    benchmark_options = dict(spec.benchmark_options)
    benchmark_options.update(entry.benchmark_options)
    return replace(spec, benchmark_options=benchmark_options)


def _output_dir(
    output_root: Path,
    benchmark_name: str,
    entry: SuiteEntry,
    model_key: str,
    suite: str,
) -> Path:
    if suite == "configured":
        return output_root / benchmark_name / model_key
    return output_root / benchmark_name / Path(entry.label) / model_key


def _select_task_ids(spec: ExperimentSpec, task_count: int | None) -> tuple[str, ...]:
    all_task_ids = _list_task_ids(spec)
    task_ids = all_task_ids if task_count is None else all_task_ids[:task_count]
    if not task_ids:
        raise RuntimeError(f"No tasks selected for {spec.benchmark_name}")
    return task_ids


def _list_task_ids(spec: ExperimentSpec) -> tuple[str, ...]:
    config = spec.to_config()
    registry = default_benchmark_registry()
    benchmark = registry.build(spec.benchmark_name, config=config.benchmark, task_ids=())
    try:
        task_ids = tuple(benchmark.list_tasks())
    finally:
        close = getattr(benchmark, "close", None)
        if callable(close):
            close()
    return task_ids


def _stable_sample_task_ids(
    task_ids: tuple[str, ...],
    count: int,
    *,
    sample_seed: str,
    benchmark_name: str,
    suite_entry: str,
) -> tuple[str, ...]:
    if count >= len(task_ids):
        return task_ids
    ranked = sorted(
        task_ids,
        key=lambda task_id: _stable_sample_key(sample_seed, benchmark_name, suite_entry, task_id),
    )
    return tuple(ranked[:count])


def _stable_sample_key(sample_seed: str, benchmark_name: str, suite_entry: str, task_id: str) -> str:
    payload = "\0".join((sample_seed, benchmark_name, suite_entry, task_id)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _output_complete(
    output_dir: Path,
    task_ids: tuple[str, ...],
    report_k_values: tuple[int, ...],
    expected_agent_options: dict[str, Any] | None = None,
) -> bool:
    """Return true only for a complete, final combo output.

    Task-level checkpointing writes manifest/main files after every completed
    task, so file presence alone is not a safe completion signal.
    """

    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists() or not (output_dir / "main.md").exists():
        return False

    artifacts_dir = output_dir / "artifacts"
    for task_id in task_ids:
        if _load_task_checkpoint(
            artifacts_dir,
            task_id,
            report_k_values,
            expected_agent_options=expected_agent_options,
        ) is None:
            return False

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    expected = _expected_result_keys(task_ids, report_k_values)
    manifest_results = manifest.get("results")
    if not isinstance(manifest_results, list):
        return False
    actual: set[tuple[str, str, int]] = set()
    for result in manifest_results:
        if not isinstance(result, dict):
            return False
        try:
            actual.add((str(result["task_id"]), str(result["protocol"]), int(result["k"])))
        except Exception:
            return False
    return len(manifest_results) == len(expected) and actual == expected


def _batch_spec(
    spec: ExperimentSpec,
    *,
    task_ids: tuple[str, ...],
    output_dir: Path,
    k_values: tuple[int, ...],
    max_steps: int | None,
) -> ExperimentSpec:
    agent_options = dict(spec.agent_options)
    if max_steps is not None:
        agent_options["max_steps"] = max_steps
    return replace(
        spec,
        task_ids=task_ids,
        output_dir=output_dir,
        k_values=k_values,
        agent_options=agent_options,
    )


def _run_one(
    spec: ExperimentSpec,
    *,
    k_values: tuple[int, ...],
    suite_entry: str = "configured",
) -> list[BenchmarkResult]:
    config = spec.to_config()
    benchmark_registry = default_benchmark_registry()
    agent_registry = default_agent_registry()
    benchmark = benchmark_registry.build(spec.benchmark_name, config=config.benchmark, task_ids=spec.task_ids)
    agent = agent_registry.build(spec.agent_name, model_config=config.model, agent_config=config.agent)
    runner = ProtocolRunner(benchmark=benchmark, agent=agent, config=config)

    results: list[BenchmarkResult] = []
    report_k_values = tuple(k for k in sorted(set(k_values)) if k > 1)
    max_k = max((1, *report_k_values))
    output_dir = spec.output_dir
    artifacts_dir = output_dir / "artifacts"

    for task_id in spec.task_ids:
        checkpoint = _load_task_checkpoint(
            artifacts_dir,
            task_id,
            report_k_values,
            expected_agent_options=config.agent.options,
        )
        if checkpoint is not None:
            results.extend(checkpoint)
            print(
                f"task_skip benchmark={spec.benchmark_name} suite_entry={suite_entry} model={spec.model_name} "
                f"task_id={task_id} reason=checkpoint results={len(checkpoint)}",
                flush=True,
            )
            continue

        print(
            f"task_start benchmark={spec.benchmark_name} suite_entry={suite_entry} model={spec.model_name} "
            f"protocol=comparison k={max_k} task_id={task_id}",
            flush=True,
        )
        try:
            comparison_results = runner.run_comparison_task(task_id, k=max_k)
        finally:
            runner._close_benchmark()

        by_protocol = {result.protocol: result for result in comparison_results}
        success_result = by_protocol[ProtocolMode.SUCCESS.value]
        task_results: list[BenchmarkResult] = [success_result]
        print(
            f"task_done benchmark={spec.benchmark_name} suite_entry={suite_entry} model={spec.model_name} "
            f"protocol=success k=1 task_id={task_id} "
            f"success={success_result.success} attempts={len(success_result.attempts)}",
            flush=True,
        )

        for mode in (ProtocolMode.RETRY, ProtocolMode.RECOVERY):
            result = by_protocol.get(mode.value)
            if result is None:
                continue
            for report_k in report_k_values:
                derived = derive_result_at_k(result, report_k)
                task_results.append(derived)
                print(
                    f"task_derived benchmark={spec.benchmark_name} suite_entry={suite_entry} model={spec.model_name} "
                    f"protocol={mode.value} k={report_k} task_id={task_id} "
                    f"success={derived.success} attempts={len(derived.attempts)} "
                    f"derived_from_k={result.k}",
                    flush=True,
                )
            print(
                f"task_done benchmark={spec.benchmark_name} suite_entry={suite_entry} model={spec.model_name} "
                f"protocol={mode.value} k={result.k} task_id={task_id} "
                f"success={result.success} attempts={len(result.attempts)}",
                flush=True,
            )
        results.extend(task_results)
        write_result_bundle(output_dir, runner, results)
        print(
            f"task_checkpoint benchmark={spec.benchmark_name} suite_entry={suite_entry} model={spec.model_name} "
            f"task_id={task_id} output={output_dir}",
            flush=True,
        )

    write_result_bundle(spec.output_dir, runner, results)
    return results


def _load_task_checkpoint(
    artifacts_dir: Path,
    task_id: str,
    report_k_values: tuple[int, ...],
    expected_agent_options: dict[str, Any] | None = None,
) -> list[BenchmarkResult] | None:
    paths = [_artifact_path(artifacts_dir, ProtocolMode.SUCCESS.value, 1, task_id)]
    for protocol in (ProtocolMode.RETRY.value, ProtocolMode.RECOVERY.value):
        for k in report_k_values:
            paths.append(_artifact_path(artifacts_dir, protocol, k, task_id))
    if not paths or not all(path.exists() for path in paths):
        return None

    results: list[BenchmarkResult] = []
    try:
        for path in paths:
            payload = json.loads(path.read_text(encoding="utf-8"))
            results.append(_benchmark_result_from_payload(payload))
    except Exception:
        return None
    if not _valid_task_checkpoint(
        results,
        task_id,
        report_k_values,
        expected_agent_options=expected_agent_options,
    ):
        return None
    return results


def _artifact_path(artifacts_dir: Path, protocol: str, k: int, task_id: str) -> Path:
    return artifacts_dir / f"{protocol}_k{k}_{task_id}.json"


def _expected_result_keys(
    task_ids: tuple[str, ...],
    report_k_values: tuple[int, ...],
) -> set[tuple[str, str, int]]:
    keys: set[tuple[str, str, int]] = set()
    for task_id in task_ids:
        keys.add((task_id, ProtocolMode.SUCCESS.value, 1))
        for protocol in (ProtocolMode.RETRY.value, ProtocolMode.RECOVERY.value):
            for k in report_k_values:
                keys.add((task_id, protocol, k))
    return keys


def _valid_task_checkpoint(
    results: list[BenchmarkResult],
    task_id: str,
    report_k_values: tuple[int, ...],
    expected_agent_options: dict[str, Any] | None = None,
) -> bool:
    expected = _expected_result_keys((task_id,), report_k_values)
    by_key = {(result.task_id, result.protocol, result.k): result for result in results}
    if set(by_key) != expected:
        return False

    success_result = by_key[(task_id, ProtocolMode.SUCCESS.value, 1)]
    if not _result_has_shared_first_metadata(success_result):
        return False
    if len(success_result.attempts) != 1 or _attempt_index(success_result.attempts[0]) != 1:
        return False
    first_attempt = success_result.attempts[0]
    first_success = bool(success_result.success)

    for protocol in (ProtocolMode.RETRY.value, ProtocolMode.RECOVERY.value):
        previous_success = first_success
        for k in sorted(report_k_values):
            result = by_key[(task_id, protocol, k)]
            if not _result_has_shared_first_metadata(result):
                return False
            if not result.attempts or result.attempts[0] != first_attempt:
                return False
            if len(result.attempts) > k:
                return False
            if not _attempt_budgets_match(result.attempts, expected_agent_options):
                return False
            if first_success:
                if not result.success or len(result.attempts) != 1:
                    return False
            elif len(result.attempts) < 2:
                return False
            if previous_success and not result.success:
                return False
            previous_success = bool(result.success)
    return True


def _result_has_shared_first_metadata(result: BenchmarkResult) -> bool:
    return result.metadata.get("comparison") == "shared-first-attempt"


def _attempt_index(attempt: Any) -> int | None:
    if isinstance(attempt, dict):
        value = attempt.get("attempt_index")
    else:
        value = getattr(attempt, "attempt_index", None)
    try:
        return int(value)
    except Exception:
        return None


def _attempt_budgets_match(
    attempts: tuple[Any, ...],
    expected_agent_options: dict[str, Any] | None,
) -> bool:
    if not expected_agent_options or "max_steps" not in expected_agent_options:
        return True
    try:
        expected = int(expected_agent_options["max_steps"])
    except Exception:
        return True
    for attempt in attempts:
        observed = _attempt_budget(attempt)
        if observed is None or observed != expected:
            return False
    return True


def _attempt_budget(attempt: Any) -> int | None:
    if isinstance(attempt, dict):
        agent_result = attempt.get("agent_result") or {}
        metadata = agent_result.get("metadata") or {}
    else:
        agent_result = getattr(attempt, "agent_result", None)
        metadata = getattr(agent_result, "metadata", {}) or {}
    value = metadata.get("max_steps")
    if value is None:
        value = metadata.get("max_iterations")
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def _benchmark_result_from_payload(payload: dict[str, Any]) -> BenchmarkResult:
    return BenchmarkResult(
        task_id=str(payload["task_id"]),
        protocol=str(payload["protocol"]),
        attempts=tuple(payload.get("attempts") or ()),
        success=bool(payload["success"]),
        k=int(payload["k"]),
        metadata=dict(payload.get("metadata") or {}),
    )


if __name__ == "__main__":
    raise SystemExit(main())
