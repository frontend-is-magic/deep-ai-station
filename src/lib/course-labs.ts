export interface CourseLab {
  id: 'api-contract' | 'sqlite-storage' | 'session-authorization';
  lessons: readonly string[];
  title: string;
  description: string;
  notice: string;
}

const courseLabs: readonly CourseLab[] = [
  {
    id: 'api-contract',
    lessons: ['fullstack-routing', 'fullstack-validation'],
    title: '可运行 API 契约实验',
    description:
      '独立 HTTP 服务，包含完整依赖配置与锁文件、启动入口、共享 API 契约和成功/失败测试。跟踪路由、服务与 Repository，再比较三种语言的相同请求与响应。',
    notice:
      '在独立练习环境按 README 启动，使用固定资料，无需模型密钥或费用。运行测试后将实际结果记入 EVIDENCE.md 与课程笔记；测试通过不会自动完成本课。',
  },
  {
    id: 'sqlite-storage',
    lessons: ['fullstack-database', 'fullstack-migrations'],
    title: '可运行 SQLite 数据实验',
    description:
      '独立 SQLite 仓储与命令行实验，包含完整依赖配置与锁文件、启动入口和成功/失败测试。练习 v1→v2 迁移、参数化查询、事务回滚，以及用户范围内的游标分页。',
    notice:
      '在独立练习环境按 README 运行，无需模型密钥或费用。本地数据文件不要提交 Git；CLI 中的 owner 只是教学输入，不代表登录认证。将实际结果记入 EVIDENCE.md 与课程笔记；不会自动标记课程或语言实践完成。',
  },
  {
    id: 'session-authorization',
    lessons: ['fullstack-auth', 'fullstack-app-security'],
    title: '可运行会话与授权实验',
    description:
      '独立 HTTP 会话与授权实验，包含完整依赖配置与锁文件、启动入口和成功/失败测试。验证服务端身份来源、会话过期与撤销、owner 隔离，以及 Cookie CSRF 防护。',
    notice:
      '公开假会话仅用于独立本地教学，不得填入真实凭据。真实登录与 HTTPS Cookie 行为需另行验收；将实际结果记入 EVIDENCE.md 与课程笔记，不会自动标记课程或语言实践完成。',
  },
];

export function courseLabFor(lessonId: string): CourseLab | undefined {
  return courseLabs.find((lab) => lab.lessons.includes(lessonId));
}
