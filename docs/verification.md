# 验证记录

本文件记录实际执行证据，未完成事项保留真实状态。

## 2026-10-03 / 免费检索评测工作台

- Playground 新增检索评测，Agent 检索/评测课与全栈 RAG / 单元测试课提供入口。每路线 10 个正例与 2 个无证据例，比较标题与加权词法策略、各自 top-k（1–5）；结果保留标注相关课时、实际排名/评分/匹配词、漏检与误召回，可下载包含配置、数据集版本、语料哈希与运行 ID 的完整 JSON。
- 正例 Precision@k、Recall@k、MRR 按题取宏平均，负例独立计算无结果准确率。默认 k=3 实测：Agent 召回从 0.8 到 0.95，全栈从 0.9 到 0.9667；两路线负例准确率均从 1 降到 0.5，页面不隐藏召回与噪声的取舍。这是固定小型开发集，不证明向量检索或生成回答质量。
- 161 项非浏览器 pytest、30 项 Vitest、Prettier / TypeScript / 构建、Ruff 均通过。后端新增手算指标、严格边界、真实课时归属、默认检索兼容、语料哈希与禁止网络/模型的测试。
- 34 条 headless 用户流程一次全量通过，另新增进行中切路线取消请求的流程单独通过，共 35 条。新增流程实际验证 React → API、配置变化清旧结果、完整 JSON 下载、故障后重试、路线隔离和取消、刷新清报告、375px 不溢出、学习记录不变且无模型/沙箱请求。第一轮定位到下拉框可访问名称不明确，补齐后复验通过。原生 Browser 与远端发布状态以对应提交的后续验收回执为准。

## 2026-10-03 / 独立毕业项目与下载包配置

- 云端支线“Deep AI Research Assistant｜云端毕业项目”已经启动，独立公开仓库为 [deep-ai-research-assistant](https://github.com/frontend-is-magic/deep-ai-research-assistant)。由 Agent 毕业骨架初始化，按 main / develop 开发版本化资料、私有研究记录持久化、报告导出与评测；产品增量仍在开发，不把骨架视为已完成产品。
- 独立仓库初始化验证后端与敏感检查共 43 项，Node 24 前端构建和响应契约 13 项通过；提交 `533291b025a53a703384976b2f3605641399a52d` 的 [develop CI](https://github.com/frontend-is-magic/deep-ai-research-assistant/actions/runs/37129318858) 与 [main CI](https://github.com/frontend-is-magic/deep-ai-research-assistant/actions/runs/37129315632) 均成功。没有调用真实模型、沙箱或使用重置卡。
- 独立 CI 暴露原毕业 ZIP 缺少 Prettier 配置。四份 ZIP 现均包含根 `.prettierrc.json`，原有成员字节保持不变。新增 `scripts/check_starter_archives.py` 在平台目录之外解包，核对实际解析到包内配置并检查四份前端及 TS 后端，已通过 5 组格式检查；临时移除包内配置的回归实验正确拒绝。
- 下载包重建一致性、修改脚本 Ruff、CI YAML 格式与 diff 检查通过。对应平台远端 CI 与发布状态需以该修复提交的回执为准；这次配置打包修复不包含新的交互功能验收。
- 下载包修复提交 `d13d34d87b333b71bd71c960bf2cdaf3e3c229e5` 的 [完整 CI](https://github.com/frontend-is-magic/deep-ai-station/actions/runs/37129446740) 已成功，main 普通快进并完成 Vercel Production deployment `6828812778`。线上四个 ZIP 与该提交逐字节一致，健康接口仍为 48 课，能力仅 demo / DeepSeek，免费 Agent SSE 正常结束；真实 DeepSeek 未启用。

## 2026-10-03 / 毕业实践证据卡

- 六节毕业课提供证据卡，按课程和参考语言分别保存源码版本、实际命令、成功输入/结果、失败输入/结果及未验证事项；Agent 固定 Python。记录由学习者填写，不自动勾选验收，不改变原笔记和完成状态，不上传云端或调用模型。
- 原 version 1 备份增加可选 evidence，旧备份继续可导入。最多24条，拒绝重复课程/语言组合、非法字段、Agent语言错误与超限文本；满额仍可更新已有记录，新增明确提示。Markdown 包含课程/语言/时间及填写内容，动态长度纯文本围栏避免反引号、HTML或Markdown逃逸。
- 新增10项Vitest覆盖旧备份、结构边界、容量、独立更新和实际Markdown渲染；平台30项前端测试、类型、Prettier与生产构建通过。100项后端结果沿用同一未修改后端版本，Ruff全部通过。
- 新增3条headless流程通过，并重跑全部32条平台流程全部通过：Go填写后刷新保留，切Python/另课不串；实际Markdown下载不混入笔记；JSON备份导出、清空测试context后恢复及旧v1导入；三个Agent毕业课固定Python、普通课隐藏、375px不溢出。测试context与浏览器在finally关闭，没有恢复桌面控制。
- 提交前敏感扫描通过131个既有index blob与135个工作区文件及ZIP成员。远端CI与上线状态以对应提交回执为准；本轮内置Browser尚待已有独立对话的具体有界验收安排。

## 2026-10-03 / DeepSeek 兼容协议与敏感文件保护

- 平台与四套毕业骨架的新真实调用统一使用 DeepSeek；保留 OpenAI 兼容的 `messages`、`tool_calls`、`choices` 和平台 SSE 格式，固定 DeepSeek endpoint、服务端 `DEEPSEEK_API_KEY`、默认 `deepseek-flash`，显式关闭 thinking。研究助手采用 JSON mode，并由本地 Pydantic 独立检查结构与已读引用；不发送 beta strict 或未文档化的并行字段。
- 新调用只接受 demo / deepseek。旧 OpenAI / Anthropic 历史仍保留真实来源，恢复后只重跑到已启用的 DeepSeek 或教学演示；官方 OpenAI 课程资料和 RSS 保留。
- 平台 94 项非浏览器 pytest（包含新增 3 项敏感文件检查）、20 项 Vitest、格式、类型、生产构建及 96 份课程 AST / TS 严格类型 / Go 编译检查通过。29 条 headless 流程中，初次有 1 条因历史提示复用样式导致定位歧义；改用课程链接可访问名称后该条复验通过，其余 28 条首次通过。
- 四套 React → 实际 API 演示链路全部通过；测试环境不含供应商密钥，8010 / 5174 自有服务已释放。骨架后端 Python 25 项、TypeScript 19 项、Go 9 组（15 个共享契约案例）、Agent 34 项及共用前端 13 项响应契约测试通过。Go 新增响应头之后读取正文的取消 / 超时测试，分别返回 499 / 504 并关闭连接。
- 根目录与四个下载包的 .gitignore 覆盖敏感环境配置、私钥、认证缓存、日志与本地数据库；空值 .env.example 保持可提交。全局与项目 AGENTS 同步简短规则。`scripts/check_secrets.py` 纳入 CI，检查已跟踪/待提交文件和 ZIP 内部路径与常见凭据特征，只输出文件名和规则，不输出匹配值。当前扫描与 295 个历史 Git 文件对象扫描均未命中常见凭据特征；这不保证识别所有可能的秘密格式。
- 首次远端 CI 捕获 Go 部署课示例的 raw string 字面量转义回归；修正为空格缩进，重新从当前源码导出96份示例并校验。此前本地 Go 检查使用的是修正前导出，不能替代本次重新生成后的结果。
- 四个 ZIP 已重新生成并逐字节校验。真实 DeepSeek / E2B 尚未调用；托管配置与费用上限继续集中在既有独立人工配置对话，未使用重置卡或购买额度。DeepSeek迁移的 [完整 CI](https://github.com/frontend-is-magic/deep-ai-station/actions/runs/37127485098) 已通过，核对提交 a8451e1，包含29条浏览器流程和四条骨架链路；之后暂存区保护提交dc0f430的 [最终完整CI](https://github.com/frontend-is-magic/deep-ai-station/actions/runs/37127785959) 通过，main/develop普通快进到该提交。Production deployment 6828498483成功；主线正常TLS验证线上健康48课、只提供demo/deepseek、四个ZIP与提交逐字节一致、免费Agent SSE结束且无error。真实DeepSeek未启用，没有真实模型调用。

补充：扫描器继续覆盖 Git index blob 与工作区双版本，避免暂存后覆盖/删除工作区导致漏检；输出区分 index/worktree，不读取符号链接目标。9项敏感文件测试使用临时Git仓库验证暂存、ZIP替换、已跟踪却被忽略、工作区新文件和空模板；当前131个index blob、131个工作区文件及ZIP成员通过。完整非浏览器回归现为100项通过。该增强单独提交并验证CI。

## 2026-10-03 / Production 与上一轮 CI 回执

- Agent 毕业骨架提交 `41154ce417fa59cd761a2397f22439ca900ac3e1` 的 [完整 CI](https://github.com/frontend-is-magic/deep-ai-station/actions/runs/37125812326) 已成功，main / develop 已普通快进。
- 用户在独立人工配置对话确认 Vercel 目标空间与 Production 发布授权，`41154ce` 的 GitHub Production deployment `6828137998` 返回 success。主线使用系统 TLS 后台验证 [线上服务](https://deep-ai-station.vercel.app) 健康接口 ok / 48 课，以及四个下载包与该提交逐字节一致。
- 独立人工配置对话已在首个生产版本 `2214c68` 使用 Codex 内置 Browser 完成 48 课、两路线、实际 TypeScript ZIP、免费 Agent SSE、56 条信息流与 6 / 6 来源验收，控制台错误为 0；任务标签关闭并释放控制。该证据对应旧提交，不冒充本次 DeepSeek 改动的内置 Browser 验收。

## 2026-10-03 / Agent 毕业研究助手

- 新增独立 React + FastAPI 研究助手，包含固定资料搜索、批量读取、OpenAI 原生工具调用、已读记录与引用摘录校验；课程页三个 Agent 毕业课提供完整下载入口。资料为三篇维护者课程摘录与两篇无外链的合成冲突练习，运行时不获取官方网页。
- 引用必须来自本次实际读取正文；搜索未读、伪造摘录、缺少引用或遗漏搜索命中冲突组的一侧均被拒绝。机械存在性校验和固定冲突标注不代替语义评测。已修复长空白前缀导致演示错误，以及混合主题查询截断冲突组的问题。
- 共用前端 13 项响应契约测试、格式、严格类型与生产构建通过；增加三种只填输入的示例按钮、实际模型/工具次数、引用摘录、部分用量和冲突无外链展示，拒绝不一致结果进入历史。
- `scripts/run_starter_e2e.py` 已实际通过 React → FastAPI / Hono / Gin / Agent FastAPI 四条链路，包含 Agent 三类案例、原文摘录、来源类型、375px、未授权拒绝且不增历史、刷新清空。全程使用无供应商凭据环境，自有 8010 / 5174 端口确认释放。
- 独立 Agent 后端 33 项 pytest 通过，包含三轮工具协议、逐 HTTP 请求限额、并行/重复 ID 拒绝、部分用量、超量响应、取消及总时限关闭连接；还修复供应商报错与客户端断连同时发生时的任务异常回收，并同步修正 Python 全栈骨架（原 24 项测试继续通过）。
- 本轮平台 94 项非浏览器 pytest、18 项 Vitest、构建与 25 条 headless 用户流程通过；新增验证 Agent 三毕业课下载、独立资料/锁文件与 375px。四个 ZIP 已重新生成并逐字节校验。原生供应商尚未真实调用，内置 Browser 与生产验证状态以人工配置对话的独立证据为准。

## 2026-10-03 / LangChain 官方资讯恢复

- 已核对新的 [LangChain 官方 RSS](https://www.langchain.com/blog/rss.xml)，返回 200 / `application/rss+xml`，现有安全解析器得到 8 条来自 `www.langchain.com` 的带日期文章。服务改用固定新入口及对应官方域名，继续拒绝任意重定向、认证 URL 和伪装域名。
- 通过本地前端同域 `/api/feed?refresh=true` 实际同步得到 56 条内容：8 条精选资料，加 6 个来源各最多 8 条资讯；本次六个来源均为 live。该结果记录当次公开源状态，不代表未来可用性或 Vercel 线上成功。
- 8 项资讯相关测试通过，新增验证 LangChain 规范域名文章可进入 live / cached、非法来源被过滤且缓存命中不重复请求；平台 94 项非浏览器 pytest 全部通过。真实本地 headless 页面显示 56 条内容、6 / 6 可用源和 8 条 LangChain 链接，375px 无横向溢出；测试浏览器已关闭。
- Vercel 连接器发布被自动审批拒绝，理由是目标账户、项目与认证状态未确认；已将目标信息与具体发布授权合并到已有的独立人工配置对话，其他开发继续推进。

## 2026-10-03 / 多语言完整毕业骨架

- 全栈毕业阶段已提供 React + FastAPI / Hono / Gin 三套完整项目 ZIP，含源码、锁文件、固定公开资料、共同契约、测试、AGENTS、.gitignore 和证据模板。`scripts/build_starters.py --check` 验证共享 fixture 与三个下载包逐字节一致；构建只打包固定文件白名单。
- 实际依赖：Node 24 / pnpm 10.32.1、React 19.3.0、Jotai 2.20.3、Tailwind 4.3.3、TypeScript 5.9.3、Vite 7.3.6；Python 3.12 / FastAPI 0.135.4 / httpx 0.28.1；Hono 4.13.12 / @hono/node-server 2.1.3；Go 1.27.1 / Gin 1.12.0。各项目提交对应锁文件。
- 共用前端 `pnpm --dir starters/frontend check` 通过格式、类型和生产构建。Python 骨架 24 项 pytest、TypeScript 骨架 18 项 Node 测试、Go 骨架 8 组测试（含 14 个共同契约子案例）通过；包含无证据、非法输入、访问码、真实请求 mock、限流、截断/过大响应、取消与连接清理，以及 Go panic 的固定错误和请求头脱敏。
- `scripts/run_starter_e2e.py` 实际启动固定维护者代码，headless 验证同一 React 前端分别连接 FastAPI、Hono、Gin。三条链路验证演示引用、无证据不调用模型、真实模式未配置时拒绝、375px 无溢出、刷新清空会话历史。脚本以不含模型密钥的环境运行，结束后已确认自有 8010 / 5174 端口释放；既有 8000 / 5173 预览继续保留。
- 平台 `pnpm check` 通过 18 项 Vitest、类型与生产构建；93 项非浏览器 pytest、Ruff 格式和静态检查通过。24 条 headless 用户流程全部通过，新增按所选语言下载实际 ZIP 并检查后端入口、锁文件与无开发产物。
- GitHub CI 已加入三个骨架的冻结安装、各自校验、ZIP 一致性与 React → API 验证。平台 Vitest 限定 `tests/`；骨架 Node 测试由独立任务运行，避免误将 Node 测试或编译产物作为 Vitest 测试。
- [完整远端 CI](https://github.com/frontend-is-magic/deep-ai-station/actions/runs/37123126587) 已通过，核对提交 `718867e`，main 与 develop 已普通快进到该毕业骨架版本。
- Vercel Python Function 排除骨架源码与开发依赖，下载 ZIP 随静态前端产物发布。真实 OpenAI 调用仅以 mock 验证；账号、上传、数据库、跨实例预算与生产部署仍是毕业实践任务。当前未产生模型或沙箱费用，未使用重置卡。
- 本轮使用 headless 验证，没有恢复已释放的桌面控制。Vercel 发布、真实供应商和真实 E2B 验收仍等待已有独立人工配置对话；下方内置 Browser 核心验收证据保持原状态。

## 2026-10-03 / 有界课程 Agent

- `pnpm check` 通过：18 项 Vitest、TypeScript 与生产构建；Playground 继续独立懒加载。
- Ruff 格式与静态检查通过；pytest 非浏览器测试 93 项全部通过。
- 原生协议覆盖 OpenAI / DeepSeek 参数分段与 Anthropic 内容块和工具结果；三家供应商均以 MockTransport 验证检索 → 阅读 → 最终回答的完整循环。并行调用、身份改变、深层/非法/过大 JSON、未闭合工具块和截断参数不成为可执行调用。
- Agent 工具只读取当前路线课程。未知名称、额外参数、跨路线课时拒绝操作；重复参数复用结果。每个模型请求独立计入共用限流，第三轮禁用工具；取消和整体超时关闭连接。
- headless Playwright 23 条流程全部通过。新增演示循环、真实模式 UI 完成/失败契约，覆盖轨迹按 ID 更新、历史中的工作流/轮次/工具次数/部分用量恢复、访问码刷新清空与 375px 布局。真实模式使用浏览器 mock，没有调用供应商或产生模型费用。
- 本轮页面使用 headless 验证；下方 Codex 内置 Browser 的既有核心验收记录保留，未恢复已释放的桌面控制。
- Vercel 发布、真实模型和真实 E2B 验收仍待独立人工配置对话中的托管配置；未购买额度或使用重置卡。

## 2026-10-03 / 初始版本

- 前端 TypeScript 检查与 Vite 生产构建通过。
- pytest：61 项接口、适配器与隔离边界测试通过，包含课程契约、96 份练习包下载、非法输入、异常访问码、资讯链接过滤、缓存失效与冷却恢复、分块请求体限制、资讯标识稳定、检索证据、原生流式首段、超时/取消/输出上限/清理与错误脱敏，以及本课目标绑定与跨路线拒绝。
- Vitest：17 项测试通过，包含 SSE 拆包、解析异常释放流、学习记录导入边界、课程历史向后兼容、资讯快照与不安全链接拒绝，以及代码编辑上下文隔离、空编辑和最近 20 组容量边界。
- Codex 内置 Browser：已验证首页、第一课测验、验收勾选、完成标记、笔记以及刷新恢复。
- 真实模型调用尚未配置；供应商适配器使用 MockTransport 验证，不代表真实 API 已调用。
- headless Playwright：20 条用户流程全部通过，覆盖完成/笔记刷新恢复、课内三语言切换并保留验收选择、练习包按语言下载、本课导师与笔记追加/容量保护/课程历史恢复、工作流/取消/历史、真实模式 UI 完成/失败/截断历史边界与回看供应商信息、三语言检查、收藏/搜索、实时资料收藏在订阅失效后恢复、实际来源数量与缓存失效提示、375px 移动布局、导入拒绝与导出、隔离 UI 的访问码与纯文本输出、模式/语言/页面导航后的编辑保留、代码下载内容/扩展名、空编辑与旧检查结果清除、语言偏好和存储额度异常。
- GitHub 此前完整 CI 已通过：[本课导师与全部课程验证记录](https://github.com/frontend-is-magic/deep-ai-station/actions/runs/37119074900)。该记录包含代码编辑保留迭代前的 61 项后端、15 项前端、19 条 headless 流程和课程语法/类型/编译检查；main 已承接此版本。新增行为先做本地验收，远端状态以对应提交的运行记录为准。
- Vercel 未发布：CLI 缺少认证，连接器部署工具不可用。已结束登录等待，并拆到独立人工配置对话；不声称线上成功。
- [Notion 私人项目目录](https://app.notion.com/p/3eee149ae540815ca676f1dbaed0128a?pvs=204) 已创建；开发手册、学习 Wiki 和验收记录全部回读验证，包含目录规则、自绘图标与封面。

CI 现在包含 headless Playwright。`scripts/run_e2e.py` 在本地实际启动前后端、执行测试，结束后清理自有进程；已核实 8000 与 5173 端口释放。

96 份逐课示例完成 48 份 Python AST、24 份 TypeScript 严格类型诊断和 24 份 Go parser / 编译校验，包含锁定的 Hono 与 Gin 依赖；这些检查不执行参考程序或测试。隔离运行用 fake SDK / 浏览器 mock 验证，未创建真实 E2B 实例，未构建收费 Go 模板；模板配方已准备，实际服务仍待托管配置。

Go 格式化器的制表符曾触发嵌入 Python 参考字符串的 Ruff E101（`0a13a6d`）；`1b869bf` 将例外限定为该资料模块，保留可执行 Python 的常规检查，远端 CI 已重新通过。

内置 Browser 的任务标签已关闭，临时视口已重置，控制会话已释放。全局 AGENTS.md 已精简到 33 行，并保留完整控制边界引用及旧规则备份。

OpenAI / Anthropic / DeepSeek 原生 SSE 适配已用 MockTransport 与暂停远端流验证；首段文本在终止帧前送达，取消与 45 秒总超时关闭连接，非法帧/流缺失终止/输出超限不发完成事件。Anthropic 用量采用累计最新值，缺失用量不当作零。模型输出上限截断在界面明确提示并不写历史。真实供应商调用仍未配置，以上不代表真实服务成功。

信息流服务函数实际联网同步得到 48 条内容，6 个来源中 5 个 live；LangChain 旧 RSS 301 到新站，后续目标继续重定向到博客 HTML，不当作有效 RSS。Hugging Face 官方公开 RSS 返回 200 并解析成功。此结果是本地公开来源验证，不能代替 Vercel 线上链路验收。

公开课程资料链接检查覆盖 46 个去重 URL：43 个初次返回 200，一个 OpenAI 旧安全文档路径返回 404，两个旧 API Reference 入口返回 403。已根据当前官方文档更新这三个入口，回读均为 200；没有把旧入口的 403 解读为供应商 API 不可用。

模型完成/失败/截断的三个现有 headless 流程同时验证 Markdown：有序列表为 decimal，无序列表为 disc，原始 script 不生成元素，图片不发外部请求，javascript 与带认证信息的链接降级为文字，合法官方 HTTPS 链接保留。
