# SSE 课程实验

- 本目录是独立本地实验，后端按 Python/FastAPI、TypeScript/Hono 或 Go/Gin 实现同一 CONTRACT.md；client 使用 React + TypeScript、Tailwind、Jotai、Radix 源码组件和 Prettier。
- 使用冻结依赖；不要删除失败测试、隐去未完成状态或用客户端 abort 替代服务端清理证据。
- 总 deadline 只创建一次；Producer 必须合作取消，每次运行独立，成功/失败/超时/断连均清理一次。记录 EVIDENCE.md 的实际结果。
- 不接入真实模型、外网或凭据，不在平台主机运行用户修改后的代码。敏感配置留在 .gitignore 中；不提交缓存、日志或认证信息。
