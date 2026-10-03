import { useCallback, useEffect, useRef, useState } from 'react';
import { useAtom, useStore } from 'jotai';
import { Link } from 'react-router-dom';
import { FlaskConical, Loader2, Play, Square, StickyNote } from 'lucide-react';
import { api } from '@/lib/api';
import { progressAtom } from '@/lib/state';
import type { Lesson, Progress, TrackId } from '@/lib/types';
import { editLatestProgress, RESET_NOTICE } from '@/lib/progress-write';
import {
  parseToolContractCatalog,
  parseToolContractReport,
  toolContractLessonEligible,
  type ToolContractCatalog,
  type ToolContractLesson,
  type ToolContractReport,
  type ToolContractRequest,
} from '@/lib/tool-contract';
import {
  TOOL_CONTRACT_REFLECTION_LIMIT,
  prepareToolContractNote,
  saveToolContractNote,
} from '@/lib/tool-contract-note';
import { Button } from '@/components/ui/button';

const fieldStyle = 'mt-2 block w-full min-w-0 rounded-lg border border-border bg-white p-3 text-sm';
const jsonStyle =
  'max-w-full whitespace-pre-wrap break-words rounded-lg bg-muted p-4 text-xs leading-relaxed [overflow-wrap:anywhere]';
const reasons = {
  invalid_arguments_json: 'JSON 无效：需要一个对象，且不能包含重复字段或非有限数值。',
  invalid_arguments: '参数不符合工具 schema。检查类型、必填字段、长度和额外字段。',
  unknown_tool: '工具不在服务端只读白名单中。',
  lesson_not_in_track: '目标课时不存在，或不属于当前 Agent 路线。',
};

function ToolNote({
  report,
  resetId,
  lesson,
  reflection,
  onReflection,
}: {
  report: ToolContractReport;
  resetId: Progress['history_reset_id'];
  lesson: ToolContractLesson;
  reflection: string;
  onReflection: (value: string) => void;
}) {
  const [progress, setProgress] = useAtom(progressAtom);
  const prepared = prepareToolContractNote(progress, report, lesson, reflection);
  const saved = prepared.status === 'saved';
  const feedback = {
    ready: '可将原始参数、实际结果和你的观察追加到本课笔记。',
    saved: '本次工具实验已保存，可随学习记录导出备份。',
    'too-long': '学习笔记空间不足或观察过长，请先整理内容后再保存。',
    'missing-reflection': '请填写观察与下一步，说明为什么成功或失败。',
    invalid: '当前结果或笔记无法保存，请检查笔记并重新运行。',
  }[prepared.status];
  return (
    <section
      aria-label="保存工具实验"
      className="min-w-0 space-y-4 rounded-xl border border-lime-200 bg-lime-50/50 p-4"
    >
      <h3 className="font-semibold">把成功和失败都留在本课笔记中</h3>
      <p className="break-words text-sm">保存目标：{lesson.title}</p>
      <p className="text-xs leading-relaxed text-muted-foreground">
        保留原有笔记，不自动完成课程或语言实践。本课笔记最多 10,000
        字符；只记录公开课程参数，请勿填写密钥。
      </p>
      {!saved && (
        <label className="block text-sm font-medium">
          观察与下一步（必填）
          <textarea
            aria-label="观察与下一步"
            required
            maxLength={TOOL_CONTRACT_REFLECTION_LIMIT}
            rows={3}
            value={reflection}
            onChange={(event) => onReflection(event.target.value)}
            className={fieldStyle}
            placeholder="哪条约束决定了结果？下一步准备改变什么参数？"
          />
          <span className="mt-1 block text-xs text-muted-foreground">
            {reflection.length}/{TOOL_CONTRACT_REFLECTION_LIMIT} 字符
          </span>
        </label>
      )}
      {prepared.entry && !saved && (
        <details>
          <summary className="cursor-pointer text-sm">预览将追加的内容</summary>
          <pre className={'mt-3 ' + jsonStyle}>{prepared.entry}</pre>
        </details>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button
          disabled={progress.history_reset_id !== resetId || prepared.status !== 'ready'}
          onClick={() =>
            setProgress(
              (previous) =>
                editLatestProgress(
                  previous,
                  (current) => saveToolContractNote(current, report, lesson, reflection),
                  { resetId },
                ).progress,
            )
          }
          className="h-auto max-w-full whitespace-normal py-2 text-left"
        >
          <StickyNote size={16} className="shrink-0" />
          {saved ? '已保存到本课笔记' : '保存工具实验到本课笔记'}
        </Button>
        <Link to={'/lesson/' + lesson.id} className="text-sm underline underline-offset-2">
          查看本课笔记
        </Link>
      </div>
      <p aria-live="polite" className="text-xs leading-relaxed text-muted-foreground">
        {feedback}
      </p>
    </section>
  );
}

function ToolSession({ lesson }: { lesson: ToolContractLesson }) {
  const [progress, setProgress] = useAtom(progressAtom);
  const store = useStore();
  const [catalog, setCatalog] = useState<ToolContractCatalog | null>(null);
  const [catalogError, setCatalogError] = useState('');
  const [reload, setReload] = useState(0);
  const [toolName, setToolName] = useState('knowledge_search');
  const [argumentsJson, setArgumentsJson] = useState('{"query":"MCP"}');
  const [completed, setCompleted] = useState<{
    report: ToolContractReport;
    resetId: Progress['history_reset_id'];
  } | null>(null);
  const report = completed?.resetId === progress.history_reset_id ? completed?.report : null;
  const [reflection, setReflection] = useState('');
  const [running, setRunning] = useState(false);
  const [message, setMessage] = useState('修改参数，观察服务端为什么接受或拒绝这次调用。');
  const [error, setError] = useState('');
  const active = useRef<{
    controller: AbortController;
    resetId: Progress['history_reset_id'];
  } | null>(null);
  const observedResetId = useRef(progress.history_reset_id);
  const timer = useRef<number | undefined>(undefined);
  const bytes = new TextEncoder().encode(argumentsJson).byteLength;
  const discard = useCallback((nextMessage: string) => {
    const previous = active.current;
    active.current = null;
    previous?.controller.abort();
    window.clearTimeout(timer.current);
    timer.current = undefined;
    setRunning(false);
    setCompleted(null);
    setReflection('');
    setError('');
    setMessage(nextMessage);
  }, []);
  useEffect(() => {
    if (observedResetId.current === progress.history_reset_id) return;
    observedResetId.current = progress.history_reset_id;
    // A run started after a silent storage change already owns this new epoch.
    if (active.current && active.current.resetId === progress.history_reset_id) return;
    if (completed && completed.resetId === progress.history_reset_id) return;
    discard(RESET_NOTICE);
  }, [progress.history_reset_id, completed, discard]);
  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    setCatalog(null);
    setCatalogError('');
    const timeout = window.setTimeout(() => {
      if (disposed) return;
      disposed = true;
      controller.abort();
      setCatalogError('工具契约加载超时，请重试。');
    }, 15000);
    api<unknown>('/playground/tool-contract', { signal: controller.signal, cache: 'no-store' })
      .then((value) => {
        if (!disposed) setCatalog(parseToolContractCatalog(value));
      })
      .catch(() => {
        if (!disposed) setCatalogError('工具契约加载失败，请重试。');
      })
      .finally(() => window.clearTimeout(timeout));
    return () => {
      disposed = true;
      controller.abort();
      window.clearTimeout(timeout);
    };
  }, [reload]);
  useEffect(
    () => () => {
      const previous = active.current;
      active.current = null;
      previous?.controller.abort();
      window.clearTimeout(timer.current);
    },
    [],
  );

  function contextIsCurrent(resetId: Progress['history_reset_id']) {
    const checked = editLatestProgress(store.get(progressAtom), (current) => current, { resetId });
    if (!checked.reset) return true;
    discard(RESET_NOTICE);
    setProgress((previous) => editLatestProgress(previous, (current) => current).progress);
    return false;
  }
  async function run() {
    if (!catalog || active.current || !toolName || bytes > 4096) return;
    const request: ToolContractRequest = {
      track: 'agent',
      lesson_id: lesson.id,
      tool_name: toolName,
      arguments_json: argumentsJson,
    };
    const previous = store.get(progressAtom);
    const latest = editLatestProgress(previous, (current) => current).progress;
    const resetId = latest.history_reset_id;
    if (previous.history_reset_id !== resetId) setProgress(latest);
    const controller = new AbortController();
    const requestContext = { controller, resetId };
    active.current = requestContext;
    setRunning(true);
    setCompleted(null);
    setReflection('');
    setError('');
    setMessage('正在验证原始 JSON 与工具契约…');
    const timeout = window.setTimeout(() => {
      if (active.current !== requestContext || !contextIsCurrent(resetId)) return;
      discard('等待超时，本次没有可保存的结果。请重试。');
    }, 15000);
    timer.current = timeout;
    try {
      const value = await api<unknown>('/playground/tool-contract', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(request),
        signal: controller.signal,
        cache: 'no-store',
      });
      if (active.current !== requestContext || controller.signal.aborted) return;
      if (!contextIsCurrent(resetId)) return;
      const result = parseToolContractReport(value, request);
      setCompleted({ report: result, resetId });
      setMessage(
        result.outcome === 'success'
          ? '调用成功。检查实际返回的 observation。'
          : '调用被拒绝。检查错误码，并尝试修正参数。',
      );
    } catch {
      if (active.current !== requestContext || !contextIsCurrent(resetId)) return;
      setMessage('本次校验未完成，没有可保存的结果。');
      setError('工具校验未完成，请检查服务后重试。');
    } finally {
      window.clearTimeout(timeout);
      if (active.current === requestContext) {
        active.current = null;
        timer.current = undefined;
        setRunning(false);
      }
    }
  }
  return (
    <div className="min-w-0 space-y-5">
      <div className="mode-notice">
        <FlaskConical size={18} className="shrink-0" />
        <span>
          免费工具契约实验：手动调用两个公开课程只读工具。服务端实际校验和执行，不调用模型、沙箱或外网。
        </span>
      </div>
      {catalogError ? (
        <div role="alert" className="rounded-lg border border-red-200 p-4 text-sm">
          <p>{catalogError}</p>
          <Button
            variant="outline"
            onClick={() => setReload((value) => value + 1)}
            className="mt-3"
          >
            重试加载工具契约
          </Button>
        </div>
      ) : !catalog ? (
        <p>正在加载工具契约…</p>
      ) : null}
      <div className="grid min-w-0 gap-5 lg:grid-cols-2">
        <section
          aria-label="工具输入"
          className="min-w-0 space-y-4 rounded-xl border border-border p-5"
        >
          <h2 className="font-semibold">01 / 提交工具参数</h2>
          {catalog && (
            <label className="block text-sm">
              教学案例
              <select
                aria-label="教学案例"
                value=""
                onChange={(event) => {
                  const example = catalog.examples[Number(event.target.value)];
                  if (!example) return;
                  discard('已填入案例，可以修改参数后运行。');
                  setToolName(example.tool_name);
                  setArgumentsJson(example.arguments_json);
                }}
                className={fieldStyle}
              >
                <option value="" disabled>
                  选择一个案例填入
                </option>
                {catalog.examples.map((example, index) => (
                  <option key={index} value={String(index)}>
                    {example.label}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className="block text-sm font-medium">
            工具名称
            <input
              aria-label="工具名称"
              maxLength={100}
              value={toolName}
              onChange={(event) => {
                discard('参数已修改，请重新运行。');
                setToolName(event.target.value);
              }}
              className={fieldStyle}
              autoComplete="off"
              spellCheck={false}
            />
          </label>
          <label className="block text-sm font-medium">
            JSON 参数
            <textarea
              aria-label="JSON 参数"
              maxLength={4096}
              rows={7}
              value={argumentsJson}
              onChange={(event) => {
                discard('参数已修改，请重新运行。');
                setArgumentsJson(event.target.value);
              }}
              className={fieldStyle + ' font-mono'}
              spellCheck={false}
            />
            <span className="mt-1 block text-xs text-muted-foreground">
              {bytes}/4096 UTF-8 字节 · 保留原文，重复字段也交给服务端检查。
            </span>
          </label>
          {bytes > 4096 && (
            <p className="text-sm text-red-800">参数超过 4096 字节，请缩短后运行。</p>
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              onClick={() => void run()}
              disabled={!catalog || running || !toolName || bytes > 4096}
            >
              {running ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
              {running ? '正在校验…' : '运行工具校验'}
            </Button>
            {running && (
              <Button
                variant="outline"
                onClick={() => discard('已停止等待，本次没有可保存的结果。')}
              >
                <Square size={16} />
                停止等待
              </Button>
            )}
          </div>
        </section>
        <section
          aria-label="工具 schema"
          className="min-w-0 space-y-4 rounded-xl border border-border p-5"
        >
          <h2 className="font-semibold">02 / 对照服务端契约</h2>
          <p className="text-sm leading-relaxed text-muted-foreground">
            这是课程 Agent 当前使用的
            schema。尝试未知工具、额外字段或错误类型；正式检索工具不接受参考简例中的 limit 参数。
          </p>
          {catalog?.tools.map((tool) => (
            <details key={tool.name} open={tool.name === toolName}>
              <summary className="cursor-pointer break-all text-sm font-medium">
                {tool.name}
              </summary>
              <p className="my-3 text-xs leading-relaxed text-muted-foreground">
                {tool.description}
              </p>
              <pre className={jsonStyle}>{JSON.stringify(tool.parameters, null, 2)}</pre>
            </details>
          ))}
          <p className="text-xs leading-relaxed text-muted-foreground">
            参数中的 lesson_id 是工具读取目标；本次笔记始终归属「{lesson.title}」。operation_id
            只标识一次操作，不表示跨请求幂等。
          </p>
        </section>
      </div>
      <p role="status" className="text-sm text-muted-foreground">
        {message}
      </p>
      {error && (
        <p
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800"
        >
          {error}
        </p>
      )}
      {completed && report && (
        <section
          aria-label="工具校验结果"
          className="min-w-0 space-y-4 rounded-xl border border-border p-5"
        >
          <h2 className="font-semibold">
            {report.outcome === 'success' ? '调用成功' : '调用被拒绝'}
          </h2>
          <p className="break-all text-xs text-muted-foreground">
            运行 ID：{report.run_id} · 模型调用：0 · 只读：true
          </p>
          {'error' in report.observation && (
            <p className="text-sm">{reasons[report.observation.error]}</p>
          )}
          {'items' in report.observation && (
            <p className="text-sm">
              {report.observation.items.length
                ? '返回 ' + report.observation.items.length + ' 条真实课程资料。'
                : '合法调用，无匹配资料。空结果也是有效 observation。'}
            </p>
          )}
          {'lesson' in report.observation && (
            <p className="text-sm">已读取课时：{report.observation.lesson.title}</p>
          )}
          <pre aria-label="实际 observation" className={jsonStyle}>
            {JSON.stringify(report.observation, null, 2)}
          </pre>
          <ToolNote
            report={report}
            resetId={completed.resetId}
            lesson={lesson}
            reflection={reflection}
            onReflection={setReflection}
          />
        </section>
      )}
    </div>
  );
}

export default function ToolContractExperiment({
  track,
  lesson,
  lessons,
}: {
  track: TrackId;
  lesson?: Lesson;
  lessons: Lesson[];
}) {
  return (
    <section aria-label="工具契约实验" className="min-w-0">
      {track === 'agent' && toolContractLessonEligible(lesson) ? (
        <ToolSession key={track + ':' + lesson.id} lesson={lesson} />
      ) : (
        <div className="space-y-4 rounded-xl border border-border p-5">
          <h2 className="font-semibold">请从支持的课程开始工具实验</h2>
          <p className="text-sm text-muted-foreground">
            当前实验配套结构化输出与工具契约两课。选择课程后，可把实际结果保存到对应笔记。
          </p>
          <ul className="space-y-2 text-sm">
            {lessons.filter(toolContractLessonEligible).map((item) => (
              <li key={item.id}>
                <Link
                  className="underline underline-offset-2"
                  to={'/playground?track=agent&lesson=' + item.id + '&mode=tool-contract'}
                >
                  {item.title}
                </Link>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
