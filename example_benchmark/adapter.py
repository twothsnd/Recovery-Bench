from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from recovery_bench.config import AgentConfig, BenchmarkConfig, ModelConfig
from recovery_bench.io import dump_dataclass_json
from recovery_bench.types import (
    ActionRecord,
    AgentCapabilities,
    AgentContext,
    AgentRunResult,
    BenchmarkCapabilities,
    BenchmarkResult,
    StateSnapshot,
    Task,
    TaskOutcome,
)


@dataclass(slots=True)
class ExampleBenchmark:
    """Small external benchmark example loaded through benchmark.import_path.

    The task succeeds only after the state records both "prepare" and
    "finish". Retry resets the state, so Retry@2 fails. Recovery inherits the
    dirty state from attempt 1, so Recovery@2 succeeds.
    """

    task_ids: tuple[str, ...] = ("example-task-1",)
    name: str = "example_benchmark"
    state: dict[str, Any] = field(default_factory=dict)

    def list_tasks(self) -> list[str]:
        return list(self.task_ids)

    def load_task(self, task_id: str) -> Task:
        if task_id not in self.task_ids:
            raise KeyError(f"Unknown task_id: {task_id}")
        return Task(
            task_id=task_id,
            prompt="Prepare the workspace, then finish the task.",
            metadata={"source": "example_benchmark.adapter"},
        )

    def reset(self, task: Task) -> StateSnapshot:
        self.state = {"task_id": task.task_id, "progress": []}
        return self.snapshot(label="reset")

    def snapshot(self, *, label: str | None = None) -> StateSnapshot:
        return StateSnapshot(
            payload=deepcopy(self.state),
            label=label,
            metadata={"benchmark": self.name, "snapshot": "deepcopy"},
        )

    def restore(self, snapshot: StateSnapshot) -> StateSnapshot:
        self.state = deepcopy(snapshot.payload)
        return self.snapshot(label=snapshot.label)

    def agent_environment(self) -> dict[str, Any]:
        return self.state

    def evaluate(self, task: Task) -> TaskOutcome:
        progress = list(self.state.get("progress", []))
        success = progress == ["prepare", "finish"]
        return TaskOutcome(
            success=success,
            score=1.0 if success else 0.0,
            details={"progress": progress},
        )

    def export_artifact(self, output_dir: Path, result: BenchmarkResult) -> None:
        dump_dataclass_json(output_dir / f"{result.protocol}_k{result.k}_{result.task_id}.json", result)

    def capabilities(self) -> BenchmarkCapabilities:
        return BenchmarkCapabilities(
            state_materialization="in_process_deepcopy",
            state_snapshot="strict",
            restore_strategy="deepcopy",
            evaluator_isolation="read_only",
            budget_reset="per_attempt_full",
            official_invariance="example-benchmark",
            official_harness="none",
            strict_recovery=True,
        )


@dataclass(slots=True)
class ExampleAgent:
    """Small external agent example loaded through agent.import_path."""

    name: str = "example_agent"

    def run(
        self,
        task: Task,
        prompt: str,
        environment: dict[str, Any],
        context: AgentContext,
    ) -> AgentRunResult:
        del task, prompt
        progress = environment.setdefault("progress", [])
        if context.protocol == "recovery" and context.previous_attempts:
            action = "finish"
        else:
            action = "prepare"
        progress.append(action)
        return AgentRunResult(
            actions=(ActionRecord(action=action, observation={"progress": list(progress)}),),
            metadata={
                "previous_attempts_visible": len(context.previous_attempts),
                "attempt_index": context.attempt_index,
            },
        )

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            memory_mode="context_previous_attempts",
            retry_memory_reset="previous_attempts_empty",
            recovery_memory="previous_attempts_forwarded",
            trajectory_export="action_records",
            official_agent="example-agent",
        )


def build_benchmark(
    config: BenchmarkConfig | None = None,
    task_ids: tuple[str, ...] = (),
) -> ExampleBenchmark:
    selected = task_ids or ("example-task-1",)
    return ExampleBenchmark(task_ids=selected, name=(config.name if config else "example_benchmark"))


def build_agent(
    model_config: ModelConfig | None = None,
    agent_config: AgentConfig | None = None,
) -> ExampleAgent:
    del model_config
    return ExampleAgent(name=(agent_config.name if agent_config else "example_agent"))
