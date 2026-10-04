# API 契约实验：可导航检索客户端冻结合同

客户端教学版本 `api-navigation-v1`，所属实验仍为 `api-contract`，服务端契约仍为 `api-contract-v1`。对应 `fullstack-routing`、`fullstack-validation`，保留 Python / TypeScript / Go 三个包与六个课时语言证据键，总证据组合仍为 48。

新增内容是同一份 React + TypeScript 客户端，练习草稿、URL、真实 API 数据的分工。不是新的后端、CRUD、账号、模型、上传、流式协议或历史数据库。实际 HTTP 契约、固定资料与三种服务端源码保持不变；客户端不会从本地 fixtures 生成结果。

## 1. 页面与 URL

使用已有项目锁定版本的 React Router 处理浏览器历史，附加一个纯函数解析固定 URL；不实现通用路由框架。用 pathname + search，不使用 hash 路由。

| URL                           | 页面/行为                                                          |
| ----------------------------- | ------------------------------------------------------------------ |
| `/`，可带合法 q               | replace 到等价的规范 `/search` URL                                 |
| `/search`                     | 查询页，无已提交查询，不请求 API                                   |
| `/search?q=<value>`           | 查询页，按 URL 查询真实 POST /api/search                           |
| `/lessons/<id>`               | 摘要详情页，真实 GET /api/lessons/<id>；返回链接指向 /search       |
| `/lessons/<id>?q=<value>`     | 相同详情请求；q 只保存返回查询上下文，返回链接指向对应 /search?q=… |
| 其它 pathname                 | “页面不存在”，不请求 API，提供“返回查询页”                         |
| 已知 pathname 的非法参数/hash | “地址参数无效”，不请求 API，提供“返回查询页”                       |

精确规则：

- pathname 区分大小写；不接受尾斜杠别名。`/search/`、`/lessons/tools/` 都是未知页面。
- `<id>` 是原始路径中的单个 ASCII `[a-z0-9][a-z0-9-]{0,63}`，不接受百分号、斜杠或其它字符。ID 不限于三条固定资料；`missing` 合法，必须真正 GET 并显示服务端 404。非法 ID 归未知页面，不发请求。
- 已知页面只允许 **零个参数，或恰好一个 q 参数**。未知参数、重复 q（即使同值）、空参数段、缺少 `=`、非空 hash 均拒绝，不选择第一个、不静默忽略。初始版 `view=compact` 也属于未知参数。
- 解析顺序：识别 pathname → 检查 search 长度/参数/hash → 解码/校验 q → 规范化。未知 pathname 优先显示“页面不存在”。孤立 `?` 等同无参数，规范 URL 去掉它。
- search 原始字符串至多 8192 个 UTF-16 code unit。单个 key/value 采用 URL form 规则：`+` 解为 U+0020，再用严格 `decodeURIComponent` 解百分号；畸形 `%` 或非法 UTF-8 拒绝，不能悄悄用 U+FFFD 替换。key 解码后必须精确 `q`。
- q 依第 3 节规则规范化后必须 1–500 个 Unicode 码点。`?q=` 或全 White_Space 不是空首页，而是非法地址；不发请求。
- 生成 URL 一律使用 `new URLSearchParams({ q: normalizedQuestion }).toString()`：中文 UTF-8 百分号编码，空格为 `+`，字面加号为 `%2B`。浏览器可以显示解码形式，验收以实际 pathname/search 与解码值为准。
- 等价但非规范输入（如编码 key、`%20` 空格、q 首尾 White_Space）以 **replace** 改为唯一规范 URL，不能增加历史项，也不能因此发两次请求。规范化不变更大小写、内部空白或 U+FEFF。
- 示例：`/search?q=%20%E5%B7%A5%E5%85%B7%20` 规范为 `/search?q=%E5%B7%A5%E5%85%B7`；`q=HTTP+API` 表示 `HTTP API`；`q=C%2B%2B` 表示 `C++`。
- query 会进入地址和浏览器历史。界面/README 提醒只使用公开教学问题，不填写私人资料；不写 localStorage/sessionStorage，不上报分析。

## 2. 草稿、提交与请求

查询页有一个 textarea 草稿、一个明确的“搜索”提交按钮；Enter 提交，Shift+Enter 换行（输入法 composing 时 Enter 不提交）。form 原生 submit 也走相同路径。React/Jotai 保留最少必要内存状态，URL 是已提交查询的来源；不额外持久化 URL、副本历史或结果缓存。

### 进入/编辑

- 初次进入、刷新、每次有效 URL 导航（含后退/前进），草稿重置为当前 URL 的规范 q；没有 q 则为空。详情页无编辑器。
- 输入编辑不改 URL、不发请求、不替换当前结果/请求面板、不取消当前已提交查询。草稿与已提交 q 不相等时提示“草稿尚未提交；当前结果仍对应上次查询。”；尚无提交时提示“填写问题后点击搜索。”。
- 离开当前 URL 丢弃未提交草稿；不拦截导航、不弹确认，也不跨页面恢复草稿。点击结果详情链接只能携带已提交 q，不能携带尚未提交的编辑内容。

### 提交/导航

| 事件                                      | URL / 历史                            | 请求                                 |
| ----------------------------------------- | ------------------------------------- | ------------------------------------ |
| 草稿无效时提交                            | URL/结果不变，字段反馈并聚焦 textarea | 0；不取消仍在运行的旧有效查询        |
| 有效新 q 提交                             | push `/search?q=…`，草稿改为规范 q    | 对新 q 恰好一次搜索                  |
| 在当前查询页再次提交相同规范 q            | 不新增历史项                          | 明确新尝试，重新搜索一次             |
| 有效 q 提交时旧请求尚未结束               | 按上述 URL 规则                       | 先使旧尝试失效并 abort，再启动新尝试 |
| 结果链接进入详情                          | push `/lessons/<id>?q=…`              | 一次详情 GET，无额外 search          |
| 点击“返回搜索结果”                        | push 规范搜索 URL                     | 一次搜索，不使用旧结果缓存           |
| 浏览器后退/前进，进入有效 q 或详情 URL    | 不写入额外历史项                      | 对目的 URL 请求一次                  |
| 刷新/直达有效 q 或详情 URL                | 从地址恢复                            | 对目的 URL 请求一次                  |
| 点“重试请求”                              | URL 不变；不消费未提交草稿            | 重新请求当前 URL 目标一次            |
| 进入 `/search` 无 q / 无效地址 / 未知页面 | 清旧结果与面板                        | 0，旧请求失效并 abort                |

详情 q 改变虽然不改变 GET 路径，仍是一次新的 URL 上下文：重置旧视图并重新 GET 一次，保持规则简单。点击指向完全相同 URL 的普通链接无需重复加载；显式搜索/重试才创建同地址的新尝试。

不后台轮询、不自动重试。这里的 POST /search 是既有无副作用查询，所以刷新/历史导航可以重新请求；不能把这个规则推广为自动重放写入 POST。开发 StrictMode、组件重渲染、规范化 replace 不应增加实际逻辑请求；验收以显式操作和目的 URL 为单位，不根据是否出现 spinner 判断次数。

## 3. 实际 API 与 Unicode

浏览器只使用以下同源请求，`credentials: 'omit'`、`redirect: 'error'`，不发送 token，不接受运行时外部地址：

| 页面目标 | 方法与客户端路径        | 请求 body                                                   | 接受的成功结构                                            |
| -------- | ----------------------- | ----------------------------------------------------------- | --------------------------------------------------------- |
| 搜索 q   | POST `/api/search`      | `{"question":q}`，JSON UTF-8，Content-Type application/json | 精确 `{question:string,items:[{id:string,title:string}]}` |
| 详情 id  | GET `/api/lessons/<id>` | 无                                                          | 精确 `{id:string,title:string}`                           |

客户端无需额外 health 请求。成功/错误响应的 Content-Type 首段须为 application/json（去两端 ASCII 空格/tab，ASCII 大小写不敏感，忽略分号参数）；缺失或其它类型拒绝。HTTP 成功必须是 200；响应字段不得多/少，数组项目精确 id/title、ID 唯一且符合 URL 的 ID 格式；title 是非空且可编码 UTF-8 的字符串。整个响应受 32 KiB 限制，不硬编码只能返回 fixtures 的三项。搜索 response.question 必须等于本次规范 q；详情 response.id 必须等于请求 ID。响应结构失败不能显示为正常空列表。

客户端不收到正文，摘要页仅展示 ID/title 和请求事实，不命名为“阅读原文”，不拼模型答案。

输入规则与现有三后端一致：

- 先拒绝孤立代理码点，保留合法 emoji；只去首尾 Unicode White_Space：U+0009–000D、0020、0085、00A0、1680、2000–200A、2028、2029、202F、205F、3000。
- **不使用 JS trim() 替代该规则**。U+FEFF、U+001C–001F 不被去除。规范 q 按 Unicode 码点计 1–500，不用 JS UTF-16 length 计数。
- 本地输入无效时不发请求。构造请求只允许 question 字段，UTF-8 JSON 实际字节不超过 4096。不要向后端添加错误演示开关。
- 后端搜索规则维持对 title/body 的完整子串匹配，只忽略 ASCII A–Z 大小写；不是语义搜索。客户端展示后端顺序，不重新评分/排序。

必须接受的固定成功例：

```json
{"question":"工具","items":[{"id":"tools","title":"工具契约"}]}
{"question":"HTTP","items":[{"id":"http","title":"HTTP API"}]}
{"question":"火星盆栽","items":[]}
{"id":"tools","title":"工具契约"}
```

U+FEFF 作为单独合法 q 不应被客户端错误拦截，真实 API 返回该 question 与空 items。后台仍独立校验；原生/HTTP 测试证明绕过客户端的非法值会返回 422，不声称前端校验形成安全边界。

## 4. 状态与固定反馈

只保留当前目的 URL 的结果；新请求开始即清旧结果/旧响应 body，保留新的请求描述。loading、合法空结果、找不到资料和请求失败分别呈现。

| 情况                                                       | 可读反馈与操作                                                      |
| ---------------------------------------------------------- | ------------------------------------------------------------------- |
| 无已提交 q                                                 | “填写问题后点击搜索。”                                              |
| 本地空白/过长/非法 Unicode 草稿                            | “请输入 1–500 个 Unicode 码点的问题。”；aria-invalid + 关联字段提示 |
| 搜索进行中                                                 | “正在查询…”                                                         |
| 详情进行中                                                 | “正在读取资料摘要…”                                                 |
| 搜索成功非空                                               | `找到 N 条资料`                                                     |
| 200 空列表                                                 | “没有匹配资料；这是合法的空结果。”                                  |
| GET 404 精确 `{error:"lesson_not_found"}`                  | “资料不存在（404）。”；返回链接可用                                 |
| POST 422 精确 `{error:"invalid_input"}`                    | “服务端拒绝了输入（422），请修改问题后重新提交。”；不自动改草稿     |
| POST 413 精确 request_too_large                            | “请求超过服务端字节上限（413）。”                                   |
| POST 415 精确 unsupported_media_type                       | “服务端不接受此请求格式（415）。”                                   |
| 503 精确 repository_unavailable                            | “资料服务暂不可用（503），请稍后重试。”                             |
| fetch/读取流拒绝                                           | “连接失败，请检查本地 API 后重试。”                                 |
| 5 秒总期限                                                 | “请求超时，请重试。”                                                |
| 非 JSON、超长、非法 UTF-8、坏 schema、未列出的 status/code | “服务响应不符合实验契约。”                                          |

浏览器使用 redirect:error；重定向导致的 fetch 拒绝与其它网络拒绝通常不可区分，统一显示连接失败，不读原 Error 字符串猜测原因。若注入依赖仍交回 redirected=true 的 Response，则拒绝为非契约响应。

错误 body 只接受精确 `{error:固定code}` 且 status/code/请求类型匹配。其它服务端状态不猜测业务含义，不回显原始 body、Error.message/stack 或代理日志。特别是未知 HTTP 路由在不同框架可能有不同 404 code，客户端只请求固定端点，不能混同 lesson_not_found。

服务端内部 500 并非三个实现统一的成功读取契约；本轮客户端按非契约响应显示，不改后端来凑统一文案。代理断开可能返回非 JSON HTTP 错误，也会显示非契约响应，不能仅凭该文案宣称底层发生了哪种网络失败。

“重试请求”出现在请求错误或超时状态；404 可以只显示返回链接。重试目标来自当前 URL，不使用 textarea 中未提交的草稿。搜索时可继续编辑/提交；没有额外 Stop UI，导航/新提交/卸载是本轮唯一取消来源。取消旧等待不显示成目的页的错误。

## 5. 最小异步隔离与资源限额

- 每次尝试拥有私有 identity + AbortController；有效导航、新尝试、卸载先移走 active identity，再 abort。旧 fetch、body 读取、解析或 finally 都不能回填新页面、清新 loading 或重设 active。
- 总期限 **5000 ms** 包含 fetch 到完整响应 body 读取与解析；不是每个 chunk 重置计时。超时在忽略 AbortSignal 的依赖下也必须让当前 UI 退出 loading，之后迟到结果无效。
- 成功/失败响应统一最多 **32768 实际字节**，逐块计数，不信任 Content-Length。严格 UTF-8 解码，完整 body 后才 JSON parse。越界及时取消 reader 并释放锁，不等待不合作的 cancel Promise 挡住收尾；timer 在正常/异常/取消均回收。
- 不扩同时请求池、缓存、重试队列或完整网络日志。一个当前请求槽足够；新目的 URL 使旧槽无效。
- 唯一深入迟到场景：A 搜索挂起 → B 搜索成功 → A 依赖忽略 AbortSignal 后返回合法旧报告。URL、结果、请求面板必须始终属于 B。可在纯控制器测试覆盖 body 阶段，不需要把每一阶段再乘三语言/browser 矩阵。

## 6. 可访问定位与观察面板

页面 h1：`可导航 API 检索实验`。顶部固定说明：`公共 React 客户端连接你选择的本地后端；不调用模型，不保存学习记录。查询会进入地址和浏览器历史，请只使用公开教学问题。`

固定定位：

- 查询 section `aria-label="查询表单"`；textarea label=`检索问题`；submit=`搜索`。
- 当前提交值 output `aria-label="已提交查询"`，无 q 时显示 `尚未提交`；草稿提示 role=status，字段错误可用 role=alert。
- 搜索结果 region `aria-label="查询结果"`；每条 article `aria-label=<id>`、`data-lesson-id=<id>`；详情链接 accessible name=`查看资料：<title>`。
- 详情 region `aria-label="资料摘要"`；标题、ID 都按纯文本显示；返回链接=`返回搜索结果`（有 q）或 `返回查询页`（无 q）。
- 请求状态用 `p[role="status"]`，避免统计 output 隐式 role=status 造成选择歧义；请求错误 `role="alert"`，按钮=`重试请求`。
- 原生 details summary=`查看本次请求`；内部 pre `aria-label="本次请求记录"`、`tabIndex=0`、有限最大高度并可滚动。不得将服务端标题/查询解析成 HTML/Markdown 或执行链接。
- URL 页面变更后可将焦点放在当前主标题（tabIndex=-1），异步完成不再抢焦点；本地字段错误聚焦该 textarea。全部控件可键盘使用，375px 无横向页面溢出。

请求记录 pre 是当前一次尝试的纯 JSON，不是持久历史。精确 shape：

```text
{
  request: {method:"POST"|"GET", path:string, body:{question:string}|null},
  response: {status:number|null, body:已校验的成功或固定错误对象|null},
  outcome:"loading"|"success"|"empty"|"not_found"|"invalid_input"|"http_error"|"network_error"|"timeout"|"protocol_error"
}
```

初次 loading 的 status/body 为 null；若已实际收到响应头，可以记录观察到的 status，body 仅在完整校验后填写。连接失败时未观察到的 status 保持 null；不能造出 HTTP 0/200 或本地错误的假服务端 body。进入没有请求的页面时面板不出现。textarea 编辑不覆盖原请求记录。没有 UUID、token、时间估算或假 model_calls 响应字段。

## 7. 本地命令、代理与冻结依赖

客户端位于包根 `client/`。后端仍在原包根，原生命令不变：Python uvicorn 的 `--host 127.0.0.1 --port 8020`，TypeScript/Go 默认 `127.0.0.1:8020`，后二者可用 PORT 覆盖。

客户端使用 Node 24 / pnpm 10.32.1：

```sh
cd client
pnpm install --frozen-lockfile
pnpm check
pnpm dev
# 构建后真实验收可换为：
pnpm build
pnpm preview
```

dev/preview 都固定 host `127.0.0.1`、默认 **5199**、strictPort=true；显式 `--port` 可供自有测试端口使用，不抢占已有服务。后端地址只由 `LAB_API_PORT` 决定，缺省 **8020**；合法词法为 ASCII 十进制 1–5 位、数值 1–65535，不接受空白、URL、主机名或路径。不要修改用户环境文件。

Vite 的 server 和 preview 都显式提供相同 proxy：仅 `/api` 段边界匹配（`/api` 或 `/api/…`，不匹配 `/apix`），目标 `http://127.0.0.1:<port>`，只移除开头 `/api`。例如 `/api/search` → `/search`，`/api/lessons/tools` → `/lessons/tools`。SPA `/lessons/tools` 刷新必须返回客户端页面，不能误送后端。所有业务 fetch 路径由两种固定请求构造，没有地址输入框。

沿用已安装公共客户端精确版本：React/react-dom 19.3.0、Jotai 2.20.3、Radix Slot 1.3.3、class-variance-authority 0.7.1、clsx 2.1.1、tailwind-merge 3.7.0；React Router DOM **7.18.4**（来自当前根 pnpm-lock 的实际解析版本）。开发依赖：@types/react/@types/react-dom 19.3.0、@types/node 24.19.1、@vitejs/plugin-react 5.2.0、Vite 7.3.6、TypeScript 5.9.3、Tailwind/@tailwindcss/vite 4.3.3、Prettier 3.9.9、Vitest 4.1.11。冻结新客户端 lock，不滚动升级。

`pnpm check` = Prettier（包含 pnpm-lock.yaml）→ Vitest 原生纯模块/控制器行为 → tsc noEmit → Vite build。共享源码 Button 沿用既有可访问样式，不拉远程组件。

## 8. 固定新增文件白名单

仓库 `labs/api-contract/shared/client/` 新增 15 文件，ZIP 中保持 `client/` 相对目录：

```text
package.json
pnpm-lock.yaml
tsconfig.json
vite.config.ts
index.html
src/main.tsx
src/App.tsx
src/style.css
src/components/ui/button.tsx
src/navigation.ts
src/navigation.test.ts
src/protocol.ts
src/protocol.test.ts
src/controller.ts
src/controller.test.ts
```

职责：navigation 负责纯 URL/查询规则；protocol 负责输入与响应校验、有界读取；controller 只管理当前异步尝试和只读快照；App/main 使用 React Router 与 Jotai 组织视图，不能另存相互竞争的网络状态。原生测试需要公开固定纯函数/控制器入口，不开放运行时插件。

shared 另加 **CLIENT.md** 保存此客户端合同；更新现有 README/EVIDENCE/AGENTS 的浏览器操作与教学说明。原有 CONTRACT.md 保留服务端 v1 语义，链接 CLIENT.md。原 lessons.json/contract-cases.json 与三后端源/锁不变。

按当前白名单，最终 ZIP 成员数为 Python **31**、TypeScript **33**、Go **32**（原 15/17/16 + 15 client + 1 CLIENT.md）。不包 node_modules、dist、.venv、缓存或日志。根 .prettierrc.json 仍供包内工具发现，不另复制一份不一致配置。

## 9. 三后端共同人工序列 oracle

每个最终 ZIP 外部解包、冻结安装、启动自己的真实后端和 Vite preview。API 名称/返回值来自既有 fixtures 人工表，不 import 产品 parser 计算答案。

| 步骤 | 操作                    | URL / 实际请求                                                       | 精确关键结果                                 |
| ---- | ----------------------- | -------------------------------------------------------------------- | -------------------------------------------- |
| 1    | 打开 `/search`          | 0 API 请求                                                           | 尚未提交、无结果、无请求面板                 |
| 2    | 填“工具”，Enter         | push `/search?q=%E5%B7%A5%E5%85%B7`；POST body `{"question":"工具"}` | 200，一项 tools / 工具契约                   |
| 3    | 改草稿为“HTTP”，不提交  | URL 不变；0 新请求                                                   | 仍是 tools 结果，显示未提交提示              |
| 4    | 点 tools 详情           | push `/lessons/tools?q=%E5%B7%A5%E5%85%B7`；GET /api/lessons/tools   | 200，精确 tools / 工具契约；草稿 HTTP 被丢弃 |
| 5    | 详情页刷新              | 同 URL；再次 GET 一次                                                | 同一摘要，不做 POST                          |
| 6    | 浏览器后退              | 回工具 search URL；POST 工具一次                                     | 草稿恢复“工具”，一项 tools                   |
| 7    | 浏览器前进              | 回同详情 URL；GET 一次                                               | 同一摘要                                     |
| 8    | 点击返回搜索结果        | push 工具 search URL；POST 一次                                      | tools；草稿“工具”                            |
| 9    | 提交“火星盆栽”          | POST `{"question":"火星盆栽"}`                                       | 200 + items=[]；显示合法空结果               |
| 10   | 直达 `/lessons/missing` | GET /api/lessons/missing                                             | 404 + lesson_not_found；返回查询页可用       |

三个后端至少执行 1–6 和 9–10，证明相同真实业务契约；完整历史/键盘/窄屏及异常 UI 深度流程只需在其中一个后端跑，不机械乘三。上述请求计数不包含静态资源/Vite 连接，preview 不开启 HMR。

一份深入验收再覆盖：相同 q 明确重提不增长历史；q 重复/未知/坏编码均无请求；White_Space 规范化与 U+FEFF 合法空结果；invalid draft 无请求；有限流/总期限；唯一迟到 A→B；375px+键盘。422 等服务端拒绝由真实 HTTP/原生已有契约证明，前端对应展示可以固定受控响应测试，不伪称来自真实网络。网络恢复测试由自有服务/受控 fetch 完成，不调用外部地址。

独立临时副本只破坏一条 URL → 查询派生（直达/刷新时故意不用 q），让与原包相同的实际浏览器断言失败；恢复原包后通过。该反事实证明本轮验收关注导航能力。所有服务/浏览器由外部验收器创建、记录、关闭并 wait/reap，不 broad kill。

## 10. 保留给学习者的练习与证据

初始包 **不实现** `view=compact|comfortable` 控件与 URL 枚举。练习是在自己独立副本中添加它：布局变化来自 URL，刷新/后退保留；不能改变 q、API 请求或结果。显式更新参数白名单与枚举，补三个状态的同一操作断言；不要用 localStorage 代替 URL。

先写预测，再看本次请求面板和实际地址，记录一个被证据纠正的假设。仍使用现有本课本语言的 revision/command/success/failure/pending 五项证据；旧证据、完成和实践标记不自动清空，也不被声称已覆盖新客户端。无新增第 49 个键，不修改 v1 备份、48 容量或 2 MB（2,000,000 字节）导入限制。
