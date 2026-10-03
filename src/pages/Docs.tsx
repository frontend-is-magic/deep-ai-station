import { ArrowUpRight, BookOpen, Github, Layers3, ShieldCheck, Terminal } from 'lucide-react';
import { PageHeading } from '@/components/common';

export default function Docs() {
  return (
    <div className="page docs-page">
      <PageHeading
        eyebrow="HANDBOOK / OPEN SOURCE"
        title="从学习者，到构建者"
        description="这里是产品的运行边界、开发方式与交付约定。"
      />
      <div className="docs-grid">
        <section>
          <Layers3 size={24} />
          <h2>两个方向，一套学习闭环</h2>
          <p>
            AI Agent 路线围绕模型、工具、知识、状态和评测；AI 全栈路线围绕 React 与
            TypeScript、Go、Python 服务端，最终都通向可上线的项目。
          </p>
          <p>
            学习路径是：阅读原理 → 实践步骤 → 检查理解 → 验收确认 →
            保存笔记与进度。课程不会强制锁定顺序。
          </p>
        </section>
        <section>
          <ShieldCheck size={24} />
          <h2>真实能力，明确边界</h2>
          <p>
            教学工作流从课程索引检索资料，不调用模型。真实模型在服务端配置后启用，并要求实验访问码。静态检查默认开放，代码运行只交给配置后的独立沙箱。
          </p>
          <p>
            可选择有界 Agent 循环，观察模型使用只读检索和课程阅读工具，最多三轮模型请求。
            演示模式使用固定工具顺序；历史保留实际轨迹，缺失用量明确显示未知。
          </p>
          <p>
            学习记录保存在当前浏览器，支持导出和导入。隔离代码运行需托管沙箱配置；账号体系与跨设备同步属于后续集成方向。
          </p>
        </section>
        <section>
          <Terminal size={24} />
          <h2>本地开发</h2>
          <p>
            前端使用 React + TypeScript、Tailwind、Jotai 和 shadcn 风格 Radix 组件，使用
            Prettier。后端使用 uv、FastAPI、Pydantic，使用 Ruff。
          </p>
          <pre>
            pnpm install
            <br />
            uv sync
            <br />
            uv run uvicorn backend.app:app --reload
            <br />
            pnpm dev
          </pre>
        </section>
        <section>
          <BookOpen size={24} />
          <h2>交付与验证</h2>
          <p>
            main 是稳定分支，develop 是开发分支。提交使用前缀和中文信息，保持小而可验证。CI
            检查格式、测试与构建，Vercel 负责部署。
          </p>
          <p>
            用 Codex 内置 Browser
            验收真实页面与接口。文档区分已验证和待配置状态，密钥只进入服务端托管环境变量。
          </p>
        </section>
      </div>
      <a
        className="docs-repo"
        href="https://github.com/frontend-is-magic/deep-ai-station"
        target="_blank"
        rel="noreferrer"
      >
        <Github size={23} />
        <div>
          <h3>继续探索项目源码</h3>
          <p>开发说明、架构决策、验收记录与公开代码</p>
        </div>
        <ArrowUpRight size={22} />
      </a>
    </div>
  );
}
