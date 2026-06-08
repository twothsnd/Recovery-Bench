# DiscoveryWorld 任务可恢复性分析

本文分析 `external/discoveryworld/src/discoveryworld` 下 DiscoveryWorld 的任务实现、场景生成逻辑、对象交互逻辑和 scorer，而不是只看 README 或任务描述。重点文件包括：

- `ScenarioMaker.py`：canonical scenario、difficulty、variation 的枚举。
- `TaskScorer.py`：主任务 scorer，包括 Space Sick、Combinatorial Chemistry、Archaeology、Plant Nutrients、Reactor Lab、Lost in Translation、Proteomics、Tutorial。
- `scenarios/*.py`：小技能任务、Not Rocket Science、Proteomics、Rosetta Stone、Plant Growing 等额外 task/scorer。
- `objects/*.py` 和 `Agent.py`：化学清洗、土壤/种子、反应堆、发射终端、拾取/放置/吃掉/对话等状态变化。

这里给出的不是实测 Fatal Attempt Rate，而是任务级别的可恢复性判断：如果第一次 attempt 做错，后续 attempt 在不 reset、不 rollback 的前提下，理论上是否还能完成原任务，并且是否已经触发了 scorer 层面的失败终局。

## 判定口径

DiscoveryWorld 和 OSWorld 的一个关键区别是：DiscoveryWorld 的评分状态本身就是 live world 的一部分。很多任务的 `updateTick()` 会在世界推进时直接更新 `completed`、`completedSuccessfully` 和 scorecard。有些任务还有：

```python
if self.completed:
    return
```

这意味着一旦某个错误动作把任务标成 `completed=True, completedSuccessfully=False`，后续即使物理世界里的物体还能拿出来、重新放进去，官方 scorer/episode 语义也可能不再重新评估。这里的 fatal 指的是 **official-scorer fatal**，不是说物理世界一定无法修。

步数用完是另一类情况。官方 benchmark 会给 run 设置最大步数；如果步数用完时任务仍是 `completed=False, completedSuccessfully=False`，这表示该 run 没完成任务，但不等于 scorer 已经写入失败终局。它应被当作 attempt-level failure：如果当前 world state 没有触发 scorer fatal、也没有耗尽关键资源，则可以作为 Conditional Recovery@k 的起点。只有 `completed=True, completedSuccessfully=False` 这种已经被 scorer 判定结束的状态，才是 official-scorer fatal。

因此，DiscoveryWorld 的 recovery 协议必须明确 attempt 边界。如果规定一次 attempt 只能在官方 `completed=True` 时结束，并且不把 step budget 用完或 agent 主动停止作为 failed attempt，那么失败 attempt 基本都会落入 `completed=True, completedSuccessfully=False`，也就是 scorer 失败终局。在这种严格口径下，DiscoveryWorld 几乎只能测 Fatal Attempt Rate，Conditional Recovery@k 没有稳定分母。要评估 Conditional Recovery@k，必须允许 `completed=False` 的失败 attempt 作为下一次 attempt 的起点，例如 attempt budget 用完、agent 主动停止、或 harness 判定本轮未完成后切到下一轮。

源码里有 `worldHistory` 导出和 `getWorldHistoryAtStep()`，但没有看到官方 `restore/checkpoint` API。`worldHistory` 更像轨迹记录，不是可恢复执行的 checkpoint。因此如果坚持“不 reset、不 rollback、保持官方 benchmark 不变”，错误终局状态应该按 fatal 处理；如果我们另行改 scorer 或实现 checkpoint，那是在定义一个不同的 recovery 协议。

本文把任务分成四类：

| 类别 | 含义 |
| --- | --- |
| 可恢复 | 任务相关错误通常不会触发失败终局，也不会消耗唯一关键资源；后续可以继续走、继续测、重新设置、重新拿取或继续完成。 |
| 条件可恢复 | 大部分中间错误可恢复，但存在自然的任务相关 fatal 动作，比如把最终 flag 放错、把错误物体放进目标容器、给错 NPC、错误提交答案、耗尽有限种子/测试田。只要第一次 attempt 没触发这些动作，后续仍可恢复。 |
| 不可恢复倾向 | 任务核心动作本身是一次性提交或明确“no coming back”；错误提交后在官方 scorer 语义下会失败终局。 |
| 不适用 | DiscoveryWorld 当前 canonical benchmark 中没有 OSWorld 那种 `infeasible` 任务。 |

这里的“可恢复”仍然只针对合理动作空间里的 task-directed mistake。任何任务如果 agent 恶意丢弃关键物品、用异常方式破坏对象引用或让环境运行到极端状态，都可能不可恢复；这不作为正常 recovery 分析的主要口径。

## 总体结论

按 `SCENARIO_INFOS` 统计，DiscoveryWorld 当前 canonical benchmark 有 171 个实例：

- `Tutorial`：1 个。
- 8 个主 discovery scenario，每个 3 个 difficulty、5 个 variation，共 120 个。
- 10 个 Small Skills scenario，每个 5 个 variation，共 50 个。

总体分类如下：

| 类别 | 数量 | 占全部 171 |
| --- | ---: | ---: |
| 可恢复 | 56 | 32.7% |
| 条件可恢复 | 100 | 58.5% |
| 不可恢复倾向 | 15 | 8.8% |
| 不适用 | 0 | 0.0% |

如果只看“第一次失败后状态仍然没有被终局动作污染”的情况，DiscoveryWorld 大量任务是可恢复的：化学、反应堆、导航、找物、开门、对话等都可以继续做。但从 recovery benchmark 的角度，DiscoveryWorld 比 OSWorld 更适合研究 Fatal Attempt Rate，因为它有很多显式的“错误最终提交即 fatal”的任务。

最重要的结论是：

> DiscoveryWorld 的 fatal state 主要不是地图死局，而是 scorer 终局状态、一次性提交动作、错误对象进入目标容器、错误 flag 放置、有限资源消耗。

## 按任务族统计

| 任务族 | 实例数 | 分类 | 主要 fatal 来源 |
| --- | ---: | --- | --- |
| Tutorial | 1 | 可恢复 | 基本是对话、拿钥匙、离开房间；无明显错误终局。 |
| Combinatorial Chemistry | 15 | 可恢复 | 化学 dispenser 可补充，jar 可用 bottle cleaner 清空，key 不会因错误混合物被破坏。 |
| Reactor Lab | 15 | 可恢复 | reactor frequency 可继续调整，reactor 可重新激活；错误中间设置不会直接失败终局。 |
| Small Skills: Dialog Test | 5 | 可恢复 | 错误对话通常不写任务失败终局；可重新对话或继续尝试。 |
| Small Skills: Doors Test | 5 | 可恢复 | 开门、拿 flag 是单调进展；无错误终局。 |
| Small Skills: Doors with Keys Test | 5 | 可恢复 | 收集钥匙、开门是单调进展；无错误终局。 |
| Small Skills: Search Test | 5 | 可恢复 | 找到并拾取 flag 即成功；普通搜索错误可继续。 |
| Small Skills: Moving Agents Test | 5 | 可恢复 | 和移动 NPC 对话；没看到错误终局。 |
| Space Sick | 15 | 条件可恢复 | 喂错蘑菇会让无病连续计数归零，并消耗食物/时间；但 scorer 不会立即失败终局。 |
| Archaeology Dating | 15 | 条件可恢复 | red flag 一旦放在错误 site/sign 附近，scorecard completed=0，任务进入失败终局。 |
| Plant Nutrients | 15 | 条件可恢复 | Normal/Challenge 的 test fields 配置后不能改；种子会发芽或被消耗；Easy 的 soil controller 选择也像答案提交。 |
| Lost in Translation | 15 | 条件可恢复 | Easy/distilled 继承 pick-and-give 风格，给错对象会失败终局；Normal/Challenge 给错对象也可能把物品锁进 NPC 容器。 |
| Proteomics | 15 | 条件可恢复 | red flag 一旦放在错误 statue 附近，任务失败终局。 |
| Small Skills: Pick and Place Test | 5 | 条件可恢复 | 目标容器里出现错误物体会立即失败终局。 |
| Small Skills: Pick and Give Test | 5 | 条件可恢复 | 目标 NPC 或 distractor NPC 收到错误物体会失败终局。 |
| Small Skills: Instrument Measurement Test | 5 | 条件可恢复 | 测量可继续，但把物体放进错误 pot 会失败终局。 |
| Small Skills: Navigation in a House Test | 5 | 条件可恢复 | flag 如果被放在错误房间/区域，scorecardFlag completed=0 并失败终局。 |
| Small Skills: Discovery Feed Test | 5 | 条件可恢复 | 与 pick-and-place 相同，错误物体进入目标容器会失败终局。 |
| It's (not) Rocket Science! | 15 | 不可恢复倾向 | `Start countdown -> Confirm` 明确是一次性发射；错误 orbit speed/fuel 会冻结失败终局。 |

## 可恢复任务类型

### 化学组合与除锈

`Combinatorial Chemistry` 对应 `RustedKeyTaskEasy/Normal/Challenge`。这类任务看起来像会有化学污染，但代码层面对 recovery 比较友好：

- Chemical dispenser 会自动补充物质，不是一次性资源。
- Bottle cleaner 可以清空 jar 里的 substance。
- 错误 mixture 通常不会销毁 key。
- key 的 rust level 可能被部分降低，但不会因为错误尝试变成更坏的不可修状态。

所以合理错误包括：

- 往 jar 里加了错误 chemical；
- mixture 比例不对；
- 把 key 放进错误 mixture；
- 忘记清洗 jar；
- 用了不相关 instrument。

这些后续都可以通过清洗 jar、重新配 mixture、再次放入 key 来恢复。除非 agent 把 key 丢到不可访问位置，否则这类任务应算可恢复。

### 反应堆调参

`Reactor Lab` 的 scorer 检查 crystal 是否被拿过、instrument 是否用过、reactor frequency 是否被改过、reactor 是否成功 activated。错误设置 frequency 不会立即失败；后续可以继续调 frequency，再激活。

这类任务的 recovery 结构很像连续参数调试：

- 错误测量可以继续测；
- 错误 frequency 可以继续改；
- reactor 没激活成功可以继续尝试；
- 正确激活后才进入成功完成。

因此它适合测 Conditional Recovery@k 中的“从错误中间状态继续推理和修正”，但不太容易产生 fatal attempt。

### 导航、搜索、开门、移动 NPC 对话

这些 Small Skills 任务多数是单调进展：

- `Doors Test`：拿 flag、开所有门。
- `Doors with Keys Test`：拿钥匙、开门。
- `Search Test`：找到 flag 并拾取。
- `Moving Agents Test`：和所有目标 NPC 对话。
- `Tutorial`：和 elder 对话、拿 key、离开房间。

普通错误只是走错、没拿到、没开完、漏掉 NPC。后续可以继续探索，不会污染最终 scorer。

## 条件可恢复任务类型

### 错误最终 flag 放置

这是 DiscoveryWorld 最清晰的一类 fatal。

代表任务：

- `Archaeology Dating`
- `Proteomics`
- `Small Skills: Navigation in a House Test`

这些任务的中间步骤通常可恢复：可以继续挖掘、继续测量、继续找地点、继续移动。但一旦把 red flag 放到错误目标附近，scorer 会把对应 scorecard 标为 completed，且 score 为 0；任务随即进入 `completed=True, completedSuccessfully=False` 或等价失败状态。

例子：

```text
Proteomics:
1. agent 拿到 proteomics meter 和 red flag。
2. agent 只测了两个 animal species，误判 anomaly。
3. agent 把 red flag 放在错误 statue 旁边。
4. scorer 看到 flagPlaced=True 且 placedCorrectly=False。
5. scorecardFlagPlaced completed=True, score=0。
6. 在官方 scorer 语义下任务已经失败终局；后续再把 flag 挪到正确 statue 旁边，也不能按原 benchmark 的成功来计。
```

所以这类任务不是“永远不可恢复”，而是：

> final answer 之前可恢复；错误 final answer 之后，在官方 scorer/episode 语义下不可恢复。

### 错误物体进入目标容器或 NPC

代表任务：

- `Small Skills: Pick and Place Test`
- `Small Skills: Pick and Give Test`
- `Small Skills: Instrument Measurement Test`
- `Small Skills: Discovery Feed Test`
- `Lost in Translation` 的 easy/distilled 版本

这些任务常见逻辑是：如果目标容器里有东西，但不是正确 object，立刻失败；或者 distractor NPC 收到东西，立刻失败。

例子：

```text
Pick and Place:
1. 任务要求把 seed 放进 jar。
2. agent 拿起 mushroom。
3. agent 把 mushroom 放进 jar。
4. scorer 发现 destinationContainer.contents 非空，但 scorecardPlace 还没完成。
5. 任务被标成 completed=True, completedSuccessfully=False。
```

物理上 agent 也许还能把 mushroom 从 jar 里拿出来，再把 seed 放进去。如果我们把任务改成“只看最终容器状态”，那这种状态可以恢复。但 DiscoveryWorld 原始 scorer 在这类小技能任务里通常会把错误放置视为失败终局，并且 `updateTick()` 开头会直接返回。因此在保持官方 benchmark 不变的 recovery 协议里，这应计入 Fatal Attempt Rate。

### 土壤、种子和测试田

`Plant Nutrients` 是条件可恢复且资源敏感的任务。

关键原因：

- Normal/Challenge 描述明确说 test field 的 nutrient levels 设置后不能改。
- 种子在满足条件后会变成 mushroom，原 seed 会从 world 中移除。
- 错误 test field 配置会消耗可用测试田。
- 错误种植会消耗 seed、时间和可观测机会。
- Easy 版本把正确 nutrient 作为 soil controller 的选择，错误选择更接近答案提交。

这类任务的中间探索仍可恢复：走错、测错 soil、拿错工具、还没配置 field 时都可以继续。但如果 agent 过早配置所有 test fields，或者把有限种子全种在错误条件下，后续可能没有足够资源完成“长出两个新植物”的要求。

因此它适合被标成条件可恢复，而不是稳健可恢复。

### Space Sick

`Space Sick` 要求让 colonists 连续吃 10 个不会生病的 mushroom。scorer 会监控 colonist 吃 mushroom 后 50 tick 内是否生病；如果有人生病，连续成功计数会被 reset 到 0。

这不是严格 fatal，因为任务不会立刻 `completed=True, completedSuccessfully=False`。agent 可以继续找规律、继续喂正确 mushroom。但它仍然是条件可恢复：

- mushroom 被吃掉后从 world 移除；
- 错误喂食会浪费时间和样本；
- 多次错误喂食可能让剩余资源或时间预算不足；
- 需要等待 50 tick 的监控窗口，错误状态会拖慢后续 recovery。

所以它更像“可恢复但代价高”的失败，而不是不可恢复死局。

### Lost in Translation

`Lost in Translation` 的风险来自给错对象。

Easy/distilled 版本的 scorer 和 pick-and-give 类似：如果目标 NPC 收到错误 object，任务可以失败终局。Normal/Challenge 版本主要检查需要的 object 是否在 elder inventory；如果给错对象不一定立刻被显式判失败，但 NPC 容器通常不是普通开放容器，错误给出的 object 可能难以取回，并可能污染最终状态。

因此这类任务的合理分类是条件可恢复：

- 读 sign、问 NPC、找对象、拿错对象，一般可恢复。
- 给出错误对象，尤其给到目标 NPC 或 distractor NPC，可能 fatal。

## 不可恢复倾向任务

### It's (not) Rocket Science!

`It's (not) Rocket Science!` 是 DiscoveryWorld 中最强的一次性提交任务。

`LaunchTerminal` 的 dialog 里有 `Start countdown`，确认节点文案明确写着：

```text
Once you confirmed, there's no coming back.
```

代码层面，确认后会给 terminal 加上 `launchConfirmed` state。scorer 一旦看到 `launchConfirmed`：

- Easy/Normal：检查 orbit speed 是否正确，随后任务完成；错误则 `completedSuccessfully=False`。
- Challenge：同时检查 orbit speed、fuel type 和 fuel amount；任一错误都会失败。
- `updateTick()` 开头有 `if self.completed: return`，终局后不再重新评分。

例子：

```text
Rocket Science Challenge:
1. agent 读了 rocketry book，也测了 pendulum 和 load cell。
2. agent 把 orbit speed 设成 7420 m/s，但正确值是 7480 m/s。
3. agent 选了正确 fuel type，但 fuel amount 少了 50 L。
4. agent 点击 Start countdown -> Confirm。
5. scorer 看到 launchConfirmed，记录 orbit/fuel answer completed，但 score=0。
6. 任务 completed=True, completedSuccessfully=False。
7. 后续 attempt 不能“取消发射”或重新提交。
```

这类任务最适合用来测 Fatal Attempt Rate，因为错误 final action 是 benchmark 设计中的自然动作，不是异常破坏。

## 对 Recovery 指标的启发

DiscoveryWorld 比 OSWorld 更适合分解 recovery：

1. **Preserve Recoverability**：agent 是否能避免过早提交最终答案、避免把错误 object 放进目标容器、避免把 flag 放在错误位置、避免耗尽种子/test fields。
2. **Recover When Possible**：如果只是走错、测错、拿错、配错但还没触发终局，agent 能否利用当前状态继续完成任务。

因此在 DiscoveryWorld 上计算 Recovery@k 时，建议明确记录每次失败后的状态类型：

| 状态类型 | 例子 | 应进入哪个指标 |
| --- | --- | --- |
| 非终局可恢复状态 | 走错路、拿错工具、reactor frequency 设错但未成功提交、jar 里混错 chemical 但可清洗 | Conditional Recovery@k |
| 步数用完但未终局 | attempt budget 用完，scorecard 仍是 `completed=False`，且关键资源仍可修 | Conditional Recovery@k |
| scorer 失败终局 | flag 放错 statue/site、错误 object 放进目标 pot、给错 NPC、rocket launch confirmed with wrong answer | Fatal Attempt Rate，前提是保持官方 scorer/episode 语义 |
| 资源耗尽/机会耗尽 | test fields 全部配置错、种子/样本被吃光或种错、关键物体进入不可访问容器 | Fatal Attempt Rate 或单独资源 fatal bucket |

最终建议的 DiscoveryWorld recovery 分层是：

- 稳健可恢复任务：Combinatorial Chemistry、Reactor Lab、开门/找物/移动 NPC/教程等。
- 条件可恢复任务：Archaeology、Proteomics、Plant Nutrients、Space Sick、Lost in Translation、pick/place/give/measurement/navigation/discovery-feed 小技能。
- 不可恢复倾向任务：Rocket Science，尤其是 Confirm Launch 之后。

如果要给一个任务级粗略比例：

- 约 32.7% 是稳健可恢复；
- 约 58.5% 是条件可恢复；
- 约 8.8% 有强不可恢复倾向；
- 若把“未触发终局动作的条件可恢复状态”也算入可恢复池，则 156/171，也就是约 91.2% 的任务实例存在有意义的 recovery 测试空间。

但真正的 Fatal Attempt Rate 不能从任务列表直接得出，必须看 agent 的第一次 attempt 到底有没有触发这些 fatal 动作。DiscoveryWorld 的价值就在这里：同一个任务里，失败 attempt 可以是可恢复的，也可以在官方 scorer 语义下变成 fatal。若研究者决定忽略 `completed` 终局并改成纯最终状态检查，则分类会改变，但那已经不是原始 DiscoveryWorld benchmark。
