# Agent 写工具授权与幂等实验 v1

固定版本 `agent-write-safety-v1`，绑定 `agent-tool-safety`。Python/FastAPI + 真实本地 SQLite；React 客户端通过自己的 Vite 回环代理调用服务。唯一写工具 `publish_revision` 只把摘要发布到练习数据库，不访问模型、外部网站或任意代码执行器。

## 教学身份与可信范围

fixtures.json 全部为公开假会话，不能用于生产。页面角色切换只是选择预置 fixture，不证明真人身份，也不是生产审批通道。服务端 Principal 从有效会话取得 owner_id、requester_id、capabilities，客户端 owner/confirmed/role 字段不产生授权。Agent与审批者分别持有不同 capability；所有数据均在当前 owner 内查找，其他owner/不存在返回相同404。同owner的read可查看操作，execute必须是原requester。

仅接受一个 Authorization: Bearer VALUE；scheme ASCII大小写不敏感，分隔符一或多个ASCII空格，VALUE格式 `[A-Za-z0-9._~+/-]+={0,}` 且1–128字符。重复Authorization、合并逗号统一400 ambiguous_credentials；Cookie不能认证，不配置跨站CORS。缺失/未知/错误格式/过期/撤销401 authentication_required，WWW-Authenticate为 Bearer realm="agent-write-safety"。会话从每次进程启动的Clock计算到期；这是内存教学身份，重启会重建身份有效期与撤销状态。SQLite中的操作、批准期限与回执不因重启重置。

Clock默认服务器UTC秒时间，可测试注入；HTTP不允许设置Clock/会话/故障。认证先于正文验证。读取正文后以及等待数据库写锁后重新验证同一会话及Principal；最终检查之后已获准的在途操作可能完成，不承诺分布式撤销。会话库故障503 session_store_unavailable，数据库故障503 repository_unavailable，不回显会话值、原始异常或SQL。

## 严格输入与响应

所有响应 Cache-Control: no-store；错误严格为 {"error":"固定代码"}。私有请求非空query拒绝422 invalid_input。实际正文最多4096字节，超过413 request_too_large；媒体类型application/json（分号前ASCII大小写不敏感），否则415 unsupported_media_type。严格UTF-8/JSON，拒绝BOM、重复/额外字段、非对象、非法常量、孤立surrogate、递归过深及尾随正文，422 invalid_input。operation_id 为小写规范UUIDv4（含variant）；document_id 为1–80 ASCII小写字母数字连字符；expected_version是1..2147483647的JSON整数，不接受bool/float；content为1..2000 Unicode码点，不含NUL，不偷偷trim或归一化Unicode。intent_hash为64个小写hex字符。

Document={id,title,content,version}。
Receipt={operation_id,document_id,version,content,intent_hash,applied_at}。
Operation={operation_id,owner_id,requester_id,tool,document_id,expected_version,before_content,content,intent_hash,status,prepared_at,approved_at,expires_at,receipt}。
时间是有限的Unix秒数；approved_at/expires_at/receipt 在未批准/未执行时为null。tool固定publish_revision。所有返回字段来自服务端存储。execute以外的成功操作都返回Operation本身，execute返回{operation:Operation,replayed:boolean}。GET操作仅在本owner内查询；不给客户端枚举所有操作的端点。

| 路由                          | 输入和成功响应                                                                                                 |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------- |
| GET /health                   | 公开200 {status:"ok",lab:"agent-write-safety-v1"}                                                              |
| GET /me                       | read；200 {owner_id,requester_id,capabilities}                                                                 |
| GET /documents                | read；200 {items:[Document]}，按ID排序                                                                         |
| GET /documents/{id}           | read；200 Document，他人/不存在404 document_not_found                                                          |
| POST /operations              | prepare；正文仅{operation_id,tool,arguments:{document_id,expected_version,content}}；200 Operation，准备不发布 |
| GET /operations/{id}          | read；200 Operation，他人/不存在404 operation_not_found                                                        |
| POST /operations/{id}/approve | approve；正文仅{intent_hash}；200 Operation，有效期默认60秒                                                    |
| POST /operations/{id}/revoke  | approve；正文仅{intent_hash}；200 Operation，已执行409 already_applied                                         |
| POST /operations/{id}/execute | execute且原requester；正文仅{intent_hash}；200 {operation,replayed}                                            |

未知路径404 route_not_found，已知路由错误方法405 method_not_allowed。路径/正文ID格式错误422 invalid_input。自己资源缺capability403 forbidden；同owner其他requester执行403 requester_mismatch。正文或HTTP头不能注入数据库路径或SQL。

## 意图、批准与幂等

服务端对已校验业务对象 `{"owner_id", "requester_id", "tool", "document_id", "expected_version", "content"}` 使用JSON UTF-8（ensure_ascii=False, sort_keys=True, separators=(',',':')）计算SHA256。hash是意图标识，不是授权凭据；差异必须显示before_content、content和expected_version。

同owner同operation_id再次prepare：原requester+同业务参数返回原Operation（不重新校验当前文档版本、不覆盖原差异），参数或requester任一变化409 operation_conflict。同键在不同owner独立；越权资源404。新的prepare要求目标存在且expected_version等于当前版本，否则409 stale_document。

状态：prepared→approved→applied；prepared/approved可撤销为revoked；now >= expires_at时approved变为expired。expired/revoked不能重新批准；过期一旦观察到即以事务持久化，不因随后时钟回拨恢复。系统仍依赖可靠服务器时钟，不声称抵御首次观察之前的时钟回拨。

所有动作先核对存储中的精确hash，否则409 intent_mismatch。approve只有prepared能生成期限；重复approve approved返回原Operation并保持原时刻/期限，不续期，即使文档已被其他操作更新；expired/revoked分别409 operation_expired/operation_revoked，applied为409 already_applied。首次批准时重新校验目标版本，不替学习者自动修改expected_version；execute始终重新校验当前目标版本。

revoke重复revoked返回原值；expired为409 operation_expired。execute未批准403 approval_required，expired/revoked分别409 operation_expired/operation_revoked。当前目标版本变化409 stale_document，批准不会转移到新版本。

已applied且hash/owner/requester/当前身份正确的execute返回原Receipt且replayed=true，不再要求过去的批准未过期，不新增publication。首次提交replayed=false。查询和重放始终经过当前认证/权限，不因有回执就省略身份验证。

## 事务与未知结果

SQLite每次操作独立连接，启用外键、约束与参数绑定，忙等有界（建议0.5秒）。初始化迁移和固定fixture种子仅在空库时创建，重启不能覆盖业务数据。版本采用数据库schema版本1；未知未来schema拒绝启动。

execute在BEGIN IMMEDIATE内完成最终身份重检、意图/批准/当前版本检查、更新document、插入唯一publication/receipt、标记applied并COMMIT。不可变意图字段和publication不能被HTTP更新；约束/触发器应阻止服务端错误写入。批准/撤销与执行使用同一数据库事务顺序；撤销先提交则execute拒绝，execute先提交则revoke返回already_applied。

同键并发只能一次发布；不同键同expected_version仅一个成功；持久回执在独立进程重启后仍可读。同事务中途故障全部回滚。COMMIT结果无法确认或提交后响应故障返回503 result_unconfirmed：不删回执、不声称已回滚、不改用新operation_id自动重试；查询原操作确认结果。其他已知提交前数据库故障503 repository_unavailable。

CLI `python app.py --db PATH --port 8042` 仅监听127.0.0.1；db默认lab.db且所有SQLite文件禁止Git跟踪。可选 `--fault-after-commit` 使每个首次execute在真实提交后返回固定503（同键回读/重放仍成功）；可选 `--response-delay-ms` 为0..2000，仅在首次execute提交后等待，供真实HTTP断连验收。无HTTP调试控制面；故障配置不是生产能力。

## 验收边界

原生pytest使用真实SQLite文件、独立连接/进程、受控Clock和数据库故障：权限/owner、精确批准、到期/撤销、hash与参数冲突、同键/不同键并发、事务回滚、COMMIT后确认丢失、重启恢复、等待正文/写锁期间身份失效。敏感值不进入错误或默认请求日志。

独立解包验证必须经过真实HTTP准备→批准→执行→回读和重放，并用数据库计数确认一次publication；故障模式验证503后回读及进程重启，真实客户端在提交后断连再查询。浏览器验证草稿变化使旧批准不可直接执行、身份切换忽略旧响应、待确认保留operation_id、纯文本展示和375px。生产身份、分布式审批、真实模型与外部发布均未实现。
