# 从精选资料进入课程实践

信息流和学习库的精选资料卡保留官方原文入口，另提供“配套课程”链接及该语言的实践状态。阅读原文、进入课时不会自动完成课程或实践；实践状态来自学习者在课程内确认的成功与失败样例。

## 明确维护的关联

| 精选资料                                                                   | 配套课时              | 参考语言           |
| -------------------------------------------------------------------------- | --------------------- | ------------------ |
| [Agents SDK](https://openai.github.io/openai-agents-python/)               | `agent-agent-loop`    | Python             |
| [MCP 入门](https://modelcontextprotocol.io/docs/getting-started/intro)     | `agent-mcp`           | Python             |
| [LangGraph 概览](https://docs.langchain.com/oss/python/langgraph/overview) | `agent-state-machine` | Python             |
| [评测指南](https://platform.openai.com/docs/guides/evals)                  | `agent-datasets`      | Python             |
| [FastAPI 教程](https://fastapi.tiangolo.com/tutorial/)                     | `fullstack-routing`   | Python             |
| [Hono 文档](https://hono.dev/docs/)                                        | `fullstack-routing`   | TypeScript         |
| [Go context](https://go.dev/blog/context)                                  | `fullstack-async`     | Go                 |
| [Jotai 文档](https://jotai.org/docs)                                       | `fullstack-jotai`     | 保留当前服务端偏好 |

以上是教学编辑关联，不代表官方指定学习顺序。Agents SDK 对应执行循环入门；Jotai 是公共 React 前端内容，界面标明“公共前端 · 所选语言服务端参考”。官方页面于 2026-10-04 读取确认主题，匹配仍使用 `backend/feed.py` 中的原始配置 URL，不把重定向目标或相似标题当作同一条资料。

## 链接与学习记录

FastAPI、Hono 和 Go 链接形如 `/lesson/fullstack-routing?language=python`。复制链接、新标签、刷新和浏览器历史导航均采用明确语言；有效全栈入口同步服务端偏好，练习包与 Playground 随之使用该语言。课内切换语言会更新已有 query，保留其他参数、笔记和验收选择。没有 query 时沿用现有偏好。

重复、空值、不支持的语言参数不生效；不存在的课时和 Agent 课不会改写全栈语言偏好。Agent 始终使用 Python；Jotai 不附加语言参数。访问只更新已有学习位置和有效语言选择，不创建完成、实践、笔记、证据或运行记录。

关联保存在前端 `src/lib/feed-course.ts`，不扩展 `FeedItem` 或 `deep-ai-station:v1`。收藏快照恢复后，通过当前课表重新解析入口；来源请求失败仍可用。旧备份缺少资料快照时保持原有提示，不凭收藏 ID 拼造来源。

## 把阅读摘记带进课程

已有配套课程的精选卡片提供“写阅读摘记”。打开原文阅读后，填写“我的理解”和“准备验证的问题”，先预览，再明确追加到本课笔记。摘要保留资料标题、来源链接、课时、参考语言、记录时间及两段个人原文；平台不会替你判断阅读完成或理解正确。

每份草稿固定开始时的来源和参考语言。修改文字后需重新预览；同一草稿只追加一次，点“再写一条”才能开始新的观察。已有笔记完整保留，容量不足时不截断，草稿仍可修改。字段上限分别为1,500和500字符，采用浏览器表单的计数方式，仍受单篇10,000与总计1,000篇笔记限制。导入或清空学习记录会使旧草稿失效，须按当前记录重新填写。

保存后可进入配套课时，用已有步骤、Playground或下载实验验证问题，再补充实际观察；学习库“我的笔记”与原v1备份可回看这些内容。存储失败时会明确提示仅追加到本页内存，应导出学习记录；未保存草稿刷新后丢失。记录不会自动更改课程完成、语言实践、证据卡或运行历史，也不请求模型或复制远端全文。

## 维护与验证

新增关联要同时维护精选资料的 ID、精确原始 HTTPS URL、路线、guide 类型和课时 ID。解析时课时须全局唯一、属于指定路线，语言须在路线中且具有非空参考代码。不满足即不显示课程链接；新闻、未知资料、URL 或路线错配均不根据标题或 tags 推断。

Vitest 覆盖白名单、真实字段错配、缺失课程、语言与实践隔离及旧备份；另以真实 `CURATED` / `TRACKS` 数据运行全部 8 条关联和三种用户语言偏好。headless 流程覆盖键盘进入课程、对应语言下载、Playground、query 历史与手动切换、来源失败、收藏与备份往返；精确结果见[验证记录](verification.md)。这些流程不调用模型或沙箱。
