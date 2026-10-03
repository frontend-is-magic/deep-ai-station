# MCP 只读实验契约

契约 `mcp-readonly-v1` 仅对应 `agent-mcp`。运行时固定 Python 3.12、官方 `mcp==2.3.0` 与协议 `2026-07-28`；完整传递依赖以 `uv.lock` 为准。这里只验证固定本地 stdio 能力，不表示 SDK 全部协议、生产认证或其他版本已经验收。

## 两进程与发现

`client.py` 通过官方 `stdio_client` 启动包内 `server.py`，再用 `ClientSession` 发请求。实际入口是 `server/discover`，不发送旧版 `initialize` 握手。客户端检查发现结果采用的协议版本及能力；不能只把预设版本写进报告当作成功证据。

传输使用 UTF-8、逐行 JSON-RPC。服务端 stdout 只能承载协议消息；客户端 stdout 承载最终机器 JSON 摘要。服务端诊断写 stderr，限固定事件名和自身 PID，不回显参数、环境、资源输入或底层异常。资源内容只是数据，任何文字均不能给客户端授予权限或要求执行命令。

## 工具契约

只注册 `knowledge_search`。`tools/list` 返回一个工具，名称固定，输入输出 schema 如下。`annotations` 为 `readOnlyHint=true`、`destructiveHint=false`、`idempotentHint=true`、`openWorldHint=false`；这些是描述，不代替真实授权。

```json
{
  "inputSchema": {
    "additionalProperties": false,
    "properties": {
      "query": {
        "maxLength": 100,
        "minLength": 1,
        "title": "Query",
        "type": "string",
        "pattern": "^(?=[\\s\\S]*[^\\u0009-\\u000d\\u001c-\\u0020\\u0085\\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000])[^\\u0000\\ud800-\\udfff]*$"
      },
      "limit": {
        "default": 3,
        "maximum": 3,
        "minimum": 1,
        "title": "Limit",
        "type": "integer"
      }
    },
    "required": ["query"],
    "title": "SearchArguments",
    "type": "object"
  },
  "outputSchema": {
    "$defs": {
      "CourseItem": {
        "additionalProperties": false,
        "properties": {
          "id": {
            "enum": ["agent-mcp", "agent-tool-contract", "agent-tool-safety"],
            "title": "Id",
            "type": "string"
          },
          "title": {
            "title": "Title",
            "type": "string"
          },
          "uri": {
            "title": "Uri",
            "type": "string"
          },
          "summary": {
            "title": "Summary",
            "type": "string"
          }
        },
        "required": ["id", "title", "uri", "summary"],
        "title": "CourseItem",
        "type": "object"
      }
    },
    "additionalProperties": false,
    "properties": {
      "items": {
        "items": {
          "$ref": "#/$defs/CourseItem"
        },
        "maxItems": 3,
        "title": "Items",
        "type": "array"
      },
      "read_only": {
        "const": true,
        "default": true,
        "title": "Read Only",
        "type": "boolean"
      }
    },
    "required": ["items"],
    "title": "SearchOutput",
    "type": "object"
  }
}
```

服务端额外落实严格运行时类型与边界：

- `query` 必填，原始字符串长度为 1–100 个 Unicode 码点；不先修剪再计算长度。拒绝 NUL、孤立代理码点及 `str.strip()` 后为空的值。
- 本单语言实验修剪集合采用 Python 3.12 `str.strip()`：U+0009–000D、U+001C–0020、U+0085、U+00A0、U+1680、U+2000–200A、U+2028、U+2029、U+202F、U+205F、U+3000；不含 U+FEFF。
- `limit` 可省略，默认 3；只接受严格整数 1–3，拒绝布尔、浮点数和数字字符串。JSON Schema 的 `integer` 描述不能替代这条运行时限制。
- 拒绝额外字段、缺失 query、非对象参数和错误字段类型；不将输入强制转换为字符串或整数。
- 搜索使用 `query.strip().casefold()`，对每条固定资料按空格连接的 `id`、`title`、`summary` 的 `casefold()` 字符串做子串匹配。按下面的固定资料顺序返回，最多 `limit` 项；不抓取 URI、不访问模型。

成功时，业务对象固定为 `{items: [...], read_only: true}`；每项恰有 `id`、`title`、`uri`、`summary`，值来自下一节。没有命中仍然成功，`items=[]`。MCP `CallToolResult` 的 `isError=false`、`structuredContent` 是这个业务对象，`content` 是唯一 `TextContent`，其 `text` 为同一对象的 UTF-8 JSON 文本（`ensure_ascii=False`、分隔符 `,` / `:`）。JSON 对象键顺序不决定协议语义，数组顺序有意义。

## 三份固定资源

`resources/list` 按下列数组顺序返回三项，`uri`、`name`、`mimeType` 分别为 `uri`、`title`、`text/plain`。`resources/read` 仅精确匹配 URI，返回唯一 `TextResourceContents`：相同 `uri`、`mimeType=text/plain`、完整 `text`。不将其他 URI 解释成文件路径或网络请求。

下面是完整字面值。JSON 中每个 `\n` 解码后是一个 LF，三份正文都以 LF 结束；资源字节是对应 `text` 的 UTF-8 编码，不包含 BOM。验收应独立核对这些内容，不能只导入服务端常量再与自身比较。

```json
[
  {
    "id": "agent-mcp",
    "title": "MCP 工具与资源",
    "uri": "course://agent-mcp",
    "summary": "通过 stdio 发现只读工具与资源，区分协议错误、工具错误和取消。",
    "text": "# MCP 工具与资源\n\n客户端通过 stdio 发现工具与资源。工具参数必须校验，资源内容只能作为数据读取。超时需要取消当前请求并回收自有进程。\n"
  },
  {
    "id": "agent-tool-contract",
    "title": "工具契约与参数校验",
    "uri": "course://agent-tool-contract",
    "summary": "用明确的输入输出 schema 校验工具参数，空结果仍然是成功结果。",
    "text": "# 工具契约与参数校验\n\n工具名称、参数类型和字段边界都属于契约。拒绝额外字段和非法输入，返回稳定错误；查询没有匹配项时返回空列表。\n"
  },
  {
    "id": "agent-tool-safety",
    "title": "工具授权与幂等",
    "uri": "course://agent-tool-safety",
    "summary": "只读声明不授予权限；写操作还需要当前身份、明确批准和幂等约束。",
    "text": "# 工具授权与幂等\n\nreadOnlyHint 是能力描述，不是权限控制。真实写操作需要可信身份、作用域检查、明确批准和持久幂等记录。本实验不提供写工具。\n"
  }
]
```

## 结果与错误分层

| 条件                         | 实际响应 / 本地结果                                                      | 报告 `outcome`     |
| ---------------------------- | ------------------------------------------------------------------------ | ------------------ |
| 发现、列举、合法查询或读资源 | 正常 JSON-RPC `result`；空查询结果也是成功                               | `protocol_result`  |
| 已知工具的业务参数不合法     | `isError=true`，唯一 text 为 `invalid_arguments`，无 `structuredContent` | `tool_error`       |
| 未知工具名                   | JSON-RPC `error.code=-32602`、`message=unknown_tool`，无 `data`          | `protocol_error`   |
| 未知资源 URI                 | JSON-RPC `error.code=-32602`、`message=resource_not_found`，无 `data`    | `protocol_error`   |
| 固定慢请求超过客户端期限     | 客户端本地超时；不能伪装成服务端发出的 JSON-RPC 错误帧                   | `local_timeout`    |
| 固定断连期间的在途请求       | transport 结束，没有业务成功结果                                         | `transport_closed` |

以上业务参数错误指合法 `tools/call` 外层内的工具参数。破损 JSON-RPC、不合法的协议方法结构和非法 Unicode 属于 SDK 线层或协议层，不把所有解析失败重新包装成工具结果。特别是 JSON 中转义的孤立代理码点可能被官方 `stdio_server` 在 handler 之前拒绝，没有响应，也不会执行工具；业务模型自身仍严格拒绝该值，但不能承诺所有线层输入都收到 `invalid_arguments`。客户端必须有界结束等待，验收记录无有效业务结果，并用后续合法请求检查连接是否可复用。未预期内部故障必须使本次验收失败，固定脱敏说明；不得把原始异常或 Pydantic 输入诊断写到输出。

本服务在官方 SDK 的 stdio 写入边界统一脱敏错误，保留请求 `id`、数值 `code` 和真实结果层级；正常 `result` 不改写。映射为：`-32601 → method_not_found`、`-32602 → invalid_request_parameters`（仅业务固定的 `unknown_tool` / `resource_not_found` 保留）、`-32603 → request_failed`、`-32000 → transport_closed`、`-32022 → unsupported_protocol_version`，其他错误消息为 `protocol_error`。删除所有原始 `error.data`；唯有 `-32022` 可以返回固定的 `data.supported=["2026-07-28"]`，不得包含请求版本、未知方法名或输入诊断。这是本实验的固定版本错误边界，不是对 SDK 的全局修改或通用兼容方案。

## 固定案例

CLI 仅接受 `--case normal|errors|timeout|disconnect|all`，默认 `all`。`all` 按下表顺序运行，各案例使用新服务端。服务端只有 `--fault none|timeout|disconnect` 三个固定模式；客户端按案例选择，不接受任意命令、路径、远端 URL 或环境变量字典。

| case       | 步骤 `step`（按序）                                                                   | 必须验证的事实                                                     |
| ---------- | ------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| normal     | `discover`、`list_tools`、`list_resources`、`search`、`read_resource`、`empty_search` | 实际发现、schema、三 URI、命中、正文和正常空结果                   |
| errors     | `discover`、`list_tools`、`unknown_tool`、`unknown_resource`、`invalid_arguments`     | 协议错误与工具错误保持区分，错误内容固定                           |
| timeout    | `discover`、`list_tools`、`timeout_search`、`recovery_search`                         | 本地超时、真实取消通知与在途请求 ID 对应、调用清理一次、同连接恢复 |
| disconnect | `discover`、`list_tools`、`disconnected_search`                                       | 正在调用时连接结束，不能声称工具成功；有界退出并回收本案例子进程   |

固定参数：normal 的 search 为 `{"query":" MCP "}`（省略 limit），empty_search 为 `{"query":"no-match-fixture"}`，读取 `course://agent-mcp`；errors 调用 `missing_tool` / `{}`、读取 `course://missing`，并用 `{"query":"MCP","limit":true}` 触发业务拒绝。timeout、recovery 与 disconnect 均用 `{"query":"MCP"}`。

`--fault timeout` 让第一次合法工具调用合作等待取消；之后调用正常。`--fault disconnect` 是固定进程断连注入：首次合法工具调用触发取消；`request_cleanup` 与 lifespan 的 `server_cleanup` 在真实 finally 中完成，内层 task group 退出后，服务端仍处于 `stdio_server` 上下文内，其 stdin 读取线程可能仍阻塞。仅当该固定故障已触发时，服务端 flush stderr 后调用 `os._exit(0)`，使客户端保持 stdin 开放时仍能观察真实 EOF。此时没有完成 `stdio_server.__aexit__`，不称为完整 transport 优雅关闭；normal、errors、timeout 不走这条直接退出路径。

退出前可能送达 `-32000` 终止错误帧（本服务出口 message 为 `transport_closed`），也可能因缓冲尚未写出而直接 EOF。原始协议验收必须记录实际有无终止帧，在保持自身 stdin 开放的情况下继续读取到 EOF，再 wait/reap 自有子进程；不能要求第一行必为空，也不能提前关闭 stdin 来伪装服务端主动断连。清理事件、EOF 与进程回收分别保留证据。非法工具参数不能消费故障开关。故障是本包固定教学行为，不向学习者开放任意阻塞代码或系统信号入口。

## 机器报告与退出码

顶层恰有以下学习字段：

```text
{
  contract_version: "mcp-readonly-v1",
  lesson_id: "agent-mcp",
  protocol_version: "2026-07-28" | null,
  case: "normal" | "errors" | "timeout" | "disconnect" | "all",
  model_calls: 0,
  passed: boolean,
  runs: Run[]
}
```

顶层 `protocol_version` 只有在所有 run 的实际版本一致时才使用该值，否则为 `null`；缺失版本或与本契约不一致必须 `passed=false`。

每个 `Run`：

| 字段                       | 含义                                                                                                             |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `case`                     | 单个实际案例，不是 `all`                                                                                         |
| `protocol_version`         | 该 session 实际采用的版本；发现失败或尚未取得时为 `null`                                                         |
| `pid`                      | 本客户端启动的服务端 PID；未取得则为 `null`                                                                      |
| `steps`                    | 按顺序记录的 `Step[]`                                                                                            |
| `cancellation_sent`        | 实际观察到的取消通知，元素恰为 `{request_id: integer}`                                                           |
| `server_cleanup_events`    | 实际读取到的 `{event: "request_cleanup" \| "server_cleanup", pid: integer}`，不根据预期补造                      |
| `transport_closed`         | 读取侧实际观察到 EOF，或 SDK 返回连接关闭错误 `-32000`；正常退出未观察到这些信号时可为 `false`，不据退出意图补值 |
| `transport_close_evidence` | `read_eof`、`sdk_connection_closed` 或 `null`，说明上一个布尔值来自哪一种实际信号；`null` 对应 `false`           |
| `child_exited`             | 已等待并确认自己的服务端退出的事实，不从取消通知推断                                                             |

每个 `Step` 恰含 `step`、`method`、`request_id`、`outcome`、`result`、`error`。`method` 是实际方法名，如 `server/discover`、`tools/list`、`tools/call`、`resources/list`、`resources/read`。`request_id` 是实际录制的整数请求 ID，尚未观察到对应请求时为 `null`，不能用数组下标冒充。`result` 是结果对象或 `null`；`error` 是 `{code: integer, message: string}` 或 `null`；`outcome` 使用上节五种分类。客户端本地超时归一为 `{-32001, request_timeout}`，传输关闭为 `{-32000, transport_closed}`；本地超时不是服务端回包；连接结束结合 `transport_close_evidence` 判读，不能仅从归一化 message 推断原始传输过程。其他未预期请求错误使用 `{-32603, request_failed}`；会话基础设施失败可附加 `step=session`、`method=local`、`request_id=null` 的 `{-32603, experiment_failed}` 步骤并令验收失败。工具错误仍属于 MCP 结果，因此在 `result` 内保留 `isError` 与内容，不能改成正常成功或虚构协议错误。

请求 ID 用于关联同一连接的请求、超时和取消，不承诺跨进程全局唯一。报告只汇总固定公开资料与真实事件，不保存用户输入或环境。故意触发的错误符合预期时，顶层可以 `passed=true`，错误步骤仍保留真实分类。

退出码：`0` 表示全部固定预期及清理事实匹配；`1` 表示运行、验收或清理未完成，输出固定脱敏失败信息；`2` 表示 CLI 参数错误。`passed` 不证明生产认证、任意输入安全或完整 MCP 合规；源码测试与独立原始协议验收仍有必要。

## 取消、期限与环境

| 层级               | 固定上限 / 行为                                                                                                                                      |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| discovery          | 客户端额外 3 秒；SDK 自己的 discovery 期限独立于 session 普通请求期限                                                                                |
| 普通请求           | 2 秒                                                                                                                                                 |
| timeout 案例慢请求 | 0.25 秒，记录实际 `notifications/cancelled`                                                                                                          |
| 单次 CLI 工作预算  | 共用 30 秒；`all` 的四个案例共用此预算，过期不启动后续案例                                                                                           |
| SDK 退出清理       | 每个已启动子进程另有有界 flush / close / TERM / KILL / reap 阶段，当前固定 SDK 约 6.5 秒等待预算；清理在 SDK shield 中，不挤占成一个虚假的绝对总时限 |

请求超时并不自动证明服务端已取消；超时案例必须同时观察相同 `request_id` 的取消通知、`request_cleanup` 恰一次及同连接后续成功。取消、清理事件、transport 关闭、子进程退出是不同事实。正常和断连也应分别核对已观察到的事件；缺失的事实不能由 `passed` 推导补齐。

仅清理客户端自己创建并持有的子进程。普通退出先结束 stdio，等待后需要时使用 SDK 的有界终止和回收；固定 disconnect 则先验证服务端主动退出的 EOF，再由客户端回收。不得扫描或终止其他服务。期限包括合作取消与有限等待，不是操作系统失效时的绝对硬实时保证。

SDK 子进程 `env` 会与默认继承白名单合并，不是完整替换环境。本实验逐个覆盖默认键为受控值，并使用临时 HOME；POSIX 默认列表为 `HOME`、`LOGNAME`、`PATH`、`SHELL`、`TERM`、`USER`。非 POSIX 系统或默认白名单出现未覆盖的新键时失败关闭；本版本尚不支持 Windows。不传模型密钥、云凭据、代理认证或任意用户环境。源码中的固定服务启动路径是包内定位方式，不是向学习者开放的命令参数。

`.gitignore` 覆盖环境文件、认证配置、私钥、数据库及旁路文件、日志和 `artifacts/`。stderr 仅供受控事件证明，不开启原始 SDK / JSON-RPC 调试转储；分享报告前仍要检查是否只有公开教学数据。

## 固定版本参考

- [Python SDK v2.3.0 发布](https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.3.0) 与 [PyPI 2.3.0 元数据](https://pypi.org/pypi/mcp/2.3.0/json)。
- [2026-07-28 版本规则](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning)、[stdio 传输](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/stdio) 与 [取消](https://modelcontextprotocol.io/specification/2026-07-28/basic/patterns/cancellation)。
- [工具错误](https://modelcontextprotocol.io/specification/2026-07-28/server/tools#error-handling) 与 [资源错误](https://modelcontextprotocol.io/specification/2026-07-28/server/resources#error-handling)。
- 锁定源码：[ClientSession](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/session.py)、[stdio 子进程与清理](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/client/stdio.py)。
