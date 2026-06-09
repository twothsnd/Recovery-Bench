# Recovery-Bench Adapter 接入指南

这份文档说明怎么把一个新的 benchmark 或 agent 接入 Recovery-Bench。核心原则是：

> Core 负责 recovery 协议调度；benchmark 和 agent 的具体运行逻辑全部放在 adapter 里。

接入新 benchmark 时，优先把变化收敛在 adapter 层。需要改 `ProtocolRunner` 的情况，先重新检查 adapter 边界是否已经切清楚。

## 1. 责任边界

Recovery-Bench core 负责：

- 组织 `Success@1`、`Retry@k`、`Recovery@k`；
- 保证每次 attempt 后先调用官方 evaluator/scorer；
- 在第一次失败后分出 retry/recovery 两个分支；
- retry 时 reset 环境并清空 agent previous attempts；
- recovery 时继承失败后的真实状态并传入完整 previous attempts；
- 给每个 attempt 刷新官方单次 attempt budget；
- 写 artifact、manifest、summary 和 capability 信息。

Benchmark adapter 负责：

- 加载官方任务；
- 按官方方式创建 clean initial state；
- 捕获失败 attempt 后的真实状态；
- 从 snapshot/checkpoint/live handle 恢复环境；
- 调用官方 evaluator/scorer；
- 隔离 evaluator 副作用，并在 capability 里声明 strict recovery 支持情况；
- 暴露 benchmark 原生 action environment 给 agent。

Agent adapter 负责：

- 调用官方 agent 或自定义 agent；
- 在 retry attempt 中使用全新 agent session 和空 previous attempts；
- 在 recovery attempt 中保留并使用完整 previous attempts；
- 把动作真正执行到 benchmark environment；
- 返回可审计的 action/observation 轨迹。

## 2. BenchmarkAdapter 接口

新 benchmark 需要实现 `recovery_bench.types.BenchmarkAdapter`：

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

关键语义：

- `reset(task)` 必须回到官方 clean initial state。
- `snapshot()` 必须捕获 recovery 所需的真实环境状态。
- `restore(snapshot)` 必须让下一次 recovery attempt 看到同一个失败状态。
- `evaluate(task)` 必须使用官方 evaluator/scorer。
- 如果 evaluator 会改环境，adapter 必须在副本/分支上评分，让评分副作用留在隔离环境里。
- `capabilities().strict_recovery` 应反映真实状态能力：精确 snapshot/restore 标为 `True`，近似恢复或 live-handle-only 标为 `False`。

## 3. AgentAdapter 接口

新 agent 需要实现 `recovery_bench.types.AgentAdapter`：

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

`context` 是协议传给 agent 的结构化信息，最重要的是：

- `context.protocol`: `success` / `retry` / `recovery`
- `context.attempt_index`: 当前是第几次 attempt
- `context.k`: 当前协议允许的最大 attempts
- `context.previous_attempts`: recovery 时可见的完整失败轨迹；retry 时传入空 tuple
- `context.state_before`: 当前 attempt 开始前的 snapshot

agent 可以是官方 agent、实验室自己的 agent、或者一个 wrapper。模型调用、工具调用和动作循环由 agent adapter 自己决定。

## 4. ProviderAgent Bridge

如果使用内置 `openai-agent` / `anthropic-agent` / `vllm-agent` 这类 provider-backed agent，benchmark 的 `agent_environment()` 应该暴露一个 bridge 方法：

```python
def run_recovery_bench_agent(
    *,
    task: Task,
    prompt: str,
    model_client: ModelClient,
    context: AgentContext,
    options: dict,
) -> AgentRunResult:
    ...
```

这个 bridge 负责 benchmark-specific 的事情：

- 把模型输出解析成官方 action；
- 调用官方 step/tool/command API；
- 收集 observation；
- 控制每个 attempt 的 step budget；
- 返回 `AgentRunResult`。

action parsing 和 action-space handling 由 bridge/adapter 负责。

## 5. Capability 声明

每个正式 adapter 都应该实现 `capabilities()`。这是 manifest 里判断结果可信度的依据。

Benchmark capability 示例：

```python
from recovery_bench.types import BenchmarkCapabilities

def capabilities(self) -> BenchmarkCapabilities:
    return BenchmarkCapabilities(
        state_materialization="official_checkpoint",
        state_snapshot="strict",
        restore_strategy="provider-checkpoint",
        evaluator_isolation="pre_evaluate_checkpoint",
        budget_reset="per_attempt_full",
        official_invariance="official_harness",
        official_harness="my-benchmark",
        strict_recovery=True,
    )
```

Agent capability 示例：

```python
from recovery_bench.types import AgentCapabilities

def capabilities(self) -> AgentCapabilities:
    return AgentCapabilities(
        memory_mode="agent_native_memory",
        retry_memory_reset="new_agent_session",
        recovery_memory="same_agent_session_plus_previous_attempts",
        trajectory_export="action_records",
        official_agent="official-wrapper-agent",
    )
```

`strict_recovery=True` 表示 adapter 可以把失败 attempt 的终止状态精确 materialize，并让后续 recovery attempt 从同一状态继续。live-handle continuation、近似 replay、或缺少可验证 restore 机制的实现，应声明 `strict_recovery=False`。

## 6. import_path 接入

外部成员通过配置里的 import path 接入 adapter：

```toml
[benchmark]
name = "my-benchmark"
import_path = "my_lab.my_benchmark_adapter:build_benchmark"

[agent]
name = "my-agent"
import_path = "my_lab.my_agent_adapter:build_agent"
```

factory 函数推荐签名：

```python
def build_benchmark(config: BenchmarkConfig, task_ids: tuple[str, ...]) -> BenchmarkAdapter:
    ...

def build_agent(model_config: ModelConfig, agent_config: AgentConfig) -> AgentAdapter:
    ...
```

`import_path` 同时支持 `module:attribute` 和 `module.attribute`。

## 7. 组员自己的 benchmark 项目怎么组织

具体 benchmark 的数据、源码、运行状态和结果目录由 benchmark 子项目自己决定。推荐组织方式是：

> 每个组员把自己要接的 benchmark 做成一个自包含子项目，并通过 `import_path` 调用 Recovery-Bench 框架。

这个子项目可以放在 Recovery-Bench 目录外，也可以作为本地子文件夹放在同一 workspace 里。它应该自己管理：

- adapter 代码；
- config 模板；
- 官方 benchmark 源码或 harness；
- 数据集、任务文件、初始数据库；
- checkpoint、复制数据库、VM/container state 等运行状态；
- 实验输出。

一个典型结构是：

```text
mybench_recovery/
  README.md
  configs/
    mybench.local.toml
  mybench_adapter/
    __init__.py
    benchmark_adapter.py
    agent_adapter.py
  external/
    official_benchmark_source/
  data/
    tasks_or_databases/
  state/
    checkpoints_or_runtime_state/
  runs/
    dev/
```

其中：

- `mybench_adapter/` 是 Python package，提供 `BenchmarkAdapter` 和 `AgentAdapter` factory；
- `configs/mybench.local.toml` 写清楚 adapter import path、数据路径、源码路径、state 路径和输出路径；
- `external/`、`data/`、`state/`、`runs/` 是该子项目自己的目录约定，路径语义由 config 和 adapter 定义；
- 子项目自己的 `README.md` 应该说明数据怎么下载、官方源码怎么准备、需要哪些环境变量、如何运行 smoke test。

示例 config：

```toml
[experiment]
output_dir = "/abs/path/to/mybench_recovery/runs/dev"
k_values = [1, 2, 3]
task_ids = ["task_001"]

[benchmark]
name = "mybench"
import_path = "mybench_adapter.benchmark_adapter:build_benchmark"

[benchmark.options]
dataset_path = "/abs/path/to/mybench_recovery/data"
source_path = "/abs/path/to/mybench_recovery/external/official_benchmark_source"
state_dir = "/abs/path/to/mybench_recovery/state"

[agent]
name = "my-agent"
import_path = "mybench_adapter.agent_adapter:build_agent"

[model]
name = "example-model"
provider = "local"
```

运行时把 Recovery-Bench core 和 benchmark 子项目都放进 `PYTHONPATH`：

```bash
PYTHONPATH=/abs/path/to/Recovery-Bench/src:/abs/path/to/mybench_recovery \
python -m recovery_bench.cli suite \
  --config /abs/path/to/mybench_recovery/configs/mybench.local.toml
```

注意路径语义：

- `data`、`external` 或 `runs` 的含义来自 config 和 adapter，目录名仅作示例；
- config 里的相对路径按运行命令的当前工作目录解析；
- 为了让组员之间复现实验，正式 config 推荐使用绝对路径，或者在子项目 README 里明确要求从哪个目录运行；
- `experiment.output_dir` 是 Recovery-Bench 写 artifact、manifest、summary、CSV/Markdown report 的位置；
- `benchmark.options.*` 会原样传给 benchmark adapter，由 adapter 自己解释。

这样每个 benchmark 的复杂性都留在自己的子项目里，Recovery-Bench 主仓库保留协议、接口、runner 和 report 逻辑。

## 8. 最小可运行模板

仓库里有一个完整 example benchmark：

- [example_benchmark/adapter.py](../example_benchmark/adapter.py)
- [configs/example_benchmark.example.toml](../configs/example_benchmark.example.toml)

它通过 `import_path` 接入 core registry。运行：

```bash
PYTHONPATH=.:src .venv/bin/python -m recovery_bench.cli suite \
  --config configs/example_benchmark.example.toml
```

预期现象：

- `Success@1` 失败；
- `Retry@2` 失败，因为 retry 回 clean state；
- `Recovery@2` 成功，因为 recovery 继承 attempt 1 的 `prepare` 状态。

## 9. Conformance 自检

写完 benchmark adapter 后，先跑基础契约检查：

```bash
PYTHONPATH=.:src .venv/bin/python -m recovery_bench.cli check-benchmark \
  --config configs/example_benchmark.example.toml
```

也可以直接在命令行传入必要参数：

```bash
PYTHONPATH=.:src .venv/bin/python -m recovery_bench.cli check-benchmark \
  --benchmark my-benchmark \
  --benchmark-import-path my_lab.my_benchmark_adapter:build_benchmark \
  --task-id task_001
```

这个检查会覆盖：

- `list_tasks`
- `load_task`
- `reset`
- `snapshot`
- `restore`
- `evaluate`
- `capabilities`

注意：这是基础 lifecycle 检查。严格状态一致性需要每个真实 benchmark 增加自己的 sentinel 测试，例如：

- 在 attempt 1 写入数据库/file/VM；
- snapshot；
- evaluate；
- restore；
- 检查 attempt 2 看到的状态是否和失败状态完全一致；
- 检查 evaluator 是否污染了 recovery state。

## 10. 接入 checklist

接一个新 benchmark 前，逐项确认：

- 官方任务定义、action space、evaluator、默认配置保持原样；
- 每次 attempt 后先调用官方 evaluator；
- evaluator 之前保存 uncontaminated failure state，或者隔离 evaluator 副作用；
- `reset` 回到 clean root；
- `snapshot/restore` 回到失败状态；
- recovery 保留完整 previous attempts；
- retry 清空 previous attempts 和 agent memory；
- 每次 attempt 拿到完整官方 budget；
- artifact 记录足够的 action/observation/debug 信息；
- `capabilities().strict_recovery` 和真实工程能力一致。

## 11. 复杂 harness 怎么接

带有独立 harness、容器、官方 agent loop 或 evaluator runner 的 benchmark，推荐把 harness 逻辑放在 adapter 或 benchmark 子项目里。推荐拆法：

- `MyBenchmarkAdapter`：负责任务列表、容器/文件系统/数据库/VM 初始状态、checkpoint 或状态管理、官方 test/evaluator；
- `MyAgentAdapter`：负责调用官方 agent loop 或实验室自己的 agent loop；
- adapter 之间通过 `agent_environment()` 传递 official harness session 或 task runtime；
- Recovery-Bench core 负责决定什么时候 clean rerun、什么时候从 failed runtime state 继续。

官方 harness 缺少 strict checkpoint 时，adapter 在 capability 里声明 `strict_recovery=False`；具备可验证的容器/filesystem/database/VM snapshot 后，再声明 strict recovery 支持。
