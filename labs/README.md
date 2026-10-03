# 可运行课程实验

课程页按当前所选语言下载独立项目，原有参考片段练习包继续保留。五个全栈实验各提供 Python、TypeScript、Go 下载，均包含源码、入口、成功/失败测试、冻结依赖、README、契约、证据模板与 .gitignore。

| 实验                       | 对应课时                   | 核心行为                                                    | 说明                                                                                              |
| -------------------------- | -------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| `api-contract-v1`          | 路由与依赖注入、输入校验   | FastAPI / Hono / Gin，共同 HTTP 请求与响应、注入 Repository | [运行](api-contract/shared/README.md) · [契约](api-contract/shared/CONTRACT.md)                   |
| `sqlite-storage-v1`        | SQL 与数据建模、迁移与事务 | 真实 SQLite、幂等写入、owner 分页、迁移与原子回滚           | [运行](sqlite-storage/shared/README.md) · [契约](sqlite-storage/shared/CONTRACT.md)               |
| `session-authorization-v1` | 认证、应用安全             | 有效会话、owner隔离、撤销/过期、Cookie CSRF、正文后重检     | [运行](session-authorization/shared/README.md) · [契约](session-authorization/shared/CONTRACT.md) |

`text-upload-v1` 对应文档问答课，使用假Bearer、UTF-8/文件名白名单、私有内存对象、原子配额与逐字节附件下载。[运行](text-upload/shared/README.md) · [契约](text-upload/shared/CONTRACT.md)。此实验不接入RAG、不解析内容、不落盘；它只验证上传边界，不代表生产存储已经验收。

`sse-stream-v1` 对应流式聊天与取消、异步并发两课，共享 React 客户端连接 FastAPI / Hono / Gin。包含分块UTF-8、唯一运行ID、递增序号、错误/总超时、真实HTTP断连及A/B隔离；无模型调用。[运行](sse-stream/shared/README.md) · [契约](sse-stream/shared/CONTRACT.md)。

`agent-write-safety-v1` 对应 Agent 工具安全课，提供一个 Python / FastAPI / SQLite 包与 React 客户端：完整意图批准、原 requester 执行、原子发布回执、并发幂等及提交后结果不明的查询恢复。[运行](agent-write-safety/shared/README.md) · [契约](agent-write-safety/shared/CONTRACT.md)。只有本地教学写入，没有外部发布或模型调用。

每个实验的 `shared/` 放契约、案例与说明，语言子目录放完整实现，manifest 记录版本/课时/语言。SQLite 的共享 SQL 在 `shared/migrations/`。`public/labs/` 为固定白名单生成的确定性 ZIP，不包含依赖缓存、数据文件或秘密配置。

```sh
python3 scripts/build_course_labs.py
python3 scripts/build_course_labs.py --check
python3 scripts/verify_course_labs.py
python3 scripts/verify_sqlite_labs.py
python3 scripts/verify_session_labs.py
python3 scripts/verify_upload_labs.py
python3 scripts/verify_stream_labs.py --browser
python3 scripts/verify_agent_write_lab.py
python3 scripts/verify_agent_write_lab.py --browser
# 三语言实验的验证脚本可选择一种语言
python3 scripts/verify_sqlite_labs.py --language go
```

维护环境需 Python 3.12 / uv、Node.js 24 / pnpm 10.32.1、Go 1.27.1。验证脚本在仓库之外解包并冻结安装、检查格式/类型、运行原生测试；API 实验再启动临时本地服务检查37次HTTP请求，结束后释放自有监听；SQLite 实验再逐命令启动独立进程完成6组78次CLI调用与真实SQL故障断言，连接关闭后清理临时数据库。认证实验另在真实HTTP保留重复头执行84个共同案例，并在新进程验证3个会话重置请求；Cookie属性由服务端校验，不冒充HTTPS浏览器行为验收。子进程不接收模型密钥。文本上传实验另运行78个HTTP案例与3个新进程清空检查，Go带race，下载比较原始字节；CI 按三种语言执行相同检查，headless 页面检查九课27个课程/语言下载组合（15种不同实验包）、原资料包、学习记录保留及375px显示。

这些维护脚本只运行固定教学代码。学习者修改应在独立练习环境运行，平台 API 不执行修改后的输入。SQLite CLI 的身份字段不是登录验证，不可直接信任来自客户端的 owner。实验无需模型凭据、不会调用模型；首次依赖安装需要网络。通过固定案例不等于新增业务已验收，应记录自己的实际结果。

SSE下载包另运行三语言真实HTTP生命周期测试与12个共同HTTP案例；共享客户端31项协议测试覆盖UTF-8单字节分块、换行形式、提前EOF和终态校验。浏览器连接每个实际后端检查完成/故障/超时、停止A而B完成、重跑和375px布局；独立控制的提前EOF及忽略AbortSignal旧响应用于检查客户端故障隔离。每个服务及浏览器在 finally 关闭并确认端口释放，包括验证失败时。
