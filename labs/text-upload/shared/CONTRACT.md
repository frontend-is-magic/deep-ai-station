# text-upload-v1：受限文本上传共同契约

适用 Python / FastAPI、TypeScript / Hono、Go / Gin。仅监听 `127.0.0.1:8023`，`PORT` 可改变端口，不允许通过配置改变监听地址。公开假会话沿用会话授权实验，仅显式 Bearer；真实登录、Cookie/CSRF 完整矩阵见该实验。本实验不落盘、无外网/模型、无解析器、无持久化。

## 请求和身份

- `fixtures.json` 建立公开假会话，时钟/会话库/仓储可以在服务端注入。时钟以秒为契约单位，初始化时刻 + `expires_after_seconds` 为到期时间；`now >= expires_at` 或 revoked 拒绝。重启重置会话并清空文档。
- 一个 `Authorization: Bearer <token>`；scheme ASCII 不区分大小写，至少一个 ASCII 空格，token 1..128 字符，RFC b64token 字符集。缺失/未知/无效/到期/撤销 → 401 `authentication_required`，附 `WWW-Authenticate: Bearer realm="text-upload"`。
- 重复 Authorization、包含逗号的合并 Authorization，或显式 Authorization 与目标 `__Host-lab_session` Cookie 同时出现 → 400 `ambiguous_credentials`。重复目标 Cookie 同样400。Cookie单独不能认证（401）；其他无关 Cookie 不影响显式 Bearer。保留真实 HTTP 重复头检测，不只测试 fetch 合并后的视图。
- 所有私有请求先认证；非空 query →422 `invalid_input`，不从 query/owner头/文件名推导身份。写入需要 can_write；初始只读身份直接403 `forbidden`，不读取正文。
- 上传顺序：初始认证 → query → 写权限 → 有界读取实际字节 → 上传头检查 → 文件名/正文策略 → 使用同一 token 再解析有效会话和写权限 → 仓储原子提交。读正文期间不持全局锁；正文期间撤销/到期必须401，权限收回必须403，不创建文档。最终授权后已进入同步原子提交的操作可完成，不承诺跨实例即时撤销。

## 上传与正文

`POST /documents` 使用原始请求体，不是 JSON 或 multipart。上传必须各有一个 `Content-Type`、`X-Filename`。任何重复上述头（包括相同值）→400 `ambiguous_upload_headers`；合并逗号值由后续格式检查拒绝。任何 Content-Encoding 头都415 `unsupported_media_type`（不解压）。

- 逐块统计实际正文，超过4096字节立即413 `request_too_large`；不信任 Content-Length，4096接受、4097拒绝。该检查在上传头格式/文本检查之前。不把整个超大正文缓冲到内存后才限长。
- 媒体值只允许 `text/plain` 或 `text/markdown`，ASCII 不区分大小写，可跟唯一 `charset=utf-8`（可有双引号），分号/等号两侧可有空格或TAB。其他参数/编码/类型/缺失媒体头415 `unsupported_media_type`。首尾空格/TAB允许；保存 canonical 小写媒体类型，不保存 charset。
- `X-Filename` 首尾空格/TAB去除后须完整匹配 `[A-Za-z0-9][A-Za-z0-9_-]{0,59}\.(txt|md)`，扩展名 ASCII 不区分大小写。`.txt` 仅配 text/plain、`.md` 仅配 text/markdown；不匹配、缺失、路径、额外点、百分号、空白、Unicode、冒号均422 `invalid_filename`。保留校验后的显示名；不做 URL 解码。客户端名永不参与对象 ID 或路径。
- 正文必须非空 strict UTF-8；拒绝任何 U+0000..001F（TAB/LF/CR除外）、U+007F..009F、U+FEFF。只有 ASCII 空格/TAB/LF/CR 的正文也拒绝，422 `invalid_text`。不规范化换行、Unicode或空白；SHA-256/字节数/下载均针对原始字节。
- `<script>`、Markdown链接等只作为不可信字节保留，不能执行、渲染或抓取链接；文本校验不是杀毒或通用文件类型探测。PDF/图片/压缩包不在范围。

## 仓储和 HTTP

每个新进程为空仓储。成功提交生成全局递增 `doc-000001` 等六位十进制 ID；这是教学确定性 ID，不是授权凭据。元数据只有 `id`、`filename`、`media_type`、`size_bytes`、`sha256`（小写hex）。owner只存在服务端，不出现在响应。

每个 owner 最多3份文档且合计最多8192字节（等于上限允许）。拒绝任一超限 →409 `quota_exceeded`；不能消耗ID、增加计数/字节、覆盖已有文档。仓储一个短原子操作负责校验、生成ID并发布元数据和不可变内容；失败前后存储完全一致。同一显示名重复上传生成新ID；列表按ID升序，owner范围内查询。没有删除/覆盖接口，重启清空；不要把本实验当耐久数据库。

| Method / path              | 成功响应                                           |
| -------------------------- | -------------------------------------------------- |
| GET /health                | 200 `{"status":"ok","lab":"text-upload-v1"}`，公共 |
| POST /documents            | 201 单个元数据对象                                 |
| GET /documents             | 200 `{"documents":[元数据...]}`                    |
| GET /documents/:id         | 200 单个元数据对象                                 |
| GET /documents/:id/content | 200 原始字节                                       |

有效会话读取不存在或其他owner的文档（含内容）一律404 `document_not_found`；只读会话可读取自己文档。内容返回 `Content-Type: application/octet-stream`、`Content-Disposition: attachment; filename="upload-<id>.<ext>"`（扩展名根据canonical媒体生成小写txt/md）。其余成功和所有错误为JSON `{ "error": "固定类别" }`，错误不可泄露正文/文件名/会话/异常细节。

所有响应含 `Cache-Control: no-store` 和 `X-Content-Type-Options: nosniff`，不授予 CORS，不设置Cookie。已知路由不支持方法405 `method_not_allowed`，未知路由404 `route_not_found`，不自动重定向。GET不读取/处理正文。会话库异常503 `session_store_unavailable`；仓储异常503 `repository_unavailable`；其他内部异常500 `request_failed`，不记录敏感错误或请求头。禁止通过HTTP注入测试仓储、时钟、角色或故障标记。

## 案例与实际边界

`contract-cases.json` 为一个新进程内顺序执行的共同HTTP案例。请求支持 headers/header_pairs、raw（UTF-8）、body_base64（畸形字节）、repeat_body。响应为 expected JSON 或 expected_body_base64；response_headers要求精确相等。框架前的HTTP协议错误不承诺此JSON契约。

原生测试另外验证：无Content-Length分块超限；文件正文暂停期间会话到期/撤销/权限收回，不持全局锁；注入仓储故障无残留；并发上传争用最后名额/最后字节预算时不超限；元数据/内容副本不会通过调用方修改；重复同名不会覆盖。用可控同步点，不靠sleep碰运气。

这只证明私有内存对象不使用客户端路径、进程内原子性与固定文本政策，不证明真实文件系统权限/符号链接防护、对象存储事务、杀毒、分布式配额、上传超时/并发连接预算、HTTPS Cookie、生产身份或端到端RAG。网页展示内容必须自行安全转义；下载头不能作为允许执行文本的理由。

参考：[OWASP File Upload](https://cheatsheetseries.owasp.org/cheatsheets/File_Upload_Cheat_Sheet.html)、[MDN Content-Disposition](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Disposition)。
