# SSE 流式教学契约 v1

实验名称 `sse-stream`，关联 `fullstack-ai-stream` 和 `fullstack-async`。三个后端共享本文件及 fixtures.json；React 客户端通过 Vite 的同域 `/api` 代理访问后端（去掉 `/api` 前缀）。只绑定回环地址，不调用模型、访问外网、接收任意代码或持久化。

## HTTP

- `GET /health` → 200 JSON `{"ok":true,"lab":"sse-stream"}`。
- `GET /stream?scenario=success|error|timeout|hold`。必须恰好一个 scenario，不能有其他 query key、重复值、空值或未知场景；拒绝时 400 JSON `{"error":{"code":"invalid_request","message":"仅支持一个有效的 scenario 参数"}}`，不得启动 Producer。
- 有效流 200，Content-Type 主类型 `text/event-stream`（可带 utf-8），`Cache-Control: no-store`、`X-Content-Type-Options: nosniff`、`X-Accel-Buffering: no`。不设置 CORS 或 Cookie。
- 每次有效请求服务端生成独立小写 UUID v4 `run_id`。事件为 UTF-8 `event: NAME\ndata: JSON\n\n`，不得假定 HTTP 网络 chunk 等于事件；客户端须兼容 LF/CRLF/CR、注释和分块 UTF-8。应用只生成 LF。

## 事件顺序

1. 恰好一个 `start`：`{"run_id":"UUID","scenario":"实际请求场景"}`。
2. `delta`：`{"run_id":"UUID","seq":1,"text":"理解 "}`，成功时随后 seq 2 `流式 `、seq 3 `响应 🌱`，文本来自 fixtures.json，不能改变空格。
3. 成功时唯一终态 `done`：`{"run_id":"UUID","seq":3}`；随后关闭响应。不存在模型用量，不填假 usage。
4. `error` 场景只产生第一条 delta，然后唯一 `error`：`{"run_id":"UUID","code":"producer_failed","message":"教学数据源失败"}`，随后关闭，无 done；不可泄露原始异常。
5. `timeout` 和 `hold` 场景在第一条 delta 后阻塞，直至客户端断连或同一个总 deadline（fixtures 默认 3000ms）到期；超时唯一 `error`：`{"run_id":"UUID","code":"deadline_exceeded","message":"教学运行超时"}`，随后关闭，无 done。
6. 成功片段之间默认等待100ms，便于看见增量；deadline 覆盖本次流的全部生产等待，不能每帧重置。测试使用可注入 Producer/时间或同步信号，避免靠长 sleep 猜测。

start、delta、done/error 的 run_id 必须一致；delta seq 从1严格递增。断连可能与完成或超时竞争；确认断连后不得继续生产或尝试补发终态。取消由服务端请求的生命周期传入 Producer；每个请求自己的 Producer 在成功、错误、超时及断连时恰好清理一次，不能影响另一个请求。

## 客户端与验收

共享客户端使用 Fetch + AbortController + 增量 TextDecoder。完整事件才解析 JSON，验证 start/seq/run_id/终态；缺少终态的 EOF 显示未完成。发生错误、超时、断流或停止时保留已收到文字，不标成功；新运行身份阻止旧响应覆盖新结果。事件大小和累计输出有上限，文本按 React 普通文本显示。

原生测试必须覆盖实际 HTTP 首帧到达后关闭连接，观察注入 Producer 的取消与清理信号；取消 A 时 B 能正常完成，清理恰好一次。客户端 abort 返回不能单独证明服务端已清理。完整共享 HTTP 验证检查成功/error/timeout的事件、HTTP校验和唯一run_id；headless 另验证共享 React 客户端连接三个实际后端。所有服务使用自有随机端口并在 finally 释放；不触碰现有主项目服务。

这里的 Producer 是固定教学数据源，不代表真实供应商流、代理缓冲、Vercel Python 断连或生产负载已经验收。
