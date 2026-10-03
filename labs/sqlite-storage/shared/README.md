# SQLite 数据实验

对应“SQL 与数据建模”和“迁移与事务”两课。三个语言包共用 [契约](CONTRACT.md)、迁移 SQL 与78次 CLI 验证，使用真实本地 SQLite。没有 HTTP 服务、登录、模型调用或云账号，适合先读代码再修改独立练习副本。

## 运行所下载的语言包

所有命令在解包根目录执行；先安装冻结依赖并验证，然后选择该语言入口。数据库 `learning.sqlite` 及旁文件已忽略，不要提交。第一次安装依赖需要网络，运行命令不会访问模型或其他远端服务。

| 语言       | 环境                      | 安装和检查                                                                                                               | CLI 入口                                        |
| ---------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------- |
| Python     | Python 3.12 + uv          | `uv sync --locked`；`uv run --frozen ruff check .`；`uv run --frozen ruff format --check .`；`uv run --frozen pytest -q` | `uv run --frozen python app.py learning.sqlite` |
| TypeScript | Node.js 24 + pnpm 10.32.1 | `pnpm install --frozen-lockfile`；`pnpm check`                                                                           | `node dist/main.js learning.sqlite`             |
| Go         | Go 1.27.1                 | `go test -mod=readonly ./...`；`go build -mod=readonly -o storage-lab .`                                                 | `./storage-lab learning.sqlite`                 |

TypeScript 使用 Node 内置 `node:sqlite`；Node 24.14.1 会输出 experimental warning，位于 stderr，不影响 stdout JSON。Python 使用标准库 sqlite3；Go 使用固定版本的纯 Go SQLite 驱动，无需 cgo。锁文件冻结依赖，不表示系统自带 SQLite 版本相同，因此迁移仅使用三种已验证运行时共同支持的 SQL。

将下列输入用管道传给所选入口。以 Python 为例：

```sh
printf '%s\n' '{"op":"migrate"}' | uv run --frozen python app.py learning.sqlite
printf '%s\n' '{"op":"set_progress","owner_id":"alice","lesson_id":"tools","completed":true,"at":"2026-10-03T12:00:00.000Z"}' | uv run --frozen python app.py learning.sqlite
printf '%s\n' '{"op":"list_progress","owner_id":"alice","cursor":0,"limit":1}' | uv run --frozen python app.py learning.sqlite
printf '%s\n' '{"op":"list_progress","owner_id":"bob","cursor":0,"limit":1}' | uv run --frozen python app.py learning.sqlite
```

migrate 返回 schema_version=2；Alice 可读到自己的行，Bob 返回空列表。逐命令启动的新进程仍能读到已提交结果。真实服务必须从服务端已验证会话获得 owner，不能信任前端传入的 owner；本 CLI 切换身份仅用于观察仓储过滤。

## 数据建模课：唯一约束与用户分页

1. 找到 `UNIQUE(owner_id, lesson_id)`、completed 的 CHECK 与审计外键，运行测试确认数据库拒绝不一致数据。
2. 重复相同的 set_progress，确认 changed=false、时间和 audit 条数不变；改 completed 后应新增一次审计。
3. 给 Alice 与 Bob 分别写入多课进度，用 limit=1 和 next_cursor 翻页。观察 id 间隔及 SQL 内 owner 条件，确认 progress 和 audit 都不越界。
4. 在练习副本暂时移除 owner 条件，运行隔离测试观察失败，然后恢复。不要把破坏后的副本接到公开服务。

## 迁移课：升级与真实回滚

1. 原生测试创建 v1 数据库，写入旧数据，再运行迁移；核对 id、created_at、审计记录保留，updated_at 回填正确。
2. 重复迁移不应重写数据；把版本设为未来值时应拒绝且保持原库。
3. 检查审计写入失败的测试：它在真实 SQLite 中触发故障，确认新进度/状态变更都回滚。
4. 检查回填失败时列和版本一起回滚，移除故障后重新迁移成功。不要在生产数据库练习故障注入。
5. 对比写锁竞争与业务错误；事务不要包含模型请求，1000ms锁超时只是一项本地教学配置。

运行实际命令后填写 [EVIDENCE.md](EVIDENCE.md)，再把结果记入课程笔记及语言实践记录。下载、测试通过都不会自动完成课程。本实验未实现托管 PostgreSQL、ORM、生产备份/恢复、账号、云同步与分布式执行；这些仍需独立设计和验证。
