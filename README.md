# Recovery-Bench

Recovery-Bench 是一个用于评测 **stateful agent recovery** 的轻量框架。

它不提供某个具体 benchmark 的复刻版，也不把外部 benchmark、数据、Docker/VM 镜像、模型权重或实验结果放进仓库。这个仓库只负责一件事：

> 在不改变原始 benchmark 语义的前提下，把单次任务执行组织成 `Success@1`、`Retry@k` 和 `Recovery@k` 三种协议。

## 核心比较

Recovery-Bench 的主比较是：

```text
Retry@k    : 每次失败后回到 clean initial state，重新做一次官方任务。
Recovery@k : 每次失败后不 reset，下一次 attempt 继承 agent 自己造成的真实失败状态。
```

两者使用相同的 attempt 数量 `k`，区别只在状态和记忆：

- `Retry@k` 是 clean-state rerun；环境 reset，agent memory reset。
- `Recovery@k` 是 stateful continuation；环境继承失败状态，agent 保留完整失败轨迹。

框架要回答的问题是：

> 同样给 agent 多次机会，它只是会从干净状态重试，还是能从自己造成的失败状态里恢复？

## 协议约束

Recovery-Bench 的设计原则是 benchmark invariance：原始 benchmark 该是什么样就是什么样。

必须保持不变：

- 官方 task definition；
- 官方 initial state；
- 官方 action space；
- 官方 evaluator/scorer；
- 官方 success/failure criteria；
- 官方默认参数；
- 官方单次 attempt budget。

框架只改变多次 attempt 的组织方式，不改任务、不改评分、不改动作空间。

每次 attempt 的控制流是：

```text
1. 从某个状态节点开始执行一次官方 attempt。
2. attempt 结束后，先调用官方 evaluator/scorer。
3. 如果成功，当前协议成功结束。
4. 如果失败：
   - Retry 分支回到 clean root state；
   - Recovery 分支从该失败 attempt 留下的真实状态继续。
```

概念图：

```text
clean state S0
  attempt 1
    ├── success -> solved
    └── failure state S1
          ├── retry    -> reset to S0, memory reset
          └── recovery -> continue from S1, memory preserved
```

## 一致性要求

Recovery 的对象必须是 agent 自己造成的失败后真实环境状态。

因此 recovery start state 不能是：

- reset 后的 clean state；
- rollback 后的历史状态；
- 手动修复过的状态；
- 近似 replay 出来的状态；
- 被 evaluator 副作用污染过的状态。

如果官方 evaluator/scorer 会改变环境，adapter 必须隔离评分副作用，例如在 clone、checkpoint、copy-on-write 分支或一次性副本上评分。评分结果用于决定是否进入下一次 attempt，但 recovery 分支必须继续使用未被评分污染的失败状态。

## Budget 和 Memory

每一次 attempt 都拿到完整的官方单次 attempt budget。

例如官方 benchmark 给单次任务 50 steps，那么：

- attempt 1 有 50 steps；
- retry attempt 2 也有 50 steps；
- recovery attempt 2 也有 50 steps。

前一次 attempt 用掉的 steps、tokens、tool calls 或时间，不会扣到下一次 attempt 上。

Memory 规则：

- `Retry@k`：每次都是新的官方运行，agent 不能看到前几次失败轨迹。
- `Recovery@k`：agent 必须保留完整 previous attempts，包括 observation、action、错误轨迹、中间判断和失败后果。

## 仓库包含什么

- `ProtocolRunner`：执行 `Success@1`、`Retry@k`、`Recovery@k`。
- `BenchmarkAdapter` / `AgentAdapter`：稳定接入接口。
- `import_path` plugin loading：外部 adapter 不需要改 core。
- artifact、manifest、summary、CSV/Markdown report 输出。
- 基础 conformance check。
- 一个 smoke benchmark，用于验证协议语义。
- 一个最小外部 adapter 示例。
- 单元测试。

## 仓库不包含什么

- 外部 benchmark 源码或数据。
- 具体外部 benchmark adapter。
- 本地 conda/venv 环境。
- Docker、VM、云环境状态。
- 模型 checkpoint。
- 真实实验 run outputs。

课题组成员接入自己的 benchmark 时，应在独立包、独立分支或本地目录里实现 adapter，然后通过 `import_path` 加载。

## 安装

```bash
python -m pip install -e .
```

运行测试：

```bash
python -m pytest
```

## Smoke Check

内置 smoke benchmark 用来检查 retry 和 recovery 的语义区别：

```bash
PYTHONPATH=src python -m recovery_bench.cli suite \
  --config configs/progress_smoke.toml
```

预期现象：

- `Success@1` 失败；
- `Retry@2` 失败，因为 retry 回到 clean state；
- `Recovery@2` 成功，因为 recovery 继承 attempt 1 留下的进展状态。

输出目录由配置里的 `output_dir` 指定，包含：

- `main.md`
- `summary.md`
- `main.csv`
- `summary.csv`
- `manifest.json`
- `artifacts/*.json`

## 外部 Adapter 示例

仓库提供一个最小外部 adapter 示例：

- [examples/adapters/minimal_recovery_adapter.py](examples/adapters/minimal_recovery_adapter.py)
- [configs/external_minimal_adapter.example.toml](configs/external_minimal_adapter.example.toml)

运行基础契约检查：

```bash
PYTHONPATH=.:src python -m recovery_bench.cli check-benchmark \
  --config configs/external_minimal_adapter.example.toml
```

运行完整 suite：

```bash
PYTHONPATH=.:src python -m recovery_bench.cli suite \
  --config configs/external_minimal_adapter.example.toml
```

## Adapter 接口

新的 benchmark 实现 `BenchmarkAdapter`：

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

新的 agent 实现 `AgentAdapter`：

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

通过 TOML `import_path` 接入外部实现：

```toml
[benchmark]
name = "my-benchmark"
import_path = "my_package.my_benchmark_adapter:build_benchmark"

[agent]
name = "my-agent"
import_path = "my_package.my_agent_adapter:build_agent"
```

推荐 factory 签名：

```python
def build_benchmark(config: BenchmarkConfig, task_ids: tuple[str, ...]) -> BenchmarkAdapter:
    ...

def build_agent(model_config: ModelConfig, agent_config: AgentConfig) -> AgentAdapter:
    ...
```

更完整的接入说明见 [docs/adapter_guide.md](docs/adapter_guide.md)。

## Conformance Check

写完 adapter 后先跑基础契约检查：

```bash
PYTHONPATH=.:src python -m recovery_bench.cli check-benchmark \
  --benchmark my-benchmark \
  --benchmark-import-path my_package.my_benchmark_adapter:build_benchmark \
  --task-id task_001
```

这个检查覆盖：

- `list_tasks`
- `load_task`
- `reset`
- `snapshot`
- `restore`
- `evaluate`
- `capabilities`

注意：conformance check 只检查基础生命周期，不等于证明 strict recovery 正确。真实 benchmark adapter 还需要自己增加状态一致性测试，确认失败状态、评分隔离、restore 后状态和官方 budget 都符合协议。
