import { useAtom } from 'jotai';
import { Link } from 'react-router-dom';
import { StickyNote } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { progressAtom } from '@/lib/state';
import type { RetrievalEvaluationResponse } from '@/lib/retrievalEvaluation';
import {
  EVALUATION_REFLECTION_LIMIT,
  prepareEvaluationNote,
  saveEvaluationNote,
  type EvaluationLesson,
} from '@/lib/retrieval-note';

export default function RetrievalEvaluationNote({
  result,
  lesson,
  reflection,
  onReflectionChange,
}: {
  result: RetrievalEvaluationResponse;
  lesson: EvaluationLesson;
  reflection: string;
  onReflectionChange: (value: string) => void;
}) {
  const [progress, setProgress] = useAtom(progressAtom);
  const prepared = prepareEvaluationNote(progress, result, lesson, reflection);
  const saved = prepared.status === 'saved';
  const feedback = {
    ready: '可以保存本次配置、指标和你的观察。课程完成与语言实践记录保持不变。',
    saved: '本次评测摘要已保存在本课笔记，可随学习记录导出备份。',
    'too-long': '学习笔记空间不足或观察过长，请先整理内容后再保存。',
    'missing-reflection': '请填写观察与下一步，记录你从这次对比中得出的判断。',
    invalid: '当前结果或笔记无法保存，请检查笔记并重新运行评测。',
  }[prepared.status];

  return (
    <section
      aria-label="保存评测摘要"
      className="min-w-0 space-y-4 rounded-xl border border-lime-200 bg-lime-50/50 p-4"
    >
      <div>
        <h3 className="text-sm font-semibold">把这次比较留在课程笔记中</h3>
        <p className="mt-2 break-words text-sm">保存目标：{lesson.title}</p>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          摘要会追加到本课笔记，保留原有内容；本课笔记最多 10,000
          字符。固定教学集的指标不代表真实回答质量。
        </p>
      </div>
      {!saved && (
        <label className="block text-sm font-medium">
          观察与下一步（必填）
          <textarea
            aria-label="观察与下一步"
            required
            maxLength={EVALUATION_REFLECTION_LIMIT}
            rows={4}
            value={reflection}
            onChange={(event) => onReflectionChange(event.target.value)}
            placeholder="哪一组漏检更少？是否增加了误召回？下一步准备验证什么？"
            className="mt-2 block w-full min-w-0 rounded-lg border border-border bg-white p-3 text-sm font-normal"
          />
          <span className="mt-1 block text-xs font-normal text-muted-foreground">
            {reflection.length}/{EVALUATION_REFLECTION_LIMIT} 字符
          </span>
        </label>
      )}
      {prepared.entry && !saved && (
        <details className="min-w-0 text-sm">
          <summary className="cursor-pointer">预览将追加的内容</summary>
          <pre className="mt-2 max-w-full whitespace-pre-wrap break-words rounded-lg bg-white p-3 text-xs leading-relaxed [overflow-wrap:anywhere]">
            {prepared.entry}
          </pre>
        </details>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          disabled={prepared.status !== 'ready'}
          onClick={() =>
            setProgress((current) => saveEvaluationNote(current, result, lesson, reflection))
          }
          className="h-auto max-w-full whitespace-normal py-2 text-left"
        >
          <StickyNote size={16} className="shrink-0" aria-hidden="true" />
          <span>{saved ? '已保存到本课笔记' : '保存评测摘要到本课笔记'}</span>
        </Button>
        <Link to={`/lesson/${lesson.id}`} className="text-sm underline underline-offset-2">
          查看本课笔记
        </Link>
      </div>
      <p aria-live="polite" className="break-words text-xs leading-relaxed text-muted-foreground">
        {feedback}
      </p>
    </section>
  );
}
