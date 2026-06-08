# OSWorld 任务可恢复性分析

本文分析 `external/osworld/src/evaluation_examples/examples` 下的 369 个 OSWorld 任务。分析对象是任务 JSON 中的 `instruction`、`config`、`evaluator.func`、`evaluator.result` 和相关应用，而不是某个 agent 的真实执行轨迹。

因此这里给出的不是实测 Fatal Attempt Rate，而是任务级别的可恢复性判断：如果第一次 attempt 做错，后续 attempt 在不 reset、不 rollback 的前提下，理论上是否还能完成原任务并清理坏状态。

## 判定口径

我把任务分为四类：

| 类别 | 含义 |
| --- | --- |
| 可恢复 | 错误 attempt 通常只改变本地文件、应用配置、窗口状态或生成物；后续可以继续编辑、覆盖、删除多余文件或重做输出。 |
| 条件可恢复 | 后续仍可能恢复，但要求关键源文件、账号、邮件、云端目录、系统权限或外部服务状态没有被进一步破坏；需要显式清理副作用。 |
| 不可恢复倾向 | 任务表面包含删除唯一状态、移除账号、清空历史/cookie、外部 git push 等操作；一次方向错误的 attempt 可能直接造成无法无痕恢复的状态。 |
| 不适用 | `evaluator.func == "infeasible"`。这类任务的正确行为是 `FAIL`，本身不是“完成目标状态后再 recovery”的问题。 |

这里的“不可恢复倾向”不是说该任务任何错误都会 fatal，而是说这类任务存在非常自然、任务相关的错误动作，一旦发生就会丢失原状态或产生外部副作用。相反，“可恢复”也不是说任意破坏都能恢复；如果 agent 主动 `rm -rf` 或清空全部源文件，几乎所有桌面任务都会变成不可恢复。本文按合理的 task-directed mistake 来判断。

## 总体结论

OSWorld 总任务数为 369，其中 29 个是 infeasible，应从普通 recovery 统计中单独拿出。

| 类别 | 数量 | 占全部 369 | 占可执行任务 340 |
| --- | ---: | ---: | ---: |
| 可恢复 | 306 | 82.9% | 90.0% |
| 条件可恢复 | 30 | 8.1% | 8.8% |
| 不可恢复倾向 | 4 | 1.1% | 1.2% |
| 不适用 / infeasible | 29 | 7.9% | - |

如果把“条件可恢复”也算作在谨慎清理下可恢复，则 OSWorld 的可执行任务中约 336/340，也就是 98.8%，理论上仍可从一次非致命错误中恢复。

这说明 OSWorld 和 Sokoban 的 recovery 结构不同。Sokoban 很容易出现清晰的 dead state；OSWorld 大多数任务是文件/配置编辑，错误后通常还能继续编辑。但 OSWorld 的 fatal 风险集中在少数真实副作用任务：删除浏览器状态、移除邮件账号、外部 git push、云端上传/邮件移动/系统安装等。

## 按 Domain 统计

| Domain | 总数 | 可恢复 | 条件可恢复 | 不可恢复倾向 | Infeasible |
| --- | ---: | ---: | ---: | ---: | ---: |
| chrome | 46 | 41 | 0 | 2 | 3 |
| gimp | 26 | 16 | 0 | 0 | 10 |
| libreoffice_calc | 47 | 46 | 0 | 0 | 1 |
| libreoffice_impress | 47 | 47 | 0 | 0 | 0 |
| libreoffice_writer | 23 | 22 | 0 | 0 | 1 |
| multi_apps | 101 | 72 | 27 | 1 | 1 |
| os | 24 | 16 | 3 | 0 | 5 |
| thunderbird | 15 | 13 | 0 | 1 | 1 |
| vlc | 17 | 15 | 0 | 0 | 2 |
| vs_code | 23 | 18 | 0 | 0 | 5 |

## 可恢复任务类型

### Office 文档编辑

`libreoffice_calc`、`libreoffice_impress`、`libreoffice_writer` 是最典型的可恢复任务。它们的 evaluator 大多是 `compare_table`、`compare_pptx_files`、`compare_docx_files`、`compare_pdfs`，最终检查的是本地文件内容。

例子：

- `libreoffice_calc/01b269ae-2111-4a07-81fd-3fcd711993b0`：填充 B1:E30 空白单元格。
- `libreoffice_calc/0cecd4f3-74de-457b-ba94-29ad6b5dafb6`：重命名和复制 sheet。
- `libreoffice_impress/2b94c692-6abb-48ae-ab0b-b3e8a19cb340`：移动 slide 2 的图片。
- `libreoffice_writer/0b17a146-2934-46c7-8727-73ff6b6483e8`：把 H2O 中的 2 改成下标。

这些任务的错误通常是格式错、单元格错、slide 错、导出文件错。后续 attempt 可以重新打开文件、继续修改、删除错误生成物、重新导出。只要源文档没有被彻底删掉或覆盖成不可读状态，任务仍然可恢复。

### GIMP / VLC / VS Code 本地编辑

GIMP 的可执行任务主要是图像编辑和配置变更，VLC 是播放、导出音频/视频、修改 VLC 配置，VS Code 是 settings/keybindings/workspace/文本修改。这些多数也是可恢复的。

例子：

- `gimp/554785e9-4523-4e7a-b8e1-8016f565f56a`：增强照片色彩。
- `gimp/7b7617bd-57cc-468e-9c91-40c4ec2bcb3d`：把 GIMP 最小 undo steps 设为 100。
- `vlc/8f080098-ddb1-424c-b438-4e96e5e4786e`：从视频导出 MP3。
- `vs_code/276cc624-87ea-4f08-ab93-f770e3790175`：修改 VS Code wrapping line length。
- `vs_code/ec71221e-ac43-46f9-89b8-ee7d80f7e1c5`：把第 2 到 10 行缩进增加一个 tab。

错误 attempt 可能生成错文件、改错配置或安装错扩展，但后续可以覆盖目标文件、编辑 JSON 配置、卸载/重装扩展或重新导出。

### Chrome 浏览与页面定位

Chrome 里大量任务只是打开正确网页、设置过滤条件、保存 PDF、创建书签或调整浏览器设置。

例子：

- `chrome/1704f00f-79e6-43a7-961b-cedd3724d5fd`：筛选 Zurich 租车。
- `chrome/6c4c23a1-42a4-43cc-9db1-2f86ff3738cc`：筛选可用 miles 购买的航班。
- `chrome/bb5e4c0d-f964-439c-97b6-bdb9747de3f4`：把 Bing 设为默认搜索引擎。
- `chrome/e1e75309-3ddb-4d09-92ec-de869c928143`：把当前网页保存成 PDF。

这类错误通常是页面错、筛选错、tab 错、书签名错或设置错。后续 attempt 可以导航到正确页面或改回正确配置。

## 条件可恢复任务类型

这类任务不是马上不可恢复，但 recovery 依赖额外条件：源文件仍在、账号仍能访问、云端错误文件可以删除、系统状态能清理、邮件没有被永久删除。

### OS 文件/系统状态任务

代表任务：

- `os/37887e8c-da15-4192-923c-08fa390a176d`：按修改时间压缩旧文件并移动其他文件。
- `os/5812b315-e7bd-4265-b51f-863c02174c28`：创建受限 SSH 用户 `charles`。
- `os/5ea617a3-0e86-4ba6-aab2-dac9aa2e8d57`：从 Trash 恢复误删海报。

这些任务仍然可能恢复，但风险比普通文件编辑高。例如按 mtime 分文件时，如果 agent 移错并覆盖，后续需要知道原文件结构；创建系统用户时，如果建错用户、改错权限、污染 sudo/ssh 配置，后续需要显式删除错误用户和配置；Trash 恢复任务如果错误 attempt 清空 Trash，则目标文件可能直接丢失。

### 云端和邮件相关 multi_apps

代表任务：

- `multi_apps/22a4636f-8179-4357-8e87-d1743ece1f81`：把 `Meeting-Agenda.docx` 转 PDF 并上传到 Google Drive。
- `multi_apps/46407397-a7d5-4b6b-92c6-dbe038b1457b`：从邮件 docx 中导出图片并上传到 Google Drive。
- `multi_apps/78aed49a-a710-4321-a793-b611a7c5b56b`：保存最旧邮件附件到 Google Drive，并把邮件移动到 `have_seen`。
- `multi_apps/a0b9dc9c-fc07-4a88-8c5d-5e3ecad91bcb`：备份 Bills 邮件到 Google Drive。
- `multi_apps/b52b40a5-ad70-4c53-b5b0-5650a8387052`：合并邮件附件 PDF 并上传到 Google Drive。

这类任务如果上传错文件、放错目录或移动错邮件，理论上可以删除云端错误文件、重新上传、把邮件移回。但这要求 agent 还能访问同一账号，并且能识别和清理错误副作用。它们应该算 conditional recovery，而不是普通本地 recoverable。

### 大规模文件整理/安装/环境配置

代表任务：

- `multi_apps/337d318b-aa07-4f4f-b763-89d9a2dd013f`：核对 invoice 和 bank statement，把不匹配 invoice 放到 `problematic`。
- `multi_apps/869de13e-bef9-4b91-ba51-f6708c40b096`：整理 desktop，把论文、项目和其他文件分到不同文件夹。
- `multi_apps/48d05431-6cd5-4e76-82eb-12b60d823f7d`：修复 `conda` 不存在的问题。
- `multi_apps/69acbb55-d945-4927-a87b-8480e1a5bb7e`：为 GitHub 项目配置环境。
- `multi_apps/f8369178-fafe-40c2-adc4-b9b08a125456`：安装 Orchis theme 并切换 GNOME theme。
- `multi_apps/e1fc0df3-c8b9-4ee7-864c-d0b590d3aa56`：安装 LibreOffice 的 LanguageTool extension。

这些任务的问题是状态面比较大。整理文件时，错误分类可能还能移回，但如果覆盖或删除了唯一文件，恢复难度会显著上升。安装/环境配置任务通常可修，但错误安装可能污染 PATH、包版本、系统配置或应用扩展状态，后续 recovery 需要清理。

## 不可恢复倾向任务

这里我标了 4 个高风险任务。它们不是“绝对不可恢复”，但很容易因为一次任务相关错误进入无法无痕恢复的状态。

### `chrome/44ee5668-ecd5-4366-a6ce-c1c9b8d4e938`

任务要求清除 YouTube browsing history，以便查找一个月前访问的网站。

如果 agent 错删了更多历史，尤其是把非 YouTube 历史也清掉，那么原本用于查找目标网站的信息就丢了。浏览器历史不是普通可编辑文档，删除后 OSWorld VM 内通常没有任务级恢复路径。

### `chrome/7b6c7e24-c58a-49fc-a5bb-d57b80e5b4c3`

任务要求删除 Amazon tracking/cookie 数据。

如果 agent 清掉了所有站点 cookie，或删除了其他站点登录态/本地数据，后续虽然可以继续完成“Amazon cookie 被删”这个 evaluator，但外部副作用无法完全恢复。按 side-effect-aware recovery，这属于 fatal-prone。

### `thunderbird/dfac9ee8-9bc4-4cdc-b465-4a4bfcd2f397`

任务要求移除 Thunderbird 账户 `anonym-x2024@outlook.com`。

如果第一次 attempt 移除了错误账户、删掉本地邮件数据或破坏 profile，后续未必能从 VM 内恢复原配置。移除账号比改一个偏好设置更接近不可逆操作。

### `multi_apps/2c9fc0de-3ee7-45e1-a5df-c86206ad78b5`

任务要求从命令行把当前项目 changes push 到 `origin main`，commit message 是 `daily update`。

这是典型外部副作用任务。错误 commit、错误 branch 或错误 push 即使之后可以再 push 修正，也已经改变远端 git 历史或引入额外提交。除非允许 force-push/rollback，否则无法做到无痕恢复。

## Infeasible 任务

OSWorld 中有 29 个 infeasible 任务，evaluator 逻辑要求 agent 最后 `FAIL`。这些不应和普通 recovery 任务混在一起计算。

例子：

- `chrome/3720f614-37fd-4d04-8a6b-76f54f8c222d`：把 Chrome 界面语言改成虚构语言 `Xenothian`。
- `gimp/38f48d40-764e-4e77-a7cf-51dfce880291`：只用 GIMP 修剪视频。
- `libreoffice_writer/bb8ccc78-479f-4a2f-a71e-d565e439436b`：实时共享 Writer 文档给团队协作编辑。
- `os/b3d4a89c-53f2-4d6b-8b6a-541fb5d205fa`：打开蓝牙。
- `vlc/7882ed6e-bece-4bf0-bada-c32dc1ddae72`：直接在 VLC 播放 Google Play Movies & TV 购买的剧集。
- `vs_code/dcbe20e8-647f-4f1d-8696-f1c5bbb570e3`：不用扩展把 VS Code 背景改成 Downloads 里的照片。

这些任务的 recovery 语义不同：如果 agent 第一次没有 `FAIL` 而是乱改环境，后续理论上可以清理环境并 `FAIL`，但这测试的是识别不可行和副作用控制，不是“从失败状态完成原任务”。

## 对 Recovery 指标的启发

OSWorld 适合把 recovery 拆成两层：

1. 是否保留可恢复性。重点看删除、清空、移除账号、push、云端上传、系统配置污染这些动作。
2. 在仍可恢复时，能否完成修复。重点看 Office/图片/代码/配置类任务中，agent 是否能识别已发生的错误并继续编辑到正确最终状态。

如果做 OSWorld Recovery@k，我建议不要直接把所有任务混在一起算。更合理的分层是：

- 本地可编辑任务：Office、GIMP、VLC、VS Code、本地 Chrome 设置。
- 条件可恢复任务：云端上传、邮件移动、系统安装、文件整理、环境配置。
- 不可恢复倾向任务：浏览器历史/cookie 删除、账号移除、git push。
- Infeasible 任务：单独评估是否正确 `FAIL`，不要并入普通 recovery 分母。

这样才能解释 Recovery@k 失败到底是因为 agent 没有修复能力，还是第一次 attempt 已经破坏了恢复空间。
