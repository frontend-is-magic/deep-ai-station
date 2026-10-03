import { useEffect, useState } from 'react';
import { useAtom } from 'jotai';
import { Link, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  Clipboard,
  Clock3,
  Download,
  ExternalLink,
  FlaskConical,
} from 'lucide-react';
import { progressAtom } from '@/lib/state';
import type { Track } from '@/lib/types';
import { languageNames } from '@/lib/utils';
import { Button } from '@/components/ui/button';

export default function LessonPage({ tracks }: { tracks: Track[] }) {
  const { lessonId } = useParams();
  const track = tracks.find((x) => x.lessons.some((l) => l.id === lessonId));
  const lesson = track?.lessons.find((x) => x.id === lessonId);
  const [progress, setProgress] = useAtom(progressAtom);
  const [answer, setAnswer] = useState<number | null>(null);
  const [checks, setChecks] = useState<number[]>([]);
  const [copyState, setCopyState] = useState('');
  useEffect(() => {
    setAnswer(null);
    setChecks([]);
    setCopyState('');
  }, [lessonId]);
  if (!lesson || !track)
    return (
      <div className="state-panel">
        <h1>课时不存在</h1>
        <Link to="/">返回总览</Link>
      </div>
    );
  const completed = progress.completed.includes(lesson.id);
  const index = track.lessons.findIndex((x) => x.id === lesson.id);
  const language = track.id === 'agent' ? 'python' : progress.language;
  const code = lesson.snippets[language] || '';
  const ready = checks.length === lesson.criteria.length && answer === lesson.quiz.answer;
  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setCopyState('已复制');
    } catch {
      setCopyState('请选中代码复制');
    }
  }
  return (
    <div className="page lesson-page">
      <Link className="back-link" to={`/roadmap/${track.id}`}>
        <ArrowLeft size={16} />
        返回 {track.title}
      </Link>
      <div className="lesson-layout">
        <article className="lesson-article">
          <p className="eyebrow">
            {track.title} / LESSON {String(index + 1).padStart(2, '0')}
          </p>
          <h1>{lesson.title}</h1>
          <p className="lesson-objective">{lesson.objective}</p>
          <div className="lesson-meta">
            <span>
              <Clock3 size={14} />约 {lesson.minutes} 分钟
            </span>
            <span>
              <BookOpen size={14} />
              {lesson.level}
            </span>
            <span className={completed ? 'completion-chip' : 'subtle-label'}>
              {completed ? '已完成' : '阅读 + 实践 + 验收'}
            </span>
          </div>
          <hr />
          <h2>理解这件事</h2>
          {lesson.body.map((paragraph, i) => (
            <p key={i}>{paragraph}</p>
          ))}
          <div className="lesson-callout">
            <span>本课目标</span>
            <p>{lesson.objective}完成后，以实践结果验证你的理解。</p>
          </div>
          <h2>动手实践</h2>
          <ol className="practice-steps">
            {lesson.steps.map((step, i) => (
              <li key={step}>
                <span>{String(i + 1).padStart(2, '0')}</span>
                <p>{step}</p>
              </li>
            ))}
          </ol>
          <div className="code-heading">
            <h2>
              {track.id === 'agent' ? '本课 Python 参考' : `${languageNames[language]} 服务端参考`}
            </h2>
            <Button variant="outline" size="sm" onClick={() => void copy()}>
              <Clipboard size={14} />
              {copyState || '复制代码'}
            </Button>
          </div>
          <p className="code-description">
            参考代码展示本课的核心机制。补充成功与失败样例，将输入、结果和验证证据记入笔记。
            {track.id === 'fullstack' &&
              ' React 界面统一使用 TypeScript；Go/Python 示例展示对应服务契约。框架示例需在独立练习项目安装依赖。'}
          </p>
          <pre className="code-block">
            <code>{code}</code>
          </pre>
          <Button variant="outline" asChild>
            <Link to={`/playground?track=${track.id}&lesson=${lesson.id}&mode=code`}>
              <FlaskConical size={16} />
              在实验空间编辑
            </Link>
          </Button>
          <Button variant="ghost" asChild>
            <a href={`/api/lessons/${lesson.id}/exercise.zip?language=${language}`} download>
              <Download size={16} />
              下载本课练习资料
            </a>
          </Button>
          <h2>检查你的理解</h2>
          <div className="quiz-box">
            <p>{lesson.quiz.question}</p>
            {lesson.quiz.options.map((option, i) => (
              <label className={`quiz-option ${answer === i ? 'chosen' : ''}`} key={option}>
                <input
                  type="radio"
                  name="quiz"
                  checked={answer === i}
                  onChange={() => setAnswer(i)}
                />
                <span>{option}</span>
              </label>
            ))}
            {answer !== null && (
              <div
                className={`quiz-result ${answer === lesson.quiz.answer ? 'correct' : ''}`}
                role="status"
              >
                {answer === lesson.quiz.answer ? '回答正确。' : '再想一想。'}
                {lesson.quiz.explanation}
              </div>
            )}
          </div>
          <h2>官方资料</h2>
          {lesson.resources.map((resource) => (
            <a
              className="resource-link"
              key={resource.url}
              href={resource.url}
              target="_blank"
              rel="noreferrer"
            >
              <BookOpen size={18} />
              <span>
                {resource.title}
                <small>{new URL(resource.url).hostname}</small>
              </span>
              <ExternalLink size={16} />
            </a>
          ))}
          <div className="lesson-pagination">
            {index > 0 ? (
              <Link to={`/lesson/${track.lessons[index - 1].id}`}>
                <ArrowLeft size={16} />
                上一课
              </Link>
            ) : (
              <span />
            )}
            {index < track.lessons.length - 1 ? (
              <Link to={`/lesson/${track.lessons[index + 1].id}`}>
                下一课：{track.lessons[index + 1].title}
                <ArrowRight size={16} />
              </Link>
            ) : (
              <Link to={`/roadmap/${track.id}`}>
                回到路线
                <ArrowRight size={16} />
              </Link>
            )}
          </div>
        </article>
        <aside className="lesson-aside">
          <div className="lesson-checklist">
            <p className="eyebrow">YOUR CHECKPOINT</p>
            <h3>本课验收</h3>
            <p>完成实践后逐项确认，再保存学习进度。</p>
            {lesson.criteria.map((criterion, i) => (
              <label key={criterion}>
                <input
                  type="checkbox"
                  checked={completed || checks.includes(i)}
                  disabled={completed}
                  onChange={() =>
                    setChecks((x) => (x.includes(i) ? x.filter((v) => v !== i) : [...x, i]))
                  }
                />
                <span>{criterion}</span>
              </label>
            ))}
            <Button
              disabled={!completed && !ready}
              variant={completed ? 'outline' : 'default'}
              onClick={() =>
                setProgress((p) => ({
                  ...p,
                  completed: completed
                    ? p.completed.filter((id) => id !== lesson.id)
                    : [...new Set([...p.completed, lesson.id])],
                }))
              }
            >
              <CheckCircle2 size={16} />
              {completed ? '取消完成标记' : '标记本课完成'}
            </Button>
            {!completed && !ready && <small>通过测验并确认全部验收项后可完成。</small>}
          </div>
          <div className="notes-box">
            <h3>我的思考</h3>
            <p>记录你的理解、问题和实验结果。</p>
            <textarea
              aria-label="课程笔记"
              placeholder="我学到了什么？下一步想验证什么？"
              value={progress.notes[lesson.id] || ''}
              maxLength={10000}
              onChange={(e) =>
                setProgress((p) => ({ ...p, notes: { ...p.notes, [lesson.id]: e.target.value } }))
              }
            />
            <small>自动保存到当前浏览器</small>
          </div>
        </aside>
      </div>
    </div>
  );
}
