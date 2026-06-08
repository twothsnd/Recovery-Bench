# WorkArena 任务可恢复性分析

本文分析 `external/workarena/src/src/browsergym/workarena/tasks` 里的任务代码，目标是判断：如果 agent 第一次 attempt 做错，后续 attempt 在不 reset、不 rollback 的同一个 ServiceNow 状态里，是否还有机会完成原任务。

## 一句话结论

WorkArena 的可恢复性主要取决于：

> 错误 attempt 有没有把错误内容写进 ServiceNow 的业务数据库。

如果只是导航错、筛选错、排序错、读错 dashboard、chat answer 答错，通常可恢复。  
如果只是把目标记录的可覆盖字段填错，通常是条件可恢复。  
如果已经创建了错误记录、提交了错误采购单、删除了错误记录、追加了错误评论，就有明显不可恢复倾向。

这里的“不可恢复倾向”不是说这个任务任意错误都会 fatal。很多任务在提交前做错、页面走错、查询错，都还能改回来。它的意思是：这个任务存在一个很自然的错误最终动作，一旦执行，就会留下真实业务副作用。

## 代码依据

我按代码而不是 README 来判断，关键依据有四个。

1. 官方 agent 任务入口是 `get_all_tasks_agents(filter="l2", meta_seed=42, is_agent_curriculum=True)`。它会按 `AGENT_CURRICULUM` 的 bucket 和 weight 采样组合任务，不是简单枚举所有 class。

2. 组合任务的 `CompositionalTask.validate()` 只验证当前 subtask。当前 subtask 成功后，`valid_index` 前进；当前 subtask 失败时，直接返回该 subtask 的 `stop`。所以 recovery 的关键不是组合任务名字，而是当前做错的是哪一种原子动作。

3. 原子任务的错误处理差异很大：

| 原子动作 | 代码行为 | 可恢复含义 |
| --- | --- | --- |
| list sort / filter | 错 URL、没 query、排序/筛选错通常 `stop=False` | 可以重新导航、清空条件、重做。 |
| create record | 没提交或提交被拒绝是 `stop=False`；但创建出的记录字段错误是 `stop=True` | 提交前可恢复；错误记录入库后 fatal-prone。 |
| edit record | 目标字段写错是 `stop=False`；改了 task scope 外字段或目标记录消失是 `stop=True` | 可覆盖字段可恢复；越界修改或删除目标不可恢复倾向。 |
| service catalog order | 没创建 request 是 `stop=False`；错 item、错数量、错配置、多 item 是 `stop=True` | 采购单一旦错误提交，通常不能无痕恢复。 |
| delete record | validator 只检查目标记录是否不存在 | 目标没删还能继续；如果删错别的记录，业务副作用无法靠正常完成原任务抹掉。 |
| infeasible answer | 没答或 reason 错是 `stop=False` | 只要没有执行真实副作用，可以重答。 |

4. `teardown()` 是 episode 结束后的清理，不是 recovery 过程的一部分。分析 recovery 时不能把 `teardown()` 当成下一次 attempt 的 rollback。

## 判定口径

本文把任务分成四类：

| 类别 | 判定标准 |
| --- | --- |
| 可恢复 | 错误只影响浏览器状态、list query、排序、筛选、读取结果或 chat answer，没有写入业务 DB。 |
| 条件可恢复 | 已经写入目标记录，但写的是可覆盖字段；只要目标记录还在、没有改 task scope 外字段、没有额外创建/采购/删除对象，就能继续修正。 |
| 不可恢复倾向 | 错误 attempt 可能留下持久业务副作用，例如错误 record、错误 request、错误删除、append-only 评论污染。 |
| Infeasible / 特殊 | 正确行为是声明不可行；只答错可恢复，但如果为了完成不可行目标而执行副作用，则按具体副作用判断。 |

## 官方采样规模

按官方默认 agent curriculum 展开，L2 和 L3 使用同样的任务分布：总共 235 个组合任务，其中 203 个普通可执行任务，32 个 infeasible 任务。

| Curriculum 类别 | 采样数 | 主要任务族 |
| --- | ---: | --- |
| planning_and_problem_solving | 44 | mark duplicate、workload balancing、work assignment、change request scheduling |
| information_retrieval | 56 | dashboard retrieval 后 order/create/filter/request、warranty check、find-and-order |
| data_driven_decision_making_and_reasoning | 55 | expense management、investment return、dashboard compute 后 order/create/filter/request |
| sophisticated_memory | 48 | navigate-and-create/order/filter/sort、onboard user、offboard user |
| contextual_understanding_infeasible_tasks | 32 | infeasible navigate-and-create/order/filter/sort |
| 合计 | 235 | - |

基于这个官方采样分布，按任务族的自然错误最终动作统计：

| 类别 | 数量 | 占全部 235 | 占可执行任务 203 |
| --- | ---: | ---: | ---: |
| 可恢复 | 62 | 26.4% | 30.5% |
| 条件可恢复 | 44 | 18.7% | 21.7% |
| 不可恢复倾向 | 97 | 41.3% | 47.8% |
| Infeasible / 特殊 | 32 | 13.6% | - |

这个比例的含义是：在默认采样中，接近一半的可执行任务包含容易产生 fatal side effect 的最终执行动作；但这些任务的前置检索、导航、筛选阶段仍然可能是可恢复的。

## 任务族总览

按任务族看：

| 任务族 | 出现位置 | 分类 | 主要 fatal 来源 |
| --- | --- | --- | --- |
| Navigation / impersonation | standalone、navigate-and-* building block | 可恢复 | 主要是 URL/session 错；通常不写业务 DB。 |
| List filter / sort / extract | standalone、navigate/filter/dashboard/warranty/find-and-order | 可恢复 | 错筛选、错排序、错回答可以清空或重答。 |
| Dashboard retrieval / knowledge search | dashboard-*、work assignment、workload、on/offboarding、protocol 查找 | 可恢复 | 读错本身不 fatal；如果读错后执行写入，fatal 来自后续动作。 |
| Edit existing record | incident/problem/hardware/change request/private task | 条件可恢复 | 目标字段写错可覆盖；改 task scope 外字段或删目标记录 fatal。 |
| Mark duplicate problem | mark_duplicate_problems | 条件可恢复 | `duplicate_of` 可改；错误 comment/删除 problem/越界改字段 fatal-prone。 |
| Change request scheduling | planning bucket | 条件可恢复 | start/end date 可重排；删除 change request 或改坏 risk/impact fatal。 |
| Work assignment / workload balancing | planning bucket | 条件可恢复 | assigned_to 错可重分配；删 incident/problem/user 或改无关字段 fatal。 |
| Create record | navigate-and-create、dashboard-and-create、onboarding、infeasible-create | 不可恢复倾向 | 错误 user/incident/problem/hardware/change request 已进入 DB。 |
| Service Catalog order | standalone order、navigate-and-order、find-and-order、dashboard-and-order、onboarding、infeasible-order | 不可恢复倾向 | 错 item、错数量、错配置、多个 request 都是真实采购副作用。 |
| Item request creation | dashboard-and-request | 不可恢复倾向 | 错误 request item 是持久业务记录。 |
| Delete record | offboarding、expense cleanup、investment cleanup | 不可恢复倾向 | 删除错用户或错 expense line 通常无法无痕恢复。 |
| Expense management | data-driven bucket | 不可恢复倾向 | 筛选可恢复；删除错 duplicate 或 extra expense fatal-prone。 |
| Investment cleanup | data-driven bucket | 不可恢复倾向 | 纯 chat answer 可恢复；删除应保留 investment line 后官方 validator 也 terminal。 |
| Infeasible tasks | contextual_understanding_infeasible_tasks | 特殊 | 答错可恢复；误执行 create/order/delete 后按副作用 fatal。 |

因此 WorkArena 不能简单标成“多数可恢复”或“多数不可恢复”。更准确的是：

1. 多数组合任务的前置检索阶段有很好的 Conditional Recovery@k 测试空间。
2. 很多组合任务的最终执行阶段有明确 Fatal Attempt Rate 风险。
3. WorkArena 特别适合验证 proposal 里的拆分：Recovery@k 失败到底是因为没有保留 recovery 机会，还是状态仍可恢复但 agent 没修回来。

## 可恢复任务类型

### 导航、筛选、排序

代表任务：

- `AllMenuTask`
- `ImpersonationTask`
- `FilterListTask` 系列
- `SortListTask` 系列
- `NavigateAndFilter*Task`
- `NavigateAndSort*Task`

典型错误：

- 进入错误 module。
- impersonate 错用户。
- filter column、operator、value 选错。
- sort field 或升降序选错。

可恢复性：

```text
通常可恢复。
```

原因是这些错误主要改变浏览器状态或 list query。后续 attempt 可以重新导航、切回用户、清空筛选、重新排序，不需要恢复业务记录。

### 信息抽取、知识库查询、仪表盘读取

代表任务：

- `ExtractListInfoTask`
- `KnowledgeBaseSearchTask`
- `DashboardRetrievalTask`
- `GetWarrantyExpirationDateTask`
- dashboard compute/filter/order/create/request 的读取阶段

典型错误：

- dashboard 数值读错。
- list 里找错记录。
- warranty date 答错。
- protocol article 找错。
- chat answer 写错。

可恢复性：

```text
通常可恢复，前提是还没基于错误信息执行写入动作。
```

例子：

`DashboardRetrieveIncidentAndMaxFilterAssetListTask` 如果第一次只是读错 maximum count 或筛错 asset list，后续可以重读 dashboard、改筛选条件。这属于 Conditional Recovery@k 的干净样本。

但 `DashboardRetrieveCatalogAndMaxOrderDeveloperLaptopTask` 如果读错 dashboard 后已经提交了错误采购单，fatal 来源不再是 dashboard retrieval，而是 order。

### 纯聊天答案任务

代表任务：

- `SendChatMessageTask`
- investment return 的 total return only / selected investments only 变体
- infeasible 任务中的 infeasible message

典型错误：

- 最后一条 assistant message 数字错。
- selected investment 列表写错。
- infeasible reason 写错或没写。

可恢复性：

```text
通常可恢复。
```

原因是 validator 一般看最后一条相关 chat message。只要没有伴随 DB 写入，后续可以重新发送正确答案。

## 条件可恢复任务类型

### 可覆盖字段更新

代表任务：

- `EditIncidentTask`
- `EditProblemTask`
- `EditHardwareAssetTask`
- `EditChangeRequestScheduleTask`
- `UpdatePrivateTask`

典型错误：

- incident assigned_to 分给错误专家。
- problem assigned_to 没分给最空用户。
- hardware asset 的 assigned_to 没清空。
- change request start/end date 排错。
- private task state 关成错误状态。

可恢复性：

```text
条件可恢复。
```

可恢复的情况：

- 错误只发生在目标字段上；
- 目标记录仍存在；
- 后续还能定位同一条记录；
- 正确值仍可推断。

不可恢复或 fatal 的情况：

- 修改了任务范围外字段；
- 删除了目标记录；
- 改坏了引用对象，例如错误用户、错误 problem、错误 change request；
- 触发了额外业务流程或外部通知。

例子：

`WorkAssignmentTask` 创建若干 incident 和专家用户，目标是按 category 把 incident 分给合适专家。如果第一次把一个 hardware incident 分给 software expert，后续可以重新设置 `assigned_to`。但如果第一次把 incident 的 `category` 改了，或删除了 incident，这就不是普通 edit recovery。

### Scheduling 和 workload 类组合任务

代表任务：

- `WorkAssignmentSmall/Medium/LargeTask`
- `PriorityAssignmentSmall/Medium/LargeTask`
- `WorkloadBalancingSmall/Medium/LargeTask`
- `ManageChangeRequestScheduleTask` 系列

这些任务都先在 setup 里创建若干 ServiceNow 记录，然后要求 agent 调整字段。

可恢复性：

```text
条件可恢复，并且比单步 edit 更容易暴露 recovery 能力。
```

原因是这类任务的错误通常可局部修正：

- 重新分配 assignee；
- 重新排 change request 时间；
- 重新筛选目标记录；
- 重新查 protocol 或 dashboard。

但它们也有明显 fatal 边界：

- 删除 setup 创建的 incident/problem/change request；
- 修改非目标字段，破坏任务前提；
- 创建额外无关记录污染列表；
- 在 L3 任务里过早关闭 private task 且后续又破坏目标记录。

### Mark duplicate problem

代表任务：

- `FilterProblemsAndMarkDuplicatesTask` 系列
- `SetProblemAsDuplicateTask`

可恢复性：

```text
条件可恢复。
```

可恢复的情况：

- 只是筛错 problem list；
- `duplicate_of` 设置错但还能编辑回正确 source；
- description 可覆盖且没有形成不可删除 comment。

不可恢复或 fatal 的情况：

- 删除 source 或 target problem；
- 把错误内容追加到不可覆盖的 comment/activity stream；
- 标错导致额外 workflow 或 notification。

例子：

如果第一次把 target problem 的 `duplicate_of` 指向错误 source，后续可以再打开 target problem 改到正确 source。若第一次把错误 problem 删除，则后续无法通过 UI 无痕恢复那条记录。

## 不可恢复倾向任务

### 新建持久记录

代表任务：

- `CreateUserTask`
- `CreateIncidentTask`
- `CreateProblemTask`
- `CreateHardwareAssetTask`
- `CreateChangeRequestTask`
- `CreateItemRequestTask`
- `NavigateAndCreate*Task`
- `DashboardRetrieve*AndCreate*Task`
- `OnBoardUserTask` 的 create user / create hardware asset 步骤

可恢复性：

```text
提交前可恢复；错误提交后 fatal-prone。
```

可恢复的情况：

- 还在 form 页面，尚未 submit；
- mandatory field 缺失导致提交被拒；
- URL 错，可以重新导航；
- 字段填错但还没保存。

不可恢复或 fatal 的情况：

- 已提交错误 user/incident/problem/hardware/change request；
- 创建了重复或多余业务记录；
- 创建记录触发后续引用、assignment、workflow；
- 官方 `teardown()` 没有追踪到额外错误记录。

例子：

`OnBoardUserTask` 要创建指定用户。第一次 attempt 如果只是 first name 填错但没提交，后续可以改；如果已经提交了错误用户，后续再创建正确用户也无法改变“错误用户曾经进入 DB”这个事实。

### Service Catalog 采购

代表任务：

- `OrderHardwareTask` 系列
- `NavigateAndOrder*Task`
- `FilterRequestedItemsAndOrder*Task`
- `DashboardRetrieveCatalogAnd*Order*Task`
- `OnBoardUserTask` 的 MacBook order 步骤
- infeasible order 任务中误下单

可恢复性：

```text
提交前可恢复；错误 request 创建后 fatal-prone。
```

官方 validator 也支持这个判断：没有 request sysid 时 non-terminal；但 request 创建后，如果 item、quantity、configuration 或 item count 错，返回 terminal。

例子：

任务要求订 1 台 Apple MacBook Pro 15。第一次 attempt 如果只是 catalog 页面配置错但没 submit，可以改；如果已经订了 iPad、订了 2 台、或提交了配置错的 MacBook，后续即使再订正确设备，错误 request 仍然是业务副作用。

### 删除记录和 cleanup

代表任务：

- `DeleteUserTask`
- `OffBoardUserTask`
- `DeleteExpenseLineExpenseManagementTask`
- `DeleteExpenseLineKnapsack`
- `ExpenseManagementTask` 系列
- `FilterExpenseLinesAndDeleteWrongInvestments` 系列

可恢复性：

```text
删目标前可恢复；删错后通常 fatal-prone。
```

可恢复的情况：

- 只是筛错列表；
- 还没确认 delete；
- 目标记录仍在。

不可恢复或 fatal 的情况：

- 删除了错误 user；
- 删除了应该保留的 duplicate expense；
- 删除了 extra expense；
- 删除了应该保留的 selected investment；
- 删除记录导致引用关系、audit trail、workflow 断裂。

例子：

`ExpenseManagementTask` 要删除 duplicate expense lines，但保留一个正确 duplicate 和所有 extra expenses。如果第一次删掉了应该保留的 expense line，后续无法从普通 UI 恢复同一条 sys_id 和完整记录。`FilterExpenseLinesAndDeleteWrongInvestments` 更直接：如果 expected investment 被删除，validator 返回 terminal。

### 追加评论或不可覆盖 artifact

代表任务：

- `AddCommentToKnowledgeArticleTask`
- `EditKnowledgeBaseTask` 中的 comment/search 相关步骤
- mark duplicate problem 中若包含 description/comment 类动作

可恢复性：

```text
高风险条件可恢复到 fatal-prone。
```

如果错误只是找错 article 或还没提交评论，可以恢复。若错误 comment 已经追加到 article 或 activity stream，后续追加正确 comment 不能删除“错误 comment 曾出现过”这个副作用，所以应作为 fatal attempt 候选。

## Infeasible 任务

WorkArena 的 infeasible 任务集中在 `contextual_understanding_infeasible_tasks`，包括：

- infeasible navigate-and-create；
- infeasible navigate-and-order；
- infeasible navigate-and-filter；
- infeasible navigate-and-sort；
- with reason / without reason 两组。

正确行为是发出 `infeasible` message，有些任务还要求包含原因。validator 在没回答、role 错或 reason 错时通常 non-terminal。

因此：

| 第一次失败类型 | 可恢复性 |
| --- | --- |
| 没有回答 infeasible | 可恢复 |
| infeasible reason 错 | 可恢复 |
| 只导航或筛选了错误页面 | 可恢复 |
| 为不可行 create 任务创建了记录 | fatal-prone |
| 为不可行 order 任务提交了 request | fatal-prone |
| 为不可行 filter/sort 任务只改 list query | 可恢复 |

例子：

infeasible order 任务的正确行为是说明缺少某个不存在配置项。如果 agent 第一次只是说错原因，后续可以重答；如果它绕过问题提交了一个近似 item 的采购 request，当前 ServiceNow 状态已经被污染。

## 对 Recovery 指标的启发

WorkArena 特别适合把 Recovery@k 拆成两个指标。

### Fatal Attempt Rate

Fatal attempt 应重点标注这些事件：

- 创建错业务记录；
- 提交错 Service Catalog request；
- 创建错 item request；
- 删除错 user/problem/expense/investment；
- 修改任务范围外字段；
- 目标记录被删除；
- 追加错误 comment；
- infeasible 任务中执行了本不该执行的新建、采购或删除。

这些状态下，后续 attempt 即使让官方 evaluator 通过，也不等于 side-effect-aware recovery 成功。

### Conditional Recovery@k

Conditional Recovery@k 应只在失败但仍可恢复的状态上计算，例如：

- 导航错、筛选错、排序错；
- dashboard/knowledge/list 信息读错，但还没执行写入动作；
- chat answer 错；
- 新建表单还没 submit，或 submit 被 client-side validation 拒绝；
- Service Catalog 还没 submit；
- 编辑任务中目标字段写错，但目标记录和非目标字段仍完好；
- private task state 关错但记录还在；
- infeasible reason 错但没有执行副作用。

这些状态才真正测试 agent 是否能识别已有错误并继续修回来。

## 实验实现注意点

WorkArena recovery 实验必须记录三层状态：

1. 浏览器状态：URL、iframe、localStorage、list query、chat messages。
2. ServiceNow DB 状态：`incident`、`problem`、`sys_user`、`alm_hardware`、`change_request`、`sc_request`、`sc_req_item`、`fm_expense_line` 等表。
3. task 对象内部状态：`valid_index`、record sys_id、request sysid、created sysids、infeasible flags。

官方 `teardown()` 只适合 episode 结束后清理，不等于 attempt 之间的 rollback。尤其是：

- 新建类 teardown 只删除它通过 localStorage 或 validation 记录到的 sys_id；
- 采购类 teardown 只删除当前 request sysid；
- 组合任务 teardown 调用子任务 teardown，但不保证清理 agent 额外创建的错误记录；
- 删除错记录无法靠 teardown 恢复原记录内容和引用关系。

因此严谨的 Recovery@k 流程应该是：

- attempts 之间保留真实错误状态；
- 每次 attempt 后记录 official validation result 和 DB diff；
- Fatal Attempt Rate 不能只看 official `stop=True`，还要看 DB diff 是否产生不可接受副作用；
- episode 结束后再统一 teardown 或实例级 snapshot/rollback 清理。

## 最终分层

建议把 WorkArena recovery 分层为：

- 稳健可恢复任务：导航、筛选、排序、列表抽取、知识库查询、dashboard 读取、纯聊天答案。
- 条件可恢复任务：编辑已有 incident/problem/hardware/change request/private task、work assignment、workload balancing、change request scheduling、mark duplicate。
- 不可恢复倾向任务：新建记录、采购 request、item request、删除用户/expense/investment、追加 comment、onboarding/offboarding 中的提交/删除阶段。
- Infeasible 任务：单独统计；答错可恢复，误执行副作用后按对应动作计入 fatal。

这和 proposal 的指标拆分是对应的：

| 状态类型 | 例子 | 应进入哪个指标 |
| --- | --- | --- |
| 非终局可恢复状态 | 筛错列表、读错 dashboard、目标字段写错但可覆盖、未提交表单 | Conditional Recovery@k |
| fatal-prone 状态 | 错误 record/request 已创建、错误 user/expense 被删除、错误 comment 已追加、越界字段被改 | Fatal Attempt Rate |

WorkArena 的价值就在这里：同一个组合任务里，第一次失败 attempt 可能只是可恢复的信息检索错误，也可能已经提交了不可无痕恢复的业务副作用。因此不能只看整体 Recovery@k，必须拆出 Fatal Attempt Rate 和 Conditional Recovery@k。
