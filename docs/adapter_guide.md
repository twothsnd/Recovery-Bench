# Recovery-Bench Adapter 接入指南

这份文档说明怎么把一个新的 benchmark 或 agent 接入 Recovery-Bench。核心原则是：

> Core 只执行 recovery 协议；benchmark 和 agent 的具体运行逻辑全部放在 adapter 里。

不要为了接入新 benchmark 去改 `ProtocolRunner`。如果必须改 core，通常说明 adapter 边界没有切清楚。

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
- 隔离 evaluator 副作用，或明确声明不能 strict recovery；
- 暴露 benchmark 原生 action environment 给 agent。

Agent adapter 负责：

- 调用官方 agent 或自定义 agent；
- 在 retry attempt 中不读取前几次失败轨迹；
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
- 如果 evaluator 会改环境，adapter 必须在副本/分支上评分，不能污染 recovery state。
- 如果做不到严格状态一致性，`capabilities().strict_recovery` 必须是 `False`。

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
- `context.previous_attempts`: recovery 时可见的完整失败轨迹；retry 时必须为空
- `context.state_before`: 当前 attempt 开始前的 snapshot

agent 可以是官方 agent、实验室自己的 agent、或者一个 wrapper。Recovery-Bench 不要求 agent 使用统一模型调用方式。

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

core 不解析 action，不猜 benchmark 的 action space。

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
        official_agent="terminus2",
    )
```

`strict_recovery=True` 只能在恢复状态一致性可信时使用。比如 OSWorld 的 Docker live handle 可以跑通，但没有 VM checkpoint，因此不能标成 strict recovery。

## 6. import_path 接入

外部成员不需要把 adapter 注册进主仓库。配置里写 import path 即可：

```toml
[benchmark]
name = "tb2"
import_path = "my_lab.tb2_adapter:build_benchmark"

[agent]
name = "terminus2"
import_path = "my_lab.terminus_adapter:build_agent"
```

factory 函数推荐签名：

```python
def build_benchmark(config: BenchmarkConfig, task_ids: tuple[str, ...]) -> BenchmarkAdapter:
    ...

def build_agent(model_config: ModelConfig, agent_config: AgentConfig) -> AgentAdapter:
    ...
```

`import_path` 同时支持 `module:attribute` 和 `module.attribute`。

## 7. 最小可运行模板

仓库里有一个完整外部 adapter 示例：

- [examples/adapters/minimal_recovery_adapter.py](../examples/adapters/minimal_recovery_adapter.py)
- [configs/external_minimal_adapter.example.toml](../configs/external_minimal_adapter.example.toml)

它不改 core registry，只通过 `import_path` 接入。运行：

```bash
PYTHONPATH=.:src .venv/bin/python -m recovery_bench.cli suite \
  --config configs/external_minimal_adapter.example.toml
```

预期现象：

- `Success@1` 失败；
- `Retry@2` 失败，因为 retry 回 clean state；
- `Recovery@2` 成功，因为 recovery 继承 attempt 1 的 `prepare` 状态。

## 8. Conformance 自检

写完 benchmark adapter 后，先跑基础契约检查：

```bash
PYTHONPATH=.:src .venv/bin/python -m recovery_bench.cli check-benchmark \
  --config configs/external_minimal_adapter.example.toml
```

也可以不写完整 experiment config：

```bash
PYTHONPATH=.:src .venv/bin/python -m recovery_bench.cli check-benchmark \
  --benchmark tb2 \
  --benchmark-import-path my_lab.tb2_adapter:build_benchmark \
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

注意：这是基础检查，不等于严格状态一致性证明。每个真实 benchmark 还应该写自己的 sentinel 测试，例如：

- 在 attempt 1 写入数据库/file/VM；
- snapshot；
- evaluate；
- restore；
- 检查 attempt 2 看到的状态是否和失败状态完全一致；
- 检查 evaluator 是否污染了 recovery state。

## 9. 接入 checklist

接一个新 benchmark 前，逐项确认：

- 是否能不改官方任务定义、action space、evaluator、默认配置；
- 是否能在每次 attempt 后先调用官方 evaluator；
- 是否能在 evaluator 之前保存 uncontaminated failure state，或者隔离 evaluator 副作用；
- 是否能实现 `reset` 到 clean root；
- 是否能实现 `snapshot/restore` 到失败状态；
- recovery 是否能保留完整 previous attempts；
- retry 是否能清空 previous attempts 和 agent memory；
- 每次 attempt 是否拿到完整官方 budget；
- artifact 是否记录足够的 action/observation/debug 信息；
- `capabilities().strict_recovery` 是否和真实工程能力一致。

## 10. TB2 / Harbor 这类 harness 怎么接

Terminal-Bench 2 / Harbor / Terminus2 这类 benchmark 不应该把 harness 逻辑塞进 core。推荐拆法：

- `TB2BenchmarkAdapter`：负责任务列表、容器/文件系统初始状态、checkpoint 或容器状态管理、官方 test/evaluator；
- `Terminus2AgentAdapter`：负责调用 Harbor/Terminus2 的官方 agent loop；
- adapter 之间通过 `agent_environment()` 传递 official harness session 或 task runtime；
- Recovery-Bench core 只负责决定什么时候 clean rerun、什么时候从 failed runtime state 继续。

如果 Harbor 本身没有 strict checkpoint，需要 adapter 明确声明 `strict_recovery=False`，或者实现可验证的容器/filesystem snapshot。
