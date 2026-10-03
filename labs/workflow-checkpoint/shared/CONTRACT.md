# 持久检查点契约 v1

实验 `workflow-checkpoint-v1`，课时 `agent-state-machine`，Python 3.12 + 标准库 SQLite。固定节点 `retrieve → draft → validate`，固定 `.data/checkpoints.sqlite3`（相对当前工作目录）。无模型、HTTP、外部网址访问、用户代码或外部副作用；只接受固定案例和操作参数。

## 命令与阶段

```text
app.py start --run-id UUIDv4 --case normal|empty|conflict
app.py inspect --run-id UUIDv4
app.py step --run-id UUIDv4 --expected-revision 0|1|2|3
app.py resume --run-id UUIDv4 --expected-revision 0|1|2|3
```

run ID 必须为规范小写 UUIDv4。拒绝未知、重复、缺失或多余 CLI 参数，不回显原始输入；不接受 stdin 命令、数据库路径、任意命令或自由查询文本。只有 start 可以创建目录、数据库和 schema；同 ID 再 start 返回 `run_exists`。inspect / step / resume 不自动初始化，缺文件或 run 为 `not_found`。已有非空或未知数据库不覆盖；不自动迁移、降级、repair、reset 或重试。

| revision | phase     | 下一节点 | outcome                                                 |
| -------- | --------- | -------- | ------------------------------------------------------- |
| 0        | ready     | retrieve | null                                                    |
| 1        | retrieved | draft    | null                                                    |
| 2        | drafted   | validate | null                                                    |
| 3        | completed | 无       | complete / insufficient_evidence / conflicting_evidence |

step 恰好提交一个下一节点。resume 最多调用三次相同的 guarded step，每次只使用本次刚确认提交的 revision 作为下一次 expected revision。任意竞争导致 `revision_conflict` 并停止，不能悄悄追随其他工作进程继续执行。终态且 revision 匹配时回读原结果，`performed=[]`；终态使用旧 revision 仍冲突。本轮没有可编辑草稿或自动纠正引用失败的状态机。

## 固定输入、节点和引用

`fixtures.json` 的 documents 是毕业骨架 `starters/agent/documents.json` 的五份公开资料快照，顺序为 api、tools、evidence、billing-a、billing-b；cases 为 normal / empty / conflict。语料版本 `agent-corpus-v1`；`corpus_sha256` 为仅 documents 的 canonical JSON（UTF-8、ensure_ascii=false、sort_keys=true、紧凑分隔符）的 SHA-256，源码固定预期摘要为 `3d22e8986d9c33832b2d4604dc27d431d8caaa21b5344f79f40065a0301f48b1`。固定 URL 不触发网络访问，冲突两份资料都是明确标识的合成练习。

retrieve 输出恰为 `{query, found_ids, read_documents}`。检索将 query 修剪后 casefold，以每份资料 casefold 后的 keyword 是否为 query 子串匹配，保留资料顺序；实际读取只来自命中 ID。每个 read_documents 项恰为 `{id,title,body,url,kind,conflict_group}`，缺失 conflict_group 规范为 null。

draft 输出恰为 `{answer,citations}`，每条 citation 恰为 `{source_id,quote,url}`：

| case     | query / 已读 ID                          | answer / 引用                                                                                                         |
| -------- | ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| normal   | `API 超时` / `[api]`                     | `API 调用需要明确输入、错误类别和超时预算。`；quote 为 `API 明确输入长度、错误类别和超时预算。`，URL 为该固定资料 URL |
| empty    | `zzzz unmatched` / `[]`                  | `固定资料中没有匹配证据。`；citations=[]                                                                              |
| conflict | `取消计费冲突` / `[billing-a,billing-b]` | `合成资料对取消计费存在冲突，不能据此给出一致结论。`；按 A、B 顺序引用各自完整正文，两个 URL 均为 null                |

validate 输出恰为 `{outcome,answer,citations,read_only:true,model_calls:0}`；三个案例 outcome 分别为 complete、insufficient_evidence、conflicting_evidence。引用 source_id 必须存在于本次已保存的实际读取记录；只有 found_ids 不足。quote 必须非空且是该正文连续子串，ID 唯一，URL 精确来自资料，命中冲突组必须保留两侧。引用存在性和固定输出校验不能证明任意摘要的语义真实性。

## stdout 与退出

成功对象恰为：

```text
{
  contract_version: "workflow-checkpoint-v1",
  ok: true,
  command: "start" | "inspect" | "step" | "resume",
  run: {
    run_id, case_id,
    workflow_version: "workflow-checkpoint-v1",
    corpus_revision: "agent-corpus-v1", corpus_sha256,
    revision, phase, outcome
  },
  checkpoints: [{revision,node,output,previous_hash,state_hash}],
  result: null | validate_output,
  performed: [node...],
  reused: [node...]
}
```

列表按节点 / revision 顺序排列；SHA-256 为64位小写十六进制。result 恰为保存的 validate 输出或 null。performed 只表示本次调用已确认 COMMIT 的节点，不表示节点计算绝不再次运行；回读、恢复仍会计算固定期望以验证保存内容。reused 是最终检查点中非本次提交的节点；inspect 没有 performed。提交去重不等于 CPU 零重算，更不等于外部副作用 exactly-once。

错误对象恰为：

```text
{contract_version:"workflow-checkpoint-v1",ok:false,error:{code},performed:[node...]}
```

| code                 | 含义                                                             |
| -------------------- | ---------------------------------------------------------------- |
| invalid_input        | CLI / 固定故障目标非法                                           |
| not_found            | 数据库或 run 不存在                                              |
| run_exists           | start 的 ID 已存在                                               |
| revision_conflict    | 保存的 revision 与明确期待不符                                   |
| database_busy        | SQLite 有界锁等待失败                                            |
| unsupported_schema   | 已有库 user_version 不是 1                                       |
| incompatible_version | 当前结构下 workflow / corpus 版本或摘要不符                      |
| checkpoint_invalid   | 物理数据库损坏、非法 JSON、结构 / 引用 / 哈希链 / 节点状态不一致 |
| storage_failure      | 提交前其他存储故障                                               |
| result_unconfirmed   | COMMIT 或提交后结果确认发生错误，不能推断未提交                  |

正常成功退出 0，固定错误退出 1。若 resume 在失败前已提交其他节点，error.performed 只含此前已确认的提交，不猜测最后一次提交状态。错误不回显原始输入、数据库路径、SQL、环境或异常。stdout 已被关闭时可能退出 1 且无法送达 JSON，stderr 也不输出原始 traceback；新进程 inspect 原 run 才能核对状态。

## 事务与故障

连接显式设置 foreign_keys=ON、busy_timeout=500ms、journal_mode=DELETE、synchronous=FULL，不使用 OFF / MEMORY。每个 step 事务使用独立连接，resume 可顺序开启多次；每个节点在短 `BEGIN IMMEDIATE` 事务内重读结构、版本、run 和检查点链，检查 expected revision，计算固定纯节点，然后 INSERT checkpoint、CAS UPDATE run 并 COMMIT。节点输出、revision 与链必须一起提交。事务不跨越交互、模型或外部工作。

step / resume 可加固定故障参数，两项必须同时提供：

```text
--fault before-commit|after-commit|pause-before-commit
--fault-node retrieve|draft|validate
```

先按给定 expected revision 检查故障目标：step 的 fault-node 必须就是下一节点，resume 可指定该 revision 之后尚待执行的任一节点，不能指向此前节点；目标合法后若数据库实际 revision 不匹配，仍返回 revision_conflict。

| fault               | 注入点                                        | 退出与可见证据                                                                         |
| ------------------- | --------------------------------------------- | -------------------------------------------------------------------------------------- |
| before-commit       | checkpoint INSERT 与 run UPDATE 后、COMMIT 前 | `os._exit(70)`，无成功 stdout；新进程检查旧 revision                                   |
| after-commit        | COMMIT 返回后、成功 stdout 前                 | `os._exit(71)`，无成功 stdout；新进程检查新 revision                                   |
| pause-before-commit | 同 before-commit 的持锁事务内                 | 固定事件后等待 2 秒，再 `os._exit(72)`；独立父测试可在事件后对自己的子进程发送 SIGKILL |

三种故障的 stderr 事件恰为 `{event:"fault_reached",fault,node,pid}`，不带输入或路径。事件只表示到达注入点；退出70/71/72本身都不是数据库提交证据。72是故障代码自退，不能声称真实 SIGKILL。真实 SIGKILL 验收必须记录父进程发送目标、自有子进程实际信号退出、重新打开数据库后的 revision / rows / 链，再恢复同一 run。

COMMIT 异常归为 result_unconfirmed，不自动重试；提交前故障回滚并关闭。检查未知结果应先 inspect，不能创建新 ID 绕过。两个真实 step 进程争同一 revision 只能有一个提交，另一个 revision_conflict；持锁超过500ms可单独观察 database_busy，不把锁等待错误当作同一成功。

## 保存格式与恢复校验

DB user_version=1；固定表：metadata(singleton,workflow_version,corpus_revision,corpus_sha256)、runs(run_id,case_id,workflow_version,corpus_revision,corpus_sha256,revision,phase,outcome,last_hash)、checkpoints(run_id,revision,node,output_json,previous_hash,state_hash)。checkpoint 主键为 run_id+revision，另有 run_id+node 唯一约束与 run 外键；不保存 running 租约或时间戳。准确 DDL 见包内 schema.sql。

state_hash 是 canonical JSON `{run_id,revision,node,workflow_version,corpus_revision,corpus_sha256,previous_hash,output}` 的 SHA-256。ready 的 last_hash=null，后续指向最后检查点。哈希用于发现意外不一致，不是针对本地数据库拥有者的真实性认证。

每条命令校验预期表 / 列 / 约束，拒绝额外 trigger / view；运行 SQLite integrity 与外键检查，校验版本、连续 revision、节点顺序 / phase、前后哈希、固定场景输出、实际已读 / 引用关系及终态。每份保存 JSON 最多16384 UTF-8字节，严格拒绝重复字段、NaN/Infinity 等非有限数（含指数溢出）、非法 Unicode、错误类型和额外字段。失败不由应用删除、重建或修复原状态。

非 start 使用 mode=rw 打开已有库，允许 SQLite 自身先恢复崩溃留下的 hot journal；这可能发生物理写入，不等于应用自动 repair。因此“损坏拒绝无写”指应用不改写坏记录，不能承诺 SQLite 启动恢复时文件每个字节不变。旧版、未来版、错误语料摘要均不静默迁移或降级。

## 验收与范围

原生测试及独立解包验收覆盖三场景、三个节点的提交前后真实退出、新进程 inspect / 独立 SQL、持锁 SIGKILL 后恢复、两个 OS 进程竞争、锁超时、完成后幂等回读、未知与损坏 / 版本拒绝、脱敏及故障目标限制。应按实际运行填写 EVIDENCE，不把该清单当作已完成结果。

仅清理自己创建的进程和练习目录。数据库、SQLite sidecars、配置、日志与报告加入 .gitignore。无生产认证、模型 / 费用账本、分布式流程框架、外部副作用恢复、断电或硬件耐久性保证。平台只提供独立下载和证据记录。

参考：[SQLite 事务](https://sqlite.org/lang_transaction.html)、[SQLite 原子提交与 hot journal](https://sqlite.org/atomiccommit.html)、[SQLite PRAGMA](https://sqlite.org/pragma.html)、[Python 3.12 sqlite3](https://docs.python.org/3.12/library/sqlite3.html)、[os._exit](https://docs.python.org/3.12/library/os.html#os._exit)。
