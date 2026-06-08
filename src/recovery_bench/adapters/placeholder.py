from __future__ import annotations

from .base import StaticBenchmarkAdapter
from ..types import Task


def build_placeholder_benchmark() -> StaticBenchmarkAdapter:
    tasks = {
        "demo-1": Task(task_id="demo-1", prompt="Placeholder task for framework validation."),
    }
    return StaticBenchmarkAdapter(name="placeholder", tasks=tasks)
