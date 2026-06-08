from recovery_bench.prompts import (
    make_recovery_prompt,
    make_task_prompt,
    prefix_with_previous_attempt_trajectory,
)
from recovery_bench.types import ActionRecord, AgentRunResult, AttemptRecord, AttemptStatus, Task


def test_task_prompt_contains_task_text() -> None:
    task = Task(task_id="t1", prompt="Do the thing.")
    prompt = make_task_prompt(task)
    assert "Do the thing." in prompt
    assert "Original task:" in prompt


def test_recovery_prompt_mentions_state_inheritance() -> None:
    task = Task(task_id="t1", prompt="Do the thing.")
    prompt = make_recovery_prompt(task, attempt_index=2)
    assert "NOT been reset" in prompt
    assert "Attempt number" not in prompt
    assert "Do the thing." in prompt


def test_recovery_prompt_can_be_prefixed_with_previous_trajectory() -> None:
    base_prompt = "Your previous attempt failed.\n\nOriginal task:\nDo it.\n"
    previous = (
        AttemptRecord(
            attempt_index=1,
            task_id="t1",
            prompt="Do it.",
            status=AttemptStatus.FAILED,
            agent_result=AgentRunResult(
                actions=(
                    ActionRecord(
                        action={"tool": "create_item", "arguments": {"name": "bad"}},
                        observation={"success": True, "id": "item-1"},
                        metadata={"assistant_content": "I will create the item now."},
                    ),
                )
            ),
        ),
    )

    prompt = prefix_with_previous_attempt_trajectory(base_prompt, previous)

    assert prompt.startswith("Previous failed attempt trajectory:")
    assert "Assistant message 1:" in prompt
    assert "I will create the item now." in prompt
    assert "Action 1:" in prompt
    assert "create_item" in prompt
    assert "Observation 1:" in prompt
    assert "Recovery instruction:" in prompt
    assert prompt.rstrip().endswith("Do it.")
