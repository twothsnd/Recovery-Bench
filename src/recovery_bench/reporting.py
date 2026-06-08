from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .types import BenchmarkResult


@dataclass(frozen=True, slots=True)
class AggregateRow:
    benchmark: str
    model: str
    protocol: str
    k: int
    tasks: int
    success_rate: float


@dataclass(frozen=True, slots=True)
class MainTableRow:
    benchmark: str
    model: str
    tasks: int
    success_at_1: float | None
    retry_at_2: float | None
    recovery_at_2: float | None
    retry_at_3: float | None
    recovery_at_3: float | None

    @property
    def recovery_gap_at_2(self) -> float | None:
        return _gap(self.retry_at_2, self.recovery_at_2)

    @property
    def recovery_gap_at_3(self) -> float | None:
        return _gap(self.retry_at_3, self.recovery_at_3)


def aggregate_results(results: list[BenchmarkResult]) -> list[AggregateRow]:
    buckets: dict[tuple[str, str, str, int], list[BenchmarkResult]] = defaultdict(list)
    for result in results:
        benchmark = str(result.metadata.get("benchmark", "unknown"))
        model = str(result.metadata.get("model", "unknown"))
        buckets[(benchmark, model, result.protocol, result.k)].append(result)

    rows: list[AggregateRow] = []
    for (benchmark, model, protocol, k), group in sorted(buckets.items()):
        tasks = len(group)
        success_rate = sum(1 for result in group if result.success) / tasks if tasks else 0.0
        rows.append(
            AggregateRow(
                benchmark=benchmark,
                model=model,
                protocol=protocol,
                k=k,
                tasks=tasks,
                success_rate=success_rate,
            )
        )
    return rows


def aggregate_main_table(results: list[BenchmarkResult]) -> list[MainTableRow]:
    grouped: dict[tuple[str, str], list[BenchmarkResult]] = defaultdict(list)
    for result in results:
        benchmark = str(result.metadata.get("benchmark", "unknown"))
        model = str(result.metadata.get("model", "unknown"))
        grouped[(benchmark, model)].append(result)

    rows: list[MainTableRow] = []
    for (benchmark, model), group in sorted(grouped.items()):
        rates = {
            (protocol, k): _success_rate(subgroup)
            for (protocol, k), subgroup in _by_protocol_k(group).items()
        }
        rows.append(
            MainTableRow(
                benchmark=benchmark,
                model=model,
                tasks=len({result.task_id for result in group}),
                success_at_1=rates.get(("success", 1)),
                retry_at_2=rates.get(("retry", 2)),
                recovery_at_2=rates.get(("recovery", 2)),
                retry_at_3=rates.get(("retry", 3)),
                recovery_at_3=rates.get(("recovery", 3)),
            )
        )
    return rows


def render_markdown_table(rows: list[AggregateRow]) -> str:
    header = "| Benchmark | Model | Protocol | k | Tasks | Success@k |"
    sep = "| --- | --- | --- | ---: | ---: | ---: |"
    lines = [header, sep]
    for row in rows:
        lines.append(
            f"| {row.benchmark} | {row.model} | {row.protocol} | {row.k} | {row.tasks} | {row.success_rate:.3f} |"
        )
    return "\n".join(lines) + "\n"


def render_main_markdown_table(rows: list[MainTableRow]) -> str:
    header = (
        "| Benchmark | Model | Tasks | Success@1 | Retry@2 | Recovery@2 | Gap@2 | "
        "Retry@3 | Recovery@3 | Gap@3 |"
    )
    sep = "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"
    lines = [header, sep]
    for row in rows:
        lines.append(
            "| "
            f"{row.benchmark} | "
            f"{row.model} | "
            f"{row.tasks} | "
            f"{_fmt(row.success_at_1)} | "
            f"{_fmt(row.retry_at_2)} | "
            f"{_fmt(row.recovery_at_2)} | "
            f"{_fmt(row.recovery_gap_at_2)} | "
            f"{_fmt(row.retry_at_3)} | "
            f"{_fmt(row.recovery_at_3)} | "
            f"{_fmt(row.recovery_gap_at_3)} |"
        )
    return "\n".join(lines) + "\n"


def write_report(path: Path, rows: list[AggregateRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown_table(rows), encoding="utf-8")


def write_main_report(path: Path, rows: list[MainTableRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_main_markdown_table(rows), encoding="utf-8")


def write_aggregate_csv(path: Path, rows: list[AggregateRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["benchmark", "model", "protocol", "k", "tasks", "success_rate"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "benchmark": row.benchmark,
                    "model": row.model,
                    "protocol": row.protocol,
                    "k": row.k,
                    "tasks": row.tasks,
                    "success_rate": f"{row.success_rate:.6f}",
                }
            )


def write_main_csv(path: Path, rows: list[MainTableRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "benchmark",
                "model",
                "tasks",
                "success_at_1",
                "retry_at_2",
                "recovery_at_2",
                "recovery_gap_at_2",
                "retry_at_3",
                "recovery_at_3",
                "recovery_gap_at_3",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "benchmark": row.benchmark,
                    "model": row.model,
                    "tasks": row.tasks,
                    "success_at_1": _fmt_csv(row.success_at_1),
                    "retry_at_2": _fmt_csv(row.retry_at_2),
                    "recovery_at_2": _fmt_csv(row.recovery_at_2),
                    "recovery_gap_at_2": _fmt_csv(row.recovery_gap_at_2),
                    "retry_at_3": _fmt_csv(row.retry_at_3),
                    "recovery_at_3": _fmt_csv(row.recovery_at_3),
                    "recovery_gap_at_3": _fmt_csv(row.recovery_gap_at_3),
                }
            )


def _by_protocol_k(results: list[BenchmarkResult]) -> dict[tuple[str, int], list[BenchmarkResult]]:
    grouped: dict[tuple[str, int], list[BenchmarkResult]] = defaultdict(list)
    for result in results:
        grouped[(result.protocol, result.k)].append(result)
    return grouped


def _success_rate(results: list[BenchmarkResult]) -> float:
    return sum(1 for result in results if result.success) / len(results)


def _gap(retry: float | None, recovery: float | None) -> float | None:
    if retry is None or recovery is None:
        return None
    return retry - recovery


def _fmt(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.3f}"


def _fmt_csv(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.6f}"
