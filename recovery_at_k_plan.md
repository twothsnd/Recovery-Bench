# Recovery@k 插拔评测方案

核心观点：

> agent 继承自己上一次失败留下的状态，不 reset、不 clean retry，而是在已有坏状态上 repair-and-continue。

## 1. 指标定义

给定任务 `T`、agent `M`、最大尝试次数 `k`。

```text
Recovery@k = 1
if agent completes T within k sequential attempts
while inheriting the environment state after each failed attempt.
```

关键约束：

```text
attempt_i 的初始状态 = attempt_{i-1} 失败后留下的状态
不 reset
不 clean retry
不丢弃前一次失败造成的副作用
```

对比：

```text
pass@k:
独立采样 k 个答案，互不影响。

retry@k:
每次失败后 reset 到 clean state。

recovery@k:
每次失败后继承坏状态，继续 repair-and-continue。
```

## 2. 评测协议

对每个 benchmark task：

```text
1. 初始化环境到 clean state S0。
2. Agent 执行 attempt 1。
3. 如果成功，Recovery@k = 1。
4. 如果失败，保留失败后的状态 S1。
5. Agent 从 S1 开始 attempt 2。
6. prompt 告诉 agent：
   - 上一次失败了
   - 环境没有 reset
   - 你必须从当前状态继续
   - 修复错误副作用
   - 保留正确进展
   - 完成原任务
7. 重复直到 attempt k。
8. k 次内成功则 Recovery@k = 1，否则 0。
```

伪代码：

```python
def recovery_at_k(agent, env, task, k):
    env.reset(task)
    history = []

    for i in range(1, k + 1):
        if i == 1:
            prompt = make_task_prompt(task)
        else:
            prompt = make_recovery_prompt(task, history)

        traj = agent.run(env, prompt)
        success = env.evaluate(task)

        history.append(traj)

        if success:
            return 1

    return 0
```

## 3. Recovery Prompt

attempt 2 到 attempt k 统一使用：

```text
Your previous attempt failed.

The environment has NOT been reset.
You are continuing from the current state left by your previous attempt.

Some previous actions may have changed the environment.
Repair any incorrect changes if needed.
Preserve any useful progress.
Complete the original task.

Original task:
{task}
```

默认协议先使用这个稳定 recovery prompt，不把复杂 trace 压缩放进核心指标定义。后续可以把 trace 摘要作为可控 ablation，而不是改变 Recovery@k 本身。

## 4. 必须同时报告 Retry@k

为了证明这不是“多试几次”，每个任务同时跑：

```text
Retry@k:
每次失败后 reset 到 S0，再重新尝试。

Recovery@k:
每次失败后不 reset，继承当前状态继续。
```

主对比：

```text
Retry@k - Recovery@k
```

解释：

```text
Retry@k 高，Recovery@k 低：
模型会重新做，但不会修复继承状态。

Recovery@k 接近 Retry@k：
模型能处理自己失败留下的状态。

Recovery@k 高于 Retry@k：
模型能利用已有正确进展，repair-and-continue 有价值。
```

## 5. 先跑的 benchmark

直接在现有 agentic benchmark 上挂这个 protocol。

第一批目标固定为四个 benchmark：

```text
1. AppWorld
2. τ-bench
3. ClawsBench
4. EnterpriseOps-Gym
```

WebArena、OSWorld / Terminal-Bench 放到第二批扩展；第一版不要把它们混进首批任务集，避免实验口径漂移。

原因：

```text
它们都有明确的环境状态或业务状态；
agent action 会改变数据库 / app state / enterprise workflow state；
更适合体现“状态继承”和失败副作用修复。
```

## 6. k 的设置

先跑：

```text
k = 1, 2, 3
```

报告：

```text
Success@1
Retry@2
Recovery@2
Retry@3
Recovery@3
```

不要一开始跑太大 k。`k=3` 足够看趋势。

## 7. 模型设置

先测闭源模型：

```text
GPT-4.1
Claude Sonnet
Gemini 2.5 Pro
```

每个模型在每个 benchmark 上跑相同任务集。

## 8. 输出表格

主表：

| Benchmark | Model | Success@1 | Retry@2 | Recovery@2 | Retry@3 | Recovery@3 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| AppWorld | GPT-4.1 |  |  |  |  |  |
| AppWorld | Claude Sonnet |  |  |  |  |  |
| AppWorld | Gemini 2.5 Pro |  |  |  |  |  |
| τ-bench | GPT-4.1 |  |  |  |  |  |
| τ-bench | Claude Sonnet |  |  |  |  |  |
| τ-bench | Gemini 2.5 Pro |  |  |  |  |  |
| ClawsBench | GPT-4.1 |  |  |  |  |  |
| ClawsBench | Claude Sonnet |  |  |  |  |  |
| ClawsBench | Gemini 2.5 Pro |  |  |  |  |  |
| EnterpriseOps-Gym | GPT-4.1 |  |  |  |  |  |
| EnterpriseOps-Gym | Claude Sonnet |  |  |  |  |  |
| EnterpriseOps-Gym | Gemini 2.5 Pro |  |  |  |  |  |

额外报告：

```text
Recovery Gap = Retry@k - Recovery@k
```

## 9. 可选状态指标

如果 benchmark 支持 state diff，再加：

```text
Repair Rate:
前一次失败造成的错误状态，有多少被修复。

Collateral Damage:
recovery 过程中有没有新增错误状态。

Progress Preservation:
前一次 attempt 中已经正确完成的部分，有没有保留。
```

这些指标放在可选 state-diff 扩展层。核心 runner 先要求所有 benchmark 都能稳定 reset、snapshot、restore、evaluate；支持 state diff 的 benchmark 再额外报告这些状态质量指标。

## 10. 稳健实验规模

```text
Benchmarks:
AppWorld, τ-bench, ClawsBench, EnterpriseOps-Gym

Tasks:
每个 benchmark 50–100 个任务；如果新 benchmark 任务规模较小，先全量跑

Models:
3 个闭源模型

k:
1, 2, 3

Metrics:
Success@1, Retry@k, Recovery@k
```

这个规模不是一次性 demo，而是第一批稳定可复现实验单元。任务集、模型配置、k 值、adapter 版本、下载归档和运行 manifest 都需要落盘，保证后续能扩展到更多 benchmark 和模型。

## 11. 预期观察

重点看三件事：

```text
1. Recovery@k 是否明显低于 Retry@k。
   说明状态继承是独立难点。

2. Recovery@k 是否随 k 增长。
   说明多次 repair-and-continue 是否有用。

3. 不同模型的 Recovery@k 排名是否不同于 Success@1 / Retry@k。
   说明 recovery 是独立能力。
```

## 12. 一句话总结

```text
Recovery@k 是 pass@k 在 agentic setting 下的状态继承版本：
不是独立采样，不是 reset 重试，而是连续继承失败状态，在已有坏状态上修复并完成任务。
```

## 13. 实现路线：稳健优先

### 阶段 0：稳定仓库骨架

- 建立长期可维护目录结构：`src/`, `configs/`, `docs/`, `scripts/`, `tests/`, `runs/`
- 定义不可随 benchmark 摇摆的核心数据结构：`Task`, `StateSnapshot`, `AgentRunResult`, `AttemptRecord`, `BenchmarkResult`
- 明确协议核心只负责状态流和指标，不解析 benchmark 动作、不绑定任何模型 SDK

### 阶段 1：协议 runner

- 实现 `success@1`, `retry@k`, `recovery@k` 的共享执行框架
- 强制区分两种状态流：
  - `retry`: 每次失败后 `reset(task)`
  - `recovery`: 每次失败后 `restore(previous_state_after)`
- 每个 attempt 都记录 prompt、状态快照、agent 轨迹、evaluation outcome 和错误信息
- 保证 benchmark adapter 的 `close()` 会被调用，避免跨任务资源泄漏

### 阶段 2：benchmark adapter

- 第一批接 `AppWorld`、`τ-bench`、`ClawsBench`、`EnterpriseOps-Gym`，adapter 形状按后续 WebArena、OSWorld、Terminal-Bench 扩展设计
- 每个 adapter 必须封装：
  - `load_task(task_id)`
  - `reset(task)`
  - `snapshot(label)`
  - `restore(snapshot)`
  - `agent_environment()`
  - `evaluate(task)`
  - `export_artifact(output_dir, result)`
- benchmark-specific action loop 放在 adapter bridge 里，不进入协议核心
- 拉取外部资源统一用 `wget` 下载压缩包并落到 `external/`，不依赖 `git pull`

### 阶段 3：agent / provider 层

- agent registry 必须接收 `ModelConfig` 和 `AgentConfig`，不能只靠 agent 名字
- provider SDK 懒加载，`list-agents` 显示可用/不可用和原因
- provider agent 不猜动作格式，只调用 benchmark 暴露的 `run_recovery_bench_agent(...)` bridge
- AppWorld bridge 默认走：模型输出 Python code block -> `world.execute(code)`
- τ-bench bridge 默认走：模型输出 action string -> `env.step(action)`
- EnterpriseOps-Gym bridge 默认走：模型输出 JSON MCP tool call -> 官方 MCP server；Recovery 继承 live database id，Retry 重建 seed database
- `[agent.options]` 控制步数、桥接策略等运行参数

### 阶段 4：官方依赖获取

- 外部 benchmark 源码只走官方来源：官方 GitHub archive/codeload 或官方 PyPI source distribution
- 不用 `git pull` 更新 benchmark；统一用 `wget` 下载压缩包，校验归档有效后再解压到 `external/`
- GitHub 普通 archive URL 失败时，自动改写到官方 `codeload.github.com`
- 当前网络把 GitHub DNS 路由到 TLS-resetting endpoint 时，再用官方 codeload IP + `Host: codeload.github.com` 兜底
- 每个下载归档保存在 `external/<benchmark>/archives/`，源码保存在 `external/<benchmark>/src/`
- Python 运行依赖从已下载的本地官方源码 checkout 安装，安装脚本和下载脚本分离，避免把源码拉取和环境修改混在一起
- AppWorld 数据单独从官方 S3 `data-<version>.bundle` 下载，用 AppWorld 自带 bundle 解包逻辑展开到 `APPWORLD_ROOT/data`
- AppWorld 数据下载默认使用可续传 `wget`; 官方 S3 单连接过慢时，脚本支持并行 HTTP byte-range `wget`，分片拼回后校验总字节数
- 如果 DNS 选到慢 S3 节点，脚本允许 pin 官方 S3 IP，同时保留 `Host: appworld.dev.s3.amazonaws.com`
- AppWorld GitHub archive 中的 Git LFS source bundles 不在 `external/appworld/src` 内原地解包；脚本会复制到 `external/appworld/runtime` 后再 materialize，保持官方 archive extraction 不变
- EnterpriseOps-Gym 源码包内含官方 `gym_dbs.zip`；任务 config 来自官方 Hugging Face dataset `ServiceNow-AI/EnterpriseOps-Gym` 或本地 materialized config folder；本地缓存路径约定为 `external/enterpriseops-gym/tasks/<mode>/<domain>`，由 `scripts/download_enterpriseops_tasks.sh` 生成
- ClawsBench 当前官方 repo 只有网站和轨迹占位，并声明 tasks 后续添加；在 executable tasks/environments 发布前只作为首批 planned target，不伪造 adapter 成绩

### 阶段 5：prompt 与任务编排

- 实现 `make_task_prompt(task)`
- 实现稳定 recovery prompt：明确没有 reset、需要修复副作用、保留正确进展、完成原任务
- trace 摘要、历史压缩、错误诊断作为后续 ablation，不污染默认 Recovery@k 定义

### 阶段 6：指标、报表和复现

- 主表稳定输出 `Success@1`, `Retry@2`, `Recovery@2`, `Retry@3`, `Recovery@3`
- 同时输出 `Recovery Gap = Retry@k - Recovery@k`
- 落盘 Markdown、CSV、manifest、per-task JSON artifacts
- manifest 记录 benchmark/model/agent 配置、task ids、k 值和输出目录

### 阶段 7：验证门槛

- 单元测试覆盖协议状态继承、retry reset、报表聚合、注册表配置传递、provider bridge、benchmark bridge
- smoke benchmark 必须验证关键语义：`Recovery@2 = 1` 且 `Retry@2 = 0`
- 外部 benchmark adapter 即使依赖缺失，也必须给出明确 unavailable reason

### 阶段 8：扩展

- 扩展 WebArena、OSWorld / Terminal-Bench
- 给支持状态 diff 的 benchmark 增加：
  - `Repair Rate`
  - `Collateral Damage`
  - `Progress Preservation`
- 增加多模型批量运行、失败重跑清单、成本/延迟统计和跨 benchmark 汇总表
