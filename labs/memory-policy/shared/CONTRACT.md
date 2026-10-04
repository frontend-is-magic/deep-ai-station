# 记忆资格与冲突实验合同

`memory-policy-v1`，课程 `agent-memory`，Python 3.12，仅标准库运行。输入是维护者公开合成资料，输出是结构化规则的结果，不是模型测量、生产认证或提示注入安全证明。无模型、网络、数据库、任意文件/URL/命令入口；不写源文件、不保存本次覆盖偏好。

## 包文件与入口

Python 八文件：`memory_policy.py`、`loader.py`、`policy.py`、`test_loader.py`、`test_policy.py`、`test_cli.py`、`pyproject.toml`、`uv.lock`。

shared 七文件：`memories.json`、`cases.json`、`README.md`、`CONTRACT.md`、`EVIDENCE.md`、`AGENTS.md`、`.gitignore`。另根 `manifest.json`，builder 补 `.prettierrc.json`，共 17 个 ZIP 成员。`manifest={id:"memory-policy",version:"memory-policy-v1",lessons:["agent-memory"],languages:["python"]}`。

运行资源从模块目录读取；两资产均不存在时才读 `../shared`。模块目录出现任何一个资产即选择整个模块目录，缺另一个不能回退。CLI 只允许：

```sh
uv run python -m memory_policy --list
uv run python -m memory_policy --case conflict
uv run python -m memory_policy --case conflict --language go
```

`--case` 与 `--language` 成对 flag 可以交换顺序；每 flag 仅一次；值不可省略。仅 `--list` 可单独列举；不提供 `--help`、`--explain`、`--flag=value` 或位置参数。未知/重复 flag、未知 case、非法 language、额外参数均 `invalid_input`。先检查 CLI 形状/语言，再完整加载所有资产，再查 case；形状合法但未知 case 遇资产损坏，优先资产错误。正常/业务无偏好/业务冲突 exit 0，一行 UTF-8 JSON + LF；失败 exit 1 固定 JSON；stdout 关闭安静 exit 1，无法保证收到 JSON。stderr 不输出输入或诊断。

## 固定资产

资产最终原文为同目录 `memories.json`、`cases.json`。case ID 固定如下，顺序输出按 ID 排序：

| case             | 未指定 language 的人工预期 | 关键事实                                                                                                     |
| ---------------- | -------------------------- | ------------------------------------------------------------------------------------------------------------ |
| normal           | resolved / python / memory | confirmed-python、project-python 同时 eligible                                                               |
| request-override | resolved / python / memory | 加 `--language go` 得 resolved / go / request，原 eligible 仍 python                                         |
| expiry           | no_memory / null / null    | expires-now 为 expired；future-go 为 future                                                                  |
| revoked          | no_memory / null / null    | revoked-go 为 revoked；unconsented-typescript 为 unconsented，即使 revoked=true                              |
| scope            | resolved / python / memory | confirmed-python eligible，knowledge-instruction knowledge_only；owner_mismatch=2，scope_mismatch=2          |
| conflict         | conflict / null / null     | confirmed-python 与 conflicting-go eligible；加 `--language typescript` 本次 resolved / typescript / request |

每个 JSON 文件最多 65,536 bytes，JSON 容器深度最多 16。拒绝 BOM、非法 UTF-8、孤立代理、重复 key、NaN/Infinity/1e999、非法 JSON、额外字段、非精确类型；保留 Python 3.12 整数字符串转换资源上限。文字长度使用 Unicode 码点。所有 string 必须可编码 UTF-8 且不含 U+0000..001F/U+007F；文本/title 1..500 码点且 strip 非空。ID/owner/scope/source 匹配 `[a-z][a-z0-9-]{0,63}` 完整串。

`memories.json` 精确字段：`version="memory-records-v1"`、`sources`、`memories`。

- sources：1..16，唯一 id，每项精确 `{id,title}`；title 1..100 码点。
- memories：1..64，唯一 id。preference 精确字段 `{id,owner,scope,kind:"preference",language,source,updated_at,expires_at,consent,revoked}`；knowledge 以 `text` 代替 `language`，kind 为 knowledge。language 仅 `python|go|typescript`；consent/revoked 严格 boolean；source 必须存在。其他 kind 拒绝。
- 日期严格 `YYYY-MM-DDTHH:mm:ss.SSSZ`，年份 1000..9999，真实日期往返一致；expires_at 可 null，否则不得早于 updated_at（相等合法）。

`cases.json` 精确字段 `version="memory-cases-v1"`、`cases`。cases：1..8，唯一 id；每项精确 `{id,title,owner,scope,as_of,memory_ids}`；memory_ids 为 1..64 唯一现有记忆 ID。title 1..100、日期/ID规则同上。整个资产必须通过验证，包括当前案例未引用的记录；不是只校验选中的案例。引擎不根据这些 case ID 硬编码结果。

版本为非空字符串但不是固定值归 `incompatible_version`；缺失/错类型/空值归 `assets_invalid`。缺文件、读取错误、超限、JSON/字段/关系错误均 `assets_invalid`。不输出文件路径、原始值或 traceback。

资产没有内容真实性签名或硬编码内容 pin。`assets_sha256` 是版本化报告指纹：先将 sources/memories/cases 各按 id 排序、每 case.memory_ids 字典排序，包成 `{memories:<完整资产>,cases:<完整资产>}`，用 `json.dumps(ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False)` 编码 UTF-8 后 SHA-256 小写 hex。原数组顺序/JSON空白/key顺序改变不影响指纹或报告；合法内容修改会改变指纹。它不是权限机制，不承诺资产防篡改。

## 通用决策与隐私输出

只处理所选 case.memory_ids。资格顺序严格 owner → scope → kind → consent → revoked → future → expired：

1. owner 不同只增加 owner_mismatch；不继续判断 scope。
2. owner 相同但 scope 不同只增加 scope_mismatch。
3. 前两者相同才可输出 record；knowledge 为 knowledge_only，不解析 text。
4. preference 中 consent=false 为 unconsented；再 revoked=true 为 revoked；再 updated_at > as_of 为 future；再 expires_at 非 null 且 <= as_of 为 expired；其余 eligible。

foreign 记录的 ID、language/text、source、source title、时间、状态不得出现在结果；只发布两个计数。可见 records 只含当前 owner/scope，含本条完整原始 memory 与 source_title，decision 精确枚举 `eligible|knowledge_only|unconsented|revoked|future|expired`。

候选语言是 eligible preference 的去重排序语言集合。0 个 no_memory；1 个 resolved/source=memory；多个 conflict。显式 `--language` 总得到 resolved/source=request，无论原候选数量；候选和排除解释仍保留。没有“最新记录自动胜出”或静默默认语言。

## 精确输出 schema

所有 object 禁止额外字段。每次成功有共同字段：

```text
contract_version: "memory-policy-v1"
lesson_id: "agent-memory"
model_calls: 0（整数）
read_only: true
assets_sha256: 64位小写hex
command: "list" | "resolve"
```

`list` 另且仅有 `cases:[{id,title}]`，按 id 排序，来自已完整验证资产。

`resolve` 另且仅有：

```text
case_id: string
as_of: canonical UTC ISO
context: {owner:string, scope:string, request_language: Language|null}
counts: {selected:int, visible:int, owner_mismatch:int, scope_mismatch:int, eligible:int}
records: [{memory: Memory（上述两种精确字段之一）, source_title:string, decision: Decision}]
resolution: {
  status: "resolved"|"no_memory"|"conflict",
  language: Language|null,
  source: "memory"|"request"|null,
  candidate_languages: Language[]
}
```

records 按 memory.id；candidate_languages 字典排序；所有 counts 非负精确整数，selected=visible+owner_mismatch+scope_mismatch，visible=records.length，eligible 为 eligible 数量。source_title 只取该可见 memory.source 的公开 title。resolved 的 language 非 null/source 非 null；其余 language/source 均 null。零候选只能 no_memory 或本次 request；多候选只能 conflict 或本次 request。内部输出校验再确认所有可见记录归当前 owner/scope、来源/计数/枚举/关系正确；失败不打印部分结果。

失败精确 `{contract_version:"memory-policy-v1",error:"invalid_input"|"assets_invalid"|"incompatible_version"|"experiment_failed"}`。不可预测内部异常/输出自检失败为 experiment_failed。错误不含 case/owner/path/message/raw bytes。CLI 不提供 failpoint。

## 验收与证据

原生 pytest 验证固定六例、显式请求、截止前/等于/后、撤回排序、知识不可提升、foreign 零内容回显、重新排序完全等价、无 mutation、全部资产加载失败、解析边界和真实子进程 CLI。独立 verifier 从本文与两资产写人工 oracle，不从 evaluator 生成预期；在 ZIP 中执行冻结安装/测试、真正 CLI、损坏副本、数组重排，逐命令前后比较文件字节，持有子进程并有界 wait/reap。

开发依赖仅 pytest==9.1.1、ruff==0.15.22；`uv sync --frozen`、`uv run pytest -q`、`uv run ruff check .`、`uv run ruff format --check .`。首次安装可能下载锁定开发依赖，实验运行不联网。不宣称固定资料策略等于模型记忆检索质量、语义冲突识别、生产授权、数据删除或长期持久化。
