# 可运行 Agent 决策循环实验

观察已经返回，下一轮会做什么？这个实验用两个可读的确定性策略，展示一次完整的 **状态 → 决策 → 工具 → 观察 → 新状态** 循环。你可以直接看到每一轮为什么继续、何时读取原文，以及为什么预算用完不等于任务完成。

这是 `agent-agent-loop` 课时的独立 Python 3.12 实验，版本 `agent-loop-v1`。运行时只有标准库，无模型、网络、服务或数据库；`model_calls: 0` 表示这个程序没有模型调用。策略不是模型推理，结果是实际读取的教学摘要，不是模型生成答案。

## 先运行，再解释观察

在独立开发目录解压下载的 ZIP，使用 Python 3.12 和 uv。所有命令在解压目录执行：

```sh
uv sync --frozen
uv run --frozen python -m agent_loop --help
uv run --frozen pytest
uv run --frozen ruff check .
uv run --frozen ruff format --check .
```

`pyproject.toml` 和 `uv.lock` 固定开发依赖 pytest 9.1.1、Ruff 0.15.22。安装可能访问依赖源；下面的循环命令运行时不访问网络。平台不执行你修改后的代码，请在自己的独立受控开发环境运行练习。

先在 [EVIDENCE.md](EVIDENCE.md) 写下你的预测，再逐条运行，不要先把下表当作运行证据：

```sh
uv run --frozen python -m agent_loop --case found --policy evidence_first --max-steps 3
uv run --frozen python -m agent_loop --case found --policy evidence_first --max-steps 1
uv run --frozen python -m agent_loop --case empty --policy evidence_first --max-steps 3
uv run --frozen python -m agent_loop --case found --policy repeat_search --max-steps 5
uv run --frozen python -m agent_loop --case found --policy evidence_first --max-steps 2
```

以下是固定资产下应当核对的预期；你的实际 stdout 才是本次证据。

| case / policy / 预算       | 预期动作               | 决策数 / 工具调用数 | 停止原因    |
| -------------------------- | ---------------------- | ------------------- | ----------- |
| found / evidence_first / 3 | search → read → finish | 3 / 2               | completed   |
| found / evidence_first / 1 | search                 | 1 / 1               | step_limit  |
| empty / evidence_first / 3 | search → finish        | 2 / 1               | no_evidence |
| found / repeat_search / 5  | search × 5             | 5 / 5               | step_limit  |
| found / evidence_first / 2 | search → read          | 2 / 2               | step_limit  |

对照第一条与最后一条：预算 2 的运行确实读到了文档，却没有完成第三轮 `finish` 决策。核心不会在预算外替策略补一步。所有三种业务停止原因都返回退出码 0；程序正常产生报告，不代表任务完成。

## 跟读一条真实轨迹

查询 `循环与工具` 的第一次 `search` 实际匹配两个资料 ID：`loop-guide`、`tool-guide`。工具按资产顺序做关键词子串匹配，只返回 ID，没有正文。`evidence_first` 看到非空观察才选择读取第一个 ID。

在报告中依次找：

1. `steps[0].before`：没有命中或已读资料，`last_observation` 是 null。
2. `steps[0].observation`：实际搜索结果。`after.found_ids` 随之更新，`read_documents` 仍为空。
3. `steps[1].observation.document.body`：`read` 从本地资产读出的完整正文。直到这一步，状态里才有已读证据。
4. `steps[2]`：`finish` 没有工具观察，`observation` 是 null；`before` 与 `after` 相同。
5. `result`：最近实际读到的文档；`final_state` 等于最后一轮 `after`。

`decision_count` 数每一次策略调用，包含 `finish`；`tool_dispatch_count` 只数实际 `search`、`read`。`max_steps` 限制的是前者。报告没有 token、费用或模型质量指标，不要从这些计数推算它们。

无命中查询案例 `empty` 会得到真正的空搜索观察。策略据此选择 `finish(no_evidence)`，没有读取动作；它与预算耗尽的 `step_limit` 不同。

## 从哪里读代码

| 文件            | 职责                                 | 先看的问题                        |
| --------------- | ------------------------------------ | --------------------------------- |
| `agent_loop.py` | 固定 CLI 参数、策略选择和报告元数据  | case 只在何处选择查询？           |
| `engine.py`     | 同一个有界循环、观察校验、事实与快照 | 什么证据才允许 completed？        |
| `policies.py`   | 两个确定性决策函数                   | 上一轮观察如何改变下一步动作？    |
| `tools.py`      | 固定资产加载、真实匹配与读取         | 搜索命中与读到正文为什么分开？    |
| `fixtures.json` | 两份教学摘要与两条查询               | 正文、关键词、查询分别影响什么？  |
| `test_loop.py`  | 人工预期和可调用反事实               | 空工具结果能否改变 found 的结局？ |
| `test_cli.py`   | 真实子进程、资产与 CLI 行为          | 程序如何拒绝不完整报告？          |

源码公开的 `engine.run_loop(query, decide, max_steps, *, search, read)` 让原生测试替换实际工具或策略。CLI 不提供插件或任意路径入口。测试里的空搜索 seam 保持 found 查询不变，却实际返回空 ID 列表；同一个核心必须走 `no_evidence`。坏策略试图跳过 read 直接完成，会得到执行错误，而不是成功报告。

`before`、`after` 与传给策略的 snapshot 都是副本。策略可以提出动作，不能靠修改传入字典把“搜索到”伪装成“已经读取”。

## 练习：让重复策略学会推进

`decide_repeat_search` 故意尚未完成：它看到上轮搜索观察后，仍提交相同的搜索。先运行它并记录五轮之间哪些状态完全没变，再在自己的副本完成以下行为：

- 搜索为空时，选择 `finish(no_evidence)`。
- 搜索非空时，选择读取观察中的第一个 ID。
- 收到真实读取观察后，才选择 `finish(completed)`。

保留 `engine.py` 的预算、读取资格和完成资格检查，不在核心中按 case 名硬编码成功，也不在循环外补 finish。复用 found / 预算 3、empty / 预算 3、found / 预算 1 的相同动作与快照预期来检验新策略。初始测试明确记录“重复搜索”教学基线；修改策略后应将该项改为你写在运行前的新预期，并保留原有 evidence_first 与核心反事实测试。不要只删除失败断言。

这项练习由你完成，初始包没有预先修复重复策略，也不自动评分。最后记录一个被实际观察纠正的假设，以及仍未验证的事项。

## 资产和错误边界

`fixtures.json` 只在包内读取；仓库源码另有固定的 `../shared/fixtures.json` 开发布局回退，仅相邻文件缺失时使用。相邻文件损坏或是目录时不会静默改用另一份。两份正文是维护者编写的教学摘要，`source` 只供延伸阅读，程序不会联网下载页面。

报告的 `assets_sha256` 是实际输入的 canonical JSON 指纹，方便对照两次运行是否使用相同资料；它不是签名。合法修改查询或正文会改变指纹与实际结果，初始包人工预期只适用于原始资产。

- 退出 2 / `invalid_input`：参数不符合固定 CLI，不运行工具。
- 退出 1 / `assets_invalid`：完整资料未通过加载或校验，不运行工具。
- 退出 1 / `execution_failed`：策略、工具或执行关系出错，不返回部分成功轨迹；出错前可能已有只读调用。

正常报告和错误都只输出一行 JSON，只有精确 `--help` 输出帮助文字。参数、逐字段报告和人工快照见 [CONTRACT.md](CONTRACT.md)。

延伸阅读：[Agents](https://developers.openai.com/api/docs/guides/agents)、[Function calling](https://developers.openai.com/api/docs/guides/function-calling)。本实验只教可观察的循环事实，不替代真实模型决策质量、生产工具权限或持久化恢复的验证。
