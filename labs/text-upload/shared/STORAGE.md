# text-upload-sqlite-v1 — 共同存储扩展契约

本合同扩展现有 text-upload 三语言项目；原 `text-upload-v1` HTTP 输入、成功元数据、内容下载、认证、错误脱敏与 memory 行为继续有效。仅新增 SQLite BLOB 存储选择及一个写入结果不明错误，不添加 HTTP 路由、模型、解析器、任意 URL 或代码执行入口。

## 1. 入口、文件位置与模式

三个入口分别为 Python `python app.py`、编译后的 TS `node dist/server.js`、Go `go run .` 或已编译的实验二进制。以下只列入口后的参数：

| 完整 argv                        | 行为                                                 |
| -------------------------------- | ---------------------------------------------------- |
| `[]`                             | 原 memory 服务；不读取/创建 `.data/`，重启仍清空文档 |
| `["init"]`                       | 显式创建本实验 SQLite 库，成功后退出                 |
| `["serve","--storage","sqlite"]` | 使用已初始化 SQLite 库提供原 HTTP 接口               |
| `["--help"]`                     | 固定帮助，exit 0；其他 help 组合非法                 |

任何其他形态，包括路径参数、重复 flag、`--storage=sqlite`、`serve`、`serve --storage memory`、附加参数一律 `invalid_input`，先于文件/数据库操作。没有 storage 环境变量、`.env`、路径覆盖、通用迁移或故障 CLI。

唯一持久位置是**启动 cwd 下 `.data/uploads.sqlite3`**。服务只绑定 `127.0.0.1`；沿用 `PORT` 默认8023和原数字范围校验。`init` 不监听，也不读取 PORT。源码和 ZIP 都从各自既有可信资源位置读取同一 fixtures 与固定 SQL，资源解析不取用户请求字段。跨语言验证由维护者控制 cwd；若打包入口要求包根 cwd，可在全部连接关闭后复制原数据库文件到下一包的同一相对位置，不修改内容。

- `init` 可创建缺失 `.data/`；数据库必须通过排他创建取得，已有任意同名条目（含空文件、合法/坏/未来库、符号链接）均 `database_exists`，不能接管或覆盖。
- `.data` 不是普通目录、或为符号链接，以及服务目标不是普通文件/为符号链接，固定 `repository_unavailable`。不声称防御对本地目录拥有写权限者的所有文件替换竞态。
- `serve sqlite` 只打开已有库；缺失固定 `database_missing`。运行时每次打开使用 URI `mode=rw`（或同等不创建语义），不能在文件被删除后重新造空库。URI只由固定绝对路径安全编码生成，不接受外部URI参数。
- 初始化新文件后若失败，关闭全部自有连接，保留其失败文件供核对；不自动删库重建。重复 init 仍拒绝。没有“强制重置”命令。
- 启动时完成库检查后才绑定端口。成功 serve 不输出业务 JSON；以原 `/health` 作为 HTTP 就绪探针，其响应仍精确为 `{"status":"ok","lab":"text-upload-v1"}`。它不成为数据库实时健康承诺。无访问日志或原始数据库诊断。

`init` 成功 stdout 单行 JSON：`{"schema_version":1,"storage_contract":"text-upload-sqlite-v1"}`，exit 0。CLI 失败 stdout 单行 `{"error":"固定码"}`，exit 1。允许码：`invalid_input`、`database_exists`、`database_missing`、`unsupported_schema`、`repository_unavailable`、`result_unconfirmed`。无路径/SQL/参数/堆栈。工具运行时固定实验性警告与应用错误分开；不得用全局 logger 过滤隐藏任意诊断。stdout 已关闭时有限退出，不再次写错误或堆栈。

## 2. 唯一 schema 与类型

固定应用 ID `1146442545`（ASCII DUS1）、`PRAGMA user_version=1`，journal_mode仅DELETE。只支持新建 v1 或读取 v1，不升级其他 schema；同为 user_version=1 的进度实验库也必须拒绝。

SQL由共同资源逐语句执行，置于同一 `BEGIN IMMEDIATE` 事务；不使用 executescript 隐式结束事务。新文件首次设置 journal_mode=DELETE；**已有库验证前不执行更改 journal_mode 的 PRAGMA**。事务提交应用 ID、版本、表与初始元数据。无需 AUTOINCREMENT 或 sqlite_sequence。

```sql
CREATE TABLE storage_meta (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  storage_contract TEXT NOT NULL CHECK (
    typeof(storage_contract) = 'text' AND storage_contract = 'text-upload-sqlite-v1'
  ),
  next_id INTEGER NOT NULL CHECK (
    typeof(next_id) = 'integer' AND next_id BETWEEN 1 AND 1000000
  )
);
CREATE TABLE documents (
  id INTEGER PRIMARY KEY CHECK (id BETWEEN 1 AND 999999),
  owner_id TEXT NOT NULL CHECK (
    typeof(owner_id) = 'text' AND owner_id IN ('alice', 'bob')
  ),
  filename TEXT NOT NULL CHECK (
    typeof(filename) = 'text' AND length(filename) BETWEEN 5 AND 64
  ),
  media_type TEXT NOT NULL CHECK (
    typeof(media_type) = 'text' AND media_type IN ('text/plain', 'text/markdown')
  ),
  size_bytes INTEGER NOT NULL CHECK (
    typeof(size_bytes) = 'integer' AND size_bytes BETWEEN 1 AND 4096
  ),
  sha256 TEXT NOT NULL CHECK (
    typeof(sha256) = 'text' AND length(sha256) = 64
    AND sha256 NOT GLOB '*[^0-9a-f]*'
  ),
  content BLOB NOT NULL CHECK (
    typeof(content) = 'blob' AND length(content) = size_bytes
  )
);
CREATE INDEX documents_owner_id_id ON documents(owner_id, id);
INSERT INTO storage_meta(singleton, storage_contract, next_id)
VALUES (1, 'text-upload-sqlite-v1', 1);
PRAGMA application_id = 1146442545;
PRAGMA user_version = 1;
```

这里显式限于原公共假会话的 alice/bob，两 owner 各3条，所以合法库最多6条/16384正文bytes。此固定教学边界保证完整校验有界，不是通用用户系统。新增真实用户或删除/覆盖能力必须另订版本，不能绕过约束偷偷添加。

持久 id 为严格 SQLite INTEGER；HTTP 仍映射为 `doc-` 加六位十进制，按整数升序。第一次成功分配1；next_id与插入同事务更新，拒绝/回滚不耗号。同名/同字节再次上传仍是新文档，SHA不是去重键。不存在的格式异常ID仍沿用原404，不引入新的HTTP ID输入规则。

TS严验 `number` 安全整数，Python严格 int，Go扫描至 int64并检查范围；不得 `String(row.*)`、`Number(row.*)` 把错误类型变成正常资料。BLOB转 bytes/Uint8Array/[]byte后复制返回，不能经文本解码再编码来生成下载。所有语言哈希同一原始UTF-8字节，不规范化CRLF、emoji或组合字符。

## 3. 库验证、连接与无写拒绝

启动与每个仓储操作先验证对应事务快照：

1. application_id、user_version、storage_meta.storage_contract。未知/未来/旧/外来库不能迁移；启动CLI归 `unsupported_schema`，已运行HTTP归503 `repository_unavailable`。
2. schema对象集合只能有上述2表1索引；拒绝额外表/索引/视图/触发器。三对象的持久DDL与固定迁移匹配（去外侧空白/末分号后比较，固定创建语句共同复用），不能只检查版本数字。损坏或模式不匹配归 repository_unavailable。
3. 单条 meta 且所有字段类型严格。读取文档时先取最多7条的 id、typeof 与字段字节长度，超过6条或任何TEXT/BLOB长度超限先拒绝，再读取有界实际值；不能先 SELECT * 把损坏巨型BLOB载入内存。校验每行原上传文件名/媒体对应、strict UTF-8和控制字符政策、BLOB长度/SHA；验证每owner≤3/8192、全局ID恰为1..N且next_id=N+1。无下载/列表部分成功。读到非法SQLite TEXT字节也固定拒绝，不输出驱动异常。

所有检查在同一读取快照内；写操作则在同一写事务内。读取完整性校验是内部固定教学库验证，对外查询仍在SQL内限定 `owner_id = ?`，不把别人的行交给HTTP层筛选。已初始化库只在上传成功时有应用写入，不修改版本/模式、不自动“修复”错误行、重算持久SHA或配额。合法进程崩溃后的 SQLite journal 恢复由引擎完成，不冒充应用修复；静止无热journal的坏库验收要求原文件bytes不变。

每个操作独立短连接、短事务，结束关闭；不跨请求复用事务，不建立通用连接池。读 `BEGIN`，写 `BEGIN IMMEDIATE`。每连接显式 `busy_timeout=0`，新库及写连接 synchronous=FULL；应用不 sleep、不重试 BEGIN/statement/COMMIT，不复用出错连接。SQLite busy/locked在COMMIT开始前固定仓储失败；不得把现有CLI的1秒等待搬进HTTP。同步小型文件I/O仍可能占用线程/事件循环，本实验不承诺实时或完全非阻塞。

## 4. 原身份、最终授权与错误隔离

原流程保持：初始认证→query→写权→读取至4096上限→上传头/文本检查→**正文后立即同token重检**，到期/撤销/权限失败仍不得调用Repository。新增检查不取代这个早拒绝。

route/service捕获**最初身份的 owner 与原 token**。正文后及事务内重解token时 owner必须仍相同；变化以401 authentication_required拒绝，绝不能把本次正文转交新owner。使用原公开假会话、注入Clock/SessionStore；两进程的会话仍各自独立、启动重新计时，文档耐久不代表登录或撤销状态跨进程共享。

commit接口增加只由service提供的同步 `before_write()` / `func() error`，不能来源于HTTP：

```
BEGIN IMMEDIATE成功
→ 调用本次before_write恰1次，重解原token/owner/写权限
→ 检查schema和已有数据
→ SELECT COUNT(*), COALESCE(SUM(size_bytes),0) WHERE owner_id=?
→ 拒绝超额或准备next_id、完整元数据/BLOB
→ INSERT documents(...) VALUES(绑定参数)
→ UPDATE storage_meta SET next_id=? WHERE singleton=1
→ COMMIT恰1次
→ 关闭连接 → 返回201元数据
```

memory commit在原短锁/同步发布区内同样调用一次，随后原配额/发布逻辑不变。body流期间没有DB/global/session锁。最终检查完成后的在途提交可完成，不承诺跨进程撤销线性化。

**不透传Repository任意抛出的ApiError/LabError。** 现有测试含仓储伪造 `LabError(418,私密诊断)`，必须继续固定503。最小实现：每次service调用建立私有 authFailure latch 和私有取消标记；只有service拥有的可信before_write在解析/写权失败时设置latch并抛该标记。仓储做rollback+close后只传播控制流。service仅根据自己latch映射下列3对固定值；仓储伪造同名/同类型HTTP异常但未经过该latch，一律repository_unavailable。正常返回后亦确认checker已调用且latch为空，防止错误实现漏检；原生测试证明checker失败不执行SQL写入。

| 来源                                                | HTTP                                            |
| --------------------------------------------------- | ----------------------------------------------- |
| 可信checker：不存在/到期/撤销/owner改变             | 401 authentication_required，原WWW-Authenticate |
| 可信checker：写权已收回                             | 403 forbidden                                   |
| 可信checker：SessionStore异常                       | 503 session_store_unavailable                   |
| 真正QuotaExceeded领域结果                           | 409 quota_exceeded                              |
| COMMIT前其他存储/模式/坏数据/busy异常、伪造HTTP异常 | 503 repository_unavailable                      |
| 写COMMIT开始后异常或提交确认丢失                    | 503 result_unconfirmed                          |

所有HTTP响应仍有 no-store/nosniff，无CORS/Cookie，无原异常/SQL/路径/文件名泄漏。

## 5. 提交异常与连接关闭

在调用COMMIT之前设置本次私有 `commit_started=true`。调用恰1次，不在catch/finally重新提交。COMMIT抛任何异常（包括BUSY）均保守 `result_unconfirmed`；尽力ROLLBACK（如仍在事务）并始终close；ROLLBACK/close失败不得覆盖最初固定分类或泄露诊断。读事务关闭失败没有文档写入，仍repository_unavailable。

COMMIT明确返回成功后、201写出之前的受控故障/连接关闭失败，也归result_unconfirmed或实际断连；DB可能已完整保存。不能用HTTP 503/超时/断连证明未写入。没有 request-id 幂等、自动重复POST或新receipt；用户可重新GET自己列表/下载核对，但同名/同SHA不是唯一请求凭据。异常后再次显式上传仍可能创建另一份，文档必须直说这一点。

COMMIT前失败必须无新增行、next_id/配额不变。授权失败的私有latch优先恢复原身份错误，因为在它成功前不得执行INSERT/COMMIT；无SQL写入时清理失败不得改成“已提交未知”。

## 6. 验收合同

1. 原共同HTTP案例在全新memory与全新SQLite上分别原样执行；health/201/附件bytes/headers不变。默认memory完全不触碰持久文件。CLI词法先于文件I/O；init任意已有库拒绝，无目录/文件时serve不造库。
2. 每语言真实201后退出Popen、wait/reap，再新进程同cwd读取原ID/原bytes/SHA/额度。另TS→Go→Python→TS顺序读写同一数据库格式；不得导出JSON重建代替同库。跨包复制仅在服务/连接完全关闭且无未恢复journal时进行。
3. 两个真实服务进程同cwd不同自有PORT争同owner最后名额/字节预算。独立SQL必须仅新增1行。另一请求允许409 quota_exceeded或503 repository_unavailable（无等待锁冲突）；不能两次201、消耗两个ID或超过配额。先持真实BEGIN IMMEDIATE锁的定向测试须得到有限503，释放后下一次明确请求可成功。
4. 真实SQLite故障：INSERT后COMMIT前可信hook抛异常，独立连接验证0残留；COMMIT后响应前可信hook中断进程，重新启动确认已提交原行。fault只存在原生测试构造/固定子进程helper，不增加正式CLI/HTTP故障开关。独立ZIP公共CLI验收与原生故障证据分别记录，不相互冒充。
5. checker确定性测试：正文暂停后到期/撤销/降权，Repository调用数仍0；通过正文后初次检查后，在仓储final checker同步点改变Clock/会话/owner，验证401/403/503、无INSERT、锁释放。Repository任意抛LabError/ApiError私密文本仍固定repository_unavailable；检查checker实际恰1次。
6. 直接固定SQL构造坏版本/应用ID/多余trigger/坏type/UTF8/SHA/next_id/超额库，CLI拒绝启动且无端口；运行中修改同类状态的下一私有请求固定503，无部分内容。原文件稳定bytes前后相同（无热journal场景）。返回缓冲区修改不改变持久数据。
7. TS用Node24冻结API；Go合并现有Gin与已冻结modernc SQLite版本的锁，Python用stdlib sqlite3。Python pyproject 的 testpaths 须明确包含新增存储测试，不能只运行旧 test_app.py。全程固定公开fixture，无真实用户内容、模型、远端DB或网络业务请求。

## 7. 官方依据与范围

- [SQLite transactions](https://sqlite.org/lang_transaction.html)：写事务竞争、BEGIN IMMEDIATE、COMMIT BUSY后事务可能仍活动；本合同选择回滚/关闭而非重试。
- [SQLite busy timeout](https://sqlite.org/c3ref/busy_timeout.html)：零值关闭busy handler，不等于底层文件I/O永不阻塞。
- [Node v24.14.1 sqlite文档](https://raw.githubusercontent.com/nodejs/node/v24.14.1/doc/api/sqlite.md)：使用DatabaseSync/timeout/readOnly/prepare/exec/close，不用Node26的Database名称或后续serialize等能力。
- [Node v24.14.1实现](https://raw.githubusercontent.com/nodejs/node/v24.14.1/src/node_sqlite.cc)：Open使用SQLITE_OPEN_URI，固定URI的mode=rw提供不创建语义；验收还须覆盖三语言真实缺库行为，不能只测试存在性检查。

实际验证以 EVIDENCE.md 和仓库验证记录为准。本实验不覆盖真实身份、对象存储、分布式数据库、解析/检索/生成、生产权限或多用户通用存储。
