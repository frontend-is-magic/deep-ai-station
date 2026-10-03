import { useAtom } from 'jotai';
import { Link } from 'react-router-dom';
import { ArrowRight, BookOpen } from 'lucide-react';
import { latestProgress, progressAtom } from '@/lib/state';
import { removeQuizReview, resolveQuizReviews } from '@/lib/quiz-review';
import type { Track } from '@/lib/types';
import { Button } from '@/components/ui/button';

export default function QuizReviewList({ tracks }: { tracks: Track[] }) {
  const [progress, setProgress] = useAtom(progressAtom);
  const { available, unavailable } = resolveQuizReviews(progress, tracks);
  function remove(lessonId: string) {
    setProgress((previous) => removeQuizReview(latestProgress(previous), lessonId));
  }
  return (
    <section aria-label="测验回顾" className="min-w-0 space-y-5">
      <div className="space-y-2">
        <h2 className="text-lg font-semibold">待复习的课时</h2>
        <p className="text-sm text-slate-600" aria-live="polite">
          可回顾 {available.length} 课 · 当前不可用 {unavailable.length} 条
        </p>
        <p className="text-sm leading-relaxed text-slate-600">
          检查答错的课时会出现在这里。回到课程检查当前测验，答对后移出；也可以手动移除。
          清单只记录回顾安排，不改变共同完成或语言实践记录。
        </p>
      </div>
      {available.length > 0 && (
        <ul className="space-y-3">
          {available.map(({ record, lesson }) => (
            <li key={record.lesson_id} className="min-w-0">
              <article
                aria-label={lesson.title}
                className="rounded-xl border border-border bg-white p-5"
              >
                <div className="text-xs text-slate-500">
                  {lesson.track === 'agent' ? 'AI Agent 工程' : 'AI 全栈工程'}
                </div>
                <h3 className="mt-2 break-words font-semibold">{lesson.title}</h3>
                <p className="mt-2 text-sm text-slate-600">
                  加入日期：
                  <time dateTime={record.added_at}>{record.added_at.slice(0, 10)}（UTC）</time>
                </p>
                <div className="mt-4 flex flex-wrap gap-3">
                  <Button asChild size="sm">
                    <Link to={`/lesson/${lesson.id}#quiz`}>
                      回顾本课
                      <ArrowRight size={14} aria-hidden="true" />
                    </Link>
                  </Button>
                  <Button variant="outline" size="sm" onClick={() => remove(record.lesson_id)}>
                    移除回顾记录
                  </Button>
                </div>
              </article>
            </li>
          ))}
        </ul>
      )}
      {unavailable.length > 0 && (
        <section aria-label="不可用回顾记录" className="space-y-3">
          <h3 className="font-semibold">当前课程中找不到的回顾记录</h3>
          <p className="text-sm text-slate-600">
            这些课时暂时无法打开。记录仍会包含在导出的学习备份中，也可以逐条移除。
          </p>
          <ul className="space-y-3">
            {unavailable.map((record) => (
              <li key={record.lesson_id} className="min-w-0">
                <article
                  aria-label={record.lesson_id}
                  className="rounded-xl border border-border bg-slate-50 p-5"
                >
                  <p className="break-all font-mono text-sm">{record.lesson_id}</p>
                  <p className="mt-2 text-sm text-slate-600">
                    加入日期：
                    <time dateTime={record.added_at}>{record.added_at.slice(0, 10)}（UTC）</time>
                  </p>
                  <Button
                    className="mt-4"
                    variant="outline"
                    size="sm"
                    onClick={() => remove(record.lesson_id)}
                  >
                    移除回顾记录
                  </Button>
                </article>
              </li>
            ))}
          </ul>
        </section>
      )}
      {available.length === 0 && unavailable.length === 0 && (
        <div className="state-panel">
          <BookOpen aria-hidden="true" />
          <h3>暂时没有待回顾的课时</h3>
          <p>在课程中检查答案，答错的课时会加入这里。清单为空不代表已经掌握所有课程。</p>
          <Button asChild>
            <Link to="/">
              继续学习
              <ArrowRight size={16} aria-hidden="true" />
            </Link>
          </Button>
        </div>
      )}
    </section>
  );
}
