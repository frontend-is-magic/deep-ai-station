# 我的切分与来源回读记录

只填写实际执行与观察；模板中的固定坐标不是你已完成实验的证明。未运行或未通过请明确记录，不粘贴凭据、环境、原始异常或用户私有文档。

- 源码版本 / 日期 / Python 与 uv 实际版本：
- 冻结安装、Ruff、pytest、CLI 完整命令及退出码：
- contract_version / corpus_id / corpus_version / corpus_revision：
- dataset_id / dataset_version / dataset_revision / splitter_version / scorer_version：
- heading 与 window 实际配置；top_k；比较报告保存位置：
- 两策略的 indexed_chunk_count / indexed_codepoints / retrieved_codepoints_total：
- 每题命中块 ID、来源 ID / URI / source_revision、start/end、byte_start/byte_end、header_path：
- 每题四指标实际值；正例5题与负例1题的分母、summary均值；未命中的证据：
- 选中一个真实命中：完整金标是否由单块包含？quote 回读命令与精确原文，如何确认同来源和同版本：
- Unicode 固定样本：172..178 与 bytes 442..454 是否得到 A😀éZ。？e+U+0301、emoji 与 CRLF 分别如何计数：
- 同一 gold 被重叠块重复命中时，召回、块精度与代码点成本各如何变化：
- 合成冲突两侧是否分别覆盖；只得到一侧为何是1/2；无证据题的不适用指标为何是null：
- 返回不足 top_k 时分母是什么；长标题块与窗口成本不同时能否直接据召回推荐策略：
- 旧 revision / 未知来源 / 越界 / 空区间 / 非法数字 / 重复 flag：完整命令、固定错误码、退出码、无输入回显与 stderr 证据：
- 包复制件的损坏 JSON / hash / Unicode / 版本检查，实际拒绝结果；原始包是否保持不变：
- 改 size / overlap / top_k 后的实际变化；不改金标的前提下，你的观察与下一步：
- 未验证 / 未通过 / 待复查事项；为何六题词法结果不代表生产 RAG、向量召回或模型生成质量：

报告放在被忽略的 `artifacts/`、`reports/` 或 `.data/`；不要修改金标来配合输出。把必要事实摘要写入本课 Python 实践证据卡；填写证据不等于确认课程或语言实践完成。
