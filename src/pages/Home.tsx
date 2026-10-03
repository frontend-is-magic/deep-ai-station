import { useAtomValue } from 'jotai';
import { Link } from 'react-router-dom';
import {
  ArrowRight,
  Bot,
  Braces,
  Check,
  Clock3,
  Code2,
  FlaskConical,
  Layers3,
  Radio,
  Route,
} from 'lucide-react';
import { progressAtom } from '@/lib/state';
import type { Track } from '@/lib/types';
import { Button } from '@/components/ui/button';
import { TextLink } from '@/components/common';

export default function Home({ tracks }: { tracks: Track[] }) {
  const progress = useAtomValue(progressAtom);
  const lessons = tracks.flatMap((x) => x.lessons);
  const completed = lessons.filter((x) => progress.completed.includes(x.id));
  const next = lessons.find((x) => !progress.completed.includes(x.id)) || lessons[0];
  return (
    <div className="page home-page">
      <div className="welcome-line">
        <span>
          <span className="tiny-dot" />
          YOUR NEXT CHAPTER STARTS HERE
        </span>
        <span>持续学习，持续交付</span>
      </div>
      <section className="hero">
        <div className="hero-copy">
          <div className="hero-kicker">
            <span className="hero-pill">LEARN BY BUILDING</span>
            <span>从原理到上线</span>
          </div>
          <h1>
            把 AI 能力，
            <br />
            做成真正的<span>产品。</span>
          </h1>
          <p>
            两条系统学习路线，一个动手实践的空间。
            <br />
            在这里连接知识、代码与下一次突破。
          </p>
          <div className="hero-actions">
            <Button asChild>
              <Link to={`/lesson/${next.id}`}>
                {completed.length ? '继续我的学习' : '开始学习之旅'}
                <ArrowRight size={16} />
              </Link>
            </Button>
            <Link className="hero-secondary" to="/playground">
              <FlaskConical size={16} />
              打开 Playground
            </Link>
          </div>
          <div className="hero-caption">
            <span className="tiny-dot" />
            THINK. BUILD. ITERATE.
          </div>
        </div>
        <div className="hero-art" aria-hidden="true">
          <div className="orbit orbit-one" />
          <div className="orbit orbit-two" />
          <div className="art-grid" />
          <div className="core-block">
            <Layers3 size={54} strokeWidth={1.2} />
          </div>
          <div className="art-node node-a">
            <Bot size={23} />
          </div>
          <div className="art-node node-b">
            <Code2 size={23} />
          </div>
          <div className="art-node node-c">
            <Braces size={23} />
          </div>
          <div className="art-chip chip-a">AGENT SYSTEMS</div>
          <div className="art-chip chip-b">FULLSTACK ENGINEERING</div>
          <div className="art-coordinate">
            32.08 / 120.62
            <br />
            IDEAS → REALITY
          </div>
        </div>
      </section>
      <section className="stats-grid" aria-label="学习统计">
        <div>
          <span className="stat-icon">
            <Route size={18} />
          </span>
          <div>
            <strong>
              02<span>条</span>
            </strong>
            <p>完整学习路线</p>
          </div>
        </div>
        <div>
          <span className="stat-icon">
            <Layers3 size={18} />
          </span>
          <div>
            <strong>
              {lessons.length}
              <span>节</span>
            </strong>
            <p>从基础到生产实践</p>
          </div>
        </div>
        <div>
          <span className="stat-icon">
            <Check size={18} />
          </span>
          <div>
            <strong>
              {completed.length}
              <span>节</span>
            </strong>
            <p>已完成课时</p>
          </div>
        </div>
        <div>
          <span className="stat-icon">
            <Code2 size={18} />
          </span>
          <div>
            <strong>
              03<span>种</span>
            </strong>
            <p>TS · Go · Python</p>
          </div>
        </div>
      </section>
      <section>
        <div className="section-heading">
          <div>
            <p className="eyebrow">CHOOSE YOUR PATH</p>
            <h2>选一条路线，开始构建</h2>
          </div>
          <span className="subtle-label">系统知识 × 项目实战</span>
        </div>
        <div className="track-grid">
          {tracks.map((track, index) => {
            const done = track.lessons.filter((x) => progress.completed.includes(x.id)).length;
            const percent = Math.round((done / track.lessons.length) * 100);
            const Icon = index === 0 ? Bot : Braces;
            return (
              <Link
                className={`track-card track-${track.id}`}
                key={track.id}
                to={`/roadmap/${track.id}`}
              >
                <div className="track-card-top">
                  <span className="track-number">PATH / 0{index + 1}</span>
                  <span className="track-icon">
                    <Icon size={28} strokeWidth={1.5} />
                  </span>
                </div>
                <h3>{track.title}</h3>
                <p>{track.description}</p>
                <div className="track-tags">
                  {(index === 0
                    ? ['LLM', 'Tools & MCP', 'RAG', 'Evals']
                    : ['React + TS', 'Go', 'Python', 'AI Apps']
                  ).map((tag) => (
                    <span key={tag}>{tag}</span>
                  ))}
                </div>
                <div className="track-meta">
                  <span>
                    <Layers3 size={14} />
                    {track.stages.length} 个阶段
                  </span>
                  <span>
                    <Clock3 size={14} />
                    {track.lessons.length} 节课
                  </span>
                  <span className="track-arrow">
                    <ArrowRight size={20} />
                  </span>
                </div>
                <div className="track-progress-label">
                  <span>{done ? `${done} 节已完成` : '你的旅程即将开始'}</span>
                  <strong>{percent}%</strong>
                </div>
                <div className="progress-bar">
                  <span style={{ width: `${percent}%` }} />
                </div>
              </Link>
            );
          })}
        </div>
      </section>
      <section className="home-bottom-grid">
        <div className="continue-card">
          <div className="section-heading">
            <div>
              <p className="eyebrow">ONE STEP AT A TIME</p>
              <h2>{completed.length ? '继续上次的旅程' : '从第一块积木开始'}</h2>
            </div>
            <span className="badge">推荐下一课</span>
          </div>
          <Link to={`/lesson/${next.id}`} className="next-lesson">
            <span className="lesson-icon">
              <Bot size={22} />
            </span>
            <div>
              <span className="subtle-label">
                {next.track === 'agent' ? 'AI AGENT 工程' : 'AI 全栈工程'} / {next.level}
              </span>
              <h3>{next.title}</h3>
              <p>{next.objective}</p>
            </div>
            <ArrowRight size={20} />
          </Link>
          <div className="continue-footer">
            <span>
              <Clock3 size={14} />约 {next.minutes} 分钟 · 包含动手练习
            </span>
            <TextLink to={`/lesson/${next.id}`}>进入课程</TextLink>
          </div>
        </div>
        <div className="explore-card">
          <Radio size={23} />
          <p className="eyebrow">STAY IN THE LOOP</p>
          <h3>让知识保持新鲜</h3>
          <p>
            官方资料与实时订阅源，
            <br />
            把行业变化连接到你的学习路径。
          </p>
          <TextLink to="/feed">探索信息流</TextLink>
        </div>
      </section>
    </div>
  );
}
