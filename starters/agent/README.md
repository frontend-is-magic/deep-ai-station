# Agent 研究助手毕业项目

React + TypeScript 前端连接 Python / FastAPI 后端。默认用固定本地资料演示「计划 → 检索 → 批量读取 → 整理 → 引用校验」；真实模式使用服务端 DeepSeek API，沿用 OpenAI 兼容的 messages / tool_calls 输入输出格式。真实模型决策由供应商返回，演示顺序则是预设的。

## 本地运行

需要 Node 24、pnpm 10.32.1、Python 3.12 和 uv。下载包包含 frontend/、backend/、锁文件、AGENTS.md 和 EVIDENCE.md。

在 backend/：

```sh
uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run uvicorn app:app --host 127.0.0.1 --port 8010
```

另开终端，在 frontend/：

```sh
pnpm install --frozen-lockfile
pnpm check
node --test src/response.test.mjs
pnpm dev
```

访问 http://127.0.0.1:5174。前端将同域 /api 转发到 8010；先确认端口空闲，保留其他服务。演示无需密钥。页面最多保留本次会话的 20 条完整结果，刷新清空；访问码只在内存中。

## 三个练习案例

| 输入           | 应观察的结果                                              |
| -------------- | --------------------------------------------------------- |
| API 超时       | 读取课程摘录，返回来源与原文引用；演示 0 次模型、2 次工具 |
| zzzz unmatched | 无匹配证据；演示 0 次模型、1 次工具                       |
| 取消计费冲突   | 同时读取两侧合成资料，保留冲突；演示 0 次模型、2 次工具   |

`eval-cases.json` 保存这三种可复现案例。`documents.json` 的三篇课程资料由维护者编写，链接仅供官方延伸阅读，运行时未获取这些网页。两篇取消费用资料是故意相互矛盾的合成练习，明确标为 conflict-fixture，无外链，不能据此判断真实计费规则。

## 工具与引用约束

只有 `document_search(query)` 和 `document_read(document_ids)` 两个只读工具。搜索只返回 ID 和标题，不构成已读证据；读取只能使用本次搜索得到的 1–3 个固定 ID。没有联网浏览、任意文件路径、代码执行或写入工具。

服务器维护每次运行的已读集合。最终每条引用必须来自已读 ID，quote 必须是对应正文的连续摘录，URL 由服务器固定映射。已读却不提供引用、搜索后未读就引用、捏造摘录，以及遗漏搜索命中冲突组的一侧，都会拒绝作为完整结果。搜索命中的冲突组由语料元数据标注，不是通用语义冲突检测。引用存在性校验不能证明摘要中的每项结论正确，仍应人工评估回答是否得到资料支持。

最终响应增加 `workflow`、`outcome`、`model_calls`、`tool_calls`、`usage_complete`、`trace` 和 `citations`。轨迹在运行完成后返回，只含操作摘要，不展示模型内部推理，也不是实时事件流。没有已读证据时返回固定的证据不足说明；真实模式可能已发生模型调用，不能将这种结果解释为免费。

## 真实模式和预算

只在服务端托管环境设置 `DEEPSEEK_API_KEY`、`PLAYGROUND_ACCESS_TOKEN`，可选 `DEEPSEEK_MODEL`（默认 deepseek-flash）；固定请求 `https://api.deepseek.com/chat/completions`，程序不自动加载 .env。先在供应商控制台设置费用上限，再显式选择真实模式并输入访问码。不要将任何密钥放进聊天、源码、VITE_ 变量或学习者沙箱。

DeepSeek 请求显式关闭 thinking，使用 `response_format: {"type":"json_object"}` 和包含字段格式的提示词；服务端独立执行 Pydantic 结构与引用校验。不依赖 beta strict 模式。

每次运行最多 3 次模型请求、2 次工具请求；第三轮禁止工具。整个运行最多 20 秒，每轮最多 800 个 completion tokens，响应不超过 1 MB，工具参数不超过 4 KiB，回答最多 20,000 字符。每实例每分钟最多 10 次实际模型 HTTP 请求，多轮分别计数。用量只累加供应商报告的已知整数；任一轮缺字段则标记部分用量，不估算费用。实例内限流不能替代多实例共享预算。

拒绝并行工具、重复调用 ID、末轮继续请求工具和截断的最终回答。非法工具或小型非法参数返回固定错误观察，模型可在剩余预算内恢复；过量参数直接终止。取消会取消上游任务并关闭连接，但供应商可能已经计费。所有供应商测试使用 MockTransport，真实模型、真实断连计费与生产部署尚未验证。

## 后续毕业任务

按 Agent 路线继续做语义评测、资料更新策略、可恢复任务状态、用户身份、持久化、共享预算、Vercel 发布和回滚。修改学习者代码时使用独立受控开发环境或隔离沙箱，平台 API 不执行学习者代码。将版本、命令、实际结果与未验证部分填入 EVIDENCE.md。当前骨架不包含账号、数据库、任意网页研究或生产级评测系统。

协议依据：[DeepSeek Tool Calls](https://api-docs.deepseek.com/guides/tool_calls/)、[DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)。平台仓库的 `scripts/build_starters.py --check` 校验 ZIP 与源码一致，`scripts/run_starter_e2e.py` 用 headless 浏览器验证四个骨架的实际 API 链路；Codex 内置 Browser 验收需遵守用户控制优先规则。
