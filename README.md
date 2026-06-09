# Recovery-Bench

Recovery-Bench is a lightweight framework for evaluating **stateful agent recovery**. It wraps existing agent benchmarks into a common multi-attempt protocol, making it possible to compare clean-state retry against recovery from the agent's own failed state.

The central boundary is benchmark invariance: the original benchmark defines the task, initial state, action space, default configuration, per-attempt budget, and official evaluator or scorer. Recovery-Bench only organizes execution into `Success@1`, `Retry@k`, and `Recovery@k`.

## Core Comparison

Recovery-Bench focuses on the following comparison:

```text
Retry@k    : after each failed attempt, start a new official run from the clean initial state.
Recovery@k : after each failed attempt, continue from the failure state caused by the agent.
```

Both protocols give the agent the same number of attempts `k`. They differ in state and memory:

- `Retry@k` is a clean-state rerun: the environment is reset and agent memory is reset.
- `Recovery@k` is stateful continuation: the environment carries over the failed state and the agent keeps the full failed trajectory.

The framework asks:

> Given the same number of attempts, does the agent merely improve when restarted from scratch, or can it recover from the state it created through its own failed actions?

## Protocol Requirements

Recovery-Bench follows benchmark invariance: the original benchmark semantics remain authoritative.

The original benchmark continues to define:

- official task definition;
- official initial state;
- official action space;
- official evaluator or scorer;
- official success and failure criteria;
- official default configuration;
- official single-attempt budget.

Recovery-Bench adds a multi-attempt scheduling layer on top of those semantics. That layer decides whether the next attempt starts again from the clean state or continues from the real state left by the previous failed attempt.

Each attempt follows this control flow:

```text
1. Start one official attempt from a state node.
2. At attempt end, score it with the official evaluator or scorer.
3. If the attempt succeeds, the protocol succeeds.
4. If the attempt fails:
   - the retry branch returns to the clean root state;
   - the recovery branch continues from the failed attempt's resulting state.
```

Conceptually:

```text
clean state S0
  attempt 1
    ├── success -> solved
    └── failure state S1
          ├── retry    -> reset to S0, memory reset
          └── recovery -> continue from S1, memory preserved
```

## State Consistency

Recovery must target the real environment state caused by the agent's own failed attempt.

A valid recovery start state should:

- materialize the actual terminal state of the previous failed attempt;
- preserve the real side effects and useful progress produced by that attempt;
- be restored through a strict checkpoint, snapshot, clone, copy-on-write branch, or equivalent mechanism;
- keep scoring read-only with respect to that state, or run scoring on an isolated copy;
- present the next attempt with the same state that existed when the previous failed attempt ended.

If the official evaluator or scorer mutates the environment, the adapter must isolate those scoring side effects. The score controls protocol flow, but the recovery branch must continue from the uncontaminated failure state.

## Budget And Memory

Each attempt receives the full official single-attempt budget.

For example, if the official benchmark allows 50 steps for one task attempt:

- attempt 1 receives 50 steps;
- retry attempt 2 receives 50 steps;
- recovery attempt 2 receives 50 steps.

Steps, tokens, tool calls, or wall-clock time consumed by one attempt do not reduce the budget of the next attempt.

Memory rules:

- `Retry@k`: every attempt is a fresh official run, with agent memory reinitialized.
- `Recovery@k`: the agent keeps complete previous attempts, including observations, actions, failed trajectories, intermediate conclusions, and consequences of earlier mistakes.

## Repository Layout

This repository contains the Recovery-Bench framework core:

- `src/recovery_bench/protocol.py`: runs `Success@1`, `Retry@k`, and `Recovery@k`.
- `src/recovery_bench/types.py`: defines the `BenchmarkAdapter` and `AgentAdapter` interfaces.
- `src/recovery_bench/plugins.py`: loads external benchmark and agent adapters through `import_path`.
- `src/recovery_bench/reporting.py`: writes artifacts, manifests, summaries, and CSV/Markdown reports.
- `src/recovery_bench/conformance.py`: provides basic adapter lifecycle checks.
- `src/recovery_bench/adapters/smoke.py`: provides a smoke benchmark for validating protocol semantics.
- `examples/adapters/minimal_recovery_adapter.py`: shows a minimal external adapter.
- `tests/`: covers the framework behavior.

Concrete benchmark source code, datasets, runtime environments, model weights, and experimental outputs should live in their own adapter or experiment repositories. To connect a new benchmark, implement an adapter in a separate package, branch, or local directory, then load it through `import_path`.

## Installation

```bash
python -m pip install -e .
```

Run tests:

```bash
python -m pytest
```

## Smoke Check

The built-in smoke benchmark verifies the semantic difference between retry and recovery:

```bash
PYTHONPATH=src python -m recovery_bench.cli suite \
  --config configs/progress_smoke.toml
```

Expected behavior:

- `Success@1` fails.
- `Retry@2` fails because retry returns to the clean state.
- `Recovery@2` succeeds because recovery inherits the progress left by attempt 1.

The configured `output_dir` receives:

- `main.md`
- `summary.md`
- `main.csv`
- `summary.csv`
- `manifest.json`
- `artifacts/*.json`

## External Adapter Example

The repository includes a minimal external adapter example:

- [examples/adapters/minimal_recovery_adapter.py](examples/adapters/minimal_recovery_adapter.py)
- [configs/external_minimal_adapter.example.toml](configs/external_minimal_adapter.example.toml)

Run the basic contract check:

```bash
PYTHONPATH=.:src python -m recovery_bench.cli check-benchmark \
  --config configs/external_minimal_adapter.example.toml
```

Run the full suite:

```bash
PYTHONPATH=.:src python -m recovery_bench.cli suite \
  --config configs/external_minimal_adapter.example.toml
```

## Adapter Interfaces

New benchmarks implement `BenchmarkAdapter`:

```python
class BenchmarkAdapter(Protocol):
    name: str

    def list_tasks(self) -> list[str]: ...
    def load_task(self, task_id: str) -> Task: ...
    def reset(self, task: Task) -> StateSnapshot: ...
    def snapshot(self, *, label: str | None = None) -> StateSnapshot: ...
    def restore(self, snapshot: StateSnapshot) -> StateSnapshot: ...
    def agent_environment(self) -> Any: ...
    def evaluate(self, task: Task) -> TaskOutcome: ...
    def export_artifact(self, output_dir: Path, result: BenchmarkResult) -> None: ...
```

New agents implement `AgentAdapter`:

```python
class AgentAdapter(Protocol):
    name: str

    def run(
        self,
        task: Task,
        prompt: str,
        environment: Any,
        context: AgentContext,
    ) -> AgentRunResult: ...
```

Use TOML `import_path` fields to connect external implementations:

```toml
[benchmark]
name = "my-benchmark"
import_path = "my_package.my_benchmark_adapter:build_benchmark"

[agent]
name = "my-agent"
import_path = "my_package.my_agent_adapter:build_agent"
```

Recommended factory signatures:

```python
def build_benchmark(config: BenchmarkConfig, task_ids: tuple[str, ...]) -> BenchmarkAdapter:
    ...

def build_agent(model_config: ModelConfig, agent_config: AgentConfig) -> AgentAdapter:
    ...
```

See [docs/adapter_guide.md](docs/adapter_guide.md) for the full adapter guide.

## Conformance Check

After writing an adapter, run the basic contract check:

```bash
PYTHONPATH=.:src python -m recovery_bench.cli check-benchmark \
  --benchmark my-benchmark \
  --benchmark-import-path my_package.my_benchmark_adapter:build_benchmark \
  --task-id task_001
```

The check covers:

- `list_tasks`
- `load_task`
- `reset`
- `snapshot`
- `restore`
- `evaluate`
- `capabilities`

The conformance check only validates the basic lifecycle. Strict recovery also requires benchmark-specific state consistency tests that verify the failure state, scorer isolation, restored state, and official budget behavior.
