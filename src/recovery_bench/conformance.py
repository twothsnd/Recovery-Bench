from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .io import to_jsonable
from .plugins import benchmark_capabilities
from .types import BenchmarkAdapter, StateSnapshot, TaskOutcome


@dataclass(frozen=True, slots=True)
class ConformanceCheck:
    name: str
    passed: bool
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConformanceReport:
    benchmark: str
    task_id: str | None
    checks: tuple[ConformanceCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


def format_conformance_report(report: ConformanceReport) -> str:
    """Format a benchmark conformance report for CLI output."""

    lines = [
        f"Benchmark: {report.benchmark}",
        f"Task: {report.task_id or '<none>'}",
        f"Passed: {'yes' if report.passed else 'no'}",
        "",
        "Checks:",
    ]
    for check in report.checks:
        status = "PASS" if check.passed else "FAIL"
        detail = _compact_details(check.details)
        suffix = f" - {detail}" if detail else ""
        lines.append(f"- {status} {check.name}{suffix}")
    return "\n".join(lines)


def run_basic_benchmark_conformance(
    benchmark: BenchmarkAdapter,
    *,
    task_id: str | None = None,
) -> ConformanceReport:
    """Run adapter-level protocol checks that do not depend on a real agent.

    These checks are intentionally conservative. They verify that the adapter
    exposes a task, can reset, can snapshot/restore, can call the official
    evaluator, and declares portability capabilities. Benchmark-specific tests
    should extend this with sentinel state and evaluator-side-effect checks.
    """

    checks: list[ConformanceCheck] = []
    selected_task_id = task_id

    try:
        task_ids = benchmark.list_tasks()
        if selected_task_id is None:
            selected_task_id = task_ids[0] if task_ids else None
        checks.append(
            ConformanceCheck(
                name="list_tasks",
                passed=bool(selected_task_id),
                details={"task_count": len(task_ids), "selected_task_id": selected_task_id},
            )
        )
    except Exception as exc:
        checks.append(_failed("list_tasks", exc))
        return ConformanceReport(benchmark=benchmark.name, task_id=selected_task_id, checks=tuple(checks))

    if selected_task_id is None:
        return ConformanceReport(benchmark=benchmark.name, task_id=None, checks=tuple(checks))

    try:
        task = benchmark.load_task(selected_task_id)
        checks.append(
            ConformanceCheck(
                name="load_task",
                passed=bool(task.task_id and task.prompt is not None),
                details={"task_id": task.task_id},
            )
        )
    except Exception as exc:
        checks.append(_failed("load_task", exc))
        return ConformanceReport(benchmark=benchmark.name, task_id=selected_task_id, checks=tuple(checks))

    try:
        reset_snapshot = benchmark.reset(task)
        checks.append(_snapshot_check("reset", reset_snapshot))
    except Exception as exc:
        checks.append(_failed("reset", exc))
        return ConformanceReport(benchmark=benchmark.name, task_id=selected_task_id, checks=tuple(checks))

    try:
        before = benchmark.snapshot(label="conformance-before-restore")
        restored = benchmark.restore(before)
        after = benchmark.snapshot(label="conformance-after-restore")
        capabilities = _safe_benchmark_capabilities(benchmark)
        payload_equivalent = to_jsonable(before.payload) == to_jsonable(after.payload)
        opaque_snapshot = _uses_opaque_snapshot_handles(before, capabilities)
        checks.append(_snapshot_check("snapshot_before_restore", before))
        checks.append(_snapshot_check("restore", restored))
        checks.append(
            ConformanceCheck(
                name="restore_roundtrip_json_equivalence",
                passed=payload_equivalent or opaque_snapshot,
                details={
                    "before_label": before.label,
                    "after_label": after.label,
                    "restore_label": restored.label,
                    "payload_equivalent": payload_equivalent,
                    "opaque_snapshot_handle": opaque_snapshot,
                },
            )
        )
    except Exception as exc:
        checks.append(_failed("restore_roundtrip", exc))

    try:
        outcome = benchmark.evaluate(task)
        checks.append(
            ConformanceCheck(
                name="evaluate",
                passed=isinstance(outcome, TaskOutcome),
                details={"success": getattr(outcome, "success", None), "score": getattr(outcome, "score", None)},
            )
        )
    except Exception as exc:
        checks.append(_failed("evaluate", exc))

    try:
        capabilities = benchmark_capabilities(benchmark)
        unknown_fields = [
            key
            for key in (
                "state_materialization",
                "state_snapshot",
                "restore_strategy",
                "evaluator_isolation",
                "budget_reset",
                "official_invariance",
            )
            if capabilities.get(key) in {None, "", "unknown"}
        ]
        checks.append(
            ConformanceCheck(
                name="capabilities_declared",
                passed=not unknown_fields,
                details={"unknown_fields": unknown_fields, "capabilities": capabilities},
            )
        )
    except Exception as exc:
        checks.append(_failed("capabilities_declared", exc))

    return ConformanceReport(
        benchmark=benchmark.name,
        task_id=selected_task_id,
        checks=tuple(checks),
    )


def _snapshot_check(name: str, snapshot: StateSnapshot) -> ConformanceCheck:
    return ConformanceCheck(
        name=name,
        passed=isinstance(snapshot, StateSnapshot),
        details={"label": snapshot.label, "metadata": snapshot.metadata},
    )


def _safe_benchmark_capabilities(benchmark: BenchmarkAdapter) -> dict[str, Any]:
    try:
        return benchmark_capabilities(benchmark)
    except Exception:
        return {}


def _uses_opaque_snapshot_handles(snapshot: StateSnapshot, capabilities: dict[str, Any]) -> bool:
    strategy = str(capabilities.get("restore_strategy") or "").lower()
    materialization = str(capabilities.get("state_materialization") or "").lower()
    payload = snapshot.payload
    payload_keys = set(payload) if isinstance(payload, dict) else set()
    return (
        "checkpoint" in strategy
        or "checkpoint" in materialization
        or "opaque" in materialization
        or bool(payload_keys & {"state_id", "checkpoint_id", "vm_checkpoint_id"})
    )


def _compact_details(details: dict[str, Any]) -> str:
    if not details:
        return ""
    preferred = []
    for key in ("task_count", "selected_task_id", "error", "unknown_fields", "payload_equivalent", "opaque_snapshot_handle"):
        if key in details:
            preferred.append(f"{key}={details[key]!r}")
    if preferred:
        return ", ".join(preferred)
    return str(to_jsonable(details))[:240]


def _failed(name: str, exc: Exception) -> ConformanceCheck:
    return ConformanceCheck(
        name=name,
        passed=False,
        details={"error": f"{type(exc).__name__}: {exc}"},
    )
