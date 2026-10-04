# API 契约实验

为“路由与依赖注入”和“输入校验”提供一个可直接启动、验证和修改的 HTTP 服务，以及同一份可导航 React 客户端。三个语言包遵守同一份 [契约](CONTRACT.md)，使用相同的固定资料与成功/失败案例。客户端练习草稿、URL 与真实 API 结果的分工，详细规则见 [CLIENT.md](CLIENT.md)。

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

## 运行公共 React 客户端

先在一个终端启动上面选定的后端，再在第二个终端进入解包目录下的 `client/`：

```sh
cd client
pnpm install --frozen-lockfile
pnpm check
pnpm dev
```

打开 `http://127.0.0.1:5199/search`。客户端始终是 React + TypeScript，与选择的服务端语言无关；平台证据卡上的语言仍表示本次使用的服务端实现。浏览器只向同源 `/api/search` 和 `/api/lessons/:id` 请求，Vite 去掉 `/api` 后转发到 `127.0.0.1:8020`。

如后端使用其它端口，显式运行 `LAB_API_PORT=8021 pnpm dev`；它只接受有效数字端口，不接受主机或 URL。客户端端口可用 `pnpm dev --port 5200` 覆盖。dev 和 preview 都只绑定 loopback，端口被占用会拒绝启动，不关闭无关服务。

构建后复验使用相同代理与端口规则：

```sh
pnpm build
pnpm preview
```

只使用公开教学问题：查询会进入地址栏和浏览器历史。客户端没有 localStorage、记录数据库、模型请求或自动重试。关闭自己启动的终端服务后结束实验。

## 先预测，再走一遍页面

1. 打开 `/search`，此时没有业务请求。输入“工具”但先不提交，地址与结果不变；按 Enter 或点击“搜索”才提交。
2. 地址变为 `/search?q=工具` 的编码形式，实际 POST body 是 `{"question":"工具"}`。展开“查看本次请求”，核对 200、资料 ID `tools` 与标题“工具契约”。界面显示经过校验的真实响应，不从本地固定表合成结果。
3. 把草稿改为“HTTP”但不提交。观察上次“工具”的结果仍保留，并显示草稿尚未提交；草稿变化不是一次新的服务端事实。
4. 点击“查看资料：工具契约”。详情 URL 保留已提交的“工具”，未提交的“HTTP”草稿被丢弃。真实 GET 只返回 ID/title；“资料摘要”不是原文正文或模型回答。
5. 在详情页刷新，再用浏览器后退/前进。每次都按目的 URL 重新请求；回查询页的草稿恢复为 URL 中的“工具”。同一地址点击“搜索”会明确重查，但不会添加重复历史项。
6. 提交“火星盆栽”，看到 200 和合法空列表；直达 `/lessons/missing`，看到真实 404。再试空白草稿：它保留现有 URL/结果并提示修改，不发请求。
7. 比较重复参数 `/search?q=工具&q=HTTP` 与普通查询：重复参数整份拒绝，不偷偷选择一个。输入/URL 最终使用 1–500 个 Unicode 码点和固定 White_Space 规则，不等于 JS 字符串 length/trim。
8. 写下一个预测与实际不同的步骤，把实际命令、地址、请求状态和未验证项记入 [EVIDENCE.md](EVIDENCE.md)，再回到原课程证据卡。实验不会自动写平台笔记、实践或完成标记。

刷新和返回可以重新执行本实验的无副作用搜索 POST。不要把这种行为搬到支付、发布或其它写入 POST 上。未确认的 HTTP 状态在面板中是 null；非契约响应不展示原始错误正文。一次请求总共等待最多 5 秒，响应按实际字节限制到 32 KiB；失败后由你明确重试。

## 沿源码学习，再补一项 URL 派生规则

- `client/src/navigation.ts`：把严格 URL 转为 search/detail 目标，生成规范地址。已提交查询来自这里，不来自旧组件内存。
- `client/src/App.tsx`：草稿只在当前页面内编辑；显式 submit 才更新 URL。React Router 管理前进/后退，Jotai 订阅当前请求快照，视图从该快照派生反馈。
- `client/src/controller.ts`：一个当前请求槽；新查询、导航和卸载先使旧身份失效再取消等待。StrictMode 的第一次 setup 若立即清理，微任务不会再发请求。
- `client/src/protocol.ts`：边界检查、实际响应字节读取和错误分类。服务端有自己的独立校验，不依赖客户端保证安全。

初始包**没有实现** `view=compact|comfortable`。在自己的独立副本中增加这个布局选择：把枚举明确加入 URL 白名单，让刷新/后退保留布局，但不改变 q、API 请求或结果。先写三种操作的预期，再补相同的用户行为测试；不要用 localStorage 代替 URL，也不要只删掉现有的未知参数检查。

现有证据键仍是原两课与本次服务端语言；旧记录不会被自动覆盖或撤销，也不代表新增客户端已经验收。完成练习后由你明确更新源码版本、成功/失败证据和未验证项。

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
