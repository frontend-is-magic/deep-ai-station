import { useEffect, useRef, useState } from 'react';
import { useAtom, useStore } from 'jotai';
import { Link, useSearchParams } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import {
  Bot,
  CheckCircle2,
  ChevronDown,
  Code2,
  Clock3,
  Download,
  FlaskConical,
  History,
  Loader2,
  Play,
  RotateCcw,
  Square,
  StickyNote,
  Terminal,
  XCircle,
} from 'lucide-react';
import { api, streamRun } from '@/lib/api';
import { latestProgress, progressAtom } from '@/lib/state';
import {
  appendTerminalRun,
  buildTerminalRun,
  runRecordStatus,
  validRunRecord,
} from '@/lib/run-history';
import { incompleteRunUsage, updateRunUsage, type RunUsageSnapshot } from '@/lib/run-usage';
import { codeDraftsAtom, updateDraft } from '@/lib/drafts';
import { languageNames } from '@/lib/utils';
import { providerForReplay, providerLabel } from '@/lib/providers';
import type {
  Capabilities,
  Language,
  Lesson,
  LiveProvider,
  RunReason,
  RunRecord,
  RunStatus,
  RunTrace,
  RunWorkflow,
  Track,
  TrackId,
} from '@/lib/types';
import { PageHeading } from '@/components/common';
import { Button } from '@/components/ui/button';
import RetrievalEvaluation from '@/components/RetrievalEvaluation';
import ToolContractExperiment from '@/components/ToolContractExperiment';

interface CheckResult {
  mode: string;
  executed: boolean;
  checks: { title: string; passed: boolean }[];
  passed: boolean;
  notice: string;
}
const usageLabels: Record<string, string> = {
  prompt_tokens: '输入',
  completion_tokens: '输出',
  total_tokens: '总量',
  input_tokens: '输入',
  output_tokens: '输出',
  cache_creation_input_tokens: '缓存写入',
  cache_read_input_tokens: '缓存读取',
};
interface ExecutionResult {
  run_id: string;
  passed: boolean;
  status: string;
  stdout: string;
  stderr: string;
  truncated: boolean;
  cleanup: string;
  duration_ms: number;
  notice: string;
}

type CancellationReason = 'user_stop' | 'context_changed';
interface ActiveRun {
  resetId: string | undefined;
  cancel: (reason: CancellationReason, showResult?: boolean) => void;
  discard: () => void;
}
const runStatusLabels: Record<RunStatus, string> = {
  completed: '实验已完成',
  failed: '运行失败',
  cancelled: '客户端已停止',
};
const runReasonMessages: Record<RunReason, string> = {
  server_error: '运行失败，已保留已接收的内容与用量。',
  transport_error: '连接中断，已保留已接收的内容；统计可能不完整。',
  stream_ended: '流已结束，但未收到完成确认；已保留已接收的内容。',
  output_limit: '输出达到模型上限，内容可能未完成。',
  user_stop: '客户端已停止等待；服务器终态未确认，已知用量可能不完整。',
  context_changed: '切换页面或实验上下文后，客户端已停止等待；服务器终态未确认。',
};

function courseTask(lesson: Lesson) {
  return `我正在学习「${lesson.title}」。目标：${lesson.objective}\n请解释核心机制，给出一个成功输入和一个失败输入，并说明如何验证：${lesson.criteria.join('；')}。`;
}
function trackLesson(tracks: Track[], track: TrackId, id?: string) {
  return tracks.find((item) => item.id === track)?.lessons.find((lesson) => lesson.id === id);
}
function safeOutputLink(href?: string) {
  if (!href || href.length > 2000) return false;
  try {
    const url = new URL(href);
    return url.protocol === 'https:' && !url.username && !url.password;
  } catch {
    return false;
  }
}

export default function Playground({ tracks }: { tracks: Track[] }) {
  const [params, setParams] = useSearchParams();
  const [progress, setProgress] = useAtom(progressAtom);
  const store = useStore();
  const [drafts, setDrafts] = useAtom(codeDraftsAtom);
  const trackId: TrackId = params.get('track') === 'fullstack' ? 'fullstack' : 'agent';
  const requestedMode = params.get('mode');
  const mode = ['evaluation', 'code', 'tool-contract'].includes(requestedMode || '')
    ? requestedMode!
    : 'agent';
  const [provider, setProvider] = useState<LiveProvider>('demo');
  const [workflow, setWorkflow] = useState<RunWorkflow>(
    params.get('workflow') === 'agent' ? 'agent' : 'retrieval',
  );
  const selectedLesson = tracks
    .flatMap((t) => t.lessons)
    .find((x) => x.id === params.get('lesson') && x.track === trackId);
  const [prompt, setPrompt] = useState(() =>
    selectedLesson
      ? courseTask(selectedLesson)
      : trackId === 'agent'
        ? '如何设计一个有工具调用和停止条件的 AI Agent？'
        : '如何用 FastAPI 构建一个可靠的 AI 问答接口？',
  );
  const [system, setSystem] = useState(
    '你是一位严谨的 AI 工程导师。给出可验证的步骤，明确不确定性。',
  );
  const [temperature, setTemperature] = useState(0.3);
  const [token, setToken] = useState('');
  const [cap, setCap] = useState<Capabilities | null>(null);
  const [running, setRunning] = useState(false);
  const [trace, setTrace] = useState<RunTrace[]>([]);
  const [output, setOutput] = useState('');
  const [error, setError] = useState('');
  const [runInfo, setRunInfo] = useState<
    (RunRecord & { restored?: boolean; viewResetId?: string }) | null
  >(null);
  const [liveUsage, setLiveUsage] = useState<(RunUsageSnapshot & { provider: string }) | null>(
    null,
  );
  const [history, setHistory] = useState(false);
  const [language, setLanguage] = useState<Language>(
    trackId === 'agent' ? 'python' : progress.language,
  );
  useEffect(() => {
    setLanguage(trackId === 'agent' ? 'python' : progress.language);
  }, [trackId, progress.language]);
  const track = tracks.find((x) => x.id === trackId)!;
  const initialCode =
    selectedLesson?.snippets[language] || track.lessons[0].snippets[language] || '';
  const draftKey = `${trackId}:${selectedLesson?.id || 'default'}:${language}`;
  const requestContext =
    mode === 'code' ? draftKey : `${trackId}:${selectedLesson?.id || 'default'}`;
  const code = drafts.find((draft) => draft.context === draftKey)?.code ?? initialCode;
  const [checkResult, setCheckResult] = useState<CheckResult | null>(null);
  const [execution, setExecution] = useState<ExecutionResult | null>(null);
  const controller = useRef<AbortController | null>(null);
  const activeRun = useRef<ActiveRun | null>(null);
  const mounted = useRef(true);
  const resetId = useRef(progress.history_reset_id);
  function clearResult() {
    setRunning(false);
    setLiveUsage(null);
    setOutput('');
    setTrace([]);
    setRunInfo(null);
    setError('');
    setCheckResult(null);
    setExecution(null);
  }
  function cancelContext() {
    activeRun.current?.cancel('context_changed', false);
    const previous = controller.current;
    controller.current = null;
    previous?.abort();
  }
  useEffect(() => {
    const c = new AbortController();
    mounted.current = true;
    api<Capabilities>('/capabilities', { signal: c.signal })
      .then((result) => {
        if (mounted.current && !c.signal.aborted) setCap(result);
      })
      .catch(() => {});
    return () => {
      mounted.current = false;
      c.abort();
      cancelContext();
    };
  }, []);
  useEffect(() => {
    // A new import/clear epoch discards unfinished work, including ignored aborts.
    if (resetId.current === progress.history_reset_id) return;
    resetId.current = progress.history_reset_id;
    if (activeRun.current && activeRun.current.resetId === progress.history_reset_id) return;
    activeRun.current?.discard();
    const previous = controller.current;
    controller.current = null;
    previous?.abort();
    clearResult();
  }, [progress.history_reset_id]);
  useEffect(() => {
    // Ordinary navigation preserves the old attempt's captured course, never the new one.
    if (controller.current) {
      cancelContext();
      clearResult();
    }
    setCheckResult(null);
    setExecution(null);
    setError('');
  }, [requestContext, mode]);
  function downloadCode() {
    const extension = { python: 'py', typescript: 'ts', go: 'go' }[language];
    const url = URL.createObjectURL(new Blob([code], { type: 'text/plain;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `${selectedLesson?.id || trackId}-draft.${extension}`;
    link.click();
    URL.revokeObjectURL(url);
  }
  function switchMode(next: string) {
    setError('');
    setParams(
      (p) => {
        p.set('mode', next);
        p.set('track', trackId);
        return p;
      },
      { replace: true },
    );
  }
  function switchTrack(value: TrackId) {
    cancelContext();
    setRunning(false);
    setLiveUsage(null);
    setLanguage(value === 'agent' ? 'python' : progress.language);
    setParams({ track: value, mode, workflow }, { replace: true });
    setOutput('');
    setTrace([]);
    setError('');
    setRunInfo(null);
    setPrompt(
      value === 'agent'
        ? '如何设计一个有工具调用和停止条件的 AI Agent？'
        : '如何用 FastAPI 构建一个可靠的 AI 问答接口？',
    );
  }
  function stopRun() {
    activeRun.current?.cancel('user_stop', true);
  }
  async function run() {
    if (running || controller.current || !prompt.trim()) return;
    const currentProgress = store.get(progressAtom);
    const newestProgress = latestProgress(currentProgress);
    // A queued storage event must not make a new attempt inherit an obsolete epoch.
    if (newestProgress.history_reset_id !== currentProgress.history_reset_id) {
      setProgress(newestProgress);
    }
    const epoch = newestProgress.history_reset_id;
    const c = new AbortController();
    const context = {
      id: crypto.randomUUID(),
      prompt,
      provider,
      track: trackId,
      lesson_id: selectedLesson?.id,
      workflow,
    };
    const started = performance.now();
    controller.current = c;
    setRunning(true);
    setOutput('');
    setTrace([]);
    setError('');
    setRunInfo(null);
    setLiveUsage({ usage: null, usageComplete: false, provider });
    let answer = '';
    let serverId: string | undefined;
    let terminal = false;
    let usageSnapshot: RunUsageSnapshot | null = null;
    const observed: RunTrace[] = [];
    const current = () =>
      mounted.current && controller.current === c && activeRun.current === attempt && !terminal;
    function finalize(
      status: RunStatus | null,
      reason?: RunReason,
      data: Record<string, unknown> = {},
      showResult = true,
      temporaryError?: string,
    ) {
      if (terminal || activeRun.current !== attempt) return;
      // Invalidate synchronously before abort can reject or deliver a late frame.
      terminal = true;
      activeRun.current = null;
      if (controller.current === c) controller.current = null;
      c.abort();
      const latest = latestProgress(store.get(progressAtom));
      if (status === null || latest.history_reset_id !== epoch) {
        if (mounted.current && showResult) clearResult();
        return;
      }
      if (status === 'cancelled' || reason === 'transport_error' || reason === 'stream_ended') {
        usageSnapshot = incompleteRunUsage(usageSnapshot);
      }
      const record = buildTerminalRun({
        ...context,
        status,
        reason,
        server_run_id: serverId,
        answer,
        date: new Date().toISOString(),
        duration_ms:
          typeof data.duration_ms === 'number' &&
          Number.isFinite(data.duration_ms) &&
          data.duration_ms >= 0
            ? data.duration_ms
            : Math.max(0, Math.round(performance.now() - started)),
        trace: observed,
        usage: usageSnapshot?.usage ?? null,
        usage_complete: usageSnapshot?.usageComplete,
        steps: data.steps,
        tool_count: data.tool_count,
      });
      if (!record || !validRunRecord(record)) {
        if (mounted.current && showResult) {
          setRunning(false);
          setError('本次结果无法形成有效历史记录，已接收内容仅保留在当前页面。');
        }
        return;
      }
      setProgress((previous) => {
        const newest = latestProgress(previous);
        return newest.history_reset_id === epoch ? appendTerminalRun(newest, record) : newest;
      });
      if (!mounted.current || !showResult) return;
      if (store.get(progressAtom).history_reset_id !== epoch) {
        clearResult();
        return;
      }
      setRunning(false);
      setOutput(record.answer);
      setTrace(record.trace || []);
      setRunInfo({ ...record, viewResetId: epoch });
      setLiveUsage({ usage: record.usage ?? null, usageComplete: record.usage_complete, provider });
      setError(temporaryError || (record.reason ? runReasonMessages[record.reason] : ''));
    }
    const attempt: ActiveRun = {
      resetId: epoch,
      cancel: (reason, showResult = false) => finalize('cancelled', reason, {}, showResult),
      discard: () => finalize(null, undefined, {}, false),
    };
    activeRun.current = attempt;
    function snapshot(data: Record<string, unknown>, final = false) {
      usageSnapshot = updateRunUsage(usageSnapshot, data, final);
      setLiveUsage({ ...usageSnapshot, provider });
    }
    try {
      await streamRun(
        {
          prompt,
          system,
          provider,
          track: trackId,
          temperature,
          workflow,
          ...(selectedLesson ? { lesson_id: selectedLesson.id } : {}),
        },
        token,
        c.signal,
        (event) => {
          if (!current() || c.signal.aborted) return;
          if (store.get(progressAtom).history_reset_id !== epoch) {
            finalize(null);
            return;
          }
          if (typeof event.data.run_id === 'string' && serverId && event.data.run_id !== serverId)
            return;
          if (
            event.event === 'start' &&
            typeof event.data.run_id === 'string' &&
            /^[A-Za-z0-9._:-]{1,100}$/.test(event.data.run_id)
          ) {
            serverId = event.data.run_id;
          }
          if (event.event === 'usage') snapshot(event.data);
          if (event.event === 'trace') {
            const step = event.data as unknown as RunTrace;
            const index = step.id ? observed.findIndex((item) => item.id === step.id) : -1;
            if (index >= 0) observed[index] = step;
            else observed.push(step);
            setTrace([...observed]);
          }
          if (event.event === 'delta') {
            answer += String(event.data.text);
            setOutput(answer);
          }
          if (event.event === 'error') {
            snapshot(
              {
                ...event.data,
                usage_complete:
                  typeof event.data.usage_complete === 'boolean'
                    ? event.data.usage_complete
                    : false,
              },
              true,
            );
            finalize(
              'failed',
              'server_error',
              event.data,
              true,
              typeof event.data.message === 'string' ? event.data.message : undefined,
            );
          }
          if (event.event === 'done') {
            snapshot(event.data, true);
            finalize(
              event.data.truncated === true ? 'failed' : 'completed',
              event.data.truncated === true ? 'output_limit' : undefined,
              event.data,
            );
          }
        },
      );
      if (current()) finalize('failed', 'stream_ended');
    } catch (e) {
      if (current()) {
        finalize(
          c.signal.aborted ? 'cancelled' : 'failed',
          c.signal.aborted ? 'user_stop' : 'transport_error',
          {},
          true,
          e instanceof Error ? e.message : undefined,
        );
      }
    }
  }
  function stopCodeRequest() {
    const previous = controller.current;
    controller.current = null;
    previous?.abort();
    setRunning(false);
    setCheckResult(null);
    setExecution(null);
    setError('本次操作已停止，请重新运行。');
  }
  async function check() {
    if (running) return;
    setRunning(true);
    setError('');
    setExecution(null);
    const c = new AbortController();
    controller.current = c;
    try {
      const result = await api<CheckResult>('/playground/check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ language, code }),
        signal: c.signal,
      });
      if (mounted.current && controller.current === c) setCheckResult(result);
    } catch (e) {
      if (mounted.current && controller.current === c)
        setError(c.signal.aborted ? '检查已停止' : e instanceof Error ? e.message : '检查失败');
    } finally {
      if (mounted.current && controller.current === c) {
        setRunning(false);
        controller.current = null;
      }
    }
  }
  async function executeCode() {
    if (running || !code.trim() || !token) return;
    const c = new AbortController();
    controller.current = c;
    setRunning(true);
    setError('');
    setExecution(null);
    setCheckResult(null);
    try {
      const result = await api<ExecutionResult>('/playground/execute', {
        method: 'POST',
        signal: c.signal,
        headers: { 'Content-Type': 'application/json', 'X-Playground-Token': token },
        body: JSON.stringify({ language, code }),
      });
      if (mounted.current && controller.current === c) setExecution(result);
    } catch (e) {
      if (mounted.current && controller.current === c)
        setError(
          c.signal.aborted ? '隔离运行已停止' : e instanceof Error ? e.message : '隔离运行失败',
        );
    } finally {
      if (mounted.current && controller.current === c) {
        setRunning(false);
        controller.current = null;
      }
    }
  }
  const displayedUsage =
    liveUsage ??
    (runInfo
      ? { usage: runInfo.usage, usageComplete: runInfo.usage_complete, provider: runInfo.provider }
      : null);
  const displayedError = error || (runInfo?.reason ? runReasonMessages[runInfo.reason] : '');
  const canExecute = cap?.sandbox?.languages.includes(language) || false;
  const noteMarker = runInfo ? `### 实验记录 ${runInfo.id}` : '';
  const noteEntry = runInfo
    ? `${noteMarker}\n\n${providerLabel(runInfo.provider)} · ${runInfo.workflow === 'agent' ? 'Agent 循环 · ' : ''}${new Date(runInfo.date).toLocaleString('zh-CN')}\n\n任务：${runInfo.prompt}\n\n${output}\n\n以上是学习参考，请另行记录实践输入、实际结果与验收证据。`
    : '';
  const succeeded = runInfo ? runRecordStatus(runInfo) === 'completed' : false;
  const canSaveNote = Boolean(
    succeeded &&
    runInfo?.viewResetId === progress.history_reset_id &&
    selectedLesson &&
    runInfo?.track === selectedLesson.track &&
    runInfo.lesson_id === selectedLesson.id,
  );
  const currentNote = selectedLesson ? progress.notes[selectedLesson.id] || '' : '';
  const alreadySaved = Boolean(noteMarker && currentNote.includes(noteMarker));
  const noteFits = `${currentNote}${currentNote ? '\n\n' : ''}${noteEntry}`.length <= 10000;
  const templates =
    trackId === 'agent'
      ? [
          '如何设计一个有工具调用和停止条件的 AI Agent？',
          '怎样验证 RAG 回答的引用是否可靠？',
          'MCP 工具应该如何处理授权和幂等？',
        ]
      : [
          '如何用 FastAPI 构建一个可靠的 AI 问答接口？',
          'TypeScript、Go 和 Python 如何处理请求取消？',
          'Jotai 学习进度应该怎样持久化？',
        ];
  return (
    <div className="page playground-page">
      <PageHeading
        eyebrow="EXPERIMENT / LEARN BY DOING"
        title="Playground"
        description="把想法带到实验台，观察每一步发生了什么。"
      >
        <Button variant="outline" onClick={() => setHistory((x) => !x)}>
          <History size={16} />
          运行历史{' '}
          {progress.runs.length > 0 && <span className="badge">{progress.runs.length}</span>}
        </Button>
      </PageHeading>
      <div className="playground-toolbar">
        <div className="segmented-control">
          <button
            disabled={running}
            className={mode === 'agent' ? 'selected' : ''}
            onClick={() => switchMode('agent')}
          >
            <Bot size={16} />
            Agent 工作流
          </button>
          <button
            disabled={running}
            className={mode === 'code' ? 'selected' : ''}
            onClick={() => switchMode('code')}
          >
            <Code2 size={16} />
            代码实验
          </button>
          <button
            disabled={running}
            className={mode === 'evaluation' ? 'selected' : ''}
            onClick={() => switchMode('evaluation')}
          >
            <FlaskConical size={16} />
            检索评测
          </button>
          <button
            disabled={running}
            className={mode === 'tool-contract' ? 'selected' : ''}
            onClick={() => switchMode('tool-contract')}
          >
            <FlaskConical size={16} />
            工具契约
          </button>
        </div>
        <label className="track-select">
          学习方向
          <select
            aria-label="学习方向"
            disabled={running}
            value={trackId}
            onChange={(e) => switchTrack(e.target.value as TrackId)}
          >
            <option value="agent">AI Agent</option>
            <option value="fullstack">AI 全栈</option>
          </select>
          <ChevronDown size={14} />
        </label>
      </div>
      {selectedLesson && (
        <p className="linked-lesson">
          当前课程：<Link to={`/lesson/${selectedLesson.id}`}>{selectedLesson.title}</Link>
          <span>{mode === 'agent' ? ' · 成功实验可写入本课笔记' : ' · 回到课程记录实践结果'}</span>
        </p>
      )}
      {history && (
        <section className="run-history" aria-label="实验历史">
          <h3>最近运行 · 当前浏览器</h3>
          {progress.runs.length ? (
            progress.runs.map((record) => (
              <button
                key={record.id}
                data-run-id={record.id}
                data-run-status={runRecordStatus(record)}
                disabled={running}
                onClick={() => {
                  const latest = latestProgress(store.get(progressAtom));
                  const currentRecord = latest.runs.find((item) => item.id === record.id);
                  if (!currentRecord) {
                    clearResult();
                    setHistory(false);
                    return;
                  }
                  // Accept this same latest snapshot before restoring its view. A queued
                  // storage event must not discard a record we have just read from it.
                  resetId.current = latest.history_reset_id;
                  if (store.get(progressAtom).history_reset_id !== latest.history_reset_id) {
                    setProgress(latest);
                  }
                  const lesson = trackLesson(tracks, currentRecord.track, currentRecord.lesson_id);
                  controller.current?.abort();
                  controller.current = null;
                  setLiveUsage(null);
                  setWorkflow(currentRecord.workflow || 'retrieval');
                  setLanguage(currentRecord.track === 'agent' ? 'python' : latest.language);
                  setParams(
                    {
                      track: currentRecord.track,
                      mode: 'agent',
                      workflow: currentRecord.workflow || 'retrieval',
                      ...(lesson ? { lesson: lesson.id } : {}),
                    },
                    { replace: true },
                  );
                  setPrompt(currentRecord.prompt);
                  setProvider(providerForReplay(currentRecord.provider, cap));
                  setOutput(currentRecord.answer);
                  setTrace(currentRecord.trace || []);
                  setRunInfo({
                    ...currentRecord,
                    restored: true,
                    viewResetId: latest.history_reset_id,
                  });
                  setError(currentRecord.reason ? runReasonMessages[currentRecord.reason] : '');
                  setHistory(false);
                }}
              >
                <span>
                  {runStatusLabels[runRecordStatus(record)]} · {providerLabel(record.provider)}
                  {record.workflow === 'agent' && ' · Agent 循环'}
                  {record.lesson_id &&
                    ` · ${trackLesson(tracks, record.track, record.lesson_id)?.title || '课程实验'}`}
                </span>
                <strong>{record.prompt}</strong>
                <small>{new Date(record.date).toLocaleString('zh-CN')}</small>
              </button>
            ))
          ) : (
            <p>运行结束或停止后，已接收的结果会保存到这里。</p>
          )}
          <p>成功、失败和停止尝试共用最近 20 条；输入、已接收内容与轨迹保存在当前浏览器。</p>
        </section>
      )}
      {mode === 'tool-contract' ? (
        <ToolContractExperiment
          track={trackId}
          lesson={selectedLesson}
          lessons={tracks.flatMap((item) => item.lessons)}
        />
      ) : mode === 'evaluation' ? (
        <RetrievalEvaluation track={trackId} lesson={selectedLesson} />
      ) : mode === 'agent' ? (
        <>
          <div className={`mode-notice ${provider === 'demo' ? '' : 'live'}`}>
            <FlaskConical size={18} />
            <span>
              {provider === 'demo'
                ? workflow === 'agent'
                  ? '教学演示：预设课程工具调用顺序，没有模型决策或模型费用。'
                  : '教学演示：确定性课程检索，不调用模型、不产生模型费用。'
                : workflow === 'agent'
                  ? '真实 DeepSeek Agent：使用 OpenAI 兼容接口，模型可选择两个只读课程工具，最多 3 次模型请求、2 次工具请求。45 秒总上限，调用会产生费用。'
                  : '真实 DeepSeek：通过 OpenAI 兼容接口逐步显示输出。需要实验访问码，调用会产生费用；停止会关闭上游连接。'}
            </span>
          </div>
          {runInfo?.restored && (
            <p className="linked-lesson" role="status">
              历史结果来源：{providerLabel(runInfo.provider)}；再次运行使用当前选择的{' '}
              {providerLabel(provider)}。
            </p>
          )}
          <div className="lab-grid">
            <section className="lab-input">
              <div className="panel-heading">
                <span>01 / INPUT</span>
                <strong>配置你的实验</strong>
              </div>
              <label className="field-label">
                模型服务
                <select
                  disabled={running}
                  aria-label="模型服务"
                  value={provider}
                  onChange={(e) => setProvider(e.target.value as LiveProvider)}
                >
                  <option value="demo">教学演示 · 无需密钥</option>
                  {(cap?.providers || [])
                    .filter((x) => x.id === 'deepseek')
                    .map((p) => (
                      <option key={p.id} disabled={!p.enabled} value={p.id}>
                        DeepSeek
                        {p.enabled ? ` · ${p.model}` : ' · 未启用'}
                      </option>
                    ))}
                </select>
              </label>
              <label className="field-label">
                工作流
                <select
                  aria-label="工作流"
                  value={workflow}
                  disabled={running}
                  onChange={(e) => {
                    const value = e.target.value as RunWorkflow;
                    setWorkflow(value);
                    setParams(
                      (p) => {
                        p.set('workflow', value);
                        return p;
                      },
                      { replace: true },
                    );
                  }}
                >
                  <option value="retrieval">课程检索 · 单次回答</option>
                  <option value="agent">有界 Agent 循环 · 最多三轮</option>
                </select>
                <small>
                  仅提供 knowledge_search /
                  lesson_read。工具参数由服务端校验，限流按每次真实模型请求计数。
                </small>
              </label>
              <label className="field-label">
                任务描述
                <textarea
                  disabled={running}
                  aria-label="任务描述"
                  maxLength={4000}
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder="你想探索什么问题？"
                  rows={6}
                />
                <small>{prompt.length}/4000 字符</small>
              </label>
              <div className="prompt-templates">
                <span>试试这些任务</span>
                {selectedLesson && (
                  <button disabled={running} onClick={() => setPrompt(courseTask(selectedLesson))}>
                    使用本课目标与验收项
                    <ArrowRightIcon />
                  </button>
                )}
                {templates.map((text) => (
                  <button disabled={running} key={text} onClick={() => setPrompt(text)}>
                    {text}
                    <ArrowRightIcon />
                  </button>
                ))}
              </div>
              <details className="model-settings">
                <summary>高级配置</summary>
                <label className="field-label">
                  系统提示
                  <textarea
                    disabled={running}
                    aria-label="系统提示"
                    rows={3}
                    maxLength={2000}
                    value={system}
                    onChange={(e) => setSystem(e.target.value)}
                  />
                </label>
                <label className="field-label">
                  Temperature · {temperature}
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.1}
                    value={temperature}
                    disabled={running || provider === 'demo'}
                    onChange={(e) => setTemperature(Number(e.target.value))}
                  />
                </label>
                <small>演示模式仅使用任务和课程索引，不应用系统提示或 Temperature。</small>
                {provider !== 'demo' && (
                  <label className="field-label">
                    实验访问码
                    <input
                      aria-label="实验访问码"
                      type="password"
                      autoComplete="off"
                      value={token}
                      onChange={(e) => setToken(e.target.value)}
                      placeholder="仅用于本次页面会话"
                    />
                  </label>
                )}
              </details>
              <div className="lab-actions">
                {running ? (
                  <Button variant="dark" onClick={stopRun}>
                    <Square size={15} />
                    停止运行
                  </Button>
                ) : (
                  <Button
                    disabled={!prompt.trim() || (provider !== 'demo' && !token)}
                    onClick={() => void run()}
                  >
                    <Play size={15} />
                    运行实验
                  </Button>
                )}
                <Button
                  variant="ghost"
                  disabled={running}
                  aria-label="清空实验结果"
                  onClick={() => {
                    setOutput('');
                    setTrace([]);
                    setError('');
                    setRunInfo(null);
                    setLiveUsage(null);
                  }}
                >
                  <RotateCcw size={16} />
                </Button>
              </div>
            </section>
            <section
              className="lab-output"
              aria-label="运行结果"
              data-run-id={runInfo?.id}
              data-run-status={runInfo ? runRecordStatus(runInfo) : undefined}
            >
              <div className="panel-heading">
                <span>02 / OUTPUT</span>
                <strong>
                  {running
                    ? '实验进行中'
                    : runInfo
                      ? runStatusLabels[runRecordStatus(runInfo)]
                      : displayedError
                        ? '运行失败'
                        : '等待你的第一次运行'}
                </strong>
                {running && <Loader2 size={16} className="animate-spin" />}
              </div>
              {!output && !trace.length && !displayedError && (
                <div className="lab-empty">
                  <div>
                    <Bot size={35} strokeWidth={1.2} />
                  </div>
                  <h3>让一次实验，回答一个问题</h3>
                  <p>
                    输入任务并运行，观察工具轨迹、
                    <br />
                    资料来源与最终输出。
                  </p>
                  <span>输入 → 校验 → 检索 / 模型 → 输出</span>
                </div>
              )}
              {trace.length > 0 && (
                <div className="trace-list">
                  <p className="eyebrow">EXECUTION TRACE / 可观察事件</p>
                  {trace.map((step, i) => (
                    <div key={step.id || i}>
                      {step.status === 'error' ? (
                        <XCircle size={17} />
                      ) : step.status === 'running' ? (
                        running ? (
                          <Loader2 size={17} className="animate-spin" />
                        ) : (
                          <Clock3 size={17} />
                        )
                      ) : (
                        <CheckCircle2 size={17} />
                      )}
                      <div>
                        <strong>{step.title}</strong>
                        <p>{step.detail}</p>
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {displayedError && (
                <div className="inline-error" role="alert">
                  <XCircle size={17} />
                  {displayedError}
                </div>
              )}
              {runInfo && !succeeded && (
                <p role="status" className="mx-5 text-sm leading-relaxed text-muted-foreground">
                  已接收片段；本次实验未完成，不能写入本课笔记。
                </p>
              )}
              {output && (
                <div className="markdown-output">
                  <ReactMarkdown
                    components={{
                      a: ({ children, href, title }) =>
                        safeOutputLink(href) ? (
                          <a href={href} title={title} target="_blank" rel="noreferrer">
                            {children}
                          </a>
                        ) : (
                          <span>{children}</span>
                        ),
                      img: ({ alt }) => <span>{alt}</span>,
                    }}
                  >
                    {output}
                  </ReactMarkdown>
                </div>
              )}
              {runInfo && (
                <div className="run-metadata">
                  <span>
                    {runInfo.restored ? '历史结果来源' : '结果来源'}：
                    {providerLabel(runInfo.provider)}
                  </span>
                  <span>
                    {succeeded ? '完成' : '已观察'} · {(runInfo.duration_ms / 1000).toFixed(2)}s
                  </span>
                  {runInfo.workflow === 'agent' && runInfo.steps !== undefined && (
                    <span>
                      {runInfo.steps}
                      {runInfo.provider === 'demo' ? ' 步预设流程' : ' 轮模型请求'} ·{' '}
                      {runInfo.tool_count} 次工具请求
                    </span>
                  )}
                  <code>记录 / {runInfo.id.slice(0, 8)}</code>
                  {runInfo.server_run_id ? (
                    <code>run / {runInfo.server_run_id.slice(0, 8)}</code>
                  ) : (
                    <span>
                      {runInfo.status ? '未取得服务端运行 ID' : '旧版记录未单独保存服务端运行 ID'}
                    </span>
                  )}
                </div>
              )}
              {displayedUsage && (
                <section aria-label="模型用量" className="run-metadata">
                  <span>
                    {displayedUsage.usage
                      ? running
                        ? '已知用量 · 运行尚未结束'
                        : displayedUsage.usageComplete === false
                          ? succeeded
                            ? '部分模型轮次未返回用量'
                            : '已知用量 · 本次统计不完整'
                          : displayedUsage.usageComplete === true && !succeeded
                            ? '已知用量 · 已发出请求均已统计'
                            : '供应商已返回 usage'
                      : displayedUsage.provider === 'demo'
                        ? '未调用模型'
                        : succeeded
                          ? '供应商未返回用量'
                          : '用量未知 · 未收到可用统计'}
                  </span>
                  {displayedUsage.usage && (
                    <span>
                      tokens ·{' '}
                      {Object.entries(displayedUsage.usage)
                        .map(
                          ([key, count]) => `${usageLabels[key]} ${count.toLocaleString('zh-CN')}`,
                        )
                        .join(' / ')}
                    </span>
                  )}
                </section>
              )}
              {canSaveNote && selectedLesson && (
                <div className="lab-note-action">
                  <Button
                    variant="outline"
                    disabled={running || alreadySaved || !noteFits}
                    onClick={() =>
                      setProgress((p) => {
                        const current = latestProgress(p);
                        if (
                          !runInfo ||
                          runRecordStatus(runInfo) !== 'completed' ||
                          runInfo.viewResetId !== current.history_reset_id ||
                          runInfo.track !== selectedLesson.track ||
                          runInfo.lesson_id !== selectedLesson.id
                        )
                          return current;
                        const previous = current.notes[selectedLesson.id] || '';
                        const next = `${previous}${previous ? '\n\n' : ''}${noteEntry}`;
                        if (previous.includes(noteMarker) || next.length > 10000) return current;
                        return {
                          ...current,
                          notes: { ...current.notes, [selectedLesson.id]: next },
                        };
                      })
                    }
                  >
                    <StickyNote size={16} />
                    {alreadySaved ? '已写入本课笔记' : '写入本课笔记'}
                  </Button>
                  <p role="status">
                    {alreadySaved
                      ? '原有笔记已保留，同一次实验只写入一次。'
                      : !noteFits
                        ? '本课笔记容量不足，请先整理笔记后再写入。'
                        : '保留原有笔记；实验回答仍需要你用实际实践验证。'}
                  </p>
                </div>
              )}
            </section>
          </div>
        </>
      ) : (
        <>
          <div className="mode-notice">
            <Terminal size={18} />
            <span>
              静态检查模式：不运行代码。Python 检查语法；TS / Go
              检查基础文本结构。隔离运行需托管沙箱配置与访问码，运行会产生沙箱费用。
            </span>
          </div>
          <div className="code-lab">
            <section>
              <div className="panel-heading">
                <span>01 / CODE</span>
                <label>
                  语言
                  <select
                    aria-label="代码语言"
                    value={language}
                    disabled={running}
                    onChange={(e) => {
                      const value = e.target.value as Language;
                      setLanguage(value);
                      if (trackId === 'fullstack') setProgress((p) => ({ ...p, language: value }));
                    }}
                  >
                    {(['python', 'typescript', 'go'] as Language[]).map((lang) => (
                      <option key={lang} value={lang}>
                        {languageNames[lang]}
                      </option>
                    ))}
                  </select>
                </label>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={running}
                  onClick={() => {
                    setDrafts((items) => items.filter((draft) => draft.context !== draftKey));
                    setCheckResult(null);
                    setExecution(null);
                    setError('');
                  }}
                >
                  恢复示例
                </Button>
              </div>
              <textarea
                className="code-editor"
                aria-label="代码编辑器"
                value={code}
                maxLength={20000}
                spellCheck={false}
                disabled={running}
                onChange={(e) => {
                  const value = e.target.value;
                  setDrafts((items) => updateDraft(items, draftKey, value));
                  setCheckResult(null);
                  setExecution(null);
                  setError('');
                }}
              />
              {canExecute && (
                <label className="field-label sandbox-access">
                  实验访问码
                  <input
                    aria-label="沙箱访问码"
                    type="password"
                    autoComplete="off"
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    placeholder="只保留在当前页面会话"
                    disabled={running}
                  />
                </label>
              )}
              <div className="code-editor-footer">
                <span>
                  {code.split('\n').length} 行 · {code.length} 字符
                </span>
                <Button onClick={() => void check()} disabled={running || !code.trim()}>
                  {running ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}
                  检查代码
                </Button>
                {running ? (
                  <Button variant="outline" onClick={stopCodeRequest}>
                    <Square size={15} />
                    停止运行
                  </Button>
                ) : (
                  <Button
                    variant="dark"
                    disabled={!canExecute || !token || !code.trim()}
                    onClick={() => void executeCode()}
                  >
                    <Play size={15} />
                    隔离运行
                  </Button>
                )}
              </div>
              <div className="code-draft-tools">
                <p>最近 20 组编辑保留在本次页面会话；刷新前可下载当前代码。</p>
                <Button variant="outline" size="sm" disabled={!code.trim()} onClick={downloadCode}>
                  <Download size={14} />
                  下载当前代码
                </Button>
              </div>
              <p className="sandbox-help">
                {canExecute
                  ? '独立沙箱 · 出站网络关闭 · 12 秒运行上限 · 不传入应用密钥'
                  : language === 'go' && cap?.sandbox?.enabled
                    ? 'Go 运行需要预装编译器的沙箱模板。'
                    : '隔离运行尚未配置，静态检查可以直接使用。'}
              </p>
            </section>
            <section className="check-output">
              <div className="panel-heading">
                <span>02 / CHECKS</span>
                <strong>检查结果</strong>
              </div>
              {error && (
                <div className="inline-error" role="alert">
                  {error}
                </div>
              )}
              {execution ? (
                <div className="check-results">
                  <h3>
                    {execution.passed
                      ? '隔离运行完成'
                      : execution.status === 'timeout'
                        ? '运行超时'
                        : execution.status === 'output-limit'
                          ? '输出达到上限'
                          : '隔离运行未通过'}
                  </h3>
                  <pre className="sandbox-output">{execution.stdout || '没有标准输出'}</pre>
                  {execution.stderr && (
                    <pre className="sandbox-output sandbox-stderr">{execution.stderr}</pre>
                  )}
                  <p>{execution.notice}</p>
                  <span className="badge">
                    {execution.cleanup === 'expiry-fallback'
                      ? '清理未确认，等待 45 秒存活上限到期'
                      : '沙箱已销毁或已过期'}
                  </span>
                  <div className="run-metadata">
                    <span>{(execution.duration_ms / 1000).toFixed(2)}s</span>
                    <code>run / {execution.run_id.slice(0, 8)}</code>
                  </div>
                </div>
              ) : checkResult ? (
                <div className="check-results">
                  <h3>{checkResult.passed ? '基础检查通过' : '还有需要修改的地方'}</h3>
                  {checkResult.checks.map((item, i) => (
                    <div className={item.passed ? 'passed' : 'failed'} key={i}>
                      {item.passed ? <CheckCircle2 size={18} /> : <XCircle size={18} />}
                      <span>{item.title}</span>
                    </div>
                  ))}
                  <p>{checkResult.notice}</p>
                  <span className="badge">未执行代码</span>
                </div>
              ) : (
                <div className="lab-empty">
                  <Code2 size={32} strokeWidth={1.2} />
                  <h3>先检查结构，再验证行为</h3>
                  <p>
                    修改代码后点击检查。
                    <br />
                    配置隔离沙箱后，可独立运行并查看输出。
                  </p>
                </div>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}
function ArrowRightIcon() {
  return <span aria-hidden="true">↗</span>;
}
