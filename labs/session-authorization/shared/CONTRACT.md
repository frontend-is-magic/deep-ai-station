# 会话与授权实验 v1

绑定认证和应用安全两课。层次：HTTP → SessionStore → Principal → 授权服务 → owner 范围 Repository。仅在独立本地练习环境运行，入口固定监听127.0.0.1，默认端口8022；不调用模型、数据库或外网。

## 教学身份

`fixtures.json` 中所有凭据均是公开、无权限的教学假值，不能用于生产。每次应用创建独立内存会话与资料；到期时间由创建时 Clock 加 expires_after_seconds 得出，now >= expires_at 或 revoked=true 即无效。Clock、会话库、资料库、allow_cookie（默认true）和 allowed_origin（默认 https://lab.example.test）只允许启动配置或测试依赖注入，HTTP 不提供配置/重置/切换入口。生产必须使用可靠身份提供商/随机不可预测会话、持久撤销、HTTPS和登录保护；本实验不实现登录、密码、JWT、OIDC或密码学。重启会重置教学资料和撤销状态。

Principal.user_id、can_write、csrf_token 只能来自服务端匹配的有效会话。X-User-Id、owner、role 等客户端字段不能形成身份。认证失败不得调用资料Repository。

## 凭据入口与 CSRF

- 接受一个 Authorization: Bearer VALUE（scheme仅ASCII大小写不敏感，分隔符一个或多个ASCII空格）；VALUE符合 `[A-Za-z0-9._~+/-]+={0,}` 且长度1–128。外围传输空白由HTTP库规范化；不主动对token解码或去空白。
- allow_cookie=true时，也可接受 `__Host-lab_session=VALUE`。Cookie按分号分项、键值两端只去ASCII空格/tab；会话值不做百分号或引号解码；无效值401。其他Cookie不提供身份。
- 重复Authorization（含合并后逗号）、重复目标Cookie，或Authorization与目标Cookie同时出现（即使一方无效/空）统一400 ambiguous_credentials，不允许回退。allow_cookie=false时，目标Cookie不能认证，也不能与Authorization混用；其他Cookie不影响Bearer。
- 缺失/错误格式/未知/过期/撤销会话统一401 authentication_required，并带 `WWW-Authenticate: Bearer realm="session-authorization"`。错误不回显原凭据。
- 使用Cookie认证的PATCH和POST，必须同时有且仅有一个Origin，值与allowed_origin完整相等，以及一个X-CSRF-Token与当前会话csrf_token相等。缺失、null、相似域名、重复头、错误/跨会话值均403 csrf_failed；失败不进入资料Repository或撤销会话。比较用语言标准的安全比较函数或等价长度有界机制。
- Bearer明确通过Authorization提交，写操作不要求CSRF头；放在Cookie里的值仍按Cookie规则。不给跨站请求配置CORS允许头，不声称SameSite/HttpOnly能代替服务端CSRF验证。
- 没有假登录接口。测试可注入Cookie请求头验证服务端规则；实际浏览器HTTPS Cookie策略另行验收。Cookie属性函数生成 `__Host-lab_session`，Secure、HttpOnly、SameSite=Strict、Path=/，不设Domain；Cookie注销清除同名同路径并Max-Age=0。不要把本地HTTP测试当作浏览器安全Cookie验证。

## HTTP 契约

Document响应严格为id/title/archived，不返回owner或会话值。默认资料按id升序；更新只改archived，不改变所属用户。所有路由/错误均JSON且Cache-Control: no-store（含health）。

| 路由                 | 成功响应                                                            |
| -------------------- | ------------------------------------------------------------------- |
| GET /health          | 200 {"status":"ok","lab":"session-authorization-v1"}，公开          |
| GET /me              | 200 {"user_id":"alice"}                                             |
| GET /documents       | 200 {"items":[Document]}，只含当前用户                              |
| GET /documents/:id   | 200 Document；他人/不存在相同404 document_not_found                 |
| PATCH /documents/:id | 正文仅{"archived":true或false}；200 Document                        |
| GET /csrf            | 200 {"csrf_token":"当前有效会话的教学值"}                           |
| POST /logout         | 正文必须{}；200 {"ok":true}，原子撤销当前会话；Cookie模式另清Cookie |

私有路由处理顺序：认证 → 拒绝任何非空query（422 invalid_input）→ 写请求实际字节/媒体/JSON限制 → 写操作前重新检查同一会话有效性 → Cookie CSRF → owner范围查找 → 写权限 → 更新。自己的资料缺写权限403 forbidden，他人资料仍404。注销不要求资料写权限。注销后的原凭据再次请求401，其他会话保持有效。

写请求实际正文最多4096字节，不信任Content-Length；超限413 request_too_large，然后检查Content-Type第一个分号前媒体类型（ASCII空格/tab两端清理、ASCII大小写不敏感）须application/json，否则415 unsupported_media_type。正文为严格UTF-8/JSON，仅JSON定义的ASCII外围空白，拒绝BOM、重复字段、额外字段、非对象、错误布尔类型、非法编码及尾随正文，422 invalid_input。没有有效认证的超限/坏JSON仍先401。未知路径404 route_not_found；已知路径错误方法405 method_not_allowed。

会话库异常503 session_store_unavailable；资料库异常503 repository_unavailable；其余内部异常500 request_failed。异常、请求头和正文不进入响应或默认请求日志。资料访问必须在Repository方法中携带可信owner，不先全量获取再由客户端筛选。读取请求正文时不得持有会话或仓储全局锁。正文通过后必须重新检查同一会话的过期/撤销状态，再开始CSRF和数据操作；等待正文期间被撤销/过期应401，不能写入。Repository的owner查找/权限/更新在一次原子操作完成，会话撤销本身也原子。最终检查之后已准入的在途操作仍可能完成，撤销不撤回既有操作；不承诺分布式撤销或持久性。

## 共同验收

`contract-cases.json` 按顺序在一个全新应用实例执行；真实HTTP共享验收后重启应用再验证假会话初始化。请求可用json/raw/repeat_body与headers/header_pairs；重复头需保持原始重复，不转dict。expected精确比较JSON类型/内容，response_headers按字段核对；set_cookie标志检查属性，不依赖属性顺序。

各语言原生测试另需覆盖：可注入Clock的到期精确边界；假会话/资料库异常与不回显；无效认证/CSRF/JSON不访问资料库；重复原始认证/Origin/CSRF头；allow_cookie=false；两用户隔离、同用户不同会话独立撤销；正文分块超限（没有Content-Length）；Cookie设置/清除属性。测试不发出外网请求，也不接触真实身份资料。

## 参考

- [RFC6750 Bearer](https://www.rfc-editor.org/rfc/rfc6750.html#section-2.1)
- [OWASP CSRF](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
- [OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
