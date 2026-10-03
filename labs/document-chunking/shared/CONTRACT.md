# 固定 Markdown 切分与来源定位契约

`document-chunking-v1`，只绑定 `agent-chunking` 的 Python 实验。运行 Python 3.12 标准库，只有固定资料的三个只读 CLI 命令；禁止外部路径、URL、上传、模型与网络调用。此文件定义验收要求，不记录尚未执行的测试结果。

## 1. 固定资产与版本

包内 `corpus.json` 的 `sources[].text` 是解码后原文；`cases.json` 保存固定问题与独立金标。资产是唯一正文来源，包内没有第二份 Markdown 副本。

所有业务正文逐字来自当前 `starters/agent/documents.json` 的 5 个 `body`；不是官方网页全文。标题、`保留来源。`、Unicode/围栏样本是此实验固定的教学包装。`provenance` 保留原项目路径/id/字段；`references` 仅列已有 URL 作来源背景，不请求、不把它声称成这些段落的逐字原址。取消计费两声明始终标记合成冲突，不能择一声称事实。

| 顺序 | source_id        | 包内位置                 | 来源 body             | 代码点 | UTF-8 bytes | 换行    |
| ---- | ---------------- | ------------------------ | --------------------- | -----: | ----------: | ------- |
| 0    | api-guide        | `corpus.json sources[0]` | api                   |     97 |         249 | 5 LF    |
| 1    | tool-evidence    | `corpus.json sources[1]` | tools + evidence      |    233 |         551 | 14 CRLF |
| 2    | billing-conflict | `corpus.json sources[2]` | billing-a + billing-b |     79 |         195 | 5 LF    |

固定 source_uri 为 `course://chunking/<source_id>`。`source_revision` 是 `sha256:` 加原文严格 UTF-8 bytes 的完整 SHA256，不做 trim、Unicode normalization 或通用换行转换。

- api-guide：`sha256:c5e76dddbee38ce0619ec01e6ce41ffc94570bf423f3cb993c5431614a0e1f3e`
- tool-evidence：`sha256:bf6a09d8e98c52294f205f7f8063fc7c6b11596cce3e70c9926ec9d9cc3cd5c7`
- billing-conflict：`sha256:584a01d55dcdb7c8d9e42aa4d2da1cd891a829ae4b625f247fe972579e512077`

版本层：

- contract_version = `document-chunking-v1`
- corpus_id = `agent-chunking-course-notes`，corpus_version = `chunking-corpus-v1`
- dataset_id = `chunking-evidence`，dataset_version = `chunking-evidence-v1`
- splitter_version = `markdown-boundary-v1`
- scorer_version = `course-lexical-v1`
- coordinate_unit = `unicode_codepoint`

`canonical(x)` 精确为 `json.dumps(x, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')`。集合版本不能用 JSON 文件排版或 mtime 代替：

- corpus_revision = SHA256(canonical(按 sources 顺序的 `{source_id,source_uri,source_revision}` 数组))，固定 `sha256:c1ef7914ddd34fdec4ed604c3dfb270c6ce19e431a10ccb9679837181bc79da5`。
- dataset_revision = SHA256(canonical(cases 数组))，固定 `sha256:a03d274140de23e046eb385b51717037d34f009d2a1d741c67ee34ab7e2db247`。
- 为覆盖标题/provenance/references 与标签漂移，代码另 pin 完整资产 canonical hash。corpus 资产：`sha256:67e12f61bc6954699784b5d28df09c1cabcc973230e3e42b58282e16e74a4d06`；cases 资产：`sha256:3eed9ec3443603fca295bca70a5f7bb755c8bc22c1f496497754930837eb427b`。它是冻结实验资料检查，不是签名/恶意本机用户安全边界。

## 2. 六个独立金标问题

`cases.json` 已包含精确 question、query、gold_id、来源完整 revision、start/end 和 quote。金标先按正文独立选取，用字面 quote 唯一定位并审核坐标；制作金标时未运行切分或排名，也未按候选输出选择标签。

| id                      | 固定 query                     | 问题/需要的证据                                               | 代码点 [start,end) | UTF-8 bytes [start,end) |
| ----------------------- | ------------------------------ | ------------------------------------------------------------- | ------------------ | ----------------------- |
| api-timeout             | API 超时 契约                  | API 输入/失败预算：api-budget / api-guide                     | [20,41)            | [38,93)                 |
| tool-write-boundary     | 只读工具 operation_id 未知结果 | 权限、写入去重与未知结果：tool-write / tool-evidence          | [31,81)            | [75,197)                |
| citation-no-fabrication | 引用 资料 没有匹配证据         | 实际资料来源及无证据不捏造：citation-boundary / tool-evidence | [103,144)          | [249,372)               |
| unicode-location        | 坐标样本                       | 原文 `A😀éZ。`：unicode-sequence / tool-evidence              | [172,178)          | [442,454)               |
| billing-conflict        | 取消 计费                      | 两侧声明：billing-side-a + billing-side-b / billing-conflict  | [21,37)、[46,78)   | [45,89)、[102,194)      |
| no-evidence             | zzzz unmatched                 | 无原文证据，gold=[]                                           | 无                 | 无                      |

五个正例、一个负例；六个证据 span。question 是人类评测说明，真正输入打分器的是 query。每个正例至少一个 gold，冲突题两个 gold 等权；不得把它们合并为一个“有返回就成功”。quote 原文完整值见 `cases.json`，禁止由 splitter 重新生成金标。默认 80/16 窗口无法在一个块内完整保留 tool-write 的 [31,81)，这是一个真实边界样本，不应为了提高评分缩短金标或拼接块后冒充连续块。

这是一份 6 题教学开发集，无盲测集，不对真实 RAG 泛化质量作结论。Unicode 题检验结构定位，不能据此推断中文语义理解能力。

## 3. 切分与坐标不变量

两策略只对三份固定 source 操作，不跨 source；完整输出文字都来自 `source_text[start:end]`。start/end 是 Python Unicode 代码点的零起点半开区间，不是 bytes、JS UTF-16 units 或视觉字形。派生 byte_start/byte_end = 各原文前缀 UTF-8 长度，必须满足 bytes 切片严格解码等于 text。CRLF 两点、emoji 一点、e+combining acute 两点；可截开组合字形，这是代码点窗口的限制。

每块固定字段：`chunk_id,source_id,source_uri,source_revision,start,end,byte_start,byte_end,header_path,text`。`header_path` 是所处最近标题层级的纯元数据，不 prepend 到 text，也不参与排名。window 的 header_path 按其 start 所处标题层级取值；块内部跨新标题时不会伪称全部正文属于该标题，完整 text/range 始终是依据。

chunk_id = `sha256:` + SHA256(canonical({splitter_version,strategy,configuration,source_id,source_revision,start,end}))；heading 的 configuration={}，window 的 configuration={size,overlap}。不截哈希，不只 hash text。改变来源、revision、范围、策略或实际配置都改变身份；同正文重复段落不去重、不覆盖。

heading 只识别以下子集，按每个真实标题行起点切开，到下一真实标题起点为止，最后到原文末尾；首标题前有非空长度前言（包括纯空白）也保留一块。空原文为 []。块不 trim，不丢换行，单源所有块连续无洞无重叠。仅标题节仍保留，长节不二次暗分割。

- Markdown 行分隔仅 CRLF / CR / LF（CRLF 作为一个行终止符，其两个代码点仍在原文中）。U+2028 不另作 Markdown 换行。
- ATX：行首 0..3 ASCII 空格，1..6 个 `#` 后为空格/tab/行末；允许可选闭合井号（必须前有空格/tab），header_path 去掉标记及标题首尾空格/tab；空标题保留空字符串。进入新标题时弹出所有 level>=新level 的祖先，再追加新标题，不为跳级标题虚构中间节点。`#x`、7 个井号、4 空格缩进不识别。
- fenced code：行首 0..3 空格，至少 3 个同类 backtick 或 tilde；backtick 开栏剩余行不能含 backtick。闭栏同类、长度至少开启长度、其后只能空格/tab。栏内标题均不是标题，未闭栏一直延伸到 EOF。
- 不识别 Setext/blockquote/list/HTML block/inline 标记，固定原文不要求这些。不得声称完整 CommonMark。

window 使用 stride=size-overlap，从 0 起取 `[start,min(start+size,n))`；end==n 后立即停止，没有空尾块。默认 size=80、overlap=16；允许 size 32..256、overlap 0..16 且 overlap<size。实际正文长度/成本必须报告，不能把代码点叫 tokens。

## 4. 固定检索与指标

分词沿用平台 `backend/retrieval.py` 的定义，在本实验独立版本化；运行时不 import 平台或 Pydantic。精确行为：ASCII词模式 `[a-z][a-z0-9_.-]+` 作用于 query.lower()；中文连续 `[\u4e00-\u9fff]+` 加原词和所有相邻二字；剔除当前 STOP 九词 `如何 怎样 什么 一个 应该 可以 需要 进行 以及`，按(-len,词)保留64个唯一词。单字中文 phrase 仍可保留，单字母英文不匹配。

每个 query term 若作为子串出现于 chunk.text.lower() 就贡献1分；不使用 heading/path/provenance 的额外权重。score>0 才返回；同分按 source 固定顺序、start、end 稳定排序。matched_terms 为实际命中词按 Unicode 顺序升序列出。`.lower()` 仅用于匹配，绝不把转换后的索引返回为原文坐标；本轮不改为 `.casefold()` 而声称复用旧 scorer。

每题每策略返回 top_k 1..5（默认2）。gold 仅当一个结果完整包含同 source_id + revision 的整个 span 才覆盖；多个块各覆盖一半不能算完整，重叠块重复覆盖同 gold 只计一次召回。

- 正例 evidence_recall_at_k = 唯一覆盖 gold 数 / gold 数。
- 正例 chunk_precision_at_k = 至少完整覆盖一个 gold 的结果块数 / top_k（即使结果少于 k，分母仍 k）。它是块精度，不是独立事实精度。
- 正例 first_evidence_reciprocal_rank = 首个完整覆盖任一 gold 的块排名倒数；无则0。
- 正例 no_result_accuracy=null；负例以上三个指标=null，no_result_accuracy 为结果空则1否则0。
- 每题同时返回 retrieved_codepoints=sum(len(result.text))；summary 分别给 indexed_chunk_count、indexed_codepoints=sum块长（重叠重复计成本）、retrieved_codepoints_total、positive_case_count=5、negative_case_count=1、前三正例指标的均值、负例 accuracy 均值。不把负例填0混进正例均值，不造综合胜负结论。

## 5. CLI、稳定输出和错误

入口 `python app.py`，仅三个只读子命令：

```sh
python app.py chunks --strategy heading
python app.py chunks --strategy window --size 80 --overlap 16
python app.py compare --size 80 --overlap 16 --top-k 2
python app.py quote --source-id tool-evidence --revision sha256:bf6a09d8e98c52294f205f7f8063fc7c6b11596cce3e70c9926ec9d9cc3cd5c7 --start 172 --end 178
```

chunks 的 strategy 必填；heading 禁止 size/overlap，window 支持两参数各自默认。compare 固定 baseline=heading、candidate=window；不接任意 query/gold。quote 四参数全必填；source_id 词法为 `[a-z][a-z0-9-]{0,63}`，仅固定 source ID 能返回原文；revision 必须完整 lowercase `sha256:<64hex>`；quote 区间必须非空且最多512代码点。没有路径、命令、URL、stdout destination 或正文参数。

整数词法 `0|[1-9][0-9]{0,9}` 且数值 <=2147483647；拒绝负号、加号、前导零、空白、末尾换行、小数、指数。范围不对不自动 clamp。允许 `--flag value` 和 `--flag=value`；重复/缩写/未知/缺参全部 invalid_input，不采用 argparse 默认末值覆盖或 allow_abbrev。`--help` 是唯一人类可读帮助出口，仅接受完整 argv 为 `["--help"]` 或 `["chunks"|"compare"|"quote", "--help"]` 两种形态，退出0、程序名固定 app.py；夹带其他参数、重复help、`-h`与未知子命令都为 invalid_input，不能让help掩盖非法输入。正常业务命令/失败均只在 stdout 输出一行 canonical JSON + LF，stderr 空。

成功公共字段：`contract_version,ok:true,command,corpus_id,corpus_version,corpus_revision,coordinate_unit,model_calls:0`。不含随机 UUID、时间戳、本机路径或 PID，相同固定配置跨进程应逐字相同。

- chunks：另含 splitter_version、strategy、configuration、sources（三份完整资产源对象）、chunks（按 source 顺序/start 的上述块）、indexed_chunk_count、indexed_codepoints。
- compare：另含 splitter_version、scorer_version、dataset_id、dataset_version、dataset_revision、configurations:{baseline:{strategy:'heading',configuration:{}},candidate:{strategy:'window',configuration:{size,overlap}},top_k}；cases 按固定顺序，每项 `{id,question,query,gold,baseline,candidate}`。每一侧 `{results,metrics,retrieved_codepoints}`，results 是完整块字段再加 score/matched_terms。summary={baseline,candidate}，每侧包含 `indexed_chunk_count,indexed_codepoints,retrieved_codepoints_total,positive_case_count,negative_case_count,evidence_recall_at_k,chunk_precision_at_k,first_evidence_reciprocal_rank,no_result_accuracy`；四聚合指标沿用每题同名字段，前三项按5个正例分别算术平均，最后一项按1个负例平均。每题 metrics 也固定上述四指标，非适用项为 null。notice 为固定“仅比较固定资料的词法检索，不代表模型回答质量或通用 RAG 质量。”。不预写结果或策略胜负，不加第二套笔记 schema。
- quote：另含 source_id、source_uri、source_revision、start、end、byte_start、byte_end、quote；上例必须 quote=`A😀éZ。`、byte范围[442,454)。它证明同版本原文回读，不单凭 quote 命令证明它曾被检索/读取或能支持某个回答；教学流程另核该范围被选中命中包含。

错误固定退出1：`{contract_version:'document-chunking-v1',ok:false,error:{code}}`，不得输出 argv、原始异常、文件名/路径或 traceback。

| code                 | 固定边界                                                                                               |
| -------------------- | ------------------------------------------------------------------------------------------------------ |
| invalid_input        | CLI 语法/枚举/数字词法、size/overlap/top_k非法、source/revision参数形状错误                            |
| source_not_found     | 形状合法但不在固定来源白名单的 source_id                                                               |
| revision_mismatch    | 来源存在但请求的完整 revision 非当前精确值                                                             |
| invalid_range        | 自然数范围 start>=end、end超过原文长度，或 quote>512                                                   |
| incompatible_version | 资产结构可读但 version不支持、完整 canonical pin与固定值不同（包括同步重算revision后的资料漂移）       |
| corpus_invalid       | 包内资产缺失/坏UTF8/非法或重复keyJSON、非有限数/孤立surrogate、字段结构/范围/原文切片/自述digest不一致 |
| experiment_failed    | 其他内部脱敏错误；不降级成空结果或自动修复                                                             |

检查优先级：CLI 词法/形状 → 两份固定资产结构和自述一致性（含gold quote/source/revision/range）→版本/完整canonical pin →具体命令 source_not_found→revision_mismatch→invalid_range。资产读取位置只相对 app.py 所在目录，不能依赖用户 cwd。加载资产上限各64KiB，递归深度32，UTF8 strict/拒重复keys/非有限数（含1e999）/孤立surrogate；业务字段严格且不允许bool充整数。错误合约针对实验边界，不声称能可靠送出进程被kill/OS stdout断开后的JSON。

## 6. 实现文件与独立验收

包内源码为 `app.py`（严格 CLI）、`loader.py`（资产校验与规范编码）、`chunking.py`（切分与回读）、`retrieval.py`（固定词法排名与评测），另有 `corpus.json`、`cases.json`、四个 `test_*.py`、`pyproject.toml`、`uv.lock`。开发依赖固定 pytest 9.1.1 / Ruff 0.15.22，业务代码不依赖它们。

- ZIP 必须在仓库外独立解包运行；不同 cwd / PYTHONHASHSEED 下，相同参数输出逐字一致。验证器不能 import 产品 parser、splitter、scorer 或 metrics 充当 oracle。
- 独立核对三份原文、版本 pin、六个金标与两套坐标；标题切分无洞无重叠，窗口覆盖到尾且没有空尾块。前言、空文、无标题、重复标题、围栏、CR/LF/CRLF/U+2028、emoji / combining character 用固定纯函数输入验证。
- chunk_id 绑定来源、版本、位置、策略与配置；同正文重复出现不覆盖。核对同分顺序、零分过滤、金标完整包含与成本；重复证据不增加召回，冲突一侧为1/2，负例不进入正例分母。
- quote 成功与旧 revision、未知 source、越界、空区间、非法整数、重复 flag 均需真实 CLI 证据。资产复制件的损坏、自述 hash 错误、非有限数、坏 Unicode、超深 JSON 必须拒绝；一致重算但违反固定 pin 的资料仍不兼容，不自动修复或回退为空结果。
- 无模型、外部网络、任意输入路径与业务文档/索引写入是验收边界；仅 `model_calls:0` 或 `ok:true` 不能代替行为验证。平台只供下载，学习者修改后的练习在独立环境运行。

格式、字符编码和分词分别受本契约约束。Python 的 [Unicode 文档](https://docs.python.org/3.12/howto/unicode.html)说明字符与编码的区别；[JSON 文档](https://docs.python.org/3.12/library/json.html)说明编码参数和解析扩展点，本实验另外拒绝重复字段、非有限数及孤立代理码点。语料 `references` 仅保留原教学来源背景，不宣称其中的网页被本实验读取。
