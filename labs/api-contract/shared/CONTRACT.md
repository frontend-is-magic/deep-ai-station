# API 契约实验 v1

这是两课共用的独立 HTTP 实验，不调用模型、数据库或外部网络。默认只监听 `127.0.0.1:8020`。

| 请求                                                                                              | 状态与 JSON 响应                                                      |
| ------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| `GET /health`                                                                                     | `200 {"status":"ok","lab":"api-contract-v1"}`                         |
| `GET /lessons/tools`                                                                              | `200 {"id":"tools","title":"工具契约"}`                               |
| `GET /lessons/missing`                                                                            | `404 {"error":"lesson_not_found"}`                                    |
| `POST /search`，`{"question":"工具"}`                                                             | `200 {"question":"工具","items":[{"id":"tools","title":"工具契约"}]}` |
| 合法但没有命中的问题                                                                              | `200`，`question` 为去掉首尾空白后的输入，`items: []`                 |
| 非 JSON Content-Type                                                                              | `415 {"error":"unsupported_media_type"}`                              |
| 非法 JSON、非法 UTF-8、孤立代理码点、空白、字段缺失、额外字段、错误类型或超过 500 个 Unicode 码点 | `422 {"error":"invalid_input"}`                                       |
| POST 请求体实际超过 4096 字节                                                                     | `413 {"error":"request_too_large"}`                                   |
| Repository 调用失败                                                                               | `503 {"error":"repository_unavailable"}`                              |

POST 先限制实际字节，再检查 Content-Type，随后严格校验 JSON（正文外只接受 JSON 定义的 ASCII 空白，不移除 BOM 或其他 Unicode 空白）；不信任 Content-Length。只检查 Content-Type 第一个分号前的媒体类型：移除两端 ASCII 空格 / tab，并只对 ASCII A–Z 忽略大小写；结果须为 `application/json`。分号后的参数（包括不完整的参数）不参与媒体类型判定；JSON 正文始终按严格 UTF-8 解码。对象只允许 `question` 一个字符串字段，先去首尾 Unicode White_Space，再按 Unicode 码点限制到 1–500。空白集合固定为 U+0009–000D、0020、0085、00A0、1680、2000–200A、2028、2029、202F、205F、3000；U+FEFF 与 U+001C–001F 不在其中。拒绝孤立代理码点，接受合法代理对表示的 emoji。搜索按固定资料顺序，对标题与正文分别做完整子串匹配，且仅对 ASCII A–Z 忽略大小写；其他 Unicode 码点保持原样；只返回 id / title，不返回正文，也不生成答案。

层次为 HTTP 路由 → 服务 → Repository。服务负责转换为稳定响应，Repository 提供 `find(id)` 与 `search(question)`；测试可注入空实现和失败实现，生产入口没有切换假数据的公开参数。非法输入不得进入 Repository 的查询方法。错误不能回显异常堆栈或内部消息。

`contract-cases.json` 是三种实现共享的 HTTP 案例。请求使用 `json`、`raw` 或 `repeat_body`（字符和次数）三种之一；响应必须与 `expected` 完整相等。额外的实现测试覆盖注入 Repository、实际请求体限额与异常分类。
