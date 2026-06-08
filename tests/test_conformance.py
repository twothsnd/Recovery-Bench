from pathlib import Path

from recovery_bench.adapters.smoke import build_progress_smoke_benchmark
from recovery_bench.conformance import run_basic_benchmark_conformance
from recovery_bench.types import BenchmarkResult, StateSnapshot, Task, TaskOutcome


def test_basic_benchmark_conformance_passes_for_reference_adapter() -> None:
    report = run_basic_benchmark_conformance(build_progress_smoke_benchmark())

    assert report.passed is True
    assert report.benchmark == "progress-smoke"
    assert report.task_id == "progress-1"
    assert {check.name for check in report.checks} >= {
        "list_tasks",
        "load_task",
        "reset",
        "snapshot_before_restore",
        "restore",
        "restore_roundtrip_json_equivalence",
        "evaluate",
        "capabilities_declared",
    }


def test_basic_benchmark_conformance_flags_missing_capability_declaration() -> None:
    report = run_basic_benchmark_conformance(MinimalBenchmarkWithoutCapabilities())

    capabilities_check = next(check for check in report.checks if check.name == "capabilities_declared")

    assert report.passed is False
    assert capabilities_check.passed is False
    assert "state_materialization" in capabilities_check.details["unknown_fields"]


class MinimalBenchmarkWithoutCapabilities:
    name = "minimal"

    def __init__(self) -> None:
        self.state = {}

    def list_tasks(self) -> list[str]:
        return ["task-1"]

    def load_task(self, task_id: str) -> Task:
        return Task(task_id=task_id, prompt="do task")

    def reset(self, task: Task) -> StateSnapshot:
        self.state = {"task_id": task.task_id, "value": 1}
        return self.snapshot(label="reset")

    def restore(self, snapshot: StateSnapshot) -> StateSnapshot:
        self.state = dict(snapshot.payload)
        return self.snapshot(label="restore")

    def snapshot(self, *, label: str | None = None) -> StateSnapshot:
        return StateSnapshot(payload=dict(self.state), label=label)

    def agent_environment(self) -> dict:
        return self.state

    def evaluate(self, task: Task) -> TaskOutcome:
        return TaskOutcome(success=False)

    def export_artifact(self, output_dir: Path, result: BenchmarkResult) -> None:
        return None
