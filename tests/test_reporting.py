from recovery_bench.reporting import aggregate_results
from recovery_bench.types import AgentRunResult, AttemptRecord, AttemptStatus, BenchmarkResult, TaskOutcome


def test_aggregate_results_groups_by_benchmark_model_protocol_and_k() -> None:
    result = BenchmarkResult(
        task_id="t1",
        protocol="recovery",
        attempts=(
            AttemptRecord(
                attempt_index=1,
                task_id="t1",
                prompt="p",
                status=AttemptStatus.SUCCESS,
                agent_result=AgentRunResult(),
                outcome=TaskOutcome(success=True),
            ),
        ),
        success=True,
        k=3,
        metadata={"benchmark": "AppWorld", "model": "GPT-4.1"},
    )
    rows = aggregate_results([result])
    assert len(rows) == 1
    assert rows[0].benchmark == "AppWorld"
    assert rows[0].model == "GPT-4.1"
    assert rows[0].protocol == "recovery"
