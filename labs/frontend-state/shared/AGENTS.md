# frontend-state 实验

- 本包是维护者固定 React / TypeScript 教学程序；不要在平台主机执行学员提交的修改。
- Node 24、pnpm 10.32.1，使用 `pnpm install --frozen-lockfile` 与 `pnpm check`。
- 事实只写 `frontend-state-lab:v1`，精确 version / completedIds / favoriteIds；不要写平台学习记录、凭据、派生值或筛选。
- LessonCard 使用 Props 与事件回调；父组件处理明确的 boolean 意图，统计和可见列表通过真实 Jotai 派生 atom 计算。
- 不添加后端、业务网络、模型、沙箱、导入导出或跨标签同步。未实现的延伸练习应明确标识，不能声称已运行或通过。
- 本地服务仅绑定 127.0.0.1，端口须自有且可回收。用户操作电脑时不得抢焦点；自动测试使用 headless，不启动原生浏览器。
- 不在源码或证据中记录真实身份、密钥或其他敏感数据。只回收自己启动的进程，不 broad kill。
