# API 契约实验

为“路由与依赖注入”和“输入校验”提供一个可直接启动、验证和修改的 HTTP 服务。三个语言包遵守同一份 [契约](CONTRACT.md)，使用相同的固定资料与成功/失败案例。

这里不调用模型或数据库，不接受外部 URL，不执行用户代码。请在独立练习环境解包运行；不要将修改后的代码提交给学习平台 API 主机执行。服务默认只监听 `127.0.0.1:8020`。

## 启动你下载的语言版本

所有命令都从解包后的根目录执行。多个语言实验使用同一端口，先停止前一个服务。

### Python / FastAPI

需要 Python 3.12 与 uv。`app.py` 是入口，`service.py` 转换业务结果，`repository.py` 提供可替换的数据来源。

```sh
uv sync --locked
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pytest -q
uv run --frozen uvicorn app:app --host 127.0.0.1 --port 8020
```

### TypeScript / Hono

需要 Node.js 24 与 pnpm 10.32.1。`src/server.ts` 是入口，`createApp(repository)` 注入数据来源。构建后的程序从 `dist/server.js` 运行。

```sh
pnpm install --frozen-lockfile
pnpm check
pnpm start
```

### Go / Gin

需要 Go 1.27.1。`main.go` 是入口，`NewRouter(repository)` 注入数据来源。依赖由 `go.mod` / `go.sum` 冻结。

```sh
go test -mod=readonly ./...
go run -mod=readonly .
```

TypeScript 与 Go 可通过 `PORT` 选择其他端口；Python 使用 uvicorn 的 `--port`。所有版本仍绑定本机回环地址。

## 先观察成功与失败

在第二个终端发送请求：

```sh
curl -i http://127.0.0.1:8020/health
curl -i http://127.0.0.1:8020/lessons/tools
curl -i http://127.0.0.1:8020/lessons/missing
curl -i http://127.0.0.1:8020/search -H 'Content-Type: application/json' --data '{"question":"工具"}'
curl -i http://127.0.0.1:8020/search -H 'Content-Type: application/json' --data '{"question":"火星盆栽"}'
curl -i http://127.0.0.1:8020/search -H 'Content-Type: application/json' --data '{"question":"工具","admin":true}'
```

前三个请求分别验证健康状态、已知资料和不存在的资料。搜索分别应返回固定结果、合法的空结果、拒绝额外字段的 422；响应体见 `CONTRACT.md`。空结果与输入错误是不同的业务情况。

## 路由与依赖注入课

1. 跟踪 `GET /lessons/tools`：HTTP 路由调用服务，服务调用 Repository，再转换成稳定的 `id` / `title` 响应。
2. 在 `lessons.json` 增加一条固定资料并重启，不改路由即可读取它；该修改只属于你的练习副本。
3. 找到测试中的空 Repository 与失败 Repository。它们分别产生 404 / 空列表和 503，不暴露内部异常内容。
4. 用同一请求比较你选中的另一个语言包，记录响应状态和 JSON，而不是只比较实现语法。

## 输入校验课

1. 阅读 `contract-cases.json`，先运行全部测试得到基线，检查空白、错误类型、未知字段、无结果、500 个 Unicode 码点和请求体限额。
2. 在练习副本中暂时删除“拒绝未知字段”的校验，再运行测试；确认 `extra` 案例失败，然后恢复并重跑。不要把破坏后的实现发布。
3. 检查注入测试：非法输入不能调用 Repository 的查询方法。改变数据来源无需复制 HTTP 校验。
4. 比较字节限制与字符限制：emoji 是一个 Unicode 码点，但 UTF-8 使用多个字节；4096 字节上限在 JSON 校验之前执行。
5. 记录实际执行的命令、成功与失败输出、修改版本和未验证行为，填入 `EVIDENCE.md`，再返回课程笔记。测试通过不会自动改变学习完成标记。

这不是完整 AI 应用：真实模型、账号、数据库、生产部署与分布式限流属于后续课程。共享契约只覆盖文档明确声明的端点和输入规则，不能替代新增业务的独立测试。

来源：[Deep AI Station](https://github.com/frontend-is-magic/deep-ai-station)。
