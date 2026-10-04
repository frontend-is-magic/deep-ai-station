import type { Language } from './types';

export interface CourseLab {
  id:
    | 'api-contract'
    | 'sqlite-storage'
    | 'session-authorization'
    | 'text-upload'
    | 'sse-stream'
    | 'agent-write-safety'
    | 'mcp-readonly'
    | 'workflow-checkpoint'
    | 'document-chunking'
    | 'output-regression'
    | 'memory-policy'
    | 'frontend-state'
    | 'agent-loop';
  sharedFrontend?: boolean;
  lessons: readonly string[];
  languages: readonly Language[];
  title: string;
  description: string;
  notice: string;
}

const courseLabs: readonly CourseLab[] = [
  {
    id: 'agent-loop',
    lessons: ['agent-agent-loop'],
    languages: ['python'],
    title: '可运行 Agent 决策循环实验',
    description:
      '独立命令行实验，每轮根据真实工具观察选择搜索、读取或结束；改变策略与步数，对比有证据、空结果和预算耗尽，区分搜索命中、实际读取与任务完成。',
    notice:
      '使用固定规则策略和本地只读教学资料，无模型或网络调用。策略演练不代表真实模型规划质量；下载后记录实际命令与结果，课程和 Python 实践仍须由你明确确认。',
  },
  {
    id: 'frontend-state',
    sharedFrontend: true,
    lessons: ['fullstack-components', 'fullstack-jotai'],
    languages: ['typescript'],
    title: '可运行 React 组件与状态实验',
    description:
      '公共前端独立项目，用三张固定课程卡片练习类型化 Props、完成与收藏事实、派生统计和筛选；刷新后核对状态恢复，再尝试新增一条派生规则。',
    notice:
      '公共前端统一使用 TypeScript，不改变你的 Go/Python 服务端参考偏好。实验只操作独立页面的固定数据，无后端或模型；下载、证据和课程完成不会自动记录实践。',
  },
  {
    id: 'memory-policy',
    lessons: ['agent-memory'],
    languages: ['python'],
    title: '可运行记忆资格与冲突实验',
    description:
      '独立命令行实验，按所属用户、范围、授权和时间筛选固定教学偏好，区分无合格记忆、同语言多来源和冲突。显式指定本次语言，观察本次选择生效，并核对原记忆文件保持不变。',
    notice:
      '仅使用包内固定教学偏好与本地结构化规则，无模型、网络或数据库写入。不代表生产长期记忆或注入安全。将实际命令、成功失败与未验证事项记入下方证据卡，不会自动完成课程或 Python 实践。',
  },
  {
    id: 'output-regression',
    lessons: ['agent-regression'],
    languages: ['python'],
    title: '可运行结构化结果回归与门禁实验',
    description:
      '逐条检查固定合成输出的 JSON、字段结构与原文引用，比较基线和候选。观察总通过率提高却关键案例退化时，门禁仍然拒绝通过。',
    notice:
      '仅使用包内固定场景和合成输出，无模型或外部请求。下载后记录实际逐例原因、分母与退出码；门禁通过不代表生产模型质量达标，也不会自动完成课程或 Python 实践。',
  },
  {
    id: 'document-chunking',
    lessons: ['agent-chunking'],
    languages: ['python'],
    title: '可运行文档切分与引用实验',
    description:
      '比较标题分段与重叠滑窗，观察完整证据召回、排序和索引成本。按来源、版本与原文坐标回读引用，检查中文、emoji、重复段落及冲突资料。',
    notice:
      '仅使用包内固定资料与词法检索，无模型或外部请求。下载后按 README 运行，将实际指标、失败输入和原文回读记入下方证据卡；不会自动完成课程或 Python 实践。',
  },
  {
    id: 'workflow-checkpoint',
    lessons: ['agent-state-machine'],
    languages: ['python'],
    title: '可运行检查点与重启恢复实验',
    description:
      '独立项目，使用 SQLite 逐步保存检索、起草和引用校验结果。在提交前后中断进程，再用原运行 ID 恢复；观察版本冲突、已提交节点复用和损坏状态拒绝。',
    notice:
      '仅使用包内固定资料与纯计算节点，无模型调用或外部写入。下载后按 README 在独立练习环境运行，将实际重启、数据库状态与引用结果记入下方证据卡；不会自动完成课程或 Python 实践。',
  },
  {
    id: 'mcp-readonly',
    lessons: ['agent-mcp'],
    languages: ['python'],
    title: '可运行 MCP 只读协议实验',
    description:
      '独立项目，使用官方 SDK 客户端与服务端进行真实 stdio 通信。观察工具发现、严格 schema、固定资源、空结果与错误分层，并验证请求超时取消、断连和子进程退出。',
    notice:
      '仅访问包内固定课程资料，无模型调用、远端服务器或任意命令入口。下载后按 README 在独立练习环境运行，将实际消息与清理证据记入下方证据卡；不会自动完成课程或 Python 实践。',
  },
  {
    id: 'agent-write-safety',
    lessons: ['agent-tool-safety'],
    languages: ['python'],
    title: '可运行工具审批与幂等实验',
    description:
      '独立 FastAPI、SQLite 与 React 客户端。先准备完整修改，再切换教学审批身份审核差异，最后由原 Agent 执行；观察版本冲突、批准过期、撤销、重复回执与结果不明后的查询恢复。',
    notice:
      '仅发布到本地教学数据库，公开假身份无生产权限；不调用模型或外部发布服务。下载后按 README 在独立练习环境运行，并在下方填写实际证据；不会自动完成课程或 Python 实践。',
  },
  {
    languages: ['typescript', 'python', 'go'],
    id: 'sse-stream',
    lessons: ['fullstack-ai-stream', 'fullstack-async'],
    title: '可运行 SSE 流式实验',
    description:
      '独立流式服务与共享 React 客户端，包含完整依赖配置与锁文件、启动入口和成功/失败测试。验证分块 UTF-8、事件序号、错误与总超时、真实 HTTP 断连清理，以及两个运行的取消隔离。',
    notice:
      '固定教学数据源，无模型密钥或费用；服务端取消与一次清理由原生真实 HTTP 测试证明。页面停止不代表生产代理或真实供应商已清理。将本语言的版本、命令和实际结果记入下方实验实践证据卡，也可填写包内 EVIDENCE.md，不会自动完成本课或语言实践。',
  },
  {
    languages: ['typescript', 'python', 'go'],
    id: 'api-contract',
    lessons: ['fullstack-routing', 'fullstack-validation'],
    title: '可运行 API 契约实验',
    description:
      '独立 HTTP 服务，包含完整依赖配置与锁文件、启动入口、共享 API 契约和成功/失败测试。跟踪路由、服务与 Repository，再比较三种语言的相同请求与响应。',
    notice:
      '在独立练习环境按 README 启动，使用固定资料，无需模型密钥或费用。运行测试后将本语言的版本、命令和实际结果记入下方实验实践证据卡，也可填写包内 EVIDENCE.md；测试通过不会自动完成本课。',
  },
  {
    languages: ['typescript', 'python', 'go'],
    id: 'sqlite-storage',
    lessons: ['fullstack-database', 'fullstack-migrations'],
    title: '可运行 SQLite 数据实验',
    description:
      '独立 SQLite 仓储与命令行实验，包含完整依赖配置与锁文件、启动入口和成功/失败测试。练习 v1→v2 迁移、参数化查询、事务回滚，以及用户范围内的游标分页。',
    notice:
      '在独立练习环境按 README 运行，无需模型密钥或费用。本地数据文件不要提交 Git；CLI 中的 owner 只是教学输入，不代表登录认证。将本语言的版本、命令和实际结果记入下方实验实践证据卡，也可填写包内 EVIDENCE.md；不会自动标记课程或语言实践完成。',
  },
  {
    languages: ['typescript', 'python', 'go'],
    id: 'session-authorization',
    lessons: ['fullstack-auth', 'fullstack-app-security'],
    title: '可运行会话与授权实验',
    description:
      '独立 HTTP 会话与授权实验，包含完整依赖配置与锁文件、启动入口和成功/失败测试。验证服务端身份来源、会话过期与撤销、owner 隔离，以及 Cookie CSRF 防护。',
    notice:
      '公开假会话仅用于独立本地教学，不得填入真实凭据。真实登录与 HTTPS Cookie 行为需另行验收；将本语言的版本、命令和实际结果记入下方实验实践证据卡，也可填写包内 EVIDENCE.md，不会自动标记课程或语言实践完成。',
  },
  {
    languages: ['typescript', 'python', 'go'],
    id: 'text-upload',
    lessons: ['fullstack-ai-rag'],
    title: '可运行受限文本上传实验',
    description:
      '独立 HTTP 文本上传实验，包含完整依赖配置与锁文件、启动入口和成功/失败测试。共享 React 客户端串起选择文件、上传、列表、纯文本预览和原始字节下载，验证 UTF-8 与字节上限、文件名白名单、owner 隔离与原子配额；可显式切换 SQLite，练习重启保留和共享事务。',
    notice:
      '公开假会话仅用于本地教学，不得填入真实凭据。只接收 .txt / .md，默认内存数据重启清空，SQLite 需显式初始化并保留原文与配额；提交未确认时先核对记录，不自动重传；界面保留未确认提示，允许新尝试也不代表旧请求已查明。不解析或执行内容，不代表生产上传与文档问答已经验收。将本语言的版本、命令和实际结果记入下方实验实践证据卡，也可填写包内 EVIDENCE.md。',
  },
];

export function courseLabFor(lessonId: string, language?: Language): CourseLab | undefined {
  return courseLabs.find(
    (lab) => lab.lessons.includes(lessonId) && (!language || lab.languages.includes(language)),
  );
}

// A shared React lab has one actual package language, independent of server references.
// Keep courseLabFor strict so stored evidence cannot invent Go/Python frontend packages.
export function courseLabSelectionFor(lessonId: string, referenceLanguage: Language) {
  if (!(['typescript', 'go', 'python'] as const).includes(referenceLanguage)) return undefined;
  const lab = courseLabFor(lessonId);
  if (!lab) return undefined;
  const language: Language = lab.sharedFrontend ? 'typescript' : referenceLanguage;
  return lab.languages.includes(language) ? { lab, language } : undefined;
}
