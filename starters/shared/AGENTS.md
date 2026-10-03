# AI 知识工作台

- 这是维护者提供的可运行学习骨架，默认演示模式；用户修改的代码只在独立受控开发环境或隔离沙箱中运行。
- 前端：React + TypeScript、Tailwind、Jotai、Radix 源码组件、Prettier；服务端沿用当前选定的 FastAPI / Hono / Gin。
- 使用冻结依赖与现有测试，保留成功、失败、取消和无证据场景。
- 实际模型使用 DeepSeek 的 OpenAI 兼容协议；服务端配置 `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL`，默认 `deepseek-flash`，固定官方 endpoint。
- main 为稳定分支，develop 为开发分支；中文前缀提交，禁止强推 main。
- 供应商密钥只放服务端托管 secrets，不放 VITE_ 变量、源码、聊天、导出数据或用户沙箱。
- 用户代码不得由 API 执行。工具、上传、数据库和账号需要独立权限契约后再接入。
- 发布使用 Vercel；以 Codex 内置 Browser 验收浏览器到 API 链路，用户活动优先。
- 运行、接口、边界和待实现内容见 README.md；验收证据记录在 EVIDENCE.md，不把 mock 当成真实服务。
