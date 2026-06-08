from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..io import dump_dataclass_json
from ..types import BenchmarkAdapter, BenchmarkCapabilities, BenchmarkResult, StateSnapshot, Task, TaskOutcome


@dataclass(slots=True)
class StaticBenchmarkAdapter(BenchmarkAdapter):
    """Reference adapter scaffold for benchmark integrations."""

    name: str
    tasks: dict[str, Task]
    state: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def load_task(self, task_id: str) -> Task:
        try:
            return self.tasks[task_id]
        except KeyError as exc:
            raise KeyError(f"Unknown task_id: {task_id}") from exc

    def reset(self, task: Task) -> StateSnapshot:
        self.state = {"task_id": task.task_id, "clean": True, "history": []}
        return self.snapshot(label="reset")

    def restore(self, snapshot: StateSnapshot) -> StateSnapshot:
        self.state = deepcopy(snapshot.payload)
        return self.snapshot(label=snapshot.label)

    def snapshot(self, *, label: str | None = None) -> StateSnapshot:
        return StateSnapshot(payload=deepcopy(self.state), label=label, metadata={"benchmark": self.name})

    def agent_environment(self) -> Any:
        return self.state

    def evaluate(self, task: Task) -> TaskOutcome:
        return TaskOutcome(success=False, details={"note": "adapter not implemented"})

    def list_tasks(self) -> list[str]:
        return sorted(self.tasks)

    def export_artifact(self, output_dir: Path, result: BenchmarkResult) -> None:
        dump_dataclass_json(output_dir / f"{result.protocol}_k{result.k}_{result.task_id}.json", result)

    def capabilities(self) -> BenchmarkCapabilities:
        return BenchmarkCapabilities(
            state_materialization="in_process_deepcopy",
            state_snapshot="strict",
            restore_strategy="deepcopy",
            evaluator_isolation="read_only",
            budget_reset="per_attempt_full",
            official_invariance="reference_adapter",
            official_harness="none",
            strict_recovery=True,
        )
