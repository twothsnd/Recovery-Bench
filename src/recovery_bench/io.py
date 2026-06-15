from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .types import (
    ActionRecord,
    AgentRunResult,
    AttemptRecord,
    AttemptStatus,
    BenchmarkResult,
    StateSnapshot,
    TaskOutcome,
)


def to_jsonable(value: Any) -> Any:
    """Convert framework records into JSON-safe values."""

    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted(to_jsonable(item) for item in value)
    try:
        json.dumps(value)
    except TypeError:
        return repr(value)
    return value


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(to_jsonable(payload), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def dump_dataclass_json(path: Path, payload: Any) -> None:
    dump_json(path, payload)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_benchmark_result(path: Path) -> BenchmarkResult:
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected object in benchmark result artifact: {path}")
    return benchmark_result_from_json(payload)


def benchmark_result_from_json(payload: dict[str, Any]) -> BenchmarkResult:
    return BenchmarkResult(
        task_id=str(payload["task_id"]),
        protocol=str(payload["protocol"]),
        attempts=tuple(attempt_record_from_json(item) for item in payload.get("attempts", ())),
        success=bool(payload["success"]),
        k=int(payload["k"]),
        metadata=dict(payload.get("metadata") or {}),
    )


def attempt_record_from_json(payload: dict[str, Any]) -> AttemptRecord:
    outcome_payload = payload.get("outcome")
    state_before_payload = payload.get("state_before")
    state_after_payload = payload.get("state_after")
    return AttemptRecord(
        attempt_index=int(payload["attempt_index"]),
        task_id=str(payload["task_id"]),
        prompt=str(payload.get("prompt", "")),
        status=AttemptStatus(str(payload["status"])),
        agent_result=agent_run_result_from_json(dict(payload.get("agent_result") or {})),
        outcome=task_outcome_from_json(outcome_payload) if isinstance(outcome_payload, dict) else None,
        state_before=state_snapshot_from_json(state_before_payload) if isinstance(state_before_payload, dict) else None,
        state_after=state_snapshot_from_json(state_after_payload) if isinstance(state_after_payload, dict) else None,
        notes=dict(payload.get("notes") or {}),
    )


def agent_run_result_from_json(payload: dict[str, Any]) -> AgentRunResult:
    return AgentRunResult(
        actions=tuple(action_record_from_json(item) for item in payload.get("actions", ())),
        metadata=dict(payload.get("metadata") or {}),
        error=payload.get("error"),
    )


def action_record_from_json(payload: dict[str, Any]) -> ActionRecord:
    return ActionRecord(
        action=payload.get("action"),
        observation=payload.get("observation"),
        metadata=dict(payload.get("metadata") or {}),
    )


def task_outcome_from_json(payload: dict[str, Any]) -> TaskOutcome:
    return TaskOutcome(
        success=bool(payload["success"]),
        score=payload.get("score"),
        details=dict(payload.get("details") or {}),
    )


def state_snapshot_from_json(payload: dict[str, Any]) -> StateSnapshot:
    return StateSnapshot(
        payload=payload.get("payload"),
        label=payload.get("label"),
        metadata=dict(payload.get("metadata") or {}),
    )
