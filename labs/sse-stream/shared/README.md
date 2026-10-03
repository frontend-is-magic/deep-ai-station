# 让流式响应可观察、可取消

本包对应“流式聊天与取消”和“异步、并发与取消”。选择一个后端运行，相同 React 客户端观察逐帧文字、错误与超时；不需要密钥或模型费用。固定文本与场景见 fixtures.json，共同输入/事件见 CONTRACT.md。

需要 Python 3.12 / uv、Node.js 24 / pnpm 10.32.1，或 Go 1.27.1。后端按下载语言选择下面一组命令；前端始终需要 Node.js。首次安装依赖需要网络。

## 启动所选后端

在解压目录执行，监听均为 `127.0.0.1:8024`，不对外开放：

```sh
# Python / FastAPI
uv sync --locked
uv run --frozen pytest -q
uv run --frozen uvicorn app:app --host 127.0.0.1 --port 8024 --no-access-log
```

```sh
# TypeScript / Hono
pnpm install --frozen-lockfile
pnpm check
PORT=8024 pnpm start
```

```sh
# Go / Gin
go test -race -mod=readonly ./...
PORT=8024 go run -mod=readonly .
```

仓库内学习时先进入 `labs/sse-stream/<语言>`，共享资料位于 `../shared`；ZIP 已将共享资料放入根目录。

## 启动共享客户端

保持后端运行，在另一个终端进入解压目录的 `client/`（仓库中为 `labs/sse-stream/shared/client/`）：

```sh
pnpm install --frozen-lockfile
pnpm check
pnpm dev
```

打开 `http://127.0.0.1:5176`。Vite 将 `/api` 转到本地8024并去掉前缀；更换后端端口时用 `LAB_API_PORT=你的端口 pnpm dev`，只能指定本地数字端口。停止终端服务后检查监听已经释放。

先分别运行正常、故障、超时场景；再让 A 等待，B 正常运行，停止 A，观察 B 是否完成。重跑会取消当前连接，旧响应不能覆盖新结果。预设故障返回脱敏 error；失败、提前断流或停止时保留已有文字，只有合法 done 和完整关闭才标成功。客户端另有10秒总等待兜底，不会自动重连。

## 读懂机制

1. 后端验证请求后创建独立 run_id、Producer 与一个总 deadline，依序写 start/delta/终态。
2. Fixture Producer 模拟成功、首段后故障或阻塞；客户端断连与 deadline 都传入本次 Producer。
3. 客户端的增量 TextDecoder 跨网络字节块保留 UTF-8 状态，按空行重组完整 SSE 事件，再验证 run_id、序号和终态。
4. 页面内存由 Jotai 管理，不持久保存这次实验。React 按纯文本显示输出；帧、累计字节和事件数均有限制。
5. 原生测试通过真实随机端口证明请求取消及一次清理；单看页面“已停止”不证明服务端已释放。

尝试让 Producer 在第二段失败，或把 JSON 拆成不同字节块，记录原测试能否发现改变。不能删除错误路径让测试假通过。将命令与成功/失败证据记入 EVIDENCE.md 和本课笔记，再自行标记对应语言实践。

这不是模型调用、生产代理或 Vercel 断连验收。真实服务需独立验证网络代理缓冲、部署生命周期和资源上限；平台只提供下载，不执行学习者修改后的代码。

参考：[SSE 事件格式](https://html.spec.whatwg.org/multipage/server-sent-events.html#parsing-an-event-stream)、[TextDecoder 分块解码](https://developer.mozilla.org/en-US/docs/Web/API/TextDecoder/decode)、[React Effect 清理](https://react.dev/reference/react/useEffect)。
