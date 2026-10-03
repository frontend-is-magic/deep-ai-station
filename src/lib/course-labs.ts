import type { Language } from './types';

export interface CourseLab {
  id:
    | 'api-contract'
    | 'sqlite-storage'
    | 'session-authorization'
    | 'text-upload'
    | 'sse-stream'
    | 'agent-write-safety'
    | 'mcp-readonly';
  lessons: readonly string[];
  languages: readonly Language[];
  title: string;
  description: string;
  notice: string;
}

const courseLabs: readonly CourseLab[] = [
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
      '独立 HTTP 文本上传实验，包含完整依赖配置与锁文件、启动入口和成功/失败测试。验证 UTF-8 与字节上限、文件名白名单、owner 隔离、原子配额，以及附件下载的原始字节。',
    notice:
      '公开假会话仅用于本地教学，不得填入真实凭据。只接收 .txt / .md，内存数据重启清空；不解析或执行内容，不代表生产上传与文档问答已经验收。将本语言的版本、命令和实际结果记入下方实验实践证据卡，也可填写包内 EVIDENCE.md。',
  },
];

export function courseLabFor(lessonId: string, language?: Language): CourseLab | undefined {
  return courseLabs.find(
    (lab) => lab.lessons.includes(lessonId) && (!language || lab.languages.includes(language)),
  );
}
