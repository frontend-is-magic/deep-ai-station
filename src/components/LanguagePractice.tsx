import { useState } from 'react';
import { useAtom } from 'jotai';
import { CheckCircle2, RotateCcw } from 'lucide-react';
import { progressAtom } from '@/lib/state';
import { editLatestProgress } from '@/lib/progress-write';
import { MAX_PRACTICE_RECORDS, practiceFor, removePractice, savePractice } from '@/lib/practice';
import type { Language } from '@/lib/types';
import { languageNames } from '@/lib/utils';
import { Button } from '@/components/ui/button';

export default function LanguagePractice({
  lessonId,
  language,
  languages,
  label: sectionLabel = '本课实践记录',
}: {
  lessonId: string;
  language: Language;
  languages: readonly Language[];
  label?: string;
}) {
  const [progress, setProgress] = useAtom(progressAtom);
  const [confirmed, setConfirmed] = useState(false);
  const record = practiceFor(progress, lessonId, language);
  const atCapacity = (progress.practice?.length ?? 0) >= MAX_PRACTICE_RECORDS;
  const label = languageNames[language];

  function markComplete() {
    if (!confirmed || record || atCapacity) return;
    const completedAt = new Date().toISOString();
    setProgress(
      (previous) =>
        editLatestProgress(previous, (current) =>
          savePractice(current, {
            lesson_id: lessonId,
            language,
            completed_at: completedAt,
          }),
        ).progress,
    );
    setConfirmed(false);
  }

  function remove() {
    setProgress(
      (previous) =>
        editLatestProgress(previous, (current) => removePractice(current, lessonId, language))
          .progress,
    );
    setConfirmed(false);
  }

  return (
    <section
      aria-label={sectionLabel}
      className="mb-5 min-w-0 space-y-4 rounded-xl border border-slate-200 bg-white p-5"
    >
      <div>
        <h3 className="text-sm font-semibold">{sectionLabel}</h3>
        <p className="mt-2 text-xs leading-relaxed text-slate-600">
          由你自行确认，未由平台运行验收。与上方课程完成标记独立保存，不改变课程笔记、测验答案或验收选择。
        </p>
      </div>
      <ul aria-label="各语言实践状态" className="flex flex-wrap gap-2">
        {languages.map((value) => {
          const saved = Boolean(practiceFor(progress, lessonId, value));
          return (
            <li
              key={value}
              aria-current={value === language ? 'true' : undefined}
              className={`max-w-full rounded-md border px-2 py-1 text-xs ${
                saved
                  ? 'border-lime-200 bg-lime-50 text-lime-900'
                  : 'border-slate-200 bg-slate-50 text-slate-600'
              }`}
            >
              {`${languageNames[value]} · ${saved ? '已记录' : '未记录'}`}
            </li>
          );
        })}
      </ul>
      <p className="text-sm font-medium">当前实践：{label}</p>
      {record ? (
        <div className="space-y-3">
          <p className="break-words text-xs leading-relaxed text-slate-600">
            记录时间（本机）：
            <time dateTime={record.completed_at}>
              {new Date(record.completed_at).toLocaleString('zh-CN')}
            </time>
          </p>
          <Button
            type="button"
            variant="outline"
            className="h-auto w-full whitespace-normal py-2"
            onClick={remove}
          >
            <RotateCcw size={15} className="shrink-0" aria-hidden="true" />
            <span>撤销 {label} 实践记录</span>
          </Button>
        </div>
      ) : (
        <div className="space-y-3">
          <label className="flex items-start gap-2 text-xs leading-relaxed text-slate-700">
            <input
              type="checkbox"
              checked={confirmed}
              disabled={atCapacity}
              onChange={(event) => setConfirmed(event.target.checked)}
              className="mt-1 shrink-0"
            />
            <span>{`我已用${label}运行成功与失败样例，并记录实际结果`}</span>
          </label>
          <Button
            type="button"
            className="h-auto w-full whitespace-normal py-2"
            disabled={!confirmed || atCapacity}
            onClick={markComplete}
          >
            <CheckCircle2 size={15} className="shrink-0" aria-hidden="true" />
            <span>标记 {label} 实践完成</span>
          </Button>
        </div>
      )}
      <p role="status" className="break-words text-xs leading-relaxed text-slate-600">
        {atCapacity
          ? `实践记录已达 ${MAX_PRACTICE_RECORDS} 条上限；可撤销已有记录后再新增。`
          : record
            ? `${label} 实践已记录到当前浏览器，可随学习记录导出备份。`
            : `${label} 实践尚未记录；运行并核对结果后再确认。`}
      </p>
    </section>
  );
}
