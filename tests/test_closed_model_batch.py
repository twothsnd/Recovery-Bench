import json
from pathlib import Path

from scripts.run_closed_model_batch import (
    MODEL_CONFIGS,
    _allocate_sample_counts,
    _load_task_checkpoint,
    _output_complete,
    _stable_sample_task_ids,
    _suite_entries,
)


def test_task_checkpoint_accepts_shared_first_recovery_result(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    _write_checkpoint(artifacts_dir, "task-1", first_success=False, recovery_success_at_2=True)

    checkpoint = _load_task_checkpoint(artifacts_dir, "task-1", (2, 3))

    assert checkpoint is not None
    assert {(result.protocol, result.k) for result in checkpoint} == {
        ("success", 1),
        ("retry", 2),
        ("retry", 3),
        ("recovery", 2),
        ("recovery", 3),
    }


def test_task_checkpoint_rejects_non_monotonic_recovery(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    _write_checkpoint(artifacts_dir, "task-1", first_success=False, recovery_success_at_2=True)
    _write_result(
        artifacts_dir,
        "task-1",
        "recovery",
        3,
        False,
        [(1, False), (2, True)],
    )

    assert _load_task_checkpoint(artifacts_dir, "task-1", (2, 3)) is None


def test_task_checkpoint_rejects_old_non_shared_first_artifact(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    _write_checkpoint(artifacts_dir, "task-1", first_success=True)
    payload = json.loads((artifacts_dir / "retry_k2_task-1.json").read_text(encoding="utf-8"))
    payload["metadata"] = {}
    (artifacts_dir / "retry_k2_task-1.json").write_text(json.dumps(payload), encoding="utf-8")

    assert _load_task_checkpoint(artifacts_dir, "task-1", (2, 3)) is None


def test_task_checkpoint_rejects_mismatched_step_budget(tmp_path: Path) -> None:
    artifacts_dir = tmp_path / "artifacts"
    _write_checkpoint(artifacts_dir, "task-1", first_success=True, max_steps=15)

    assert (
        _load_task_checkpoint(
            artifacts_dir,
            "task-1",
            (2, 3),
            expected_agent_options={"max_steps": 50},
        )
        is None
    )


def test_output_complete_requires_all_task_checkpoints_and_manifest_entries(tmp_path: Path) -> None:
    output_dir = tmp_path / "run"
    artifacts_dir = output_dir / "artifacts"
    _write_checkpoint(artifacts_dir, "task-1", first_success=True)
    (output_dir / "main.md").write_text("partial\n", encoding="utf-8")
    _write_manifest(output_dir / "manifest.json", ("task-1",), (2, 3))

    assert _output_complete(output_dir, ("task-1", "task-2"), (2, 3)) is False

    _write_checkpoint(artifacts_dir, "task-2", first_success=True)
    _write_manifest(output_dir / "manifest.json", ("task-1", "task-2"), (2, 3))

    assert _output_complete(output_dir, ("task-1", "task-2"), (2, 3)) is True


def test_official_suite_selects_standard_benchmark_sets() -> None:
    appworld = _suite_entries("appworld", "official")
    tau_bench = _suite_entries("tau-bench", "official")
    enterpriseops = _suite_entries("enterpriseops-gym", "official")

    assert tuple(entry.label for entry in appworld) == ("test_normal", "test_challenge")
    assert tuple(entry.label for entry in tau_bench) == (
        "airline",
        "retail",
        "telecom",
        "banking_knowledge",
    )
    assert len(enterpriseops) == 8
    assert {entry.label for entry in enterpriseops} == {
        "oracle/calendar",
        "oracle/csm",
        "oracle/drive",
        "oracle/email",
        "oracle/hr",
        "oracle/hybrid",
        "oracle/itsm",
        "oracle/teams",
    }


def test_full_suite_keeps_enterpriseops_extra_tool_modes() -> None:
    enterpriseops = _suite_entries("enterpriseops-gym", "full")

    assert len(enterpriseops) == 32
    assert "plus_5_tools/calendar" in {entry.label for entry in enterpriseops}
    assert "plus_10_tools/calendar" in {entry.label for entry in enterpriseops}
    assert "plus_15_tools/calendar" in {entry.label for entry in enterpriseops}


def test_quick100_sampling_allocates_proportionally_across_official_entries() -> None:
    appworld = _allocate_sample_counts((("test_normal", 168), ("test_challenge", 417)), 100)
    tau_bench = _allocate_sample_counts(
        (
            ("airline", 50),
            ("retail", 114),
            ("telecom", 114),
            ("banking_knowledge", 97),
        ),
        100,
    )
    enterpriseops = _allocate_sample_counts(
        (
            ("oracle/calendar", 61),
            ("oracle/csm", 103),
            ("oracle/drive", 64),
            ("oracle/email", 67),
            ("oracle/hr", 102),
            ("oracle/hybrid", 88),
            ("oracle/itsm", 103),
            ("oracle/teams", 61),
        ),
        100,
    )

    assert appworld == (29, 71)
    assert tau_bench == (13, 31, 30, 26)
    assert enterpriseops == (9, 16, 10, 10, 16, 14, 16, 9)
    assert sum(appworld) == sum(tau_bench) == sum(enterpriseops) == 100


def test_stable_sampling_is_reproducible_without_prefix_bias() -> None:
    task_ids = tuple(f"task-{index}" for index in range(20))

    sample = _stable_sample_task_ids(
        task_ids,
        5,
        sample_seed="seed",
        benchmark_name="appworld",
        suite_entry="test_normal",
    )

    assert len(sample) == 5
    assert sample == _stable_sample_task_ids(
        task_ids,
        5,
        sample_seed="seed",
        benchmark_name="appworld",
        suite_entry="test_normal",
    )
    assert sample != task_ids[:5]


def test_sonnet46_and_opus46_are_separate_model_aliases() -> None:
    assert MODEL_CONFIGS["sonnet46"]["appworld"] == Path("configs/appworld.claude_sonnet46.toml")
    assert MODEL_CONFIGS["opus46"]["appworld"] == Path("configs/appworld.claude_opus46.toml")


def _write_checkpoint(
    artifacts_dir: Path,
    task_id: str,
    *,
    first_success: bool,
    recovery_success_at_2: bool = False,
    max_steps: int | None = None,
) -> None:
    if first_success:
        attempts = [(1, True)]
        for protocol in ("success", "retry", "recovery"):
            if protocol == "success":
                _write_result(artifacts_dir, task_id, protocol, 1, True, attempts, max_steps=max_steps)
            else:
                _write_result(artifacts_dir, task_id, protocol, 2, True, attempts, max_steps=max_steps)
                _write_result(artifacts_dir, task_id, protocol, 3, True, attempts, max_steps=max_steps)
        return

    first_attempt = [(1, False)]
    _write_result(artifacts_dir, task_id, "success", 1, False, first_attempt, max_steps=max_steps)
    _write_result(artifacts_dir, task_id, "retry", 2, False, [(1, False), (2, False)], max_steps=max_steps)
    _write_result(
        artifacts_dir,
        task_id,
        "retry",
        3,
        False,
        [(1, False), (2, False), (3, False)],
        max_steps=max_steps,
    )
    recovery_attempts = [(1, False), (2, recovery_success_at_2)]
    _write_result(
        artifacts_dir,
        task_id,
        "recovery",
        2,
        recovery_success_at_2,
        recovery_attempts,
        max_steps=max_steps,
    )
    _write_result(
        artifacts_dir,
        task_id,
        "recovery",
        3,
        recovery_success_at_2,
        recovery_attempts,
        max_steps=max_steps,
    )


def _write_result(
    artifacts_dir: Path,
    task_id: str,
    protocol: str,
    k: int,
    success: bool,
    attempts: list[tuple[int, bool]],
    max_steps: int | None = None,
) -> None:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "task_id": task_id,
        "protocol": protocol,
        "k": k,
        "success": success,
        "attempts": [
            {
                "attempt_index": index,
                "agent_result": {
                    "metadata": {"max_steps": max_steps} if max_steps is not None else {},
                },
                "outcome": {"success": attempt_success},
            }
            for index, attempt_success in attempts
        ],
        "metadata": {"comparison": "shared-first-attempt"},
    }
    (artifacts_dir / f"{protocol}_k{k}_{task_id}.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def _write_manifest(path: Path, task_ids: tuple[str, ...], report_k_values: tuple[int, ...]) -> None:
    results = []
    for task_id in task_ids:
        results.append({"task_id": task_id, "protocol": "success", "k": 1})
        for protocol in ("retry", "recovery"):
            for k in report_k_values:
                results.append({"task_id": task_id, "protocol": protocol, "k": k})
    path.write_text(json.dumps({"results": results}), encoding="utf-8")
