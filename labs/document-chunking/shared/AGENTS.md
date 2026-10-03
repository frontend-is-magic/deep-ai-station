# 固定文档切分实验

- 遵守 CONTRACT.md；Python 3.12 标准库，uv 冻结开发依赖，Ruff / pytest。只支持固定只读 chunks / compare / quote，无模型、网络、上传、任意路径或命令执行入口。
- 原文与金标分别来自 corpus.json / cases.json；严格校验 UTF-8、JSON、来源版本及完整资产 pin。错误固定脱敏，不自动修复、不回退为成功空结果。
- 坐标使用零起点半开 Unicode 代码点区间，派生 UTF-8 byte 区间；不 trim、统一换行或归一化 Unicode。块 ID 绑定来源、版本、范围、策略和实际配置。
- 只实现合同内 Markdown 子集；header_path 不改写正文、不参与排名。不声称完整 Markdown、token 预算、向量或模型质量。
- 金标独立于切分和排名；完整包含才覆盖，重叠召回去重，冲突保留两侧；报告真实分母和成本，不为胜负改资料或预填结果。
- 测试使用固定资料和自有临时目录，真实 CLI 校验 JSON / 退出码；独立验收不 import 产品实现作 oracle。无后台服务或浏览器依赖。
- 只记录实际证据。配置、凭据、日志、报告、.data/、数据库及旁路文件不得入 Git / ZIP；平台只提供下载，不执行用户修改的代码。
