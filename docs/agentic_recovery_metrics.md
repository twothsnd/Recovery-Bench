# Agentic Recovery 指标设计

## 1. 为什么研究 Agentic Recovery

很多现有 agent benchmark 默认采用这样的设定：

> 干净初始状态 + 用户任务 -> agent 执行 -> 看最终是否成功

但真实部署里的 agent 失败往往不是无副作用的。一次失败 attempt 可能已经改变了外部环境：

- 改错了 calendar event；
- 发错了 email；
- 更新错了 database row；
- 改坏了文件；
- 创建了错误订单；
- 在游戏或规划环境中把状态推入不利位置。

所以真实问题不只是：

> agent 能不能从干净状态完成任务？

而是：

> agent 失败后，能不能在自己造成的状态里继续完成任务？

这就是 **agentic recovery** 的意义。

| pass@k | agentic recovery |
| --- | --- |
| 每次尝试都从干净状态独立开始 | 多次尝试顺序执行，后一次继承前一次造成的状态 |
| 错误答案不会改变外部世界 | 错误 action 会改变环境 |
| k 次里有一次答案对即可 | 后续 attempt 必须处理前面失败留下的状态 |
| 测 solution coverage | 测 stateful recovery ability |

核心问题是：

> **Can agents recover from their own failed attempts in stateful environments?**

一个自然的总指标是 **Recovery@k**：

> 给 agent 最多 k 次 sequential attempts；前面的 attempt 失败后不 reset 环境；如果 agent 最终完成原任务，并处理好前面失败造成的坏状态，则算 recovery 成功。

## 2. 为什么 Recovery@k 还不够

Recovery@k 是必要的，但它太粗。一次 recovery 失败可能来自两种完全不同的原因。

### 情况 A：状态已经不可恢复

agent 可能在前一次 attempt 中做了不可逆动作。此时后续 attempt 再强也无法在不 reset、不 rollback 的条件下完成任务。

Sokoban 例子：

> agent 把箱子推到非目标死角。箱子不能被拉回来，因此 board 已经无解。

这时 Recovery@k 失败，不是因为 agent “不会修”，而是因为前一次 attempt 已经毁掉了修复可能性。

### 情况 B：状态仍然可恢复，但 agent 没恢复成功

失败 attempt 也可能只是让环境变差，但还没有进入死局。

Sokoban 例子：

> agent 走错位置，或者把箱子推到仍然可以补救的位置。

如果后续 attempt 仍然失败，这才更直接反映 agent 的 recovery 能力不足。

因此，整体 Recovery@k 混合了两个因素：

1. 前面的失败是否仍然保留 recovery 可能性；
2. 如果仍可恢复，agent 是否真的能恢复。

这就需要两个互补指标。

## 3. 指标一：Fatal Attempt Rate

Fatal Attempt Rate 衡量 agent 是否会把环境推进不可恢复状态。

一次 attempt 如果结束后当前环境状态已经不可恢复，则称为 **fatal attempt**：

> 从当前状态出发，在不 reset、不 rollback 的条件下，已经不存在任何后续 action sequence 可以完成原任务并满足最终约束。

指标定义：

```text
FatalAttemptRate = # FatalAttempts / # TotalAttempts
```

这个指标测的不是“修复能力”，而是 **recoverability preservation**：agent 有没有给未来 recovery 留机会。

它反映 agent 是否：

- 理解某些错误动作不可逆；
- 避免高风险动作；
- 不确定时优先选择可逆/低风险动作；
- 避免把任务推入死局。

简言之：

> agent 有没有保留未来恢复机会？

## 4. 指标二：Conditional Recovery@k

Conditional Recovery@k 衡量的是：当状态仍然可恢复时，agent 能不能真的恢复。

它只在失败但仍可恢复的 attempt 上计算。

定义：

> 一次 attempt 失败后，如果当前状态仍然可恢复，则从这个状态继续给 agent 最多 k 次尝试。如果 agent 在 k 次内完成原任务，处理好前面失败造成的坏状态，并且没有引入新的不可接受副作用，则算成功。

指标定义：

```text
ConditionalRecovery@k =
    # RecoveredRecoverableFailures / # RecoverableFailedAttempts
```

等价地：

```text
ConditionalRecovery@k =
    Pr[recover within k | failed but recoverable]
```

这个指标回答：

> 在还有救的情况下，agent 会不会救？

它排除了 fatal attempt 的影响，更纯粹地衡量 recovery 能力。

## 5. Recovery@k 的分解

核心逻辑是：

> recovery 成功需要 agent 先保留恢复可能性，再利用这个可能性完成恢复。

概念上：

```text
OverallRecovery@k ~= RecoverabilityPreservation * ConditionalRecovery@k
```

更直观地说：

> **Recovery@k = 有没有留下恢复机会 x 有没有能力利用这个机会。**

| 指标 | 解释 Recovery@k 的哪一部分 |
| --- | --- |
| **Fatal Attempt Rate** | 有多少失败已经让 recovery 不可能 |
| **Conditional Recovery@k** | 在仍可恢复的失败中，agent 真实恢复能力如何 |

这两个指标不是额外硬加的，而是 Recovery@k 的自然分解。

## 6. Sokoban 例子

Sokoban 很适合解释这个拆分，因为 recoverability 很清楚：

- 当前 board 有解，则状态可恢复；
- 当前 board 无解，则状态不可恢复；
- 箱子被推到非目标死角，是典型 fatal attempt。

### Fatal attempt

初始状态：

```text
#####
#@B #
#   #
#  G#
#####
```

agent 把箱子推到非目标死角：

```text
#####
# @B#
#   #
#  G#
#####
```

箱子不能被拉回，目标在别处。这个 board 已经无解。

这次 attempt 是 fatal，会提高 **Fatal Attempt Rate**。

### Recoverable failed attempt

agent 没完成任务，但留下的 board 仍然有解：

```text
#####
# B #
# @ #
#  G#
#####
```

这就是 recoverable failed attempt，会进入 **Conditional Recovery@k** 的分母：

> 从这个状态继续，agent 能不能在 k 次尝试内完成任务？

## 7. 两阶段 Recovery Framework

| 阶段 | 问题 | 指标 |
| --- | --- | --- |
| **Stage 1: Preserve Recoverability** | 失败 attempt 有没有把状态推入不可恢复区域？ | **Fatal Attempt Rate** |
| **Stage 2: Recover When Possible** | 如果失败状态仍可恢复，agent 能不能修回来并完成任务？ | **Conditional Recovery@k** |

主线是：

> recovery 重要，因为 agent 在 stateful environment 中的失败会改变环境。但 Recovery@k 太粗。我们把 recovery 拆成两部分：先保留可恢复性，再利用可恢复性完成恢复。

## 8. 精炼表述

> We study agentic recovery in stateful environments, where failed attempts may alter the environment and affect future attempts. A key observation is that failed attempts are not equal: some failures leave the task recoverable, while others push the environment into irrecoverable states. Therefore, aggregate Recovery@k conflates two abilities: whether the agent preserves the possibility of recovery, and whether it can recover when recovery is still possible. We propose two complementary metrics: Fatal Attempt Rate, the fraction of attempts ending in irrecoverable states, and Conditional Recovery@k, the probability of successful recovery conditioned on the failed state being recoverable.

