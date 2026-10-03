import { useState } from 'react';
import { useAtom } from 'jotai';
import { Download } from 'lucide-react';
import { latestProgress, progressAtom } from '@/lib/state';
import { MAX_EVIDENCE_RECORDS, evidenceFor, evidenceMarkdown, saveEvidence } from '@/lib/evidence';
import { languageNames } from '@/lib/utils';
import type { EvidenceRecord, Language } from '@/lib/types';
import { Button } from '@/components/ui/button';

type EvidenceField = 'revision' | 'command' | 'success' | 'failure' | 'pending';
const fields: { key: EvidenceField; label: string; placeholder: string; limit: number }[] = [
  {
    key: 'revision',
    label: '代码版本 / commit',
    placeholder: 'Git commit 或本地版本标识',
    limit: 200,
  },
  {
    key: 'command',
    label: '验证命令',
    placeholder: '填写你实际执行的命令',
    limit: 2000,
  },
  {
    key: 'success',
    label: '成功输入与实际结果',
    placeholder: '记录成功输入、观察到的结果与证据位置',
    limit: 2000,
  },
  {
    key: 'failure',
    label: '失败输入与实际结果',
    placeholder: '记录失败输入、实际错误和恢复情况',
    limit: 2000,
  },
  {
    key: 'pending',
    label: '未验证事项',
    placeholder: '注明未运行、失败或仍待确认的部分',
    limit: 2000,
  },
];

export default function EvidenceCard({
  lesson,
  language,
  labTitle,
}: {
  lesson: { id: string; title: string };
  language: Language;
  labTitle?: string;
}) {
  const [progress, setProgress] = useAtom(progressAtom);
  const [message, setMessage] = useState('');
  const record = evidenceFor(progress, lesson.id, language);
  const capacityReached = !record && (progress.evidence?.length || 0) >= MAX_EVIDENCE_RECORDS;
  const hasContent = fields.some((field) => Boolean(record?.[field.key].trim()));

  function update(field: EvidenceField, value: string) {
    const updatedAt = new Date().toISOString();
    setMessage('');
    setProgress((previous) => {
      const current = latestProgress(previous);
      const existing = evidenceFor(current, lesson.id, language);
      const next: EvidenceRecord = {
        lesson_id: lesson.id,
        language,
        revision: existing?.revision || '',
        command: existing?.command || '',
        success: existing?.success || '',
        failure: existing?.failure || '',
        pending: existing?.pending || '',
        [field]: value,
        updated_at: updatedAt,
      };
      return saveEvidence(current, next);
    });
  }

  function download() {
    if (!record || !hasContent) return;
    let url: string | undefined;
    try {
      url = URL.createObjectURL(
        new Blob([evidenceMarkdown(lesson, record, { labTitle })], {
          type: 'text/markdown;charset=utf-8',
        }),
      );
      const link = document.createElement('a');
      link.href = url;
      link.download = `${lesson.id}-${language}-evidence.md`;
      link.click();
      setMessage('实践记录已导出为 Markdown。');
    } catch {
      setMessage('下载未完成，请重试。');
    } finally {
      if (url) URL.revokeObjectURL(url);
    }
  }

  return (
    <section
      aria-label={labTitle ? '实验实践证据' : '毕业实践证据'}
      className="my-7 min-w-0 space-y-4 rounded-xl border border-slate-200 bg-white p-4 sm:p-5"
    >
      <div>
        <h2>{labTitle ? '记录本课实验依据' : '记录毕业实践证据'}</h2>
        {labTitle && <p className="break-words text-sm text-slate-600">当前实验：{labTitle}</p>}
        <p className="text-sm text-slate-600">
          当前记录：{languageNames[language]}
          。自动保存到当前浏览器，并随学习记录备份；不同课程和语言分别记录。不要填写密钥、访问码或认证信息。
        </p>
        <p className="text-sm text-slate-600">
          {labTitle
            ? '内容由你填写，不自动完成课程或语言实践，也不表示平台已验证。'
            : '内容由你填写，不自动勾选验收项或完成课程，也不表示平台已验证。'}
        </p>
        <p className="text-sm text-slate-600">清空本课五项内容会移除这条记录并释放名额。</p>
      </div>
      <div className="grid min-w-0 gap-4">
        {fields.map((field) => {
          const id = `evidence-${lesson.id}-${language}-${field.key}`;
          const value = record?.[field.key] || '';
          const className =
            'mt-2 block w-full min-w-0 rounded-lg border border-slate-300 bg-white p-3 text-sm disabled:bg-slate-50 disabled:text-slate-500';
          return (
            <div key={field.key} className="min-w-0">
              <label htmlFor={id} className="block text-sm font-medium">
                {field.label}
              </label>
              {field.key === 'revision' ? (
                <input
                  id={id}
                  aria-label={field.label}
                  value={value}
                  maxLength={field.limit}
                  placeholder={field.placeholder}
                  disabled={capacityReached}
                  onChange={(event) => update(field.key, event.target.value)}
                  className={className}
                />
              ) : (
                <textarea
                  id={id}
                  aria-label={field.label}
                  value={value}
                  maxLength={field.limit}
                  placeholder={field.placeholder}
                  disabled={capacityReached}
                  rows={3}
                  onChange={(event) => update(field.key, event.target.value)}
                  className={className}
                />
              )}
              <p className="mt-1 text-xs text-slate-500">
                {value.length}/{field.limit} 字符
              </p>
            </div>
          );
        })}
      </div>
      <Button
        variant="outline"
        className="max-w-full whitespace-normal"
        disabled={!hasContent}
        onClick={download}
      >
        <Download size={16} className="shrink-0" />
        下载实践证据 Markdown
      </Button>
      <p role="status" className="break-words text-sm text-slate-600">
        {capacityReached
          ? `实践记录已达到 ${MAX_EVIDENCE_RECORDS} 条容量；当前课程与语言无法新增，已有记录不会被覆盖。`
          : message ||
            (hasContent ? '本课实践记录随学习记录保存，可导出备份。' : '尚未填写实践证据。')}
      </p>
    </section>
  );
}
