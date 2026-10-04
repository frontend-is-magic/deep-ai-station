import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { labPracticeGroups } from '@/lib/lab-practice';
import type { Progress, Track, TrackId } from '@/lib/types';
import { languageNames } from '@/lib/utils';

export default function LabPracticeList({
  tracks,
  trackId,
  progress,
}: {
  tracks: Track[];
  trackId: TrackId;
  progress: Progress;
}) {
  const [pendingOnly, setPendingOnly] = useState(true);
  const groups = labPracticeGroups(tracks, trackId, progress);
  const visible = pendingOnly
    ? groups.filter((group) => group.lessons.some((entry) => !entry.practiceRecorded))
    : groups;
  const language = trackId === 'agent' ? 'python' : progress.language;

  return (
    <section
      aria-label="可运行实践清单"
      className="mb-8 min-w-0 space-y-4 rounded-xl border border-slate-200 bg-white p-4 [overflow-wrap:anywhere] sm:p-5"
    >
      <div className="space-y-2">
        <h2 className="text-base font-semibold">可运行实践清单</h2>
        <p className="text-sm leading-relaxed text-slate-600">
          状态由你在课时页自行记录，与课程完成独立。同一实验包关联的课时分别记录；展开后进入对应课程下载。
        </p>
        <p className="text-xs text-slate-600">
          当前语言：{languageNames[language]}
          {trackId === 'agent' ? '（Agent 固定使用 Python）' : ''}
        </p>
      </div>
      <label className="flex w-fit max-w-full items-start gap-2 text-sm">
        <input
          type="checkbox"
          checked={pendingOnly}
          onChange={(event) => setPendingOnly(event.target.checked)}
          className="mt-1 shrink-0"
        />
        <span>只看未记录实践</span>
      </label>
      {!groups.length ? (
        <p role="status" className="text-sm leading-relaxed text-slate-600">
          当前路线与语言暂无可运行实验
        </p>
      ) : !visible.length ? (
        <div className="space-y-3">
          <p role="status" className="text-sm leading-relaxed text-slate-600">
            当前语言的可运行实践均已记录
          </p>
          <Button type="button" variant="outline" size="sm" onClick={() => setPendingOnly(false)}>
            查看全部实践
          </Button>
        </div>
      ) : (
        <div className="min-w-0 space-y-2">
          {visible.map((group) => {
            const pending = group.lessons.filter((entry) => !entry.practiceRecorded).length;
            return (
              <article
                key={`${group.lab.id}:${group.language}`}
                aria-label={group.lab.title}
                data-lab-id={group.lab.id}
                data-language={group.language}
                className="min-w-0 rounded-lg border border-border"
              >
                <details>
                  <summary
                    aria-label={`查看关联课时：${group.lab.title}`}
                    className="cursor-pointer rounded-lg px-4 py-3 text-sm focus-visible:outline-offset-2"
                  >
                    <span className="font-medium">{group.lab.title}</span>
                    <span className="ml-2 inline-block text-xs text-slate-600">
                      {languageNames[group.language]} · {pending}/{group.lessons.length} 课未记录
                    </span>
                  </summary>
                  <ul className="min-w-0 space-y-3 border-t border-border px-4 py-3">
                    {group.lessons.map(({ lesson, href, practiceRecorded }) => (
                      <li
                        key={lesson.id}
                        data-lesson-id={lesson.id}
                        className="flex min-w-0 flex-wrap items-center justify-between gap-2"
                      >
                        <Link
                          to={href}
                          aria-label={`进入实验：${lesson.title} · ${languageNames[group.language]}`}
                          className="flex min-w-0 items-start gap-2 text-sm text-lime-900 underline underline-offset-4"
                        >
                          <span className="min-w-0">{lesson.title}</span>
                          <ArrowRight size={15} aria-hidden="true" className="mt-1 shrink-0" />
                        </Link>
                        <span
                          className={`text-xs ${practiceRecorded ? 'text-lime-800' : 'text-slate-600'}`}
                        >
                          {practiceRecorded ? '实践已记录' : '实践未记录'}
                        </span>
                      </li>
                    ))}
                  </ul>
                </details>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
