# 持久检查点实验

- 遵守 CONTRACT.md；Python 3.12 / uv / SQLite 标准库，Ruff 与 pytest。锁文件冻结，不引入 HTTP、模型、远端访问或任意执行入口。
- 只接受固定 case / node / fault 枚举和严格 CLI；run ID、expected revision、引用与 JSON 独立校验，不回显输入、路径或异常。
- 每节点 checkpoint 与 run revision 同事务提交；竞争立即停止，恢复先验证保存状态。performed 只表示确认提交，不宣称 CPU 零重算或外部 exactly-once。
- 语料和 URL 只作固定公开教学资料；未读不可引用，已知冲突保留两侧，引用存在性不等于语义正确。
- 只有 start 初始化；损坏、未知结构和不兼容版本拒绝，不自动 repair / overwrite。保持 SQLite 自身 hot-journal 恢复与应用写入的区别。
- 故障只在固定提交边界；真实重启、SIGKILL 和并发使用维护者固定测试、自有子进程与有界回收。不得触碰其他进程或数据库。
- .data/、数据库 / sidecars、配置、认证缓存、日志和报告不得入 Git / ZIP；EVIDENCE.md 仅记录实际执行。平台只供下载，修改后的练习在独立环境运行。
