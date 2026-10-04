# 观察驱动的决策循环与停止：冻结合同

版本 `agent-loop-v1`，关联课时仅 `agent-agent-loop`，实际包语言仅 Python。独立 CLI 运行维护者固定程序；运行依赖只有 Python 3.12 标准库，pytest/Ruff 作为冻结开发依赖。没有模型、token 估算、网络、HTTP 服务、数据库、任意路径/命令、CLI 插件、学员代码执行接口或新平台 Progress 字段。

本合同先于实现；人工预期来自下述规则及原文。

## 1. 固定资产

固定资产文件为包根 `fixtures.json`。顶层精确 `{asset_version,documents,cases}`；版本 `agent-loop-corpus-v1`。两份正文是维护者编写的教学摘要，source 是延伸阅读地址；不是获取网页的原文，不在运行时访问该地址。

| 固定顺序 / ID  | title          | body（精确全文，无末尾换行）                                                     | keywords          | source                                                           |
| -------------- | -------------- | -------------------------------------------------------------------------------- | ----------------- | ---------------------------------------------------------------- |
| 1 / loop-guide | 循环与停止     | 每轮先读取上一轮观察，再选择下一步动作。没有停止条件的循环可能反复调用同一工具。 | `["循环","停止"]` | `https://developers.openai.com/api/docs/guides/agents`           |
| 2 / tool-guide | 只读工具与观察 | 搜索结果只提供资料标识。读取工具返回原文后，才拥有本次已读证据。                 | `["工具","观察"]` | `https://developers.openai.com/api/docs/guides/function-calling` |

两份 documents 都有精确 `kind:"teaching-summary"`，文档对象字段为 `id,title,body,source,kind,keywords`。案例精确两条且按下表顺序，字段为 `id,query`：

| case ID | query（精确）                  |
| ------- | ------------------------------ |
| found   | `循环与工具`                   |
| empty   | `zzqxjx-agent-loop-empty-2048` |

初始资产 canonical SHA-256：`45a01bfcdff94b3d96826ba2f9eab7a750a51ea3e362939d8c2a0e1d1c06007d`。计算规则为 Python `json.dumps(asset, ensure_ascii=False, sort_keys=True, separators=(",", ":"))` UTF-8 字节；JSON 排版不影响该指纹。报告输出实际计算的 `assets_sha256`，它是输入对照指纹，不是签名。初始包/独立验收固定上述内容；本轮不新增资产编辑 UI 或任意文件入口。

加载只读包内固定位置：优先读取 `agent_loop.py` 相邻的 `fixtures.json`；仅该文件不存在时，才尝试固定仓库开发布局 `../shared/fixtures.json`。相邻文件存在但内容损坏、路径为目录、无法读取或其他读取错误时，不得回退；所有目录或读取错误统一 `assets_invalid`。与 cwd 无关，禁止在 cwd 搜索、不接受 `--file/--path`。校验版本、精确字段、两文档/两案例固定 ID 及顺序、非空文本/合法 HTTPS source、唯一且非空的关键词数组；不接受重复 JSON key 或非有限 JSON 值。缺文件、坏 JSON、结构/版本错误统一 `assets_invalid`，不能用坏数据跑部分案例。正文或查询的合法变化会改变实际指纹和实际结果，不借 pin 假定工具结果；独立初始包核对仍要求原始固定内容。

## 2. 两个实际只读工具

工具数据从已加载的 documents 读取，不返回预制案例报告。

### search(query)

1. 将传入 query 做 `strip().casefold()` 得到 q；本轮 query 来自两条固定案例，CLI 不接受自由 query。
2. 按 documents 的固定顺序遍历；只要该文档任意 `keyword.casefold()` 是 q 的子串则命中。不是分词、向量或语义检索。
3. 返回精确 `{type:"search_result", query:原传入query, found_ids:[实际命中ID...]}`。顺序为文档顺序、唯一，最多2个。只返回 ID，不返回正文。

基线 `found` 实际命中 `["loop-guide","tool-guide"]`，`empty` 实际命中 `[]`。

### read(document_id)

返回精确 `{type:"read_result", document:{id,title,body,source,kind}}`，从该固定 ID 对应的原文对象复制五个字段，不带 keywords；body 逐字节等于 assets 正文。循环只允许读取本次最新 search observation 已返回的 ID。没有任意文件读取或未知 ID 容错回退。

`Document` 以下指这五字段只读投影；`Observation` 指这两种 tool result 的联合类型。

## 3. State 与两个策略

每次运行初始 snapshot 精确：

```json
{
  "query": "案例的精确查询",
  "found_ids": [],
  "read_documents": [],
  "last_observation": null
}
```

- query 在本次运行中不变。
- found_ids 是**最近一次实际 search** 返回的完整有序 ID 列表；不是预期 ID，不是所有历史命中的并集。
- read_documents 是本次实际成功 read 的五字段文档，按首次读取顺序去重；不能因仅 search 命中就加入。
- last_observation 是最近一次实际 tool result。finish 没有新 tool result，不改变此字段。
- 返回给决策器和记录到报告的 snapshot 是副本；后续迭代不能回写旧 before/after。决策器没有直接修改循环事实的权限。

Decision 精确为以下三种之一：

```text
{action:"search", query:string}
{action:"read", document_id:string}
{action:"finish", outcome:"completed"|"no_evidence"}
```

### evidence_first

只依据传入 snapshot，不读取 case ID、预期结果表或步数计数：

- last_observation=null → search(snapshot.query)。
- last_observation.type=search_result 且 found_ids 非空 → read(该 observation 的第一个 ID)。
- last_observation.type=search_result 且 found_ids 为空 → finish(no_evidence)。
- last_observation.type=read_result → finish(completed)。

### repeat_search（故意不推进的教学策略）

- last_observation=null → search(snapshot.query)。
- last_observation.type=search_result → search(last_observation.query)，即看到上轮结果仍继续相同查询。
- 不主动 read/finish；意外输入 read_result 属执行错误。本包实际调用该策略的正常路径只会遇到 null/search_result。

该策略展示“观察已经到达，策略却没有把它转成进展”，不是第二套任务成功算法。两策略都标为 `deterministic_policy`；没有模型推理、内部思维链、生成式规划或模型质量声明。

## 4. 一个循环核心、明确计数

核心接口供固定原生/独立子进程测试使用：

```text
engine.run_loop(query, decide, max_steps, *, search, read) -> LoopResult
policies.decide_evidence_first(snapshot) -> Decision
policies.decide_repeat_search(snapshot) -> Decision
```

`run_loop` 不接受 case ID 或策略名，所以不能按案例/策略标签拼报告。CLI 仅在外层选择固定查询和固定函数，再加元数据。search/read 的函数注入是包内确定性测试 seam；CLI 不接受模块、路径、命令或 URL 来替换它们。

max_steps 是1..5严格整数（核心也拒绝bool）。每一次 decide 都计一轮，包含 finish；执行到第N轮前检查预算，耗尽后不得调用下一次 decide 或 tool。

每轮：复制 before → 调用 decide → 独立校验 action → 真正执行最多一个工具或确认 finish → 更新事实 → 保存 after。每次实际调用 search/read 计一次 tool_dispatch；finish 为0。报告中的 tool_dispatch_count 等于轨迹中 search/read action 数。

最少的业务完整性约束：

- search 的 query 必须等于本次 state.query，返回 query 必须与调用值相同，found_ids 必须是已知唯一固定 ID；随后从实际结果更新 found_ids/last_observation。
- read 的 document_id 必须属于 before.found_ids；返回文档 ID 必须等于请求值，随后保存实际 Document 及 last_observation。
- finish(completed) 只有 last_observation 是本次实际 read_result 且其文档存在于 read_documents 才合法；result 使用这份实际文档，不由策略输出 body。
- finish(no_evidence) 只有最新 search_result 的 found_ids 为空、且没有已读文档才合法。
- 非法 decision/工具结果、工具内部异常统一执行失败；不能把它们伪装为正常 no_evidence 或 step_limit。没有部分成功报告。该类错误可能发生在前面若干次只读调用之后，不声称零 dispatch。

三个正常停止原因：

| stop_reason | 触发                              | result                  |
| ----------- | --------------------------------- | ----------------------- |
| completed   | 合法 finish(completed)            | 最近实际读到的 Document |
| no_evidence | 合法 finish(no_evidence)          | null                    |
| step_limit  | max_steps 轮用完，尚无合法 finish | null                    |

例如预算2时已经 search+read 也不能在循环外自动生成 completed：finish 尚未占到一轮，仍是 step_limit。这个边界应让学习者看见，而不是自动补齐。

## 5. 精确报告

所有正常输出都是单行 UTF-8 JSON 加一个 LF，stderr 为空；没有时间戳、随机 ID 或真实睡眠，所以相同输入跨 cwd/hashseed 可逐字节比较。对象只含列出的字段。

```text
{
  contract_version:"agent-loop-v1",
  ok:true,
  lesson_id:"agent-agent-loop",
  asset_version:"agent-loop-corpus-v1",
  assets_sha256:64位小写hex,
  case_id:"found"|"empty",
  policy:"evidence_first"|"repeat_search",
  decision_source:"deterministic_policy",
  read_only:true,
  model_calls:0,
  max_steps:1..5,
  decision_count:1..5,
  tool_dispatch_count:0..5,
  stop_reason:"completed"|"no_evidence"|"step_limit",
  steps:[Step...],
  final_state:Snapshot,
  result:Document|null
}
Step = {
  index:从1开始连续整数,
  before:Snapshot,
  decision:Decision,
  observation:Observation|null,
  after:Snapshot
}
```

steps.length=decision_count<=max_steps；每步 before 等于前一步 after，第一步 before 是本案例的空初始 snapshot；final_state 等于末步 after。search/read 的 observation 是实际工具结果，finish 的 observation=null 且 before=after。三个停止原因由实际运行导出，不读取案例预期表。JSON report 不包含 token、usage、费用或“模型已解决”字段。

核心可固定已知 ID 为 `loop-guide` 与 `tool-guide`。核心对工具实际 observation 做形状、归属和决策关系校验，不按 case ID 或案例预期重算并替换工具结果；合法的真实空 observation 必须进入实际空结果分支。

LoopResult 精确为顶层的 `max_steps,decision_count,tool_dispatch_count,stop_reason,steps,final_state,result` 七字段；CLI 加其他固定元数据，不重新计算或改写核心轨迹。执行失败核心抛固定 `LoopError`，CLI映射如下，原异常不外泄。

## 6. 冻结人工预期

记 L 为 fixtures 中 loop-guide 的 Document 五字段投影。固定 found 的搜索观察为 F=`{type:"search_result",query:"循环与工具",found_ids:["loop-guide","tool-guide"]}`；固定 empty 为 E=`{type:"search_result",query:"zzqxjx-agent-loop-empty-2048",found_ids:[]}`；读取观察为 R=`{type:"read_result",document:L}`。

| CLI 输入                   | 精确 decision 序列                                        | observation 序列 | 决策数 / dispatch数 | stop_reason / result |
| -------------------------- | --------------------------------------------------------- | ---------------- | ------------------- | -------------------- |
| found / evidence_first / 3 | search(循环与工具), read(loop-guide), finish(completed)   | F,R,null         | 3 / 2               | completed / L        |
| found / evidence_first / 1 | search(循环与工具)                                        | F                | 1 / 1               | step_limit / null    |
| empty / evidence_first / 3 | search(zzqxjx-agent-loop-empty-2048), finish(no_evidence) | E,null           | 2 / 1               | no_evidence / null   |
| found / repeat_search / 5  | search(循环与工具) ×5                                     | F×5              | 5 / 5               | step_limit / null    |

快照人工展开：

- found 初始 A=`{query:"循环与工具",found_ids:[],read_documents:[],last_observation:null}`。
- 接收 F 后 B=`{query:"循环与工具",found_ids:["loop-guide","tool-guide"],read_documents:[],last_observation:F}`。
- 接收 R 后 C=`{query:"循环与工具",found_ids:["loop-guide","tool-guide"],read_documents:[L],last_observation:R}`。
- found/3：before/after 分别 A→B、B→C、C→C；final_state=C。
- found/1：只有 A→B；final_state=B，无已读正文。
- repeat/5：A→B，之后四步 B→B；final_state=B，read_documents始终空。
- empty 初始 D=query为empty的空snapshot；接收E后D→EState，其中found_ids/read_documents仍空、last_observation=E；finish为EState→EState。

其余合法CLI组合仍由同一规则执行。例如found/evidence_first/2为search+read后step_limit；empty/evidence_first/1为search后step_limit；repeat_search任意案例都执行恰max_steps次search后step_limit。不能只实现人工表四行。

## 7. CLI 与错误

唯一入口 `python -m agent_loop`。三个flag均必填、各恰一次、顺序可交换；仅 `--name value` 形式，不接受 `--name=value`、额外位置参数或未知flag：

```text
python -m agent_loop --case found|empty --policy evidence_first|repeat_search --max-steps 1|2|3|4|5
```

max_steps词法只有ASCII单字符1..5，不接受0、负数、01、1.0或空白拼接。`python -m agent_loop --help` 精确单参数才返回固定帮助文字、退出0；帮助为唯一非JSON正常stdout。help混其它参数仍invalid_input。不读取stdin命令或用户环境配置。

- 退出0：完整正常报告；包括no_evidence与step_limit，退出0不表示任务完成。
- 退出2：CLI无效，固定错误code=`invalid_input`，message=`循环实验参数无效`；不运行工具。
- 退出1：资产错误code=`assets_invalid`，message=`循环实验资料无效`；不运行工具。
- 退出1：内部执行错误code=`execution_failed`，message=`循环实验未产生完整报告`；不返回部分轨迹，不回显原始输入、异常、路径或环境。

错误stdout精确 `{contract_version:"agent-loop-v1",ok:false,error:{code,message}}` 加LF；stderr为空。先验证CLI，再加载完整固定资产，再运行。不为本实验扩展断管/信号/数据库提交等故障矩阵；独立验收器为所有自有CLI进程设有界期限并实际wait/reap。

## 8. 有意义的反事实和验收

原生测试操作同一个真实run_loop，固定可调用工具/策略，不读产品结果来生成预期：

1. 上表四行的逐步decision、observation、snapshot、两个计数与终态；加预算2证明“已read不等于已finish”。
2. 保持found查询与evidence_first，将search seam替换成真正返回 `{type:"search_result",query:"循环与工具",found_ids:[]}` 的固定函数，read spy在调用时失败。实际结果必须no_evidence、2决策/1dispatch，read零次；证明决定来自真实observation。
3. 固定坏策略：初始search，收到非空search_result后立即finish(completed)，跳过read。核心必须抛LoopError；CLI不能给出完成报告。独立临时副本/固定子进程再让同一个“found/3正常行为断言”拒绝该变体，不能仅根据另写的`bad:true`旗标宣称发现。
4. 预算上限用spy核对decide/tool实际调用，越限后没有一次多余调用；before/after历史保持快照，不会全部变成最终状态。
5. 最低输入/资产检查和两种工具真实匹配/原文字段测试。无需大量一般安全边界矩阵。

独立ZIP验收先核固定资产与上表人工期望，在仓库外冻结安装、跑原生测试/Ruff，再实际CLI；不import被测策略或loader来计算oracle。原文与snapshot引用来自验收器自己的固定literals/资产核对。跨cwd/PYTHONHASHSEED运行同一CLI应stdout字节一致；正常与失败进程都wait/reap。任何单测或CLI成功都不声称已经验收真实模型规划。

## 9. 文件白名单与 README 练习

Python8文件：

```text
agent_loop.py
engine.py
policies.py
tools.py
test_loop.py
test_cli.py
pyproject.toml
uv.lock
```

shared6文件：`fixtures.json,README.md,CONTRACT.md,EVIDENCE.md,AGENTS.md,.gitignore`；另含manifest.json与.prettierrc.json，合计16 ZIP成员。包代码模块/文件若需调整，先同步白名单，不能静默打包任意文件。

manifest：id=`agent-loop`，version=`agent-loop-v1`，lessons=`["agent-agent-loop"]`，languages=`["python"]`。仅新增一个证据组合，现有47→48，不修改48容量，不顺手宣称覆盖agent-budgets或model-context。

README至少完成四条对照操作，并要求学习者先写预期，再核实际报告、记录一个错误假设和未验证项。具体延伸练习：在自己的受控副本修复repeat_search，使空search提前finish(no_evidence)、有命中转read、实际读取后再finish；保留核心预算保护，并用相同found/empty/预算1断言验证。初始版保留故意重复策略，不预先替学习者修复，不自动评分或调用模型。修改后的代码仅在独立受控开发环境运行，平台不执行学员提交。
