# SQLite 仓储契约 v1

服务层通常从已验证会话传入 owner。本实验用 CLI 显式传入教学身份，**没有登录认证**，不能直接接到公开 HTTP 路由。SQLite 文件属于独立练习环境；平台不读取它、不执行修改后的代码。本实验不调用模型、不需要凭据。

## 进程与输入

在解包根目录运行入口，唯一参数是数据库文件路径；stdin 为一个 UTF-8 JSON 对象，最多4096字节，可有尾部空白，不可追加第二个对象。所有字段必须完整且无额外字段。stdout 只输出一个 JSON，成功退出0，错误退出1；运行时警告只可能出现在 stderr。每次命令创建、关闭自己的数据库连接，没有常驻服务。

- `owner_id` / `lesson_id`：匹配 `^[a-z][a-z0-9-]{0,63}$`。
- `completed`：JSON boolean，不能用数字或字符串代替。
- `at`：可信调用方注入的教学时钟，严格有效的 `YYYY-MM-DDTHH:mm:ss.SSSZ`，年份1000–9999。原样保存；不处理时钟倒退或跨设备冲突。
- `cursor`：0至9007199254740991的整数；`limit`：1至50的整数。`1.0` 与 `1` 等价，boolean 不是整数。
- 数据库主文件以及 `-wal` / `-shm` / `-journal` 不得提交；只使用自己创建的练习数据库。

## 四个操作

| 输入                                                                                                            | 输出                                            |
| --------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- |
| `{"op":"migrate"}`                                                                                              | `{"schema_version":2}`                          |
| `{"op":"set_progress","owner_id":"alice","lesson_id":"tools","completed":true,"at":"2026-10-03T12:00:00.000Z"}` | `{"item":Progress,"changed":true或false}`       |
| `{"op":"list_progress","owner_id":"alice","cursor":0,"limit":20}`                                               | `{"items":[Progress],"next_cursor":整数或null}` |
| `{"op":"list_audit","owner_id":"alice","cursor":0,"limit":20}`                                                  | `{"items":[Audit],"next_cursor":整数或null}`    |

`Progress` 为 `id, owner_id, lesson_id, completed, created_at, updated_at`；`Audit` 为 `id, progress_id, completed, at`。整数主键由 SQLite 分配，跨用户不连续是正常现象，不代表能读取其他用户的行。

查询在 SQL 内限制 owner，按对应表的 `id ASC` 做 keyset 分页，使用 `id > cursor` 与 `limit + 1`。只有存在更多数据时，next_cursor 才是本页最后一条的 id，否则为 null。它是实时列表而非固定快照；分页间其他写入可能改变结果。

同一 `(owner_id, lesson_id)` 只能有一条 progress。首次写入创建 progress 与 audit；completed 变化时更新 progress、保留 created_at，并追加 audit。completed 未变的重复写入返回原 item 与 changed=false，忽略新的 at，不追加 audit。这只保证连续相同状态幂等，**不提供 request-id 重放去重或迟到事件排序**。

## 迁移与事务

v1 创建 progress、audit、组合唯一约束、布尔 CHECK、审计外键及索引。v2 为 progress 新增 `updated_at TEXT NOT NULL DEFAULT ''` 并回填 created_at。迁移 SQL 与 PRAGMA user_version 在同一个事务提交；失败同时回滚结构、数据和版本。只支持0/1→2及2→2，拒绝未知或未来版本；不做自动降级。只验证已有 v1 数据升级；未提供迁移后 v1 写入器与 v2 同时工作的兼容层，生产系统仍需分阶段发布和独立备份恢复方案。

每个连接启用 foreign_keys 与1000ms busy_timeout，写入及迁移采用 BEGIN IMMEDIATE。完成进度写入和审计前发生故障时整个事务回滚。普通操作要求 schema=2，不偷偷运行迁移。应用用户数据始终用绑定参数；只有固定迁移文件是 SQL。

```text
调用方提供 owner + 操作
          ↓ 严格校验
  写入：BEGIN IMMEDIATE → schema 检查
          ↓ owner 条件 / 绑定参数
  progress 写入 → audit 写入
          ↓ 全部成功 COMMIT
          └ 任一失败 ROLLBACK
```

## 错误

输出只包含 `{"error":"错误码"}`，不附数据库路径、SQL 或原始异常。

- `invalid_input`：格式、字节、字段或类型非法；校验在打开数据库之前。
- `unsupported_schema`：未迁移、旧版本普通操作或未知版本。
- `database_busy`：SQLite BUSY / LOCKED；本次没有成功写入，可稍后重试。
- `storage_failure`：其他存储错误或迁移失败。

`contract-cases.json` 的6组、78次 CLI 调用每次启动新进程，覆盖持久化、幂等、分页隔离、输入边界、v1迁移、未来版本拒绝，以及注入审计/回填失败后的回滚。SQL setup/assert 仅由维护者测试使用，不是 CLI 接口。各语言原生测试额外检查约束和连接锁竞争。

参考：[SQLite 事务](https://sqlite.org/lang_transaction.html)、[ALTER TABLE](https://www.sqlite.org/lang_altertable.html)、[PRAGMA](https://sqlite.org/pragma.html)、[Python sqlite3](https://docs.python.org/3.12/library/sqlite3.html)、[Node SQLite](https://nodejs.org/api/sqlite.html)、[Go database/sql 事务](https://go.dev/doc/database/execute-transactions)、[modernc SQLite 驱动](https://pkg.go.dev/modernc.org/sqlite)。
