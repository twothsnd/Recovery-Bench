from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..types import ActionRecord, AgentCapabilities, AgentContext, AgentRunResult, Task


@dataclass(slots=True)
class ProgressSmokeAgent:
    """Deterministic agent for protocol smoke tests.

    The agent intentionally needs inherited progress to finish within two
    attempts. A retry run resets that progress and should fail at k=2.
    """

    name: str = "progress-smoke-agent"

    def run(
        self,
        task: Task,
        prompt: str,
        environment: Any,
        context: AgentContext,
    ) -> AgentRunResult:
        if not isinstance(environment, dict):
            return AgentRunResult(error="ProgressSmokeAgent requires a dict environment")

        progress = environment.setdefault("progress", [])
        actions: list[ActionRecord] = []

        if "prepared" not in progress:
            progress.append("prepared")
            actions.append(ActionRecord(action="prepare", observation={"progress": list(progress)}))
            return AgentRunResult(actions=tuple(actions), metadata={"task": task.task_id})

        if "finished" not in progress:
            progress.append("finished")
            actions.append(ActionRecord(action="finish", observation={"progress": list(progress)}))

        return AgentRunResult(actions=tuple(actions), metadata={"task": task.task_id})

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            memory_mode="environment_state_only",
            retry_memory_reset="new_attempt_context",
            recovery_memory="stateful_environment_progress",
            trajectory_export="action_records",
            official_agent="test_agent",
        )
