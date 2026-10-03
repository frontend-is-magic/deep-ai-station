# 我的 MCP 只读实验记录

由学习者填写真实结果。固定案例中的预期拒绝不等于工具成功；下载和模板本身不证明验收完成。只填写公开教学内容，不粘贴环境、凭据或未经检查的原始异常。

- 源码版本 / 日期 / Python、uv、mcp 实际版本：
- 冻结安装、Ruff、pytest 与 CLI 完整命令及退出码：
- 报告 `contract_version` / `lesson_id` / `protocol_version` / `case` / `model_calls` / `passed`：
- normal：实际发现方法、工具 schema、三个资源 URI、命中结果、空结果及资源文本核对：
- errors：每个步骤的 `step` / `method` / `request_id` / `outcome`；工具结果 `isError` 与固定错误内容，JSON-RPC `error.code` / `error.message`：
- timeout：哪个请求本地超时、`cancellation_sent[].request_id` 与实际在途请求的对应关系、`request_cleanup` 次数、同连接后续请求结果：
- disconnect：实际 `transport_closed` 步骤、请求与 lifespan 清理事件；原始协议测试在客户端 stdin 保持开放时是否收到终止错误帧、何时观察 EOF，以及 wait/reap 结果（固定进程退出不代表 transport 优雅关闭）：
- 每个 run：实际 `protocol_version`、`pid`、`server_cleanup_events` 的事件与 PID、`transport_closed` / `transport_close_evidence`、`child_exited`，以及最终确认子进程不存在的依据：
- 拒绝/超时步骤为何仍可使固定案例通过；任何 `passed=false` 或非零退出的原因与复查结果：
- 本次修改后额外验证的行为，及恢复原实现后的结果：
- 未验证：生产认证、远端服务、HTTP、模型、写操作、其他平台/SDK版本、完整协议合规等：

原始报告建议留在被忽略的 `artifacts/` 中。取消通知表示请求停止，清理事件表示回调实际发生，`child_exited` 表示子进程退出；三者不能互相代替。没有实际证据的字段填写“未验证”，不要根据预期补齐。
