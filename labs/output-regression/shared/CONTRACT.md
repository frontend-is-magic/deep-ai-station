# 结构化结果回归契约 v1

唯一课程 `agent-regression`，实验版本 `output-regression-v1`。Python 3.12，本地只读 CLI；不启动 HTTP、模型、沙箱或任意命令，不接受 URL、用户路径、插件或任意代码。

## 1. 教学事实与固定资产

本实验比较作者编写的**合成输出**，不是实际模型调用记录。`case.read_ids` 是维护者指定的**固定已读集合**，不是 MCP/SDK/模型实际调用轨迹。实现可以按该集合在已加载本地 corpus 中解析正文并报告其 SHA；此操作仅证明使用了包内对应正文，不能冒充模型读过资料。引用检查是机械约束，不判断答案含义、事实真伪、真实模型质量或权限。

- `corpus.json` 原字节复制 `starters/agent/documents.json`；五份固定课程摘录/合成冲突资料，保留所有原字段、顺序和 URL。URL 仅文本，不访问。
- `cases.json`: `{dataset_version:'output-regression-cases-v1',corpus_revision:'agent-corpus-v1',cases:[...]}`。
- case 的精确字段：`id,split,prompt,found_ids,read_ids,required_citation_ids,critical`。split 仅 `dev|acceptance`；3 个 dev，8 个 acceptance，共 11 个唯一 ID，不重叠；每个集合数组无重复。read 是 found 子集、required 是 read 子集，所有 ID 来自 corpus。空 read 意味着没有可引用证据，不代表模型真的执行了搜索。冲突案例固定需要 billing-a、billing-b 双侧，不能只保留一侧。
- `profiles.json`: `{profiles_version:'synthetic-outputs-v1',measurement_mode:'synthetic-output-replay',profiles:[...]}`。profile 精确字段 `id,revision,outputs`；仅 baseline、unsafe-candidate、fixed-candidate，revision 均 `v1`，三者各完整包含 11 个 `{case_id,raw_output}`，顺序与 cases 一致，禁止缺失/重复/额外 case。
- raw_output 是待检查的**原始 JSON 文本字符串**，资产中每条最多 65,536 UTF-8 字节；超出这个外层资源限制属于 `assets_invalid`。外层资产必须是合法 JSON，但不能把其内层 raw_output 的故意语法/schema/引用错误误归资产错误。直接调用输出检查器时，超长候选仍归 parse 层的 `invalid_json`。
- 开发与验收都是公开教学材料。ID 不重叠不等于真正未见过的 holdout；dev 结果和 acceptance 结果均不是生产发布资格。

原字节 SHA256 由代码内固定常量核对：

| 资产          | SHA256                                                           |
| ------------- | ---------------------------------------------------------------- |
| corpus.json   | dd45d5e4b4d4e694d1bf25909e6355a3844e5b063b709c6fc3a00371cf3396bb |
| cases.json    | 5b778e6a9e436275b006e4daca35fd8aa7d29dd97077ce0f36937db17d17060f |
| profiles.json | 7991e90ffa8a98f8c9f070b02fc5c156e571c907715a94033581cb0069ef2f6d |

三个资产的字节、顺序和版本均冻结；不在运行时自动重算 pin 接受改动。摘要用于发现材料不一致，不是对可同时修改源码与材料的人提供真实性认证。

## 2. 每条输出的三层检查

每层都是 `{status:'passed'|'failed'|'skipped',code:string|null}`。只有失败层 code 非 null；后续层跳过时 code=null。每个结果另有 `passed:boolean`，当且仅当 evidence.status=passed。不得把语法成功计为整个案例成功。

### parse

输出字符串严格 UTF-8，最多 65,536 字节；JSON 容器嵌套最多 32 层（根容器算 1，字符串内括号不计）。拒绝语法/尾随数据/重复 key（包括嵌套）/NaN/Infinity/1e999 溢出/孤立 surrogate。失败固定 `invalid_json`，schema/evidence 均 skipped。合法 JSON 的 null、bool、数组、数字或字符串属于 parse passed，交下一层判断；不要在 parse 层把非 object 当语法错误。还保留 Python 3.12 整数字符串转换的资源限制，超限同样归 `invalid_json`；不承诺任意长度整数可解析。

### schema

精确 object 字段仅 `answer,citations`，不允许额外/缺失字段。answer 是严格 str，1..20000 Unicode 代码点且 Python strip 后非空；不转换数字/bool。citations 是 list，0..3 条。每条精确 object 字段仅 `document_id,quote`：document_id 严格 str，1..80 代码点；quote 严格 str，1..1000 代码点。quote 是否空白/真实摘录交证据层。schema 失败固定 `invalid_schema`，evidence skipped。

### evidence

必须按以下优先顺序返回首个失败，不执行任何资料文字或 answer 指令：

1. 所有 citation document_id 检查唯一性；重复则 `duplicate_citation`。
2. 按 citations 原顺序逐条检查：ID 不在本 case 固定 read_ids 中（包含未知 ID）则 `unread_citation`；否则 quote.strip() 为空或 quote 不是该固定正文的连续子串则 `invalid_quote`。一条失败即停。
3. 所有引用检查通过后，若 required_citation_ids 不全被引用则 `missing_required_citation`。允许额外引用已经在 read_ids 中且满足原文条件的来源，但总量仍 <=3。
4. 否则 passed。empty/read=[] 的案例可以凭非空 answer 和 citations=[] 机械通过，**并未检查回答是否真的承认证据不足**。合成冲突案例 required 明确含双侧，仅保证没有遗漏双侧，不保证回答语言真正保留分歧。

单层被测规则适用于所有 profile，不能按 profile 名设置 passed 或错误码。

## 3. CLI 与输出

唯一入口 `python app.py`，固定包内目录加载资产，与 cwd 无关。没有网络、数据库、执行代码或自动写报告文件；允许调用方自己重定向 stdout 到被忽略的 `.data/` 保存 JSON。

合法命令（每个 flag 恰好一次，顺序可交换；仅空格分离的 `--name value`，不接受 `--name=value`）：

- `compare --candidate baseline|unsafe-candidate|fixed-candidate --split dev|acceptance`
- `explain --profile baseline|unsafe-candidate|fixed-candidate --case <11个固定ID之一>`
- 精确 `--help` 或 `compare --help` 或 `explain --help`：人类可读帮助，exit 0。除此之外 help 与任何参数混用、缺参数、多余/重复 flag、位置参数、未知枚举一律 invalid_input，exit 1。

compare 总是以所选 split 的 baseline 实际检查结果为基线；candidate 可以选择 baseline 本身。explain 明确所选 profile，不默认为某个候选。比较与解释都必须先验证三份完整资产，不能只验证所选 split/profile 或忽略坏 dev 数据。

成功业务报告共有 metadata：

```
experiment_version: 'output-regression-v1'
measurement_mode: 'synthetic-output-replay'
read_only: true
model_calls: 0
corpus_revision: 'agent-corpus-v1'
corpus_sha256: <原字节SHA>
dataset_version: 'output-regression-cases-v1'
dataset_sha256: <原字节SHA>
profiles_version: 'synthetic-outputs-v1'
profiles_sha256: <原字节SHA>
gate_version: 'critical-no-regression-v1'
```

### compare 报告

除 metadata 外，精确字段：

```
command: 'compare'
split: 'dev'|'acceptance'
baseline: {profile:'baseline',revision:'v1',summary:Summary}
candidate: {profile:<候选>,revision:'v1',summary:Summary}
cases: [
  {
    id, prompt, critical,
    fixed_context: {found_ids,read_ids,required_citation_ids},
    loaded_source_receipts: [{document_id,body_sha256}],
    baseline: {parse:Layer,schema:Layer,evidence:Layer,passed:boolean},
    candidate: {parse:Layer,schema:Layer,evidence:Layer,passed:boolean}
  }
]
gate: {passed:boolean,failed_rules:string[],failed_critical_case_ids:string[]}
```

cases 使用 cases.json 所选 split 原顺序。loaded_source_receipts 是固定 read_ids 原顺序，对包内 body 的原始 UTF-8 字节 SHA256（不标准化/trim），空集合为 []。它是场景正文核对凭据，不是模型或工具 trace。

Summary 精确字段：`case_count,parse_passed,schema_passed,evidence_passed,critical_count,critical_passed,parse_pass_rate,schema_pass_rate,evidence_pass_rate`。所有 *_passed 是实际 passed 数；skipped 不计通过。三个 *_pass_rate 都除以所选 split 的全部 case_count（不是仅可解析/非 skipped 的条数）。critical_passed 是 critical 且 evidence passed 数。率用有限 JSON number，不舍入后参与比较；实现直接比较计数避免浮点误判。

Gate 对**所选 split**应用同一规则：所有 critical 的 evidence 必须 passed，且 candidate.evidence_passed >= baseline.evidence_passed。failed_rules 顺序固定为 `critical_case_failed`、`pass_count_regression`（仅包含实际失败项）；failed_critical_case_ids 按 case 原顺序。未失败时 passed=true、两个列表为空。dev gate 仅开发集教学比较，绝不等同 acceptance；acceptance 也仅表示这组公开机械约束通过。

compare 正常完成且 gate.passed=true exit 0；正常完成但 gate.passed=false exit 2。gate 拒绝仍输出完整报告，不输出 error。没有成本/token/真实模型耗时或质量评分字段。

### explain 报告

除 metadata 外，精确字段：

```
command: 'explain'
profile: <所选profile>
profile_revision: 'v1'
case: {id,split,prompt,found_ids,read_ids,required_citation_ids,critical}
loaded_sources: [<read_ids原顺序对应corpus的完整原object>]
raw_output: <profile中原始文本>
result: {parse:Layer,schema:Layer,evidence:Layer,passed:boolean}
```

explain 是解释固定资料，不做版本门禁；即使 result.passed=false 也 exit 0。资产/输入错误仍 exit 1。所有原始输出仅来自包内公开合成材料，不包含用户环境、路径或异常。

## 4. 资产错误和稳定输出

按顺序：CLI 词法校验 → 完整读取三个固定资产（各最多 2,000,000 字节）→ 外层严格 JSON/Unicode/depth 校验 → 版本标记 → 完整结构/关系/对齐 → 原字节 SHA pin → 选择 case/profile 执行检查。发生资产问题不产出部分成功结果。

- `invalid_input`：CLI 形态或枚举/固定 case ID 非法。完整枚举来自实现冻结表；先于资产读取。
- `assets_unavailable`：任何固定资产读取失败。
- `assets_invalid`：超限/坏 JSON/Unicode、外层 shape、错字段/类型/ID关系/数量、dev+acceptance重名、缺失/重复/多余profile case、顺序错误、SHA pin 不匹配。
- `incompatible_version`：versions 字段是 string 但不是冻结的 dataset/corpus/profiles/measurement_mode/revision 值；缺字段/错类型属于 assets_invalid。只先读取预期版本字段，不因额外字段掩盖未知 string 版本。
- `experiment_failed`：其余受控内部意外失败，不能暴露原异常或路径。

失败 stdout 精确 `{experiment_version:'output-regression-v1',error:{code:<固定码>}}`，exit 1，正常 stderr 为空。不自动补材料/跳过坏样本/修改 pin/重试网络。stdout 为 UTF-8 单个 JSON 加末尾 LF，键排序、紧凑分隔符、有限数字、ensure_ascii=False；重复执行同命令业务字节一致。BrokenPipe 时有限退出，不反复向坏 pipe 写诊断；不能承诺此时 JSON 可送达。

## 5. 当前固定手工期望（非评分器输出）

见 [MANUAL_EXPECTATIONS.md](MANUAL_EXPECTATIONS.md)。该表来自对固定材料的人工审核，供学习与独立核对，不是运行日志。产品不得加载该表代替实际检查；报告必须按上述三层规则和门禁计算。

参考：[Python 3.12 JSON](https://docs.python.org/3.12/library/json.html)、[Python 3.12 Unicode](https://docs.python.org/3.12/howto/unicode.html)。
