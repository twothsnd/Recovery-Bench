from dataclasses import replace
from pathlib import Path

from recovery_bench.adapters.smoke import build_progress_smoke_benchmark
from recovery_bench.agents.provider import ProviderAgent
from recovery_bench.agents.smoke import ProgressSmokeAgent
from recovery_bench.config import BenchmarkConfig, ExperimentConfig, ModelConfig
from recovery_bench.experiment import ExperimentSuite, build_manifest
from recovery_bench.protocol import ProtocolRunner
from recovery_bench.types import (
    ActionRecord,
    AgentContext,
    AgentRunResult,
    BenchmarkResult,
    ProtocolMode,
    StateSnapshot,
    Task,
    TaskOutcome,
)


def _runner() -> ProtocolRunner:
    benchmark = build_progress_smoke_benchmark()
    agent = ProgressSmokeAgent()
    config = ExperimentConfig(
        benchmark=BenchmarkConfig(name=benchmark.name),
        model=ModelConfig(name="smoke-model", provider="local"),
        task_ids=("progress-1",),
    )
    return ProtocolRunner(benchmark=benchmark, agent=agent, config=config)


def test_recovery_inherits_failed_state_and_succeeds() -> None:
    result = _runner().run_task("progress-1", ProtocolMode.RECOVERY, k=2)
    assert result.success is True
    assert len(result.attempts) == 2
    assert "NOT been reset" in result.attempts[1].prompt
    assert result.attempts[1].outcome is not None
    assert result.attempts[1].outcome.details["progress"] == ["prepared", "finished"]
    assert result.attempts[0].notes["previous_attempts_visible"] == 0
    assert result.attempts[1].notes["previous_attempts_visible"] == 1


def test_retry_resets_failed_state_and_does_not_finish_by_k2() -> None:
    result = _runner().run_task("progress-1", ProtocolMode.RETRY, k=2)
    assert result.success is False
    assert len(result.attempts) == 2
    assert "NOT been reset" not in result.attempts[1].prompt
    assert result.attempts[1].outcome is not None
    assert result.attempts[1].outcome.details["progress"] == ["prepared"]


def test_success_at_1_runs_only_one_attempt() -> None:
    result = _runner().run_task("progress-1", ProtocolMode.SUCCESS, k=3)
    assert result.success is False
    assert result.k == 1
    assert len(result.attempts) == 1


def test_suite_derives_k_values_from_shared_max_k_trajectory() -> None:
    results = ExperimentSuite(_runner()).run(k_values=(1, 2, 3))

    success = {result.k: result for result in results if result.protocol == "success"}
    retry = {result.k: result for result in results if result.protocol == "retry"}
    recovery = {result.k: result for result in results if result.protocol == "recovery"}

    assert success[1].success is False
    assert retry[2].success is False
    assert retry[3].success is False
    assert recovery[2].success is True
    assert recovery[3].success is True
    assert recovery[2].metadata["derived_from_k"] == 3
    assert len(recovery[2].attempts) == 2
    assert len(recovery[3].attempts) == 2
    assert retry[2].attempts[0] == recovery[2].attempts[0]


def test_result_metadata_records_portability_capabilities() -> None:
    result = _runner().run_task("progress-1", ProtocolMode.RECOVERY, k=2)

    assert result.metadata["core_protocol"] == "retry-recovery-branching-v1"
    assert result.metadata["benchmark_capabilities"]["strict_recovery"] is True
    assert result.metadata["benchmark_capabilities"]["state_snapshot"] == "strict"
    assert result.metadata["benchmark_capabilities"]["budget_reset"] == "per_attempt_full"
    assert result.metadata["agent_capabilities"]["retry_memory_reset"] == "new_attempt_context"
    assert result.metadata["agent_capabilities"]["recovery_memory"] == "stateful_environment_progress"


def test_manifest_records_import_paths_and_capabilities() -> None:
    runner = _runner()
    runner.config = replace(
        runner.config,
        benchmark=replace(runner.config.benchmark, import_path="bench_pkg:build_benchmark"),
        agent=replace(runner.config.agent, import_path="agent_pkg:build_agent"),
    )
    result = runner.run_task("progress-1", ProtocolMode.RECOVERY, k=2)

    manifest = build_manifest(runner, [result])

    assert manifest["benchmark"]["import_path"] == "bench_pkg:build_benchmark"
    assert manifest["benchmark"]["capabilities"]["strict_recovery"] is True
    assert manifest["agent"]["import_path"] == "agent_pkg:build_agent"
    assert manifest["agent"]["capabilities"]["official_agent"] == "test_agent"


def test_suite_does_not_rerun_recovery_when_shared_attempt_one_succeeds() -> None:
    agent = FirstCallCompletesThenFailsAgent()
    runner = ProtocolRunner(
        benchmark=build_progress_smoke_benchmark(),
        agent=agent,
        config=ExperimentConfig(
            benchmark=BenchmarkConfig(name="progress-smoke"),
            model=ModelConfig(name="smoke-model", provider="local"),
            task_ids=("progress-1",),
        ),
    )

    results = ExperimentSuite(runner).run(k_values=(1, 2, 3))
    rates = {(result.protocol, result.k): result for result in results}

    assert agent.calls == 1
    assert rates[("success", 1)].success is True
    assert rates[("retry", 2)].success is True
    assert rates[("retry", 3)].success is True
    assert rates[("recovery", 2)].success is True
    assert rates[("recovery", 3)].success is True
    assert len(rates[("recovery", 3)].attempts) == 1


def test_retry_and_recovery_attempts_get_full_configured_step_budget() -> None:
    benchmark = StepBudgetBenchmark()
    agent = ProviderAgent(name="step-budget-agent", client=FakeModelClient(), options={"max_steps": 50})
    runner = ProtocolRunner(
        benchmark=benchmark,
        agent=agent,
        config=ExperimentConfig(
            benchmark=BenchmarkConfig(name="step-budget"),
            model=ModelConfig(name="fake-model", provider="fake"),
            task_ids=("step-budget",),
        ),
    )

    runner.run_comparison_task("step-budget", k=3)

    assert benchmark.seen_step_budgets == [
        ("success", 1, 50),
        ("recovery", 2, 50),
        ("recovery", 3, 50),
        ("retry", 2, 50),
        ("retry", 3, 50),
    ]


def test_recovery_restore_can_clear_attempt_completion_marker() -> None:
    benchmark = CompletionMarkerBenchmark()
    agent = CompletionMarkerAgent()
    runner = ProtocolRunner(
        benchmark=benchmark,
        agent=agent,
        config=ExperimentConfig(
            benchmark=BenchmarkConfig(name="completion-marker"),
            model=ModelConfig(name="fake-model", provider="fake"),
            task_ids=("completion-marker",),
        ),
    )

    result = runner.run_comparison_task("completion-marker", k=2)
    recovery = next(item for item in result if item.protocol == "recovery")
    retry = next(item for item in result if item.protocol == "retry")

    assert recovery.success is True
    assert retry.success is False
    assert recovery.attempts[1].agent_result.metadata["completion_marker_before_action"] is False
    assert recovery.attempts[1].outcome.details["business_progress"] == ["bad", "repair"]


def test_snapshot_after_evaluate_benchmark_records_evaluated_dirty_state() -> None:
    benchmark = EvalMutatesBenchmark()
    runner = ProtocolRunner(
        benchmark=benchmark,
        agent=EvalMutatesAgent(),
        config=ExperimentConfig(
            benchmark=BenchmarkConfig(name="eval-mutates"),
            model=ModelConfig(name="fake-model", provider="fake"),
            task_ids=("eval-mutates",),
        ),
    )

    result = runner.run_task("eval-mutates", ProtocolMode.RECOVERY, k=2)

    assert result.attempts[0].state_after is not None
    assert result.attempts[0].state_after.label == "attempt-1-after-evaluate"
    assert result.attempts[0].state_after.payload == ["agent-1", "evaluate-1"]
    assert benchmark.restored_payloads[0] == ["agent-1", "evaluate-1"]


class FirstCallCompletesThenFailsAgent:
    name = "first-call-completes"

    def __init__(self) -> None:
        self.calls = 0

    def run(
        self,
        task: Task,
        prompt: str,
        environment: object,
        context: AgentContext,
    ) -> AgentRunResult:
        self.calls += 1
        if not isinstance(environment, dict):
            return AgentRunResult(error="expected dict environment")
        progress = environment.setdefault("progress", [])
        if self.calls == 1:
            progress.extend(["prepared", "finished"])
            return AgentRunResult(actions=(ActionRecord(action="finish-first-call"),))
        progress.append("prepared")
        return AgentRunResult(actions=(ActionRecord(action="prepare-only"),))


class FakeModelClient:
    provider = "fake"
    model = "fake-model"


class StepBudgetBenchmark:
    name = "step-budget"

    def __init__(self) -> None:
        self.state: list[str] = []
        self.seen_step_budgets: list[tuple[str, int, int]] = []
        self._environment = StepBudgetEnvironment(self)

    def load_task(self, task_id: str) -> Task:
        return Task(task_id=task_id, prompt="Never succeeds.")

    def reset(self, task: Task) -> StateSnapshot:
        self.state = []
        return self.snapshot(label="reset")

    def restore(self, snapshot: StateSnapshot) -> StateSnapshot:
        self.state = list(snapshot.payload)
        return self.snapshot(label="restore")

    def snapshot(self, *, label: str | None = None) -> StateSnapshot:
        return StateSnapshot(payload=list(self.state), label=label)

    def agent_environment(self) -> "StepBudgetEnvironment":
        return self._environment

    def evaluate(self, task: Task) -> TaskOutcome:
        return TaskOutcome(success=False, details={"state": list(self.state)})

    def list_tasks(self) -> list[str]:
        return ["step-budget"]

    def export_artifact(self, output_dir: Path, result: BenchmarkResult) -> None:
        return None


class StepBudgetEnvironment:
    def __init__(self, benchmark: StepBudgetBenchmark) -> None:
        self.benchmark = benchmark

    def run_recovery_bench_agent(
        self,
        *,
        task: Task,
        prompt: str,
        model_client: FakeModelClient,
        context: AgentContext,
        options: dict[str, object] | None = None,
        **_: object,
    ) -> AgentRunResult:
        max_steps = int((options or {})["max_steps"])
        self.benchmark.seen_step_budgets.append((context.protocol, context.attempt_index, max_steps))
        self.benchmark.state.append(f"{context.protocol}-{context.attempt_index}")
        return AgentRunResult(
            actions=(ActionRecord(action={"max_steps": max_steps}),),
            metadata={"max_steps": max_steps},
        )


class CompletionMarkerBenchmark:
    name = "completion-marker"

    def __init__(self) -> None:
        self.business_progress: list[str] = []
        self.completion_marker = False
        self._environment = CompletionMarkerEnvironment(self)

    def load_task(self, task_id: str) -> Task:
        return Task(task_id=task_id, prompt="Repair bad progress.")

    def reset(self, task: Task) -> StateSnapshot:
        self.business_progress = []
        self.completion_marker = False
        return self.snapshot(label="reset")

    def restore(self, snapshot: StateSnapshot) -> StateSnapshot:
        payload = dict(snapshot.payload)
        self.business_progress = list(payload["business_progress"])
        self.completion_marker = bool(payload["completion_marker"])
        self.completion_marker = False
        return self.snapshot(label="restore")

    def snapshot(self, *, label: str | None = None) -> StateSnapshot:
        return StateSnapshot(
            payload={
                "business_progress": list(self.business_progress),
                "completion_marker": self.completion_marker,
            },
            label=label,
        )

    def agent_environment(self) -> "CompletionMarkerEnvironment":
        return self._environment

    def evaluate(self, task: Task) -> TaskOutcome:
        return TaskOutcome(
            success=self.business_progress == ["bad", "repair"],
            details={
                "business_progress": list(self.business_progress),
                "completion_marker": self.completion_marker,
            },
        )

    def list_tasks(self) -> list[str]:
        return ["completion-marker"]

    def export_artifact(self, output_dir: Path, result: BenchmarkResult) -> None:
        return None


class CompletionMarkerEnvironment:
    def __init__(self, benchmark: CompletionMarkerBenchmark) -> None:
        self.benchmark = benchmark

    def run_recovery_bench_agent(
        self,
        *,
        context: AgentContext,
        **_: object,
    ) -> AgentRunResult:
        marker_before = self.benchmark.completion_marker
        if not marker_before and context.protocol == "success":
            self.benchmark.business_progress.append("bad")
            self.benchmark.completion_marker = True
            action = "submit-bad"
        elif not marker_before and context.protocol == "recovery":
            self.benchmark.business_progress.append("repair")
            self.benchmark.completion_marker = True
            action = "repair-and-submit"
        else:
            action = "stopped-by-marker"
        return AgentRunResult(
            actions=(ActionRecord(action=action),),
            metadata={"completion_marker_before_action": marker_before},
        )


class CompletionMarkerAgent:
    name = "completion-marker-agent"

    def run(
        self,
        task: Task,
        prompt: str,
        environment: CompletionMarkerEnvironment,
        context: AgentContext,
    ) -> AgentRunResult:
        return environment.run_recovery_bench_agent(task=task, prompt=prompt, context=context)


class EvalMutatesBenchmark:
    name = "eval-mutates"
    snapshot_after_evaluate = True

    def __init__(self) -> None:
        self.state: list[str] = []
        self.eval_count = 0
        self.restored_payloads: list[list[str]] = []

    def load_task(self, task_id: str) -> Task:
        return Task(task_id=task_id, prompt="exercise post-evaluate snapshot")

    def reset(self, task: Task) -> StateSnapshot:
        self.state = []
        return self.snapshot(label="reset")

    def restore(self, snapshot: StateSnapshot) -> StateSnapshot:
        self.restored_payloads.append(list(snapshot.payload))
        self.state = list(snapshot.payload)
        return self.snapshot(label="restore")

    def snapshot(self, *, label: str | None = None) -> StateSnapshot:
        return StateSnapshot(payload=list(self.state), label=label)

    def agent_environment(self) -> "EvalMutatesBenchmark":
        return self

    def evaluate(self, task: Task) -> TaskOutcome:
        self.eval_count += 1
        self.state.append(f"evaluate-{self.eval_count}")
        return TaskOutcome(success=False)

    def list_tasks(self) -> list[str]:
        return ["eval-mutates"]

    def export_artifact(self, output_dir: Path, result: BenchmarkResult) -> None:
        return None


class EvalMutatesAgent:
    name = "eval-mutates-agent"

    def run(
        self,
        task: Task,
        prompt: str,
        environment: EvalMutatesBenchmark,
        context: AgentContext,
    ) -> AgentRunResult:
        environment.state.append(f"agent-{context.attempt_index}")
        return AgentRunResult(actions=(ActionRecord(action=f"agent-{context.attempt_index}"),))
