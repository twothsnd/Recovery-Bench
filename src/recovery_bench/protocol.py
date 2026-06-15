from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import ExperimentConfig
from .errors import TaskSkip, raise_if_fatal_api_error
from .plugins import agent_capabilities, benchmark_capabilities
from .prompts import make_recovery_prompt, make_task_prompt
from .types import (
    AgentContext,
    AgentAdapter,
    AgentRunResult,
    AttemptRecord,
    AttemptStatus,
    BenchmarkAdapter,
    BenchmarkResult,
    ProtocolMode,
    Task,
    TaskOutcome,
)


@dataclass(slots=True)
class ProtocolRunner:
    """Execute retry/recovery experiments over stateful benchmarks."""

    benchmark: BenchmarkAdapter
    agent: AgentAdapter
    config: ExperimentConfig
    artifacts: list[BenchmarkResult] = field(default_factory=list)
    skipped_tasks: list[dict[str, object]] = field(default_factory=list)

    def run_all(self, mode: ProtocolMode, *, k: int) -> list[BenchmarkResult]:
        mode = ProtocolMode(mode)
        results: list[BenchmarkResult] = []
        task_ids = self.config.task_ids or tuple(self.benchmark.list_tasks())
        for task_id in task_ids:
            try:
                results.append(self.run_task(task_id, mode, k=k))
            except TaskSkip as exc:
                self._record_task_skip(exc)
            finally:
                self._close_benchmark()
        self.artifacts.extend(results)
        return results

    def run_comparison_all(self, *, k: int) -> list[BenchmarkResult]:
        results: list[BenchmarkResult] = []
        task_ids = self.config.task_ids or tuple(self.benchmark.list_tasks())
        for task_id in task_ids:
            try:
                results.extend(self.run_comparison_task(task_id, k=k))
            except TaskSkip as exc:
                self._record_task_skip(exc)
            finally:
                self._close_benchmark()
        self.artifacts.extend(results)
        return results

    def _record_task_skip(self, exc: TaskSkip) -> None:
        self.skipped_tasks.append(
            {
                "task_id": exc.task_id,
                "reason": exc.reason,
                "details": exc.details,
            }
        )

    def run_comparison_task(self, task_id: str, *, k: int) -> tuple[BenchmarkResult, ...]:
        """Run Success@1 plus Retry/Recovery@k from one shared first attempt.

        Retry and Recovery are defined as branches after the same failed first
        attempt. If that first attempt succeeds, both branches are already
        successful and no extra attempts are executed.
        """

        if k < 1:
            raise ValueError("k must be >= 1")

        task = self.benchmark.load_task(task_id)
        self.benchmark.reset(task)
        first_attempt = self._execute_attempt(
            task,
            task_id=task_id,
            mode=ProtocolMode.SUCCESS,
            attempt_index=1,
            effective_k=1,
            previous_attempts=(),
        )
        first_success = self._attempt_succeeded(first_attempt)
        success_result = self._make_result(
            task_id=task_id,
            mode=ProtocolMode.SUCCESS,
            k=1,
            attempts=(first_attempt,),
            success=first_success,
            metadata={"comparison": "shared-first-attempt"},
        )

        if k == 1:
            return (success_result,)

        if first_attempt.status is AttemptStatus.ERROR:
            retry_result = self._make_result(
                task_id=task_id,
                mode=ProtocolMode.RETRY,
                k=k,
                attempts=(first_attempt,),
                success=False,
                metadata={"comparison": "shared-first-attempt", "first_attempt_error": True},
            )
            recovery_result = self._make_result(
                task_id=task_id,
                mode=ProtocolMode.RECOVERY,
                k=k,
                attempts=(first_attempt,),
                success=False,
                metadata={"comparison": "shared-first-attempt", "first_attempt_error": True},
            )
            return (success_result, retry_result, recovery_result)

        if first_success:
            retry_result = self._make_result(
                task_id=task_id,
                mode=ProtocolMode.RETRY,
                k=k,
                attempts=(first_attempt,),
                success=True,
                metadata={"comparison": "shared-first-attempt", "first_attempt_success": True},
            )
            recovery_result = self._make_result(
                task_id=task_id,
                mode=ProtocolMode.RECOVERY,
                k=k,
                attempts=(first_attempt,),
                success=True,
                metadata={"comparison": "shared-first-attempt", "first_attempt_success": True},
            )
            return (success_result, retry_result, recovery_result)

        if first_attempt.state_after is None:
            raise RuntimeError("First attempt did not produce a state snapshot to branch from.")

        first_failed_state = first_attempt.state_after

        # Run recovery before retry. Some adapters hold dirty state through live
        # handles; retry resets are allowed to destroy those handles.
        recovery_attempts = [first_attempt]
        recovery_state = first_failed_state
        recovery_success = False
        for attempt_index in range(2, k + 1):
            recovery_state = self.benchmark.restore(recovery_state)
            attempt = self._execute_attempt(
                task,
                task_id=task_id,
                mode=ProtocolMode.RECOVERY,
                attempt_index=attempt_index,
                effective_k=k,
                previous_attempts=tuple(recovery_attempts),
            )
            recovery_attempts.append(attempt)
            if attempt.state_after is None:
                raise RuntimeError(f"Recovery attempt {attempt_index} did not produce a state snapshot.")
            recovery_state = attempt.state_after
            if self._attempt_succeeded(attempt):
                recovery_success = True
                break

        retry_attempts = [first_attempt]
        retry_success = False
        for attempt_index in range(2, k + 1):
            self.benchmark.reset(task)
            attempt = self._execute_attempt(
                task,
                task_id=task_id,
                mode=ProtocolMode.RETRY,
                attempt_index=attempt_index,
                effective_k=k,
                previous_attempts=(),
            )
            retry_attempts.append(attempt)
            if self._attempt_succeeded(attempt):
                retry_success = True
                break

        retry_result = self._make_result(
            task_id=task_id,
            mode=ProtocolMode.RETRY,
            k=k,
            attempts=tuple(retry_attempts),
            success=retry_success,
            metadata={"comparison": "shared-first-attempt"},
        )
        recovery_result = self._make_result(
            task_id=task_id,
            mode=ProtocolMode.RECOVERY,
            k=k,
            attempts=tuple(recovery_attempts),
            success=recovery_success,
            metadata={"comparison": "shared-first-attempt"},
        )
        return (success_result, retry_result, recovery_result)

    def run_task(self, task_id: str, mode: ProtocolMode, *, k: int) -> BenchmarkResult:
        mode = ProtocolMode(mode)
        if k < 1:
            raise ValueError("k must be >= 1")

        task = self.benchmark.load_task(task_id)
        effective_k = 1 if mode is ProtocolMode.SUCCESS else k
        max_attempts = effective_k
        current_state = self.benchmark.reset(task)
        attempts: list[AttemptRecord] = []
        success = False

        for attempt_index in range(1, max_attempts + 1):
            if attempt_index > 1:
                if mode is ProtocolMode.RETRY:
                    current_state = self.benchmark.reset(task)
                elif mode is ProtocolMode.RECOVERY:
                    current_state = self.benchmark.restore(current_state)
                else:
                    raise ValueError(f"Unsupported protocol mode: {mode}")

            record = self._execute_attempt(
                task,
                task_id=task_id,
                mode=mode,
                attempt_index=attempt_index,
                effective_k=effective_k,
                previous_attempts=tuple(attempts) if mode is ProtocolMode.RECOVERY else (),
            )
            attempts.append(record)

            if record.state_after is not None:
                current_state = record.state_after
            if self._attempt_succeeded(record):
                success = True
                break

        return self._make_result(
            task_id=task_id,
            mode=mode,
            attempts=tuple(attempts),
            success=success,
            k=effective_k,
        )

    def save_results(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        for result in self.artifacts:
            self.benchmark.export_artifact(output_dir, result)

    @staticmethod
    def _make_prompt(task: Task, *, mode: ProtocolMode, attempt_index: int) -> str:
        if mode is ProtocolMode.RECOVERY and attempt_index > 1:
            return make_recovery_prompt(task, attempt_index=attempt_index)
        return make_task_prompt(task)

    def _run_agent(self, task: Task, prompt: str, context: AgentContext) -> AgentRunResult:
        try:
            return self.agent.run(task, prompt, self.benchmark.agent_environment(), context)
        except Exception as exc:
            raise_if_fatal_api_error(exc)
            return AgentRunResult(error=f"{type(exc).__name__}: {exc}")

    def _evaluate(self, task: Task) -> TaskOutcome:
        try:
            return self.benchmark.evaluate(task)
        except Exception as exc:
            raise_if_fatal_api_error(exc)
            return TaskOutcome(
                success=False,
                details={"evaluation_error": f"{type(exc).__name__}: {exc}"},
            )

    @staticmethod
    def _attempt_status(agent_result: AgentRunResult, outcome: TaskOutcome) -> AttemptStatus:
        if agent_result.error is not None:
            return AttemptStatus.ERROR
        if outcome.success:
            return AttemptStatus.SUCCESS
        return AttemptStatus.FAILED

    def _execute_attempt(
        self,
        task: Task,
        *,
        task_id: str,
        mode: ProtocolMode,
        attempt_index: int,
        effective_k: int,
        previous_attempts: tuple[AttemptRecord, ...],
    ) -> AttemptRecord:
        prompt = self._make_prompt(task, mode=mode, attempt_index=attempt_index)
        state_before = self.benchmark.snapshot(label=f"attempt-{attempt_index}-before")
        context = AgentContext(
            benchmark=self.benchmark.name,
            task_id=task_id,
            protocol=mode.value,
            attempt_index=attempt_index,
            k=effective_k,
            state_before=state_before,
            previous_attempts=previous_attempts,
            options=self.config.options,
        )
        agent_result = self._run_agent(task, prompt, context)
        state_after = None
        outcome = TaskOutcome(
            success=False,
            details={"skipped_evaluation": "agent_error"},
        )
        if agent_result.error is None:
            state_after = self.benchmark.snapshot(label=f"attempt-{attempt_index}-after")
            outcome = self._evaluate(task)
            if getattr(self.benchmark, "snapshot_after_evaluate", False):
                state_after = self.benchmark.snapshot(label=f"attempt-{attempt_index}-after-evaluate")
        status = self._attempt_status(agent_result, outcome)
        return AttemptRecord(
            attempt_index=attempt_index,
            task_id=task_id,
            prompt=prompt,
            status=status,
            agent_result=agent_result,
            outcome=outcome,
            state_before=state_before,
            state_after=state_after,
            notes={
                "mode": mode.value,
                "previous_attempts_visible": len(previous_attempts),
                "state_after_timing": (
                    "after_evaluate"
                    if getattr(self.benchmark, "snapshot_after_evaluate", False)
                    else "after_agent_before_evaluate"
                ),
            },
        )

    def _make_result(
        self,
        *,
        task_id: str,
        mode: ProtocolMode,
        attempts: tuple[AttemptRecord, ...],
        success: bool,
        k: int,
        metadata: dict[str, object] | None = None,
    ) -> BenchmarkResult:
        result_metadata = {
            "benchmark": self.benchmark.name,
            "model": self.config.model.name,
            "provider": self.config.model.provider,
            "agent": self.agent.name,
            "core_protocol": "retry-recovery-branching-v1",
            "benchmark_capabilities": benchmark_capabilities(self.benchmark),
            "agent_capabilities": agent_capabilities(self.agent),
        }
        if metadata:
            result_metadata.update(metadata)
        return BenchmarkResult(
            task_id=task_id,
            protocol=mode.value,
            attempts=attempts,
            success=success,
            k=k,
            metadata=result_metadata,
        )

    @staticmethod
    def _attempt_succeeded(attempt: AttemptRecord) -> bool:
        return bool(attempt.outcome is not None and attempt.outcome.success)

    def _close_benchmark(self) -> None:
        close = getattr(self.benchmark, "close", None)
        if callable(close):
            close()
