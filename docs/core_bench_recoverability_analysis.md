# CORE-Bench 任务可恢复性分析

本文分析 `external/core-bench` 下 CORE-Bench 的 benchmark harness、dataset 元数据、任务环境构造和评分逻辑。重点文件包括：

- `README.md`：任务定义、easy/medium/hard 三种难度、Docker/Azure 运行方式、`report.json` 约定。
- `main.py`：benchmark CLI 入口。
- `benchmark/benchmark.py`：capsule 下载、任务环境准备、agent 运行、结果收集。
- `benchmark/evaluations.py`：`report.json` 与 ground truth 的评分逻辑。
- `benchmark/dataset/core_train.json` 和 `core_test.json`：90 个 base capsule 的任务提示、问题和 ground truth 结果。
- `benchmark/benchmark_prompts.json`：三种 difficulty 的实际 prompt 模板。

这里给出的不是实测 Fatal Attempt Rate，而是任务级别和 attempt 类型级别的可恢复性判断：如果第一次 attempt 没有成功，后续 attempt 在不 reset、不 rollback 的同一个 capsule 工作目录里，理论上是否还能继续复现实验、修正输出并写出正确 `report.json`。

## 判定口径

CORE-Bench 的 scorer 不是 live environment 的持续状态机。agent 运行结束后，harness 只是在 `environment` 目录中查找 `report.json`，然后把它和 dataset 里的 ground truth answers 比较。评分结果写入 benchmark results 文件，不会为了评分而修改 capsule 工作目录。

因此 CORE-Bench 的 attempt 失败大多来自：

- 没写 `report.json`；
- `report.json` key 不匹配；
- 答案值错误；
- 没生成需要的 result files；
- 代码没跑完；
- dependency 没装好；
- notebook/Rmd/script 执行失败；
- figure/table 中的信息没读对。

这些失败本身通常可以通过后续 attempt 覆盖 `report.json`、继续安装依赖、重新运行脚本、删除错误 results 并重跑来恢复。

但是 CORE-Bench 的 live state 是一个真实文件系统和运行环境。fatal attempt 的主要来源不是 scorer，而是 agent 自己破坏了 capsule：

- 删除或覆盖唯一的 `code/`、`data/`、notebook、Rmd、脚本、README；
- 在没有 git history 或 pristine copy 的情况下大幅改坏源代码；
- 删除 medium 任务需要的 `REPRODUCING.md` 或 Code Ocean `environment/`；
- 把原始 data 文件改写成错误格式；
- 把 dependency / Docker / system state 污染到后续无法继续运行；
- 让长训练或下载卡死，耗尽 attempt-level 预算。

本文把任务分为四类：

| 类别 | 含义 |
| --- | --- |
| 可恢复 | 合理错误主要是读错结果、写错 `report.json`、选错已有 output；后续可以直接覆盖答案或重新读取。 |
| 条件可恢复 | 需要运行代码、安装依赖、生成 outputs；后续可恢复的前提是关键 code/data/instructions 没被破坏，运行环境仍可修。 |
| 不可恢复倾向 | 任务本身自然要求不可逆提交、删除唯一关键资源或污染不可重建的外部状态。CORE-Bench 任务设计里基本没有这种任务级动作；fatal 更多是 agent action-level 破坏。 |
| 不适用 | 当前 CORE-Bench 任务都要求产出答案；没有单独的“正确行为是不执行任务”的任务类型。 |

这里的“可恢复”仍然只针对合理 task-directed mistake。任何 shell-based benchmark 中，如果 agent 执行 `rm -rf environment`、清空 `data/` 或把源码全部覆盖，都会变成不可恢复；这不应被当成任务本身的 recoverability 分类。

## CORE-Bench 任务规模

本地 `core_train.json` 和 `core_test.json` 各有 45 个 base capsule，合计 90 个。每个 base capsule 会在三种 difficulty 下形成一个任务实例：

- `codeocean_easy`
- `codeocean_medium`
- `codeocean_hard`

所以总口径是：

```text
90 base capsules × 3 difficulty levels = 270 benchmark instances
```

按 base capsule 统计：

| 维度 | 数量 |
| --- | ---: |
| base capsules | 90 |
| benchmark instances | 270 |
| Computer Science | 37 |
| Social Sciences | 28 |
| Medical Sciences | 25 |
| Python capsules | 49 |
| R capsules | 41 |
| 含 figure/vision 问题的 capsules | 56 |
| 不含 figure/vision 问题的 capsules | 34 |
| base questions | 181 |
| questions × difficulty | 543 |

每个 capsule 在 dataset 中都有 3 组 ground truth `results`，numeric answer 会基于这 3 组结果计算 prediction interval。

## 三种 Difficulty 的状态差异

CORE-Bench 的 difficulty 不是不同任务，而是同一 capsule 暴露的信息不同。`benchmark/benchmark.py` 里的环境准备逻辑会按 difficulty 删除或保留文件。

### `codeocean_easy`

Easy 任务保留 `results/`，prompt 明确要求：

```text
You should not actually run or execute any code.
```

agent 的目标是读已有 results 并写 `environment/report.json`。这类任务的自然错误包括：

- 读错 table/figure/output；
- 把 answer 写成字符串而不是数字；
- `report.json` key 少了、错了；
- 选错 result file；
- vision 问题从错误 figure 中读值。

这些错误几乎都可以后续覆盖 `report.json` 或重新读取 results，因此 easy level 应归为可恢复。

fatal 边界主要是 agent 违反 prompt，删除或覆盖了 `results/`、`data/`、`code/`。这不是 easy 任务自然要求的动作，而是破坏性 shell 错误。

### `codeocean_medium`

Medium 任务会删除 `results/`，但保留：

- `REPRODUCING.md`
- Code Ocean `environment/`
- `code/run.sh` 或 `code/run`

agent 需要按照 `REPRODUCING.md` 或 run script 运行 capsule，重新生成 outputs，再写 `report.json`。

自然错误包括：

- dependency 没装全；
- Docker image 或 R/Python package 没拉下来；
- run command 运行目录错；
- results 输出到错误目录；
- notebook/Rmd 没转换成 html；
- 代码跑完但 answer 抽取错；
- figure question 读错图。

这些通常仍可恢复：后续 attempt 可以继续装依赖、重跑命令、移动 outputs、覆盖 `report.json`。

但 medium 比 easy 更依赖关键 instruction 和 environment。如果 agent 删除了 `REPRODUCING.md`、改坏 run script、覆盖 data，后续 attempt 可能失去复现实验的必要信息。因此 medium 应归为条件可恢复。

### `codeocean_hard`

Hard 任务也删除 `results/`，并且还删除：

- `REPRODUCING.md`
- Code Ocean `environment/`
- `code/run.sh`
- `code/run`

agent 只能根据 README、源码和 task prompt 自己推断依赖、运行命令和输出位置。

自然错误包括：

- 从 README 推断错运行入口；
- 安装错 package 版本；
- Python/R 环境冲突；
- notebook kernel 或 timeout 配置错；
- 修改脚本试图修复路径问题但引入 bug；
- 训练/模拟没跑完整；
- 输出格式和 evaluator 需要的问题不匹配。

hard level 仍然没有 scorer 终局失败；错了可以继续调试。但它更容易诱导 agent 修改源码、配置、环境变量和 dependency state。只要 code/data 仍可用，后续可以继续 recovery；如果关键源码或 data 被改坏且没有 pristine copy，就会变成 fatal。因此 hard 应归为条件可恢复。

## 总体分类

按 `capsule × difficulty` 的 270 个实例粗略分类：

| 类别 | 数量 | 占全部 270 |
| --- | ---: | ---: |
| 可恢复 | 90 | 33.3% |
| 条件可恢复 | 180 | 66.7% |
| 不可恢复倾向 | 0 | 0.0% |
| 不适用 | 0 | 0.0% |

这个表的意思不是 CORE-Bench 不会产生 fatal attempt，而是：

> CORE-Bench 的任务目标通常不是不可逆提交，而是生成和报告复现实验结果。它的 fatal 主要来自 agent 把文件系统或运行环境破坏到无法继续复现。

如果按 field 展开：

| Field | base capsules | instances | 可恢复 easy | 条件可恢复 medium/hard |
| --- | ---: | ---: | ---: | ---: |
| Computer Science | 37 | 111 | 37 | 74 |
| Social Sciences | 28 | 84 | 28 | 56 |
| Medical Sciences | 25 | 75 | 25 | 50 |

如果按 language 展开：

| Language | base capsules | instances | 可恢复 easy | 条件可恢复 medium/hard |
| --- | ---: | ---: | ---: | ---: |
| Python | 49 | 147 | 49 | 98 |
| R | 41 | 123 | 41 | 82 |

如果按 question type 展开：

| Question type | base capsules | instances | recovery 特点 |
| --- | ---: | ---: | --- |
| 含 figure/vision 问题 | 56 | 168 | 答案读取错误可覆盖；删除或覆盖 figure/results 后需要能重跑。 |
| 纯 written/numeric/list 问题 | 34 | 102 | 多数是 table/log/stdout/html 中读数；错误 report 可覆盖。 |

## 可恢复任务类型

### 错误或缺失 `report.json`

这是 CORE-Bench 最常见、也最干净的 recovery 状态。

例子：

```text
1. agent 成功运行 capsule。
2. agent 从 output 中读错一个数字。
3. agent 写出 report.json，但某个 value 不在 prediction interval 内。
4. evaluation 失败。
5. 下一次 attempt 重新读 output，覆盖 report.json。
```

这种状态完全可恢复，因为 official evaluation 只读最终 `report.json`。旧答案不会被 append-only 记录，也不会让 scorer 进入失败终局。

同样，如果第一次 attempt 没写 `report.json`，evaluation 会把 `result_report` 当成 `{}`。这只是 attempt failed，不是 environment fatal。

### Easy level 的结果读取错误

Easy level 已经有 `results/`。agent 不需要运行代码，只需要读文件、表格、图片或 html。

代表任务：

- `capsule-3137115`：读 manuscript table 和 figure。
- `capsule-5367566`：读 HyperETA notebook/html 的 MAPE、RMSE、MAE。
- `capsule-5777882`：从 Rmd/rendered outputs 中读 license/language figure 信息。

自然错误通常是：

- 看错 row/column；
- 忽略 confidence interval 的规则没遵守；
- 把百分号和小数格式搞错；
- 视觉题选错 subplot 或 legend。

后续 attempt 可以重新读取 result files 并覆盖答案。只要已有 results 没被删，这类任务是稳健可恢复的。

### 生成物错误但源码和数据完好

Medium/hard 中，agent 可能生成错误 outputs：

- notebook 转 html 失败；
- Rmd 输出到了错误目录；
- script 只跑了一半；
- figure 保存名不对；
- 训练日志没保存；
- `results/` 里混入多余文件。

这些通常仍可恢复：

```text
删除错误 results -> 修正命令/路径 -> 重新运行 -> 重新写 report.json
```

CORE-Bench 的 evaluator 主要看 `report.json` 的答案；harness 也记录 `result_paths_success`，但 summary 里的 task/question correctness 由 answer comparison 决定。因此多余 outputs 本身不一定 fatal，关键是能否产出正确答案。

## 条件可恢复任务类型

### Dependency 和 runtime 环境错误

CORE-Bench 的复现实验任务经常需要：

- Python packages；
- R packages；
- system packages；
- Jupyter kernel；
- Docker in Docker；
- Code Ocean registry image；
- GPU runtime。

自然错误包括：

- 装错版本；
- 混用 Python 3.9/3.10；
- R package 编译失败；
- conda/pip 环境污染；
- Docker daemon 或 image pull 失败；
- notebook kernel 不匹配。

这类状态通常条件可恢复。后续 attempt 可以：

- 创建新的 venv/conda env；
- pin package version；
- 重新安装缺失依赖；
- 清理 `results/` 后重跑；
- 直接使用 capsule 的 Docker image。

但如果 agent 把系统 package manager、Python/R runtime、Docker daemon 或 `/usr` 下关键文件改坏，后续可能难以在同一 container 内恢复。这是 action-level fatal，不是任务级 fatal。

### 源码修补和路径修正

Hard level 很可能诱导 agent 修改代码，例如：

- 修改 hard-coded path；
- 改 notebook timeout；
- patch deprecated API；
- 修改 output directory；
- 改随机种子或训练 epoch；
- 注释掉报错 cell。

小范围 patch 通常可恢复，甚至是完成任务的必要手段。但它有明显边界：

- 如果 agent 还知道自己改了什么，可以 revert 或继续修；
- 如果 agent 保留了原文件 backup，可以恢复；
- 如果 agent 大幅覆盖源码、删除 notebook cell、改坏 data preprocessing，且没有 pristine copy，就可能不可恢复。

因此源码修补类失败应放入条件可恢复，而不是稳健可恢复。

### 长时间训练、模拟和 notebook 执行

很多 Computer Science / Medical Sciences capsule 会跑训练、模拟或 notebook。第一次 attempt 可能留下：

- 半成品 checkpoint；
- cache；
- partially rendered html；
- incomplete figures；
- stdout/stderr logs；
- failed temporary files。

这些状态一般不是 fatal。后续可以继续训练、清 cache、重跑脚本或直接从已有 log 中抽取结果。

但如果 task budget 严格，第一次 attempt 已经消耗大量时间，后续即使理论可恢复，也可能无法在剩余 wall-clock 内完成。这应该单独记录为：

```text
budget-constrained recovery failure
```

它不是环境不可恢复，而是评测预算不足。

### Figure / vision 问题

56 个 base capsule 包含 `fig` 问题。它们的风险不是 scorer 终局，而是 agent 可能：

- 读错图；
- 只看缩略图；
- 选错 subplot；
- OCR/视觉模型读错文字；
- 把 generated figure 覆盖掉。

如果 code/data 仍在，figure 可以重生成；如果 easy level 的唯一 results 被删除，或者 hard/medium 中生成 figure 的脚本被改坏，就可能进入 fatal。

因此 vision question 本身不不可恢复，但它增加了 conditional recovery 的难度。

## Fatal Attempt 的主要来源

CORE-Bench 的 fatal attempt 不应按 `report.json` 对错判断，而应看环境 diff。

### 删除或覆盖关键 capsule 文件

高风险对象包括：

- `environment/task.txt`
- `environment/<capsule_id>/code/`
- `environment/<capsule_id>/data/`
- notebook / Rmd / Python/R script
- README / requirements / config
- medium level 的 `REPRODUCING.md`
- medium level 的 Code Ocean `environment/`

如果这些文件被删除或大幅改坏，后续 attempt 可能已经没有足够信息复现实验。这种状态应计入 Fatal Attempt Rate。

例子：

```text
Hard task:
1. agent 找不到入口脚本。
2. agent 错误地把 code/ 里的多个 Python 文件重写成调试 stub。
3. 脚本不再包含原始实验逻辑。
4. 下一次 attempt 没有 reset，也没有原始文件 backup。
5. 即使知道正确命令，也无法复现原结果。
```

这是 CORE-Bench 里的典型 fatal。

### 修改原始 data

有些任务需要读取或预处理 data。错误 attempt 如果：

- 清洗时覆盖原 CSV；
- 把 sample data 写回原始文件；
- 删除 large raw dataset；
- 用错误 encoding 重新保存；
- 把 train/test split 文件覆盖；

后续即使源码还在，也未必能恢复原实验。这类应视为 fatal-prone action。

### 污染运行环境到不可修

Dependency 错误通常可修，但以下情况可能 fatal：

- 删除系统 Python/R；
- 改坏 package manager；
- 破坏 Docker daemon；
- 修改 global PATH 到后续命令不可用；
- 删除 `/tmp` 或工作目录下关键 cache/source；
- 占满磁盘导致无法继续下载或生成结果。

这类 fatal 属于 runtime-level 破坏，而不是答案内容错误。

### 外部网络和远程资源

CORE-Bench harness 会从：

```text
https://corebench.cs.princeton.edu/capsules/<capsule_id>.tar.gz
```

下载 capsule。运行中也可能需要 package repositories、Docker registry 或语言包源。

网络失败本身不应算 fatal；后续可以重试。但如果 agent 把本地 package/cache/registry 配置改坏，或把错误镜像作为唯一依赖路径写入脚本，可能会让 recovery 变难。

CORE-Bench 的 recovery 指标更像：

```text
Can the agent continue debugging and reproducing a scientific computation
from its own messy workspace?
```

## 对 Recovery 指标的启发

CORE-Bench 适合把 recovery 拆成以下状态类型：

| 状态类型 | 例子 | 指标归属 |
| --- | --- | --- |
| wrong-report | `report.json` key/value 错 | Conditional Recovery@k |
| no-report | 代码跑了但没写 report，或 agent timeout | Conditional Recovery@k |
| partial-run | 依赖装了一半、脚本跑了一半、results 不完整 | Conditional Recovery@k |
| wrong-output | results 目录有错误/过时 outputs，但源码和数据完好 | Conditional Recovery@k |
| env-polluted | package/Docker/kernel 状态混乱但可修 | Conditional Recovery@k，必要时单独 bucket |
| source-mutated | 源码被改，但可通过 backup/diff 修 | Conditional Recovery@k |
| source/data-destroyed | 关键 code/data/instruction 被删或不可逆覆盖 | Fatal Attempt Rate |
| disk/runtime-broken | 磁盘满、Docker/Python/R/system 被改坏到无法继续 | Fatal Attempt Rate 或 infrastructure failure |

如果在 CORE-Bench 上计算 Recovery@k，我建议：

1. 每个 attempt 前后记录 filesystem manifest：关键 code/data/task files 的 path、size、hash。
2. `report.json` 和 `results/` 错误不要直接判 fatal；它们通常可覆盖。
3. 对 `code/` 和 `data/` 的 destructive diff 单独标注。
4. medium/hard 中允许合理 patch，但要求保留 patch diff 或 backup。
5. evaluation 在 copy/snapshot 上跑或只读 live state；不要让 adapter 为了评估而清理/覆盖 live workspace。
6. 对 timeout 分清楚是 `partial-run but recoverable`，还是环境已经被破坏。

## 最终建议

CORE-Bench 的任务级结论可以概括为：

> Easy level 大多是稳健可恢复；medium/hard level 大多是条件可恢复；CORE-Bench 没有明显任务级不可恢复提交动作，但非常需要 action-level 文件系统 diff 来识别 fatal attempts。

如果给一个粗略比例：

- 90/270，也就是 33.3%，是稳健可恢复的 easy tasks。
- 180/270，也就是 66.7%，是条件可恢复的 medium/hard tasks。
- 0/270 是任务设计层面的不可恢复倾向。
- 但 Fatal Attempt Rate 仍然可能不低，取决于 agent 是否会破坏 code/data/runtime。

因此 CORE-Bench 对 proposal 的价值在于：它把 recovery 从“撤销外部业务副作用”转向“在一个被自己弄乱的科研代码环境里继续调试、复现和修正答案”。整体 Recovery@k 失败时，必须拆开看：

```text
Recovery@k = 是否保住 code/data/runtime 可用性 × 是否能继续完成复现实验并写对 report.json
```

这正好对应：

- Fatal Attempt Rate：agent 是否破坏了 capsule 的可复现性。
- Conditional Recovery@k：如果 capsule 仍可复现，agent 是否能从部分运行、错误输出、错误报告中继续修回来。
