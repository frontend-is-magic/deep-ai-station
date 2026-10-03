# 共享请求配额

平台通过 PostgreSQL 统一限制真实模型请求与沙箱创建尝试。相同数据库、相同 `AI_QUOTA_SCOPE` 的所有服务进程共用计数；更换访问码或重启应用不会获得新额度。教学演示、免费检索评测和静态代码检查不访问配额数据库。

## 准入语义

| 资源    | 固定分钟 | UTC 自然日 | 申请位置                                           |
| ------- | -------- | ---------- | -------------------------------------------------- |
| model   | 10 次    | 100 次     | 单次回答调用前；Agent 每轮模型请求前               |
| sandbox | 2 次     | 20 次      | 通过配置、授权、输入和本地并发槽检查后，创建沙箱前 |

这两类资源互不扣除。一次准入同时检查两个窗口，任一额度耗尽则两者都不增加。窗口按数据库 UTC 时间对齐，边界附近可能连续消耗前后两个窗口的额度；它不是滑动窗口。数据库时钟倒退时保留计数，不重置到过去。

准入提交完成才允许调用供应商。提交后的创建失败、上游错误、用户停止或超时均不返还次数；因此计数表示获准尝试，可能大于实际抵达供应商的请求数。配额计数不表示 token 或金额，也不承诺全局并发上限。模型每轮另有[用量账本](model-usage.md)，保留已知计数和未知状态。供应商费用上限仍在其控制台设置。

HTTP 准入失败返回 `429` 与秒数 `Retry-After`；Agent 的 SSE 已开始时发送 `error.code=429` 和 `retry_after`。配置无效、数据库不可达、迁移未执行、策略冲突或提交结果不明返回固定 `503`，不调用供应商、不自动重试、不回退到内存。拒绝响应不包含连接串或数据库异常。

## 部署与迁移

服务端配置字段见 [空值模板](../.env.example)，程序不会自动加载 `.env`。

| 字段                    | 规则                                                                             |
| ----------------------- | -------------------------------------------------------------------------------- |
| `AI_QUOTA_MODE`         | 缺省 `postgres`；仅独立本地调试可显式 `memory`                                   |
| `AI_QUOTA_DATABASE_URL` | PostgreSQL 托管连接串；只存在服务端 secrets，禁止 `VITE_` 前缀                   |
| `AI_QUOTA_SCOPE`        | 默认 `deep-ai-station`；1–64 个 ASCII 字母、数字、下划线或连字符，首位字母或数字 |

1. 在已授权的托管 PostgreSQL 创建数据库与角色，并按服务商要求配置 TLS。Preview 和 Production 使用不同数据库或不同稳定 scope，避免误共用额度。
2. 将连接串安全注入维护进程环境，执行 `uv sync --locked`，随后执行 `uv run python scripts/setup_quota.py`。脚本在同一事务按顺序应用固定 `001_request_quota.sql` 与 `002_model_usage.sql`，升级保留已有配额计数；不打印连接串，失败只返回固定消息。初始化脚本须有建表权限，应用运行角色需要两表的 `SELECT / INSERT / UPDATE` 权限，无需 DDL 权限。
3. 将相同服务器配置加入目标 Vercel 环境，并保留现有 `PLAYGROUND_ACCESS_TOKEN` 与所需的 `DEEPSEEK_API_KEY` / `E2B_API_KEY`。数据库连接串、认证文件和日志保持 Git 忽略；不得复制到聊天或文档。
4. 验证实际数据库准入与失败行为后再启用真实供应商验收。`/api/capabilities` 只检查配置是否完整，不测试数据库连通性，不能充当数据库健康报告。

`VERCEL=1` 或 `APP_ENV=production` 禁止 `memory`。本地显式内存模式仅用于单进程调试，重启会清空，也不能验证跨实例保护。生产模式缺少数据库配置时免费功能仍可用，真实模型和沙箱能力显示禁用。

scope 是运维管理的稳定命名空间，不来自客户端、IP、访问码或模型 Key。不要通过轮换 scope、删除行或更换数据库绕过额度。固定 policy 同样保存在计数行；新进程的 policy 与现有行不一致时拒绝准入，修改上限需要明确的运维迁移，不会自动清空历史计数。

## 数据与事务

`public.ai_request_quota_v1` 每个 `(scope, resource)` 一行，仅保存分钟/日窗口、计数和固定 policy；不存 prompt、用户内容、模型 Key 或供应商 usage。运行时不创建或升级表。

短事务先以 `INSERT ... ON CONFLICT DO NOTHING` 初始化，再以 `SELECT ... FOR UPDATE` 锁定该行，获得锁后读取 `clock_timestamp()`，校验 policy 并原子更新两种计数。所有 SQL 使用参数绑定和固定表名。模型请求在该事务内另插入唯一获准行，任一步失败均回滚，确认提交后才发起供应商请求；沙箱仍只计请求配额。数据库事务不会等待模型或沙箱运行。

连接超时 3 秒、锁等待 1 秒、语句 2 秒，应用准入截止时间 5 秒；取消会关闭连接并回滚未提交事务。连接时固定覆盖 libpq `options`，托管连接串不能依赖该参数做路由；实际供应商配置须验证连通。网络故障可能让提交结果无法确认，服务保守拒绝该次上游请求且不重试，已提交的计数可能仍然存在。

驱动固定为 `psycopg[binary]==3.3.3`。专用一次性连接在取消时直接关闭，避免驱动的回滚/取消诊断泄露原始异常；该实现覆盖 `_try_cancel` 内部钩子，升级驱动时必须重跑真实 `AsyncConnection.wait`、截止时间和 PostgreSQL 取消回归，不能仅更新版本号。

## 验证边界

单元与 mock 测试覆盖两个窗口、UTC 日边界、时钟回退、独立资源、配置失败、逐轮准入、取消及无效请求不扣除。`tests/test_quota_postgres.py` 需要专用的临时测试数据库 `TEST_QUOTA_DATABASE_URL`，缺失时明确 skip；有值时连接或 SQL 失败会失败，不跳过。测试初始化固定表并只清理自身随机 scope，禁止指向生产数据库。

CI 的独立 `shared-quota` job 使用 PostgreSQL 17 临时服务及公开测试密码，运行真实竞争、重启后计数、事务回滚、锁超时和策略冲突测试；其他测试使用显式本地内存模式或 mock。实际执行回执见 [验证记录](verification.md)。本地无 PostgreSQL 的 skip 不代表通过；真实生产数据库、DeepSeek、E2B 和原生 Browser 验收各自记录。

课程 Agent 与单次回答通过 [流式用量快照](stream-usage.md) 保留已收到的计数；取消可能错过尚未发送的帧，当前页面快照不持久保存。服务端已由[模型请求账本](model-usage.md)独立保存逐轮已知计数；全局并发限制仍待实现。

参考：[PostgreSQL INSERT / ON CONFLICT](https://www.postgresql.org/docs/current/sql-insert.html)、[Psycopg async connections](https://www.psycopg.org/psycopg3/docs/advanced/async.html)。
