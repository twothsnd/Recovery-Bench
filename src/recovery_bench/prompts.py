from __future__ import annotations

import json
from typing import Any

from .types import AttemptRecord, Task


def make_task_prompt(task: Task) -> str:
    parts = [
        "Complete the original task.",
        "",
        "Original task:",
        task.prompt.strip(),
    ]
    return "\n".join(parts).strip() + "\n"


def make_recovery_prompt(task: Task, *, attempt_index: int) -> str:
    parts = [
        "Your previous attempt failed.",
        "",
        "The environment has NOT been reset.",
        "You are continuing from the current state left by your previous attempt.",
        "",
        "Some previous actions may have changed the environment.",
        "Repair any incorrect changes if needed.",
        "Preserve any useful progress.",
        "Complete the original task.",
        "",
        "Original task:",
        task.prompt.strip(),
    ]
    return "\n".join(parts).strip() + "\n"


def prefix_with_previous_attempt_trajectory(
    prompt: str,
    previous_attempts: tuple[AttemptRecord, ...],
    *,
    max_chars: int = 0,
    max_observation_chars: int = 0,
) -> str:
    if not previous_attempts:
        return prompt
    trajectory = _format_attempt_trajectory(
        previous_attempts,
        max_observation_chars=max_observation_chars,
    )
    if max_chars > 0 and len(trajectory) > max_chars:
        trajectory = "[TRUNCATED PREVIOUS TRAJECTORY]\n" + trajectory[-max_chars:]
    return (
        "Previous failed attempt trajectory:\n"
        f"{trajectory.strip()}\n\n"
        "Recovery instruction:\n"
        f"{prompt.strip()}\n"
    )


def _format_attempt_trajectory(
    previous_attempts: tuple[AttemptRecord, ...],
    *,
    max_observation_chars: int,
) -> str:
    blocks: list[str] = []
    for attempt in previous_attempts:
        status = getattr(attempt.status, "value", str(attempt.status))
        blocks.append(f"Attempt {attempt.attempt_index} status: {status}")
        if attempt.agent_result.error:
            blocks.append(f"Agent error: {attempt.agent_result.error}")
        for index, action in enumerate(attempt.agent_result.actions, start=1):
            assistant_content = action.metadata.get("assistant_content") or action.metadata.get("content")
            if assistant_content:
                blocks.append(f"Assistant message {index}:")
                blocks.append(_format_value(assistant_content))
            blocks.append(f"Action {index}:")
            blocks.append(_format_value(action.action))
            if action.observation is not None:
                observation = _format_value(action.observation)
                if max_observation_chars > 0 and len(observation) > max_observation_chars:
                    observation = observation[:max_observation_chars] + "\n[OBSERVATION TRUNCATED]"
                blocks.append(f"Observation {index}:")
                blocks.append(observation)
        if attempt.outcome is not None:
            blocks.append(f"Evaluator success after attempt {attempt.attempt_index}: {attempt.outcome.success}")
        blocks.append("")
    return "\n".join(blocks).strip()


def _format_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except Exception:
        return str(value)
