from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Task:
    """A benchmark task description."""

    task_id: str
    prompt: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TaskOutcome:
    """Outcome of evaluating a single attempt."""

    success: bool
    score: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StateSnapshot:
    """A benchmark state snapshot or opaque state handle."""

    payload: Any
    label: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ActionRecord:
    """One agent action and optional observation."""

    action: Any
    observation: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentContext:
    """Context passed to an agent for one attempt."""

    benchmark: str
    task_id: str
    protocol: str
    attempt_index: int
    k: int
    state_before: StateSnapshot | None = None
    previous_attempts: tuple["AttemptRecord", ...] = ()
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """The result of one agent attempt."""

    actions: tuple[ActionRecord, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class AttemptStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    """One execution attempt within a protocol run."""

    attempt_index: int
    task_id: str
    prompt: str
    status: AttemptStatus
    agent_result: AgentRunResult
    outcome: TaskOutcome | None = None
    state_before: StateSnapshot | None = None
    state_after: StateSnapshot | None = None
    notes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """Aggregate results for one task/protocol pair."""

    task_id: str
    protocol: str
    attempts: tuple[AttemptRecord, ...]
    success: bool
    k: int
    metadata: dict[str, Any] = field(default_factory=dict)


class ProtocolMode(str, Enum):
    SUCCESS = "success"
    RETRY = "retry"
    RECOVERY = "recovery"


@dataclass(frozen=True, slots=True)
class BenchmarkCapabilities:
    """Portability contract declared by a benchmark adapter."""

    state_materialization: str = "unknown"
    state_snapshot: str = "unknown"
    restore_strategy: str = "unknown"
    evaluator_isolation: str = "unknown"
    budget_reset: str = "unknown"
    official_invariance: str = "unknown"
    official_harness: str = "unknown"
    strict_recovery: bool = False
    limitations: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentCapabilities:
    """Portability contract declared by an agent adapter."""

    memory_mode: str = "unknown"
    retry_memory_reset: str = "unknown"
    recovery_memory: str = "unknown"
    trajectory_export: str = "unknown"
    official_agent: str = "unknown"
    limitations: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AgentAdapter(Protocol):
    """Adapter for model-backed agents."""

    name: str

    def run(
        self,
        task: Task,
        prompt: str,
        environment: Any,
        context: AgentContext,
    ) -> AgentRunResult:
        """Run one attempt against the current benchmark environment."""


@runtime_checkable
class BenchmarkAdapter(Protocol):
    """Adapter for stateful benchmark environments."""

    name: str

    def load_task(self, task_id: str) -> Task:
        ...

    def reset(self, task: Task) -> StateSnapshot:
        ...

    def restore(self, snapshot: StateSnapshot) -> StateSnapshot:
        ...

    def snapshot(self, *, label: str | None = None) -> StateSnapshot:
        ...

    def agent_environment(self) -> Any:
        ...

    def evaluate(self, task: Task) -> TaskOutcome:
        ...

    def list_tasks(self) -> list[str]:
        ...

    def export_artifact(self, output_dir: Path, result: BenchmarkResult) -> None:
        ...
