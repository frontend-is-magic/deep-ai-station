import { useEffect, useRef, useState } from 'react';
import { useAtom } from 'jotai';
import { Link, useLocation, useParams, useSearchParams } from 'react-router-dom';
import {
  ArrowLeft,
  ArrowRight,
  BookOpen,
  CheckCircle2,
  Clipboard,
  Clock3,
  Download,
  Bot,
  ExternalLink,
  FlaskConical,
} from 'lucide-react';
import { progressAtom } from '@/lib/state';
import {
  editLatestProgress,
  RESET_NOTICE,
  setLessonCompleted,
  setLessonNote,
} from '@/lib/progress-write';
import { MAX_QUIZ_REVIEWS, addQuizReview, removeQuizReview } from '@/lib/quiz-review';
import { recordLessonVisit } from '@/lib/resume';
import type { Track } from '@/lib/types';
import { languageNames } from '@/lib/utils';
import { courseLabSelectionFor } from '@/lib/course-labs';
import { toolContractLessonEligible } from '@/lib/tool-contract';
import { diagnosticLessonEligible } from '@/lib/run-diagnostics';
import { Button } from '@/components/ui/button';
import EvidenceCard from '@/components/EvidenceCard';
import LanguagePractice from '@/components/LanguagePractice';

export default function LessonPage({ tracks }: { tracks: Track[] }) {
  const { lessonId } = useParams();
  const { hash, key: locationKey } = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const track = tracks.find((x) => x.lessons.some((l) => l.id === lessonId));
  const lesson = track?.lessons.find((x) => x.id === lessonId);
  const [progress, setProgress] = useAtom(progressAtom);
  const quizKey = lesson
    ? JSON.stringify([
        lesson.id,
        lesson.quiz.question,
        lesson.quiz.options,
        lesson.quiz.answer,
        lesson.quiz.explanation,
      ])
    : '';
  const [quizState, setQuizState] = useState<{
    key: string;
    answer: number;
    checked: boolean;
  } | null>(null);
  const [checks, setChecks] = useState<number[]>([]);
  const [copyState, setCopyState] = useState('');
  const [noteMessage, setNoteMessage] = useState('');
  const [completionMessage, setCompletionMessage] = useState('');
  const visitedLesson = useRef<string | undefined>(undefined);
  const validLessonId = lesson?.id;
  const validTrackId = track?.id;
  const requestedLanguages = searchParams.getAll('language');
  const requestedLanguage =
    track?.id === 'fullstack' && lesson?.track === 'fullstack' && requestedLanguages.length === 1
      ? track.languages.find(
          (value) => value === requestedLanguages[0] && lesson.snippets[value]?.trim(),
        )
      : undefined;
  // An explicit course link selects its language once; ordinary visits preserve preferences.
  useEffect(() => {
    if (!requestedLanguage) return;
    setProgress(
      (previous) =>
        editLatestProgress(previous, (current) =>
          current.language === requestedLanguage
            ? current
            : { ...current, language: requestedLanguage },
        ).progress,
    );
    setCopyState('');
  }, [validLessonId, requestedLanguage, setProgress]);
  useEffect(() => {
    if (!validLessonId || !validTrackId) {
      visitedLesson.current = undefined;
      return;
    }
    if (visitedLesson.current === validLessonId) return;
    visitedLesson.current = validLessonId;
    const visitedAt = new Date().toISOString();
    setProgress(
      (previous) =>
        editLatestProgress(previous, (current) =>
          recordLessonVisit(current, { id: validLessonId, track: validTrackId }, visitedAt),
        ).progress,
    );
  }, [validLessonId, validTrackId, setProgress]);
  useEffect(() => {
    setChecks([]);
    setCopyState('');
    setNoteMessage('');
    setCompletionMessage('');
  }, [lessonId]);
  useEffect(() => {
    // A revised question must not restore a previously checked answer if it changes back.
    setQuizState(null);
  }, [quizKey]);
  useEffect(() => {
    const targetId = hash === '#quiz' ? 'quiz' : hash === '#course-lab' ? 'course-lab' : null;
    if (!validLessonId || !targetId) return;
    const frame = window.requestAnimationFrame(() => {
      document.getElementById(targetId)?.scrollIntoView({ block: 'start' });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [validLessonId, hash, locationKey]);
  if (!lesson || !track)
    return (
      <div className="state-panel">
        <h1>课时不存在</h1>
        <Link to="/">返回总览</Link>
      </div>
    );
  const completed = progress.completed.includes(lesson.id);
  const index = track.lessons.findIndex((x) => x.id === lesson.id);
  const language = track.id === 'agent' ? 'python' : requestedLanguage || progress.language;
  const code = lesson.snippets[language] || '';
  const labSelection = courseLabSelectionFor(lesson.id, language);
  const courseLab = labSelection?.lab;
  const labLanguage = labSelection?.language ?? language;
  const answer = quizState?.key === quizKey ? quizState.answer : null;
  const checked = quizState?.key === quizKey && quizState.checked;
  const quizPassed = checked && answer === lesson.quiz.answer;
  const reviews = progress.quizReview || [];
  const inReview = reviews.some((record) => record.lesson_id === lesson.id);
  const reviewFull = reviews.length >= MAX_QUIZ_REVIEWS;
  const ready = checks.length === lesson.criteria.length && quizPassed;
  function checkAnswer() {
    if (
      !lesson ||
      answer === null ||
      !Number.isInteger(answer) ||
      answer < 0 ||
      answer >= lesson.quiz.options.length
    )
      return;
    const currentLesson = lesson;
    const addedAt = new Date().toISOString();
    setQuizState({ key: quizKey, answer, checked: true });
    setProgress(
      (previous) =>
        editLatestProgress(previous, (current) =>
          answer === currentLesson.quiz.answer
            ? removeQuizReview(current, currentLesson.id)
            : addQuizReview(current, currentLesson.id, addedAt),
        ).progress,
    );
  }
  function updateNote(value: string) {
    if (!lesson) return;
    const lessonId = lesson.id;
    let reset = false;
    let capacityReached = false;
    setProgress((previous) => {
      const result = editLatestProgress(
        previous,
        (current) => {
          const edit = setLessonNote(current, lessonId, value);
          capacityReached = edit.capacityReached;
          return edit.progress;
        },
        { resetId: progress.history_reset_id },
      );
      reset = result.reset;
      return result.progress;
    });
    setNoteMessage(
      reset
        ? RESET_NOTICE
        : capacityReached
          ? '课程笔记已达到 1000 篇上限，本课笔记未保存；可清空一篇已有笔记后再新增。'
          : '',
    );
  }
  function updateCompletion() {
    if (!lesson) return;
    const lessonId = lesson.id;
    const shouldComplete = !completed;
    let capacityReached = false;
    setProgress(
      (previous) =>
        editLatestProgress(previous, (current) => {
          const edit = setLessonCompleted(current, lessonId, shouldComplete);
          capacityReached = edit.capacityReached;
          return edit.progress;
        }).progress,
    );
    setCompletionMessage(
      capacityReached
        ? '完成记录已达到 2000 条上限，本课标记未保存；可取消一条已有完成标记后再新增。'
        : '',
    );
  }
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
          {track.id === 'fullstack' && (
            <div className="language-selector">
              <div>
                <strong>对照其他语言的实现</strong>
                <p>围绕同一课程比较框架；切换时保留本课笔记与验收选择。</p>
              </div>
              <div className="segmented-control" role="group" aria-label="本课参考语言">
                {track.languages.map((value) => (
                  <button
                    key={value}
                    aria-pressed={language === value}
                    className={language === value ? 'selected' : ''}
                    onClick={() => {
                      setProgress(
                        (previous) =>
                          editLatestProgress(previous, (current) =>
                            current.language === value ? current : { ...current, language: value },
                          ).progress,
                      );
                      if (searchParams.has('language')) {
                        setSearchParams(
                          (params) => {
                            const next = new URLSearchParams(params);
                            next.set('language', value);
                            return next;
                          },
                          { replace: true },
                        );
                      }
                      setCopyState('');
                    }}
                  >
                    {languageNames[value]}
                  </button>
                ))}
              </div>
            </div>
          )}
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
          <Button variant="outline" asChild>
            <Link to={`/playground?track=${track.id}&lesson=${lesson.id}&mode=agent`}>
              <Bot size={16} />
              向导师提问本课
            </Link>
          </Button>
          <Button variant="ghost" asChild>
            <a href={`/api/lessons/${lesson.id}/exercise.zip?language=${language}`} download>
              <Download size={16} />
              下载本课练习资料
            </a>
          </Button>
          {toolContractLessonEligible(lesson) && (
            <Button variant="outline" asChild>
              <Link to={'/playground?track=agent&lesson=' + lesson.id + '&mode=tool-contract'}>
                <FlaskConical size={16} />
                运行工具契约实验
              </Link>
            </Button>
          )}
          {diagnosticLessonEligible(lesson) && (
            <Button variant="outline" asChild>
              <Link to={`/playground?track=${track.id}&lesson=${lesson.id}&mode=diagnostics`}>
                <FlaskConical size={16} />
                运行诊断演练
              </Link>
            </Button>
          )}
          {courseLab && (
            <>
              <section
                id="course-lab"
                aria-label={courseLab.title}
                className="my-7 scroll-mt-24 space-y-3 rounded-xl border border-lime-200 bg-lime-50 p-5"
              >
                <h2>{courseLab.title}</h2>
                <p>
                  下载 {languageNames[labLanguage]} {courseLab.description}
                </p>
                <Button
                  variant="outline"
                  className="h-auto max-w-full whitespace-normal py-2 text-left"
                  asChild
                >
                  <a href={`/labs/${courseLab.id}-${labLanguage}.zip`} download>
                    <Download size={16} className="shrink-0" aria-hidden="true" />
                    <span>下载实验 · {languageNames[labLanguage]}</span>
                  </a>
                </Button>
                <p className="text-sm text-slate-600">{courseLab.notice}</p>
              </section>
              <EvidenceCard
                key={`${lesson.id}:${labLanguage}`}
                lesson={lesson}
                language={labLanguage}
                labTitle={courseLab.title}
              />
              {courseLab.sharedFrontend && language !== 'typescript' && (
                <LanguagePractice
                  key={`${lesson.id}:shared-frontend`}
                  lessonId={lesson.id}
                  language="typescript"
                  languages={['typescript']}
                  label="公共前端实验实践记录"
                />
              )}
            </>
          )}
          {((track.id === 'agent' && ['retrieval', 'evaluation'].includes(lesson.stage)) ||
            lesson.id === 'fullstack-ai-rag' ||
            lesson.id === 'fullstack-unit-tests') && (
            <Button variant="outline" asChild>
              <Link to={`/playground?track=${track.id}&lesson=${lesson.id}&mode=evaluation`}>
                <FlaskConical size={16} />
                比较检索配置与指标
              </Link>
            </Button>
          )}
          {track.id === 'fullstack' && lesson.stage === 'ship' && (
            <section
              aria-label="毕业项目骨架"
              className="my-7 space-y-3 rounded-xl border border-lime-200 bg-lime-50 p-5"
            >
              <h2>把前后端联成一个项目</h2>
              <p>
                下载 React + TypeScript 前端与 {languageNames[language]} 服务端，共用固定资料、API
                契约和成功/失败测试，包含依赖锁文件、运行说明与验收记录模板。
              </p>
              <Button variant="outline" asChild>
                <a href={`/starters/fullstack-${language}.zip`} download>
                  <Download size={16} />
                  下载完整项目骨架 · {languageNames[language]}
                </a>
              </Button>
              <p className="text-sm text-slate-600">
                默认演示无需模型费用；真实模型须服务端托管配置。账号、上传、持久化与生产发布仍是毕业实践任务。
              </p>
            </section>
          )}
          {track.id === 'agent' && lesson.stage === 'capstone' && (
            <section
              aria-label="Agent 毕业项目骨架"
              className="my-7 space-y-3 rounded-xl border border-lime-200 bg-lime-50 p-5"
            >
              <h2>构建有证据的研究助手</h2>
              <p>
                下载 React + TypeScript 前端与 Python / FastAPI 服务端，包含只读资料工具、有界 Agent
                循环、实际读取资料的引用校验、固定评测集、依赖锁文件与验收记录模板。
              </p>
              <Button variant="outline" asChild>
                <a href="/starters/agent-research.zip" download>
                  <Download size={16} />
                  下载 Agent 研究助手骨架
                </a>
              </Button>
              <p className="text-sm text-slate-600">
                默认预设演示无需模型费用；真实模式需要服务端托管配置。引用校验不等于事实核验，真实调用与生产发布仍须验收。
              </p>
            </section>
          )}
          {track.id === 'agent' && lesson.stage === 'capstone' && (
            <section
              aria-label="云端毕业项目示例"
              className="my-7 space-y-3 rounded-xl border border-slate-200 bg-slate-50 p-5"
            >
              <h2>跟读云端毕业项目</h2>
              <p>
                Deep AI Research Assistant 在公开仓库持续迭代，把研究助手扩展为版本化资料库、
                用户隔离的研究记录与可导出报告。先运行演示，再沿着提交和测试理解每一步改动。
              </p>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" className="h-auto whitespace-normal py-2" asChild>
                  <a
                    href="https://github.com/frontend-is-magic/deep-ai-research-assistant/tree/develop"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <ExternalLink size={16} className="shrink-0" aria-hidden="true" />
                    源码与运行说明
                  </a>
                </Button>
                <Button variant="outline" className="h-auto whitespace-normal py-2" asChild>
                  <a
                    href="https://github.com/frontend-is-magic/deep-ai-research-assistant/blob/develop/EVIDENCE.md"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <ExternalLink size={16} className="shrink-0" aria-hidden="true" />
                    查看验收证据
                  </a>
                </Button>
                <Button variant="ghost" className="h-auto whitespace-normal py-2" asChild>
                  <a
                    href="https://github.com/frontend-is-magic/deep-ai-research-assistant/pull/1"
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    <ExternalLink size={16} className="shrink-0" aria-hidden="true" />
                    跟踪开发进展
                  </a>
                </Button>
              </div>
              <p className="text-sm text-slate-600">
                按对应提交的验收记录评估完成范围；真实模型与生产环境需单独验收。
                阅读示例后，把自己运行的版本和结果记入下方实践证据。
              </p>
            </section>
          )}
          {((track.id === 'fullstack' && lesson.stage === 'ship') ||
            (track.id === 'agent' && lesson.stage === 'capstone')) && (
            <EvidenceCard key={`${lesson.id}:${language}`} lesson={lesson} language={language} />
          )}
          <h2>检查你的理解</h2>
          <section id="quiz" className="quiz-box scroll-mt-24" aria-label="本课测验">
            <p id="quiz-question">{lesson.quiz.question}</p>
            <div role="radiogroup" aria-labelledby="quiz-question">
              {lesson.quiz.options.map((option, i) => (
                <label className={`quiz-option ${answer === i ? 'chosen' : ''}`} key={option}>
                  <input
                    type="radio"
                    name="quiz"
                    checked={answer === i}
                    onChange={() => setQuizState({ key: quizKey, answer: i, checked: false })}
                  />
                  <span>{option}</span>
                </label>
              ))}
            </div>
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <Button disabled={answer === null} onClick={checkAnswer}>
                检查答案
              </Button>
              <Button asChild variant="outline">
                <Link to="/library?tab=quiz-review">查看测验回顾</Link>
              </Button>
            </div>
            <div className="mt-3 text-sm leading-relaxed text-slate-600">
              先选择，再检查答案。答错的课时进入回顾清单，检查答对后移出。
              {inReview && !checked && (
                <span className="mt-1 block">本课待回顾，请检查当前题目后更新记录。</span>
              )}
            </div>
            {checked && (
              <div className={`quiz-result ${quizPassed ? 'correct' : ''}`} role="status">
                <div>
                  {quizPassed ? '本题检查通过。' : '再想一想。'}
                  {lesson.quiz.explanation}
                </div>
                <div className="mt-2">
                  {inReview
                    ? '本课待回顾（本浏览器记录）；重新检查答对后可移出。'
                    : quizPassed
                      ? '本课当前不在测验回顾清单中。'
                      : reviewFull
                        ? `回顾清单已满（${MAX_QUIZ_REVIEWS} 课），本课未加入。可先在学习库移除记录，再检查答案。`
                        : '本课当前不在测验回顾清单中；再次检查可重新加入。'}
                </div>
              </div>
            )}
          </section>
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
          <section aria-label="本课验收" className="lesson-checklist">
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
              onClick={updateCompletion}
            >
              <CheckCircle2 size={16} />
              {completed ? '取消完成标记' : '标记本课完成'}
            </Button>
            {!completed && !ready && <small>先检查答案并答对，再确认全部验收项。</small>}
            {completionMessage && <p role="status">{completionMessage}</p>}
          </section>
          <LanguagePractice
            key={`${lesson.id}:${language}`}
            lessonId={lesson.id}
            language={language}
            languages={track.id === 'agent' ? ['python'] : track.languages}
          />
          <div className="notes-box">
            <h3>我的思考</h3>
            <p>记录你的理解、问题和实验结果。</p>
            <textarea
              aria-label="课程笔记"
              placeholder="我学到了什么？下一步想验证什么？"
              value={progress.notes[lesson.id] || ''}
              maxLength={10000}
              onChange={(event) => updateNote(event.target.value)}
            />
            <small>自动保存到当前浏览器</small>
            {noteMessage && <p role="status">{noteMessage}</p>}
          </div>
        </aside>
      </div>
    </div>
  );
}
