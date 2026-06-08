from __future__ import annotations

from dataclasses import dataclass

from .base import StaticBenchmarkAdapter
from ..types import Task, TaskOutcome


@dataclass(slots=True)
class ProgressSmokeBenchmark(StaticBenchmarkAdapter):
    """Stateful benchmark used to validate retry/recovery semantics."""

    def evaluate(self, task: Task) -> TaskOutcome:
        progress = self.state.get("progress", []) if isinstance(self.state, dict) else []
        success = "prepared" in progress and "finished" in progress
        return TaskOutcome(
            success=success,
            score=1.0 if success else 0.0,
            details={"progress": list(progress)},
        )


def build_progress_smoke_benchmark() -> ProgressSmokeBenchmark:
    return ProgressSmokeBenchmark(
        name="progress-smoke",
        tasks={
            "progress-1": Task(
                task_id="progress-1",
                prompt="Prepare the workspace, then finish the task without losing prior progress.",
            )
        },
    )

