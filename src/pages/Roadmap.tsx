import { useState } from 'react';
import { useAtom } from 'jotai';
import { Link, useParams } from 'react-router-dom';
import {
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  Clock3,
  FlaskConical,
  Layers3,
} from 'lucide-react';
import { progressAtom } from '@/lib/state';
import { nextIncompleteLesson, resumeLesson } from '@/lib/resume';
import type { Track } from '@/lib/types';
import { languageNames } from '@/lib/utils';
import { PageHeading } from '@/components/common';
import { Button } from '@/components/ui/button';

export default function Roadmap({ tracks }: { tracks: Track[] }) {
  const { trackId } = useParams();
  const track = tracks.find((x) => x.id === trackId);
  const [progress, setProgress] = useAtom(progressAtom);
  const [collapsed, setCollapsed] = useState<string[]>([]);
  if (!track)
    return (
      <div className="state-panel">
        <h1>学习路线不存在</h1>
        <Link to="/">返回总览</Link>
      </div>
    );
  const done = track.lessons.filter((x) => progress.completed.includes(x.id)).length;
  const knownLessons = new Set(track.lessons.map((lesson) => lesson.id));
  const practiceCounts = track.languages.map((language) => ({
    language,
    count: (progress.practice ?? []).filter(
      (record) => record.language === language && knownLessons.has(record.lesson_id),
    ).length,
  }));
  const resumed = resumeLesson(progress, tracks, track.id);
  const next = nextIncompleteLesson(progress, track.lessons);
  const primary = resumed ?? next;
  const resumeLabel = resumed && progress.completed.includes(resumed.id) ? '回顾本课' : '继续本课';
  return (
    <div className="page">
      <PageHeading
        eyebrow={`LEARNING PATH / ${track.id === 'agent' ? '01' : '02'}`}
        title={track.title}
        description={track.description}
      >
        {primary && (
          <Button asChild>
            <Link to={`/lesson/${primary.id}`}>
              {resumed ? resumeLabel : done ? '学习下一课' : '开始第一课'}
              <ArrowRight size={16} />
            </Link>
          </Button>
        )}
      </PageHeading>
      <div className="roadmap-overview">
        <div>
          <Layers3 size={19} />
          <strong>{track.stages.length} 个阶段</strong>
          <span>{track.lessons.length} 节课程 · 逐步完成，无需解锁</span>
        </div>
        <div className="overview-progress">
          <span>
            {done}/{track.lessons.length} 已完成
          </span>
          <div className="progress-bar">
            <span style={{ width: `${(done / track.lessons.length) * 100}%` }} />
          </div>
          <strong>{Math.round((done / track.lessons.length) * 100)}%</strong>
        </div>
      </div>
      <div className="route-resume-grid">
        <section aria-label="上次学习" className="route-resume-card">
          <h2>本路线上次学习</h2>
          {resumed ? (
            <>
              <p>{resumed.title}</p>
              <Link className="text-link" to={`/lesson/${resumed.id}`}>
                {resumeLabel}
                <ArrowRight size={16} />
              </Link>
            </>
          ) : (
            <p>
              {next
                ? '还没有有效的访问记录，可从下一节未完成课开始。'
                : '还没有有效的访问记录，可从下方选择课时回顾。'}
            </p>
          )}
        </section>
        <section aria-label="下一节未完成课" className="route-resume-card">
          <h2>{next ? '下一节未完成课' : '本路线课程已完成'}</h2>
          {next ? (
            <>
              <p>{next.title}</p>
              <Link className="text-link" to={`/lesson/${next.id}`}>
                进入课程
                <ArrowRight size={16} />
              </Link>
            </>
          ) : (
            <p>可以从下方回顾课程，继续记录各语言的实践结果。</p>
          )}
        </section>
      </div>
      {track.id === 'fullstack' && (
        <div className="language-selector">
          <div>
            <strong>选择你的服务端语言</strong>
            <p>保持相同 API 契约，对照不同框架的实现。</p>
          </div>
          <div className="segmented-control">
            {track.languages.map((language) => (
              <button
                key={language}
                className={progress.language === language ? 'selected' : ''}
                onClick={() => setProgress((p) => ({ ...p, language }))}
              >
                {languageNames[language]}
              </button>
            ))}
          </div>
        </div>
      )}
      <section
        aria-label="路线实践记录"
        className="mb-8 rounded-xl border border-slate-200 bg-white p-5"
      >
        <h2 className="text-base font-semibold">各语言实践进度</h2>
        <p className="mt-1 text-sm text-slate-600">
          在课时页分别确认实际实践。这里按语言统计自行记录的结果，与上方课程完成数独立。
        </p>
        <ul className="mt-4 grid list-none gap-3 sm:grid-cols-3">
          {practiceCounts.map(({ language, count }) => (
            <li key={language} className="rounded-lg bg-slate-50 px-4 py-3">
              <strong className="text-sm">{languageNames[language]}</strong>
              <p className="mt-1 text-sm text-slate-600">
                {count}/{track.lessons.length} 节已记录实践
              </p>
            </li>
          ))}
        </ul>
      </section>
      <div className="roadmap-stages">
        {track.stages.map((stage) => {
          const isCollapsed = collapsed.includes(stage.id);
          const stageDone = stage.lessons.filter((id) => progress.completed.includes(id)).length;
          return (
            <section key={stage.id} className="stage">
              <div
                className={`stage-marker ${stageDone === stage.lessons.length ? 'completed' : ''}`}
              >
                {stageDone === stage.lessons.length ? (
                  <Check size={19} />
                ) : (
                  String(stage.number).padStart(2, '0')
                )}
              </div>
              <div className="stage-content">
                <button
                  className="stage-heading"
                  aria-expanded={!isCollapsed}
                  onClick={() =>
                    setCollapsed((x) =>
                      x.includes(stage.id) ? x.filter((id) => id !== stage.id) : [...x, stage.id],
                    )
                  }
                >
                  <div>
                    <p className="eyebrow">STAGE {String(stage.number).padStart(2, '0')}</p>
                    <h2>{stage.title}</h2>
                    <p>{stage.description}</p>
                  </div>
                  <span>
                    {stageDone}/{stage.lessons.length}
                    <ChevronDown className={isCollapsed ? '-rotate-90' : ''} size={18} />
                  </span>
                </button>
                {!isCollapsed && (
                  <div className="stage-lessons">
                    {stage.lessons.map((id) => {
                      const lesson = track.lessons.find((x) => x.id === id)!;
                      const completed = progress.completed.includes(id);
                      return (
                        <Link
                          to={`/lesson/${id}`}
                          className={`lesson-row ${completed ? 'completed' : ''}`}
                          key={id}
                        >
                          <span className="lesson-status">
                            {completed ? <CheckCircle2 size={20} /> : <span />}
                          </span>
                          <div>
                            <h3>{lesson.title}</h3>
                            <p>{lesson.objective}</p>
                          </div>
                          <span className="badge">{lesson.level}</span>
                          <span className="lesson-time">
                            <Clock3 size={14} />
                            {lesson.minutes} min
                          </span>
                          <ArrowRight size={17} />
                        </Link>
                      );
                    })}
                  </div>
                )}
              </div>
            </section>
          );
        })}
      </div>
      <div className="roadmap-cta">
        <FlaskConical size={24} />
        <div>
          <h3>读懂之后，亲手验证</h3>
          <p>把课程示例带到实验空间，观察完整运行流程。</p>
        </div>
        <Button variant="dark" asChild>
          <Link to={`/playground?track=${track.id}`}>
            进入 Playground
            <ArrowRight size={16} />
          </Link>
        </Button>
      </div>
    </div>
  );
}
