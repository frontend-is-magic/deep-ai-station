# 跟读云端毕业项目

[Deep AI Research Assistant](https://github.com/frontend-is-magic/deep-ai-research-assistant/tree/develop) 是由 Agent 研究助手骨架展开的独立毕业项目。它在云端开发任务中持续迭代，代码同步到公开 GitHub；教程的三节 Agent 毕业课提供源码、验收记录和开发进展入口。开发在云端进行，不代表应用已完成线上发布。

## 从骨架到完整项目

| 学习入口                        | 适合练习                                                         |
| ------------------------------- | ---------------------------------------------------------------- |
| 课程页下载的 Agent 研究助手 ZIP | 固定资料、只读工具、有界循环、引用校验和固定评测；独立运行演示   |
| 独立仓库 develop                | 资料版本、用户范围的研究记录、持久化、报告导出、跨实例执行与取消 |
| 对应提交的 EVIDENCE 与 CI       | 区分实现、自测、独立复验、模拟供应商与真实服务的证据             |

独立项目沿用 React + TypeScript 与 Python / FastAPI。全栈课的 TypeScript / Hono、Go / Gin 骨架仍是各自的语言练习，不会因查看此示例自动记录为已实践。

## 建议跟读顺序

1. 从 [README](https://github.com/frontend-is-magic/deep-ai-research-assistant/blob/develop/README.md) 运行免费演示，保存实际 checkout 的 SHA。先完成固定资料检索、引用和无证据场景。
2. 对照 [工作台边界](https://github.com/frontend-is-magic/deep-ai-research-assistant/blob/develop/docs/WORKSPACE.md)，追踪同一研究从创建、执行到报告导出的数据。用两组测试身份检查跨用户读取、取消、删除和导出拒绝。服务端身份映射用于受控工作台；公开账户注册、密码找回和完整身份提供商集成需另行设计。
3. 阅读 [持久化 ADR](https://github.com/frontend-is-magic/deep-ai-research-assistant/blob/develop/docs/ADR-002-vercel-persistence.md)，比较本地 SQLite 与托管 PostgreSQL 的职责。连接配置只放服务端托管 secrets，资料或笔记不能提供执行权限。
4. 按 [EVIDENCE](https://github.com/frontend-is-magic/deep-ai-research-assistant/blob/develop/EVIDENCE.md) 的版本和命令重跑测试，记录成功、失败与未验证项。重点重现重启、用户切换、取消和客户端断开，不能只验证最终正常报告。
5. 在课程证据卡中填写自己的 SHA、命令及实际结果。示例仓库的 CI 结果不会证明自己的修改已经通过，也不会自动标记课时完成。

## 一个真实的修复案例

提交 [3e1da91](https://github.com/frontend-is-magic/deep-ai-research-assistant/commit/3e1da918caa9ebf503b5946e0e421aaf0957e14d) 修复了取消竞态：第二个 app 已经确认取消，第一轮模型响应随后返回，旧 worker 却继续执行后两轮。仅定时查询取消状态，无法阻止下一轮在查询间隙开始。

修复把持久化检查点与每次模型/工具操作的准入检查结合。已返回的用量保存后停止后续步骤；取消前已准入或在途的请求仍可能收费，响应未返回时用量标记不完整。不能把“状态显示 cancelled”或“前端停止等待”当作没有后续请求的证据。

主线对该精确提交用独立 checkout 和原复现步骤验证：取消 ACK 后0个新请求，保留第一轮实际模拟用量11，model_calls=1、tool_calls=0，重读数据库一致。原始 ASGI 断开与 handler task 取消保留先前已知用量、轨迹和未知在途用量；五种跨用户访问均404。这里全部使用 MockTransport，真实模型调用为0。[对应 CI](https://github.com/frontend-is-magic/deep-ai-research-assistant/actions/runs/37135580210) 成功，包含实际 PostgreSQL 回归；生产和真实模型需查看后续独立验收记录。

## 先做本地同域预检

云端分支现提供固定 Vercel CLI 62.2.0 的 `node scripts/preflight_vercel.mjs`。脚本在独立临时副本和空 CLI 配置中运行 `vercel dev -L`，不登录或部署。它检查首页/静态资源、FastAPI健康、零模型演示，以及缺身份、模型配置、耐久数据库时的拒绝；清理后重新绑定6个自有监听端口确认释放。进程清理使用Linux `/proc`，应在云端任务或对应Linux CI环境复验，不能直接当作macOS通用脚本。

最终提交 [1f7e28f](https://github.com/frontend-is-magic/deep-ai-research-assistant/commit/1f7e28f4be9da15e1c5fd2f624c6ca1a79eaa1ac) 的 [完整CI](https://github.com/frontend-is-magic/deep-ai-research-assistant/actions/runs/37138680836) 成功。前置目录/TLS和首次CI安装路径失败及修正保留在EVIDENCE中，没有关闭TLS验证。这个结果证明本地CLI组合可以工作；Vercel平台发布、Python云端断连与真实模型仍需各自验收。

## 交付与边界

[开发 PR](https://github.com/frontend-is-magic/deep-ai-research-assistant/pull/1) 与 [Actions](https://github.com/frontend-is-magic/deep-ai-research-assistant/actions) 反映后续进展。main 保留稳定版本，develop 持续迭代；这份跟读文档不把某次历史 CI 解释为最新提交或生产环境已验证。

真实调用仍使用 DeepSeek 的 OpenAI 兼容接口。供应商密钥、身份访问配置和数据库连接串只能放服务端 secrets；不要填进课程笔记、证据卡、导出记录或聊天。需人工配置的入口统一放在 [HUMAN_ACTIONS](https://github.com/frontend-is-magic/deep-ai-research-assistant/blob/develop/HUMAN_ACTIONS.md)，由已有人工配置对话集中处理。用户修改的代码只在独立受控开发环境或隔离沙箱执行。
