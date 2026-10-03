import { useState } from 'react';
import { useAtom, useStore } from 'jotai';
import { Link } from 'react-router-dom';
import { ArrowRight, Download, FileText } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { latestProgress, progressAtom } from '@/lib/state';
import {
  evidenceRecordForExport,
  filterEvidenceEntries,
  resolveEvidenceEntries,
} from '@/lib/evidence-library';
import type { Language, Track, TrackId } from '@/lib/types';
import { languageNames } from '@/lib/utils';

const contentFields = [
  { key: 'revision', label: '代码版本 / commit' },
  { key: 'command', label: '验证命令' },
  { key: 'success', label: '成功输入与实际结果' },
  { key: 'failure', label: '失败输入与实际结果' },
  { key: 'pending', label: '未验证事项' },
] as const;
const trackNames: Record<TrackId, string> = {
  agent: 'AI Agent',
  fullstack: 'AI 全栈',
};
const selectStyle = 'mt-1 block w-full rounded-lg border border-border bg-white p-2 text-sm';

export default function EvidenceLibrary({ tracks }: { tracks: Track[] }) {
  const [progress, setProgress] = useAtom(progressAtom);
  const store = useStore();
  const [track, setTrack] = useState<TrackId | 'all'>('all');
  const [language, setLanguage] = useState<Language | 'all'>('all');
  const [pendingOnly, setPendingOnly] = useState(false);
  const [message, setMessage] = useState('');
  const entries = resolveEvidenceEntries(progress, tracks);
  const visible = filterEvidenceEntries(entries, {
    track,
    language,
    pendingOnly,
  });

  function clearFilters() {
    setTrack('all');
    setLanguage('all');
    setPendingOnly(false);
  }

  function download(lessonId: string, recordLanguage: Language) {
    const current = latestProgress(store.get(progressAtom));
    const record = evidenceRecordForExport(current, lessonId, recordLanguage);
    setProgress(current);
    if (!record) {
      setMessage('记录已删除，未下载；请检查当前记录。');
      return;
    }
    let url: string | undefined;
    try {
      url = URL.createObjectURL(
        new Blob([JSON.stringify(record, null, 2) + '\n'], {
          type: 'application/json;charset=utf-8',
        }),
      );
      const link = document.createElement('a');
      link.href = url;
      link.download = `practice-evidence-${recordLanguage}.json`;
      link.click();
      setMessage('此条记录已导出。恢复学习记录请使用设置中的完整学习记录备份。');
    } catch {
      setMessage('下载未完成，请重试。');
    } finally {
      if (url) URL.revokeObjectURL(url);
    }
  }

  return (
    <section aria-label="实践证据索引" className="min-w-0 space-y-5 [overflow-wrap:anywhere]">
      <div className="space-y-2">
        <h2 className="text-lg font-semibold">回看自己的实践证据</h2>
        <p className="text-sm leading-relaxed text-slate-600">
          内容由你自行记录，未经平台核验；有内容或没有未验证说明都不表示验收通过。单条 JSON
          仅供查阅，不能直接恢复学习记录；恢复请使用设置中的完整备份。未知课程和空旧记录仍保留。
        </p>
      </div>
      <div className="space-y-3 rounded-xl border border-border bg-white p-4">
        <div className="grid min-w-0 gap-3 sm:grid-cols-2">
          <label className="min-w-0 text-sm">
            证据路线
            <select
              aria-label="证据路线"
              value={track}
              onChange={(event) => setTrack(event.target.value as TrackId | 'all')}
              className={selectStyle}
            >
              <option value="all">全部路线</option>
              <option value="agent">AI Agent</option>
              <option value="fullstack">AI 全栈</option>
            </select>
          </label>
          <label className="min-w-0 text-sm">
            证据语言
            <select
              aria-label="证据语言"
              value={language}
              onChange={(event) => setLanguage(event.target.value as Language | 'all')}
              className={selectStyle}
            >
              <option value="all">全部语言</option>
              <option value="typescript">TypeScript</option>
              <option value="go">Go</option>
              <option value="python">Python</option>
            </select>
          </label>
        </div>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            checked={pendingOnly}
            onChange={(event) => setPendingOnly(event.target.checked)}
            className="mt-1 shrink-0"
          />
          <span>只看填写了未验证说明的记录</span>
        </label>
        <p className="text-xs leading-relaxed text-slate-600">
          此筛选只检查有没有填写文字；填写“无”也会显示，不据此判断通过或失败。
        </p>
      </div>
      <p aria-live="polite" className="text-sm text-slate-600">
        显示 {visible.length} / {entries.length} 条记录
      </p>
      <p role="status" className="text-sm text-slate-600">
        {message}
      </p>
      {visible.length > 0 && (
        <ul className="min-w-0 space-y-4">
          {visible.map(
            ({ record, track: entryTrack, lesson, href, editable, hasContent, hasPending }) => (
              <li key={`${record.lesson_id}:${record.language}`} className="min-w-0">
                <article
                  aria-label={`${record.lesson_id} · ${record.language}`}
                  data-lesson-id={record.lesson_id}
                  data-language={record.language}
                  className="min-w-0 space-y-4 rounded-xl border border-border bg-white p-4 sm:p-5"
                >
                  <div className="space-y-2">
                    <p className="text-xs text-slate-500">
                      {trackNames[entryTrack]} · {languageNames[record.language]}
                    </p>
                    <h3 className="break-words font-semibold">
                      {lesson ? lesson.title : `当前课程目录未匹配：${record.lesson_id}`}
                    </h3>
                    <p className="break-all text-xs text-slate-600">课程 ID：{record.lesson_id}</p>
                    <p className="break-words text-xs text-slate-600">
                      记录更新时间：{record.updated_at}
                    </p>
                  </div>
                  {!hasContent && <p className="text-sm text-slate-600">旧记录：尚未填写内容</p>}
                  <div className="rounded-lg bg-muted p-3 text-sm">
                    <p className="font-medium">未验证说明</p>
                    <p className="mt-1 line-clamp-3 whitespace-pre-wrap break-words">
                      {hasPending ? record.pending : '未填写未验证说明'}
                    </p>
                  </div>
                  <details className="min-w-0 text-sm">
                    <summary className="cursor-pointer font-medium">查看填写内容</summary>
                    <dl className="mt-4 min-w-0 space-y-4">
                      {contentFields.map(({ key, label }) => (
                        <div key={key} className="min-w-0">
                          <dt className="font-medium">{label}</dt>
                          <dd className="mt-1 whitespace-pre-wrap break-words rounded-lg bg-muted p-3 leading-relaxed">
                            {record[key] || '未填写'}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  </details>
                  {href && !editable && (
                    <p className="text-xs leading-relaxed text-slate-600">
                      本课当前没有此记录的编辑入口，可在此查看和导出。
                    </p>
                  )}
                  {!href && (
                    <p className="text-xs leading-relaxed text-slate-600">
                      当前目录无法为此记录提供有效课程链接；原始内容仍可查看和导出。
                    </p>
                  )}
                  <div className="flex min-w-0 flex-wrap gap-3">
                    {lesson && href && (
                      <Button
                        asChild
                        size="sm"
                        className="h-auto max-w-full whitespace-normal py-2 text-left"
                      >
                        <Link to={href}>
                          <span>
                            打开课程：{lesson.title} · {languageNames[record.language]}
                          </span>
                          <ArrowRight size={14} className="shrink-0" aria-hidden="true" />
                        </Link>
                      </Button>
                    )}
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-auto max-w-full whitespace-normal py-2 text-left"
                      onClick={() => download(record.lesson_id, record.language)}
                    >
                      <Download size={14} className="shrink-0" aria-hidden="true" />
                      导出此条记录 JSON
                    </Button>
                  </div>
                </article>
              </li>
            ),
          )}
        </ul>
      )}
      {entries.length === 0 && (
        <div className="state-panel">
          <FileText aria-hidden="true" />
          <h3>暂时没有实践证据记录</h3>
          <p>在可运行实验或毕业课程中填写实践证据，这里会按课程和语言保留索引。</p>
        </div>
      )}
      {entries.length > 0 && visible.length === 0 && (
        <div className="state-panel">
          <FileText aria-hidden="true" />
          <h3>当前筛选没有匹配记录</h3>
          <p>记录仍保留在学习库，可清除筛选后查看。</p>
          <Button variant="outline" onClick={clearFilters}>
            清除筛选
          </Button>
        </div>
      )}
    </section>
  );
}
