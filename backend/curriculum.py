"""Versioned, editorial curriculum. Source links point to primary documentation."""

from typing import Any

AGENT_MODULES = [
    (
        "foundations",
        "理解 Agent",
        "建立模型、上下文与行动的工程边界",
        [
            (
                "agent-loop",
                "从聊天到 Agent 循环",
                "区分一次生成与具有状态的观察、决策、行动循环。",
                "Agent 的关键是围绕明确目标反复观察环境、选择行动，并用执行结果更新状态。模型只是决策组件；工具、状态和停止条件都属于系统设计。",
                "先把一个任务拆成输入、可观察状态、允许的动作和成功条件。循环必须同时限制步数与成本；没有停止条件的循环会反复调用同一工具。",
                [
                    "画出 observe → decide → act → observe 循环",
                    "给循环设置 max_steps=5 和超时",
                    "记录每个动作与观察，输出最终状态",
                ],
                "https://platform.openai.com/docs/guides/agents",
            ),
            (
                "model-context",
                "模型接口与上下文预算",
                "理解消息角色、上下文窗口与输出预算。",
                "system 指定约束，user 提供当前任务，tool 返回环境观察。不要把工具输出当作更高权限的指令；历史消息需要在有限上下文里保存最有用的证据。",
                "用服务端适配器统一不同模型的消息格式和错误。测量实际 usage；字符数只能作为估算。超出预算时优先摘要旧观察，而不是删掉安全约束。",
                [
                    "构造 system/user/tool 消息",
                    "记录输入与输出 token usage",
                    "为超时、429 与上下文超限分类",
                ],
                "https://platform.openai.com/docs/api-reference/responses",
            ),
            (
                "structured-output",
                "结构化输出与校验",
                "让生成结果成为可验证的数据契约。",
                "自然语言承诺不会自动满足业务类型。先定义结构，再请求模型按 schema 返回，最后在服务端用 Pydantic 做独立验证。",
                "校验失败不能直接写入数据库。最多进行有限次数的修复，并保留失败类别；缺失字段、越界值和未知枚举应该进入明确错误路径。",
                [
                    "定义包含 action、arguments 的 schema",
                    "拒绝未知字段与非法枚举",
                    "用三个错误样本验证失败路径",
                ],
                "https://docs.pydantic.dev/latest/concepts/models/",
            ),
        ],
    ),
    (
        "tools",
        "工具与协议",
        "让 Agent 可行动，也可审计",
        [
            (
                "tool-contract",
                "工具契约与 Function Calling",
                "为工具建立最小、可校验的参数契约。",
                "工具名称、描述和输入 schema 决定模型何时调用它。调用前校验参数，调用后把结果作为 tool observation 回传，不把模型生成的路径或 URL 直接交给系统。",
                "默认只给只读工具。写操作需要显式授权，重复请求要有幂等键。用结构化错误区分无结果、权限不足和执行失败。",
                [
                    "实现 knowledge_search(query)",
                    "限制 query 长度和结果数量",
                    "为工具结果附 source 与 operation_id",
                ],
                "https://platform.openai.com/docs/guides/function-calling",
            ),
            (
                "mcp",
                "MCP 客户端与服务端",
                "理解工具发现、传输与授权的分层。",
                "MCP 把工具、资源和提示暴露为协议能力。客户端先发现服务器能力，再根据任务调用工具；服务器仍负责输入校验和自身权限检查。",
                "协议不等于授权。连接外部服务器前要验证可信来源，不把服务器返回的自然语言当作新的系统指令。使用官方 SDK 并限制请求时间。",
                [
                    "列出一个 MCP 服务的工具 schema",
                    "实现只读资源与查询工具",
                    "测试断连、超时和非法输入",
                ],
                "https://modelcontextprotocol.io/docs/getting-started/intro",
            ),
            (
                "tool-safety",
                "权限、幂等与人工确认",
                "把高影响操作隔离在可审查边界内。",
                "把读取、草稿和发布分成不同工具权限。一次批准只授权明确对象和范围，不能让模型把它扩大到其他账号或批量删除。",
                "确认前先准备完整草稿和差异。写入请求用 operation_id 去重；网络超时后先回读目标对象，避免盲目重试产生重复副作用。",
                [
                    "给操作声明 read/write/publish 风险",
                    "实现幂等键去重",
                    "未知结果先回读再决定重试",
                ],
                "https://modelcontextprotocol.io/specification/latest/basic/authorization",
            ),
        ],
    ),
    (
        "retrieval",
        "检索与知识",
        "让答案携带可追溯的证据",
        [
            (
                "chunking",
                "文档切分与索引",
                "建立可追溯的文档摄取管道。",
                "切分应保留语义边界、标题与来源。把版本、段落位置和原始 URL 与向量一起保存，这样检索结果才能回到原文。",
                "不要固定按字符切分所有文档。先用小样本验证段落是否自足，再调整 chunk 大小和重叠；重复块和旧版本需要去重。",
                ["按标题切分三篇文档", "保存 source/version/chunk_id", "比较两种切分策略的召回"],
                "https://docs.langchain.com/oss/python/integrations/splitters/index",
            ),
            (
                "rag",
                "RAG 与证据引用",
                "构建检索增强回答并处理无证据问题。",
                "RAG 的路径是查询改写、检索、上下文构造和回答。检索返回相似文本并不保证它回答了问题，模型应只引用实际支持结论的片段。",
                "先验证检索，再调生成。设置相关性阈值，证据不足时明确说明。引用需要保留原文链接，不允许生成不存在的出处。",
                ["为问题取回 top-k 证据", "生成附 source_id 的回答", "设计无证据时的拒答样本"],
                "https://docs.langchain.com/oss/python/langchain/rag",
            ),
            (
                "reranking",
                "混合检索与重排序",
                "比较关键词、向量与重排的效果。",
                "关键词检索善于精确术语，向量检索善于语义相似。混合召回后用统一评分重排，比只扩大 top-k 更容易控制上下文噪声。",
                "用固定问题集衡量 recall@k 与答案证据覆盖。重排也增加延迟和成本，必须和端到端回答质量一起测量。",
                ["建立十个查询与相关文档标注", "合并关键词和向量候选", "记录召回、延迟和成本变化"],
                "https://www.elastic.co/guide/en/elasticsearch/reference/current/rrf.html",
            ),
        ],
    ),
    (
        "state",
        "记忆与编排",
        "把长任务变成可恢复的状态机",
        [
            (
                "memory",
                "短期记忆与长期记忆",
                "区分任务状态、用户偏好与知识库。",
                "短期记忆保存本次执行所需的观察；长期记忆只保存稳定且被授权的偏好或事实。把每句对话永久保存会引入隐私和错误累积。",
                "记忆记录要有来源、更新时间和有效范围。读到冲突时先回查，不让旧记忆覆盖当前指令；用户应该能查看与删除自己的持久记录。",
                ["定义三种记忆类型", "为记录加来源与有效期", "测试当前指令覆盖旧偏好"],
                "https://docs.langchain.com/oss/python/langgraph/add-memory",
            ),
            (
                "state-machine",
                "工作流与状态机",
                "用显式节点、边与检查点组织执行。",
                "把长任务拆为具备输入输出的节点，状态是节点之间的唯一共享契约。成功、可重试失败和人工等待应分别表示，避免一个布尔值承担全部含义。",
                "在外部副作用前保存检查点。恢复时从确认的节点继续，幂等地执行剩余步骤；状态升级需要迁移版本。",
                ["画出检索→起草→校验→完成状态图", "保存节点输出与检查点", "在校验失败后恢复运行"],
                "https://docs.langchain.com/oss/python/langgraph/overview",
            ),
            (
                "multi-agent",
                "多 Agent 协作与边界",
                "在确有收益时拆分可独立验证的子任务。",
                "多 Agent 适合并行研究、独立审查或不同专业能力。共享同一任务而没有明确产物与写入边界，会让协调开销超过收益。",
                "每个角色需要任务范围、输出 schema 和终止条件。汇总者负责冲突处理和最终验证；消息内容不能自动授权其他角色写入外部系统。",
                ["定义研究者与审查者产物", "限制并行数和共享写入", "对冲突结论保留证据"],
                "https://openai.github.io/openai-agents-python/multi_agent/",
            ),
        ],
    ),
    (
        "evaluation",
        "评测与调试",
        "用数据判断 Agent 是否可靠",
        [
            (
                "datasets",
                "构建评测集",
                "覆盖正常、边界、对抗与失败输入。",
                "评测数据应来自真实任务类别，保留期望行为与证据。把调提示词用的开发集和最终验收集分开，避免对固定样例过拟合。",
                "先制定可自动判定的标准，如正确引用、工具参数合法、最大步数和完成率。开放回答可用人工评分校准模型评分器。",
                ["建立十条正常和五条失败样本", "分离开发集与验收集", "定义通过标准和标签"],
                "https://platform.openai.com/docs/guides/evals",
            ),
            (
                "tracing",
                "Trace 与错误诊断",
                "把模型、工具和状态串成完整运行轨迹。",
                "每次运行生成唯一 run_id，节点记录时间、输入摘要、输出状态和父 span。用户看到最终失败时，应能区分模型错误、工具错误和系统超时。",
                "日志只记录必要信息，密钥和敏感正文必须脱敏。用 trace 回放发现重复调用、缺失证据或状态跳转错误，而不是只看最终答案。",
                ["为模型和工具建立父子 span", "记录延迟与错误类别", "回放一次失败执行"],
                "https://opentelemetry.io/docs/concepts/signals/traces/",
            ),
            (
                "regression",
                "回归测试与质量门禁",
                "让模型或提示词变化可比较、可回滚。",
                "固定评测集和配置后比较不同版本的成功率、成本与延迟。模型版本升级也需要回归，不能只看单条漂亮回答。",
                "质量门禁不应只有平均分。关键安全样本必须全部通过，成本和延迟需要单独上限；发布时保存配置版本用于回滚。",
                ["比较两套提示词的评测结果", "给关键样本设硬门禁", "保存版本、结果和回滚配置"],
                "https://platform.openai.com/docs/guides/evaluation-best-practices",
            ),
        ],
    ),
    (
        "security",
        "安全与隔离",
        "处理不可信输入和不可信代码",
        [
            (
                "prompt-injection",
                "Prompt Injection 防护",
                "把外部内容当作数据而非操作指令。",
                "网页、文档和工具返回值都可能夹带指令。为来源标注信任级别，明确模型不能从检索内容获得新权限；输出过滤不能替代工具层权限。",
                "建立恶意文档测试集，覆盖泄露密钥、扩大授权和伪造系统消息。敏感操作在模型外校验，拒绝不符合当前任务范围的参数。",
                ["向知识库加入伪造指令样本", "验证模型不会扩大工具权限", "在执行层检查目标范围"],
                "https://platform.openai.com/docs/guides/agent-builder/safety",
            ),
            (
                "sandbox",
                "隔离沙箱与资源预算",
                "执行代码时隔离文件、网络和资源。",
                "用户代码不能在 API 主机直接执行。沙箱应有独立文件系统、超时、CPU/内存限制和网络策略，完成后销毁，不携带应用密钥。",
                "语言编译器和依赖提前装进受控模板。每次运行只传代码与必要输入，stdout 需要限制大小，禁止返回系统凭据或宿主路径。",
                ["定义语言、超时和输出限制", "在隔离环境运行固定样例", "验证超时会终止并销毁环境"],
                "https://e2b.dev/docs",
            ),
            (
                "privacy",
                "数据隐私与最小化",
                "设计数据保留、脱敏和访问边界。",
                "收集数据前先说明用途和保留期限。调试日志、记忆和评测集都可能含用户信息，不应默认永久保存完整对话。",
                "以用户身份校验每次数据访问。提供导出和删除机制，密钥使用托管 secrets，客户端只能收到能力状态而不是密钥值。",
                ["列出收集字段及必要性", "对日志做字段级脱敏", "验证跨用户访问被拒绝"],
                "https://owasp.org/www-project-top-10-for-large-language-model-applications/",
            ),
        ],
    ),
    (
        "production",
        "部署与运维",
        "把 Agent 交付成稳定服务",
        [
            (
                "streaming",
                "SSE 与增量反馈",
                "实现可取消、有错误事件的流式协议。",
                "SSE 按事件传递 trace、文本和完成状态，客户端不能假设每个网络分块就是一条完整事件。缓冲直到空行分隔，再解析 JSON。",
                "运行取消应同时中止上游请求。流中错误使用明确事件，完成事件包含 run_id 和可用 usage；不向用户暴露上游响应正文或凭据。",
                ["实现 event/data 双换行协议", "测试拆包和连续多事件", "取消运行并关闭连接"],
                "https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events",
            ),
            (
                "budgets",
                "限流、重试与成本",
                "为公开服务设置可执行的预算。",
                "重试会增加费用且可能重复副作用。只对可重试错误退避，遵守 Retry-After，并给单次运行设输出 token、步数和时间上限。",
                "公开模型接口需要认证和集中限流。服务实例内计数无法跨实例保护预算；生产用共享存储实施配额，并监控实际 token usage。",
                ["分类 429、5xx、参数错误", "实现指数退避与最大次数", "给每个用户设置日配额"],
                "https://platform.openai.com/docs/guides/rate-limits",
            ),
            (
                "deploy",
                "CI/CD 与可观测发布",
                "部署、验收并建立回滚路径。",
                "CI 应检查格式、类型、单元测试和构建，预览环境验证浏览器到 API 的完整链路。生产环境变量与预览隔离，main 只承载验收版本。",
                "发布后访问健康接口并运行真实页面流程。保存部署 URL、commit 与验证结果，错误率上升时回滚到上一个已验证版本。",
                ["配置 main/develop 分支流程", "在 Vercel 预览验收", "记录发布证据与回滚入口"],
                "https://vercel.com/docs/deployments",
            ),
        ],
    ),
    (
        "capstone",
        "毕业项目",
        "构建有证据、有边界的研究助手",
        [
            (
                "research-agent",
                "研究助手：需求与检索",
                "把模糊需求转为可验收的 Agent 产品。",
                "定义研究助手的受众、问题范围、允许来源和成功条件。先以固定资料集验证检索与引用，避免直接给无限网页访问能力。",
                "产品契约包含来源链接、无证据说明、取消按钮和运行记录。对一个问题至少覆盖正确答案、证据不足和冲突来源三种场景。",
                ["写一页需求与验收标准", "构建来源白名单和检索", "验证三种证据场景"],
                "https://openai.github.io/openai-agents-python/",
            ),
            (
                "research-workflow",
                "研究助手：工具与评测",
                "联通规划、检索、校验和可观测性。",
                "实现计划→检索→整理→引用校验的明确节点。每一步保留证据 ID，最终回答只能引用在本次运行实际读取的材料。",
                "评测同时检查引用存在、结论被支持和工具调用边界。失败时返回部分结果及具体状态，不把未完成的研究包装为成功。",
                ["联通状态机与检索工具", "添加引用有效性校验", "运行固定评测集并保存 trace"],
                "https://docs.langchain.com/oss/python/langgraph/workflows-agents",
            ),
            (
                "research-release",
                "研究助手：上线验收",
                "交付可演示、可运行、可回滚的成品。",
                "将前端、API、模型适配器和隔离执行拆出清晰边界。用环境变量配置能力，在未配置时显示真实状态，让用户可以使用安全的教学演示。",
                "最终验收包括移动端、键盘操作、取消、断网、上游错误和预算上限。文档明确哪些经过真实调用，哪些只经过 mock 验证。",
                ["部署预览并验收主要流程", "演练取消与模型故障", "提交文档、验证证据与版本标签"],
                "https://vercel.com/docs/frameworks/backend/fastapi",
            ),
        ],
    ),
]

FULLSTACK_MODULES = [
    (
        "web",
        "Web 工程基础",
        "HTTP、类型与开发工具",
        [
            (
                "http",
                "HTTP 与 API 契约",
                "为接口定义方法、状态码和数据 schema。",
                "HTTP 契约包括路径、请求体、响应体和状态码。成功、输入错误、未认证和服务失败必须可区分，客户端才能做正确恢复。",
                "先写契约样例，再实现处理函数。GET 应无副作用，创建操作返回明确资源标识，错误结构保持一致并避免暴露内部堆栈。",
                [
                    "定义 GET /api/health 契约",
                    "用 curl 验证状态与 JSON",
                    "加入非法输入的 422/400 测试",
                ],
                "https://developer.mozilla.org/en-US/docs/Web/HTTP",
            ),
            (
                "types",
                "类型、校验与错误模型",
                "在服务边界把未知输入转为可靠类型。",
                "TypeScript 类型在运行时会被擦除；Go JSON 解码和 Python 类型注解也不替代完整验证。边界要检查长度、枚举和额外字段。",
                "把领域错误映射到一致响应。数据库异常或上游错误只在受控日志中记录，用户收到可恢复的错误类别与请求 ID。",
                ["为请求定义运行时校验", "拒绝空值与越界输入", "统一错误 code/message/request_id"],
                "https://www.typescriptlang.org/docs/handbook/2/narrowing.html",
            ),
            (
                "toolchain",
                "工具链与工程规范",
                "建立可重复的安装、格式化和检查流程。",
                "锁文件保存依赖解析结果，格式化工具减少无意义差异。TS 用 pnpm 与 Prettier，Python 用 uv 与 Ruff，Go 用 go mod 与 gofmt。",
                "版本约束与锁文件一起提交。CI 使用 frozen/locked 安装，避免每次构建拿到不同依赖；运行命令和环境变量写入 README。",
                ["提交锁文件和运行说明", "配置格式化与类型检查", "验证全新安装可重复"],
                "https://docs.astral.sh/uv/guides/projects/",
            ),
        ],
    ),
    (
        "frontend",
        "React 应用",
        "组件、状态与可访问体验",
        [
            (
                "components",
                "React 与组件设计",
                "用类型化 Props 组织可组合组件。",
                "组件围绕用户能力拆分，而不是为每个 div 创建抽象。用显式 Props 表达输入，事件回调表达输出，状态尽量保留在最小拥有者。",
                "列表用稳定 key，异步状态分为加载、错误、空结果和成功。可访问交互优先使用原生按钮和 Radix primitives，避免可点击 div。",
                ["实现课程卡片与类型化 Props", "覆盖加载/错误/空状态", "用键盘访问所有按钮"],
                "https://react.dev/learn/thinking-in-react",
            ),
            (
                "jotai",
                "Jotai 与持久化状态",
                "把学习进度和收藏保存为可推导状态。",
                "atom 保存最小事实，派生 atom 计算统计。不要同时存完成列表与完成总数，重复状态容易在修改时产生不一致。",
                "持久化数据需要版本与运行时校验。用原子更新避免并发丢失；导入文件先校验再写入，导出不包含密钥和会话认证信息。",
                ["建立完成课时和收藏 atom", "推导路线完成率", "刷新后验证状态恢复"],
                "https://jotai.org/docs/utilities/storage",
            ),
            (
                "design",
                "Tailwind 与设计系统",
                "建立一致且响应式的界面规则。",
                "设计 token 把颜色、间距和圆角集中到少量语义变量。Tailwind 负责布局和状态，shadcn 源码组件让交互行为可读、可修改。",
                "先检查窄屏布局和可见焦点，再补视觉细节。文字对比度、按钮标签、弹窗焦点管理和减少动效偏好都属于验收。",
                ["定义语义颜色和排版 token", "实现 375px 与桌面布局", "验证弹窗 Escape 和焦点返回"],
                "https://ui.shadcn.com/docs",
            ),
        ],
    ),
    (
        "backend",
        "多语言服务端",
        "TS · Go · Python 框架实践",
        [
            (
                "routing",
                "路由与分层架构",
                "使用 Hono、Gin 或 FastAPI 实现同一契约。",
                "路由层只处理协议，服务层实现业务，存储层处理数据。框架差异不应改变业务契约，使多语言实现可以通过同一验收样例。",
                "TS 可用 Hono 并显式校验输入，Go 用 Gin/标准库并传递 context，Python 用 FastAPI/Pydantic 自动生成 OpenAPI。避免在 handler 里堆积所有逻辑。",
                ["用所选语言实现健康路由", "分离 handler 与业务函数", "对照 OpenAPI 验证契约"],
                "https://fastapi.tiangolo.com/tutorial/",
            ),
            (
                "async",
                "异步、并发与取消",
                "让慢请求可超时、可取消。",
                "TS Promise、Go goroutine/context、Python asyncio 都能并发等待 I/O，但阻塞代码仍会耗尽服务资源。请求取消需要传到数据库和模型客户端。",
                "为每个外部请求设置 deadline，限制并发数。对取消和超时单独分类，清理连接和临时资源；不要把 CPU 密集工作放进事件循环。",
                ["并发获取两个只读资源", "给外部请求设置超时", "取消请求并确认资源清理"],
                "https://go.dev/blog/context",
            ),
            (
                "validation",
                "依赖注入与输入验证",
                "让处理逻辑可替换、可测试。",
                "用依赖边界传入数据库、时钟和模型客户端，测试中可以替换为受控实现。业务函数不应该直接读取全局凭据或建立网络连接。",
                "校验在边界完成，业务内部处理已验证类型。FastAPI Depends、Go 接口和 TS 函数参数都是可行方式，优先简单明确的依赖。",
                ["注入模型客户端与配置", "测试替换为 mock transport", "验证未知字段与非法枚举"],
                "https://fastapi.tiangolo.com/tutorial/dependencies/",
            ),
        ],
    ),
    (
        "data",
        "数据与认证",
        "数据库、迁移与用户边界",
        [
            (
                "database",
                "SQL 与数据建模",
                "用约束保护数据一致性。",
                "先定义实体与关系，再选 ORM。用户、课程进度和运行记录需要稳定 ID、唯一约束和更新时间；幂等写入由数据库约束支撑。",
                "用参数化查询避免注入，查询必须限制用户范围。分页使用稳定排序和 cursor，不依赖无限 offset；连接池与事务边界需要明确。",
                ["设计 users/progress/runs 表", "用唯一约束防重复进度", "实现用户范围内的分页查询"],
                "https://www.postgresql.org/docs/current/ddl-constraints.html",
            ),
            (
                "migrations",
                "迁移与事务",
                "在可回滚的步骤中升级数据结构。",
                "数据迁移是生产变更。把新增字段、回填和删除旧字段拆开，兼容新旧版本同时运行，避免一次部署破坏读取。",
                "事务只包必要的一致性操作，不在事务中长时间等待模型。使用迁移版本，预览环境验证升级与恢复，生产备份由托管数据库负责。",
                ["新增 progress.updated_at 字段", "在事务中完成幂等更新", "验证迁移前后兼容"],
                "https://alembic.sqlalchemy.org/en/latest/tutorial.html",
            ),
            (
                "auth",
                "认证、授权与会话",
                "区分身份验证与资源访问权限。",
                "认证回答你是谁，授权回答你能访问哪个资源。每次服务端请求都必须核对资源所属用户，客户端隐藏按钮不能形成安全边界。",
                "会话 cookie 使用 HttpOnly、Secure 和合适 SameSite。修改操作需要 CSRF 防护或严格 origin 检查；密钥不能写入前端构建变量。",
                ["定义用户与资源授权检查", "验证跨用户读取返回拒绝", "配置安全 cookie 与注销"],
                "https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html",
            ),
        ],
    ),
    (
        "ai-app",
        "AI 产品能力",
        "模型、检索与交互协议",
        [
            (
                "providers",
                "多模型 Provider Adapter",
                "用稳定业务接口适配模型供应商。",
                "应用层定义统一的 generate(messages, config)，适配器处理供应商格式、认证和错误映射。模型名称由服务端配置，客户端只选择被允许的 provider。",
                "真实调用记录实际 usage。没有配置时返回明确未启用状态，演示模式使用标记过的固定逻辑；不能在上游失败时静默伪装为真实回答。",
                [
                    "实现统一 provider 接口",
                    "验证 OpenAI/Anthropic/DeepSeek 格式",
                    "模拟 401/429/超时错误",
                ],
                "https://platform.openai.com/docs/api-reference/chat",
            ),
            (
                "ai-stream",
                "流式聊天与取消",
                "联通浏览器、API 与模型流。",
                "浏览器用 Fetch 读取 SSE，每条消息带运行 ID 与事件类型。按协议缓冲分块，区分文本增量、工具状态、错误和 done。",
                "用户点击停止时 AbortController 终止请求；服务器检测断连并清理上游。重复发送要生成新运行 ID，历史响应不能覆盖新运行。",
                ["实现 SSE 事件解析器", "测试事件跨三个 chunk", "加入停止与错误恢复"],
                "https://developer.mozilla.org/en-US/docs/Web/API/Streams_API",
            ),
            (
                "ai-rag",
                "文档问答与引用",
                "把检索结果变成可验证的产品输出。",
                "后端先摄取文档并索引，再将相关片段带上来源给模型。前端展示来源卡片和证据不足提示，让用户能检查答案。",
                "上传需要限制类型和大小，隔离解析器。不直接下载用户提供的任意 URL；若需要 URL 摄取，必须防 SSRF 并限制目标。",
                ["索引三份公开文档", "输出可点击引用卡片", "验证上传限制与无证据回答"],
                "https://docs.langchain.com/oss/python/langchain/rag",
            ),
        ],
    ),
    (
        "testing",
        "质量与安全",
        "测试、隔离与性能预算",
        [
            (
                "unit-tests",
                "单元与接口测试",
                "用边界样例验证真实行为。",
                "测试最有价值的是错误路径和契约边界。模型调用使用受控 transport，断言请求格式、授权与错误脱敏，不为每行实现复制一个测试。",
                "TS 用 Vitest，Python 用 pytest/TestClient，Go 用 testing/httptest。将网络和时间依赖注入，保持测试可重复且不消耗真实模型费用。",
                ["验证成功与非法请求", "模拟上游失败而不泄露正文", "测试取消与超时清理"],
                "https://docs.pytest.org/en/stable/",
            ),
            (
                "browser-tests",
                "浏览器端到端验收",
                "验证用户能完成整个关键流程。",
                "端到端测试走用户路径：选路线、读课、保存进度、打开实验、运行、取消和收藏。用 role/label 定位元素，避免依赖样式类名。",
                "自动测试和内置浏览器人工可视验收互补。至少检查移动布局、键盘、错误状态和刷新持久化，不用只访问首页作为验收。",
                [
                    "写路线到实验的端到端流程",
                    "覆盖 375px 与键盘操作",
                    "使用 Codex 内置 Browser 回读",
                ],
                "https://playwright.dev/docs/best-practices",
            ),
            (
                "app-security",
                "应用安全与限流",
                "在模型外保护公开接口。",
                "公开 AI 接口会消耗费用。身份、配额、输入长度和输出预算必须服务端强制执行；一次攻击不应拖垮整个应用。",
                "限制请求体、用共享存储实施跨实例限流，并检查 CORS/origin。第三方错误只返回稳定类别，禁止把响应体或环境变量拼进日志。",
                ["验证大请求被拒绝", "为模型入口加授权和预算", "测试限流与脱敏响应"],
                "https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html",
            ),
        ],
    ),
    (
        "delivery",
        "交付与运维",
        "从代码到线上产品",
        [
            (
                "git",
                "Git 分支与 CI",
                "让每个小改动可审查、可回滚。",
                "main 保持已验收版本，develop 承载日常开发。提交用前缀加中文说明，一个可验证的改动一个提交，避免把依赖、重构和产品改动混在一起。",
                "CI 冻结安装依赖并运行格式、类型、测试和构建。允许对 develop 使用 force-with-lease 时仍要核对远端，main 不强推。",
                ["建立 main/develop 分支", "提交 feat: 中文说明", "为 PR 和主分支配置 CI"],
                "https://docs.github.com/en/actions",
            ),
            (
                "vercel",
                "Vercel 部署与环境变量",
                "发布前后验证完整链路。",
                "将前端静态构建和 FastAPI 函数部署到同一域名，避免多域认证和 CORS 复杂性。预览与生产使用不同环境配置。",
                "密钥在 Vercel 托管环境变量中设置。发布后访问健康接口和核心页面，确认函数可以读取课程数据；静态页面成功不代表后端可用。",
                [
                    "检查 Vercel 路由与函数入口",
                    "部署预览并验证 /api/health",
                    "记录 URL、commit 和结果",
                ],
                "https://vercel.com/docs/functions/runtimes/python",
            ),
            (
                "observability",
                "可观测性与恢复",
                "用指标与日志定位用户失败。",
                "分别监控 API 错误、模型失败、延迟和实际费用。请求 ID 将浏览器失败与服务端日志关联，避免保存完整敏感输入。",
                "为健康与就绪状态建立不同信号。记录可回滚部署版本，演练供应商故障、数据库断连和超时，验证用户能获得可操作提示。",
                ["加入 request_id 与结构化错误", "记录延迟与失败类别", "演练供应商故障和回滚"],
                "https://opentelemetry.io/docs/",
            ),
        ],
    ),
    (
        "ship",
        "毕业项目",
        "交付一个多语言 AI 知识工作台",
        [
            (
                "product",
                "需求与纵向切片",
                "从可验收用户故事建立最小产品。",
                "选择一个核心场景：用户添加资料、提问并查看引用。先联通浏览器到 API 的一条纵向切片，再补认证、持久化和评测。",
                "需求包含失败状态和成本预算。对三种语言保持相同 OpenAPI 契约，使框架实现能在共同测试下比较。",
                ["定义三条用户故事", "完成浏览器到 API 切片", "列出验收和失败条件"],
                "https://fastapi.tiangolo.com/tutorial/first-steps/",
            ),
            (
                "integration",
                "前后端与 AI 集成",
                "把各模块联通为真实可操作流程。",
                "前端状态表示加载、运行、取消和失败；后端管理校验、授权、检索和模型调用。每个生成有独立 ID，来源与 usage 一起返回。",
                "不把应用数据库、模型密钥或执行权限暴露给前端。用户代码只进入隔离沙箱；本地进度明确标识设备范围并支持导出。",
                ["联通问答与引用展示", "加入取消、错误和预算", "保存可导出的用户进度"],
                "https://react.dev/learn/synchronizing-with-effects",
            ),
            (
                "launch",
                "上线与验收清单",
                "完成部署、文档和恢复演练。",
                "上线交付包含代码、锁文件、CI、部署、文档和验证证据。验收从全新用户视角完成核心流程，检查真实 API 与持久化行为。",
                "在文档区分已验证、未配置和待接入能力。记录 GitHub/Vercel/Notion 链接与版本，保证下一位开发者能复现运行和定位失败。",
                ["在内置 Browser 完成验收", "记录 CI 与部署结果", "发布开发说明和 wiki"],
                "https://vercel.com/docs/deployments",
            ),
        ],
    ),
]

SNIPPETS = {
    "python": 'from fastapi import FastAPI\nfrom pydantic import BaseModel, Field\n\napp = FastAPI()\n\nclass Query(BaseModel):\n    question: str = Field(min_length=1, max_length=2000)\n\n@app.get("/api/health")\ndef health():\n    return {"status": "ok"}\n\n@app.post("/api/ask")\ndef ask(query: Query):\n    return {"answer": query.question, "sources": []}\n',
    "typescript": 'import { Hono } from "hono";\n\nconst app = new Hono();\napp.get("/api/health", (c) => c.json({ status: "ok" }));\napp.post("/api/ask", async (c) => {\n  const body: unknown = await c.req.json();\n  if (!body || typeof body !== "object" || !("question" in body)\n      || typeof body.question !== "string" || !body.question.trim()) {\n    return c.json({ error: "invalid_question" }, 400);\n  }\n  return c.json({ answer: body.question, sources: [] });\n});\n\nexport default app;\n',
    "go": 'package main\n\nimport (\n    "net/http"\n    "github.com/gin-gonic/gin"\n)\n\ntype Query struct {\n    Question string `json:"question" binding:"required,max=2000"`\n}\n\nfunc main() {\n    app := gin.Default()\n    app.GET("/api/health", func(c *gin.Context) {\n        c.JSON(http.StatusOK, gin.H{"status": "ok"})\n    })\n    app.POST("/api/ask", func(c *gin.Context) {\n        var q Query\n        if err := c.ShouldBindJSON(&q); err != nil {\n            c.JSON(http.StatusBadRequest, gin.H{"error": "invalid_question"})\n            return\n        }\n        c.JSON(http.StatusOK, gin.H{"answer": q.Question, "sources": []string{}})\n    })\n    app.Run(":8080")\n}\n',
}

AGENT_CODE = 'from dataclasses import dataclass, field\n\n@dataclass\nclass AgentState:\n    goal: str\n    observations: list[str] = field(default_factory=list)\n    max_steps: int = 5\n\ndef knowledge_search(query: str) -> str:\n    """只读工具；实际项目接入经过授权的知识索引。"""\n    return "资料：工具调用前要校验参数与权限。"\n\ndef run_agent(goal: str) -> dict:\n    state = AgentState(goal=goal)\n    for step in range(state.max_steps):\n        observation = knowledge_search(state.goal)\n        state.observations.append(observation)\n        if observation:\n            return {"answer": observation, "steps": step + 1}\n    return {"error": "step_budget_exceeded"}\n\nprint(run_agent("怎样安全调用工具？"))\n'


def build_track(track_id: str, modules: list) -> dict[str, Any]:
    lessons = []
    stages = []
    for index, (stage_id, title, description, rows) in enumerate(modules):
        stage_lessons = []
        for row in rows:
            slug, lesson_title, objective, first, second, steps, source = row
            lesson_id = f"{track_id}-{slug}"
            criteria = [
                f"能够解释：{objective}",
                f"完成实践：{steps[1]}",
                "包含成功与失败样例，并记录验证结果",
            ]
            lesson = {
                "id": lesson_id,
                "track": track_id,
                "stage": stage_id,
                "title": lesson_title,
                "objective": objective,
                "minutes": 25 + index * 5,
                "level": "基础" if index < 2 else "进阶" if index < 6 else "实战",
                "body": [first, second],
                "steps": steps,
                "criteria": criteria,
                "resources": [{"title": "官方文档", "url": source}],
                "quiz": {
                    "question": "完成这节课时，哪种交付最能证明掌握了目标？",
                    "options": [
                        "只阅读标题并标记完成",
                        "实现实践步骤，验证成功与失败样例，并保留证据",
                        "复制示例且不检查结果",
                    ],
                    "answer": 1,
                    "explanation": "验收需要行为证据；运行结果、失败样例与明确边界比只阅读或复制更可靠。",
                },
                "snippets": {"python": AGENT_CODE} if track_id == "agent" else SNIPPETS,
            }
            lessons.append(lesson)
            stage_lessons.append(lesson_id)
        stages.append(
            {
                "id": stage_id,
                "number": index + 1,
                "title": title,
                "description": description,
                "lessons": stage_lessons,
            }
        )
    return {
        "id": track_id,
        "title": "AI Agent 工程" if track_id == "agent" else "AI 全栈工程",
        "description": "从模型与工具到可靠的自主系统"
        if track_id == "agent"
        else "用 TypeScript、Go、Python 构建 AI 产品",
        "languages": ["python"] if track_id == "agent" else ["typescript", "go", "python"],
        "stages": stages,
        "lessons": lessons,
    }


TRACKS = [build_track("agent", AGENT_MODULES), build_track("fullstack", FULLSTACK_MODULES)]
LESSONS = {lesson["id"]: lesson for track in TRACKS for lesson in track["lessons"]}
