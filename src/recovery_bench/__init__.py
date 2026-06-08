"""Recovery@k evaluation framework."""

from .conformance import (
    ConformanceCheck,
    ConformanceReport,
    format_conformance_report,
    run_basic_benchmark_conformance,
)
from .config import BenchmarkConfig, ExperimentConfig, ModelConfig
from .protocol import ProtocolRunner
from .types import (
    ActionRecord,
    AgentAdapter,
    AgentCapabilities,
    AgentContext,
    AgentRunResult,
    AttemptRecord,
    AttemptStatus,
    BenchmarkAdapter,
    BenchmarkCapabilities,
    BenchmarkResult,
    ProtocolMode,
    StateSnapshot,
    Task,
    TaskOutcome,
)

__all__ = [
    "ActionRecord",
    "AgentAdapter",
    "AgentCapabilities",
    "AgentContext",
    "AgentRunResult",
    "AttemptRecord",
    "AttemptStatus",
    "BenchmarkAdapter",
    "BenchmarkCapabilities",
    "BenchmarkConfig",
    "BenchmarkResult",
    "ConformanceCheck",
    "ConformanceReport",
    "ExperimentConfig",
    "ModelConfig",
    "ProtocolMode",
    "ProtocolRunner",
    "StateSnapshot",
    "Task",
    "TaskOutcome",
    "format_conformance_report",
    "run_basic_benchmark_conformance",
]
