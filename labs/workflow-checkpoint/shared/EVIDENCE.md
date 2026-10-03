# 我的检查点与重启恢复记录

只填写实际观察；不把模板、下载或预设输出当作验收结果。不得粘贴凭据、环境或未经检查的原始异常。

- 源码版本 / 日期 / Python、uv、SQLite 实际版本：
- 冻结安装、Ruff、pytest、CLI 完整命令与真实退出码：
- contract_version / workflow_version / corpus_revision / corpus_sha256 / schema user_version：
- run_id / case_id；normal、empty、conflict 的最终 outcome、已读 ID、引用及冲突两侧：
- 每条命令的 PID、expected revision、前后 revision / phase / next node、performed / reused：
- 三个节点 before-commit 与 after-commit：故障事件、退出码、新进程 PID、inspect / 独立 SQL 的行数、哈希链和恢复结果：
- pause-before-commit：实际事件；是2秒后自退72，还是父进程发出真实 SIGKILL？后者记录自有 PID、实际信号退出与新进程数据库证据：
- 两个真实进程同 revision 竞争：各自 PID / 完整结果、唯一提交与冲突证据；锁等待失败另行记录：
- 完成后同 revision 恢复的 result 是否一致、有无额外 checkpoint；旧 revision 是否拒绝：
- 未知 run、损坏 JSON / 数据库、错误语料 / workflow / schema 版本：固定错误、原状态保留与无输入回显证据：
- 无证据与合成冲突为何仍可完成；引用存在性为什么不等于语义正确：
- performed 为什么只代表本次确认提交；CPU 重算、未知提交和外部副作用分别有什么边界：
- 未验证 / 未通过 / 待复查事项（包括生产、分布式、模型、真实断电与硬件故障）：

报告与数据库放在被忽略的 artifacts/ 或 .data/。SQLite hot-journal 恢复与应用改写记录分开说明。没有实际执行的检查写“未验证”，不要补填预期成功。
