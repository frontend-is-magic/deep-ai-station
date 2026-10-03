import { useEffect, useRef, useState } from 'react';
import { useAtom } from 'jotai';
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
import { progressAtom } from '@/lib/state';
import { incompleteRunUsage, updateRunUsage, type RunUsageSnapshot } from '@/lib/run-usage';
import { codeDraftsAtom, updateDraft } from '@/lib/drafts';
import { languageNames } from '@/lib/utils';
import { providerForReplay, providerLabel } from '@/lib/providers';
import type {
  Capabilities,
  Language,
  Lesson,
  LiveProvider,
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
  const [runInfo, setRunInfo] = useState<{
    id: string;
    duration: number;
    usage: Record<string, number> | null;
    provider: string;
    lessonId?: string;
    prompt: string;
    date: string;
    workflow?: RunWorkflow;
    steps?: number;
    toolCount?: number;
    usageComplete?: boolean;
    restored?: boolean;
  } | null>(null);
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
  const code = drafts.find((draft) => draft.context === draftKey)?.code ?? initialCode;
  const [checkResult, setCheckResult] = useState<CheckResult | null>(null);
  const [execution, setExecution] = useState<ExecutionResult | null>(null);
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    const c = new AbortController();
    mounted.current = true;
    api<Capabilities>('/capabilities', { signal: c.signal })
      .then(setCap)
      .catch(() => {});
    return () => {
      mounted.current = false;
      c.abort();
      const previous = controller.current;
      controller.current = null;
      previous?.abort();
    };
  }, []);
  useEffect(() => {
    // History navigation can change the visible experiment while a request is pending.
    const previous = controller.current;
    if (previous) {
      controller.current = null;
      previous.abort();
      setRunning(false);
      setLiveUsage(null);
      setOutput('');
      setTrace([]);
      setRunInfo(null);
    }
    setCheckResult(null);
    setExecution(null);
    setError('');
  }, [draftKey, mode]);
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
    controller.current?.abort();
    controller.current = null;
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
    const active = controller.current;
    if (!active) return;
    // Invalidate before abort: even a transport ignoring AbortSignal cannot alter a new run.
    controller.current = null;
    active.abort();
    setLiveUsage((previous) => ({
      ...incompleteRunUsage(previous),
      provider: previous?.provider ?? provider,
    }));
    setRunning(false);
    setError('运行已停止，部分结果未计入历史。');
  }
  async function run() {
    if (running || !prompt.trim()) return;
    const c = new AbortController();
    controller.current = c;
    setRunning(true);
    setOutput('');
    setTrace([]);
    setError('');
    setRunInfo(null);
    setLiveUsage({ usage: null, usageComplete: false, provider });
    let answer = '';
    let id = '';
    let terminal = false;
    let usageSnapshot: RunUsageSnapshot | null = null;
    const observed: RunTrace[] = [];
    const current = () => mounted.current && controller.current === c;
    function finish() {
      terminal = true;
      controller.current = null;
      setRunning(false);
      c.abort();
    }
    function snapshot(data: Record<string, unknown>, final = false) {
      usageSnapshot = updateRunUsage(usageSnapshot, data, final);
      setLiveUsage({ ...usageSnapshot, provider });
      return usageSnapshot;
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
          if (!current() || c.signal.aborted || terminal) return;
          if (typeof event.data.run_id === 'string' && id && event.data.run_id !== id) return;
          if (event.event === 'start') id = String(event.data.run_id);
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
            setError(String(event.data.message));
            finish();
          }
          if (event.event === 'done') {
            const finalUsage = snapshot(event.data, true);
            finish();
            if (event.data.truncated === true) {
              setError('输出达到模型上限，内容可能未完成，未写入运行历史。请缩小任务后重试。');
              return;
            }
            const duration = Number(event.data.duration_ms);
            const date = new Date().toISOString();
            const usage = finalUsage.usage;
            const steps = typeof event.data.steps === 'number' ? event.data.steps : undefined;
            const toolCount =
              typeof event.data.tool_count === 'number' ? event.data.tool_count : undefined;
            const usageComplete = finalUsage.usageComplete;
            setRunInfo({
              id,
              duration,
              usage,
              provider,
              lessonId: selectedLesson?.id,
              prompt,
              date,
              workflow,
              steps,
              toolCount,
              usageComplete,
            });
            setProgress((p) => ({
              ...p,
              runs: [
                {
                  id,
                  prompt,
                  answer,
                  provider,
                  track: trackId,
                  ...(selectedLesson ? { lesson_id: selectedLesson.id } : {}),
                  date,
                  workflow,
                  trace: observed.slice(0, 12),
                  usage,
                  steps,
                  tool_count: toolCount,
                  usage_complete: usageComplete,
                  duration_ms: duration,
                },
                ...p.runs,
              ].slice(0, 20),
            }));
          }
        },
      );
      if (current() && !terminal) {
        terminal = true;
        setLiveUsage({ ...incompleteRunUsage(usageSnapshot), provider });
        setError('运行未完成，请重试');
      }
    } catch (e) {
      if (current() && !terminal) {
        terminal = true;
        setLiveUsage({ ...incompleteRunUsage(usageSnapshot), provider });
        setError(
          c.signal.aborted
            ? '运行已停止，部分结果未计入历史。'
            : e instanceof Error
              ? e.message
              : '运行失败',
        );
      }
    } finally {
      if (current()) {
        setRunning(false);
        controller.current = null;
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
      ? { usage: runInfo.usage, usageComplete: runInfo.usageComplete, provider: runInfo.provider }
      : null);
  const canExecute = cap?.sandbox?.languages.includes(language) || false;
  const noteMarker = runInfo ? `### 实验记录 ${runInfo.id}` : '';
  const noteEntry = runInfo
    ? `${noteMarker}\n\n${providerLabel(runInfo.provider)} · ${runInfo.workflow === 'agent' ? 'Agent 循环 · ' : ''}${new Date(runInfo.date).toLocaleString('zh-CN')}\n\n任务：${runInfo.prompt}\n\n${output}\n\n以上是学习参考，请另行记录实践输入、实际结果与验收证据。`
    : '';
  const canSaveNote = Boolean(selectedLesson && runInfo?.lessonId === selectedLesson.id);
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
          <span>{mode === 'agent' ? ' · 实验内容可写入本课笔记' : ' · 回到课程记录实践结果'}</span>
        </p>
      )}
      {history && (
        <div className="run-history">
          <h3>最近运行 · 当前浏览器</h3>
          {progress.runs.length ? (
            progress.runs.map((record) => (
              <button
                key={record.id}
                disabled={running}
                onClick={() => {
                  const lesson = trackLesson(tracks, record.track, record.lesson_id);
                  controller.current?.abort();
                  controller.current = null;
                  setLiveUsage(null);
                  setWorkflow(record.workflow || 'retrieval');
                  setLanguage(record.track === 'agent' ? 'python' : progress.language);
                  setParams(
                    {
                      track: record.track,
                      mode: 'agent',
                      workflow: record.workflow || 'retrieval',
                      ...(lesson ? { lesson: lesson.id } : {}),
                    },
                    { replace: true },
                  );
                  setPrompt(record.prompt);
                  setProvider(providerForReplay(record.provider, cap));
                  setOutput(record.answer);
                  setTrace(record.trace || []);
                  setRunInfo({
                    id: record.id,
                    duration: record.duration_ms,
                    usage: record.usage ?? null,
                    provider: record.provider,
                    lessonId: lesson?.id,
                    prompt: record.prompt,
                    date: record.date,
                    workflow: record.workflow,
                    steps: record.steps,
                    toolCount: record.tool_count,
                    usageComplete: record.usage_complete,
                    restored: true,
                  });
                  setError('');
                  setHistory(false);
                }}
              >
                <span>
                  {providerLabel(record.provider)}
                  {record.workflow === 'agent' && ' · Agent 循环'}
                  {record.lesson_id &&
                    ` · ${trackLesson(tracks, record.track, record.lesson_id)?.title || '课程实验'}`}
                </span>
                <strong>{record.prompt}</strong>
                <small>{new Date(record.date).toLocaleString('zh-CN')}</small>
              </button>
            ))
          ) : (
            <p>完成一次运行后，结果会保存到这里。</p>
          )}
        </div>
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
            <section className="lab-output">
              <div className="panel-heading">
                <span>02 / OUTPUT</span>
                <strong>
                  {running
                    ? '实验进行中'
                    : runInfo
                      ? '实验已完成'
                      : error
                        ? '运行未完成'
                        : '等待你的第一次运行'}
                </strong>
                {running && <Loader2 size={16} className="animate-spin" />}
              </div>
              {!output && !trace.length && !error && (
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
              {error && (
                <div className="inline-error" role="alert">
                  <XCircle size={17} />
                  {error}
                </div>
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
                  <span>完成 · {(runInfo.duration / 1000).toFixed(2)}s</span>
                  {runInfo.workflow === 'agent' && runInfo.steps !== undefined && (
                    <span>
                      {runInfo.steps}
                      {runInfo.provider === 'demo' ? ' 步预设流程' : ' 轮模型请求'} ·{' '}
                      {runInfo.toolCount} 次工具请求
                    </span>
                  )}
                  <code>run / {runInfo.id.slice(0, 8)}</code>
                </div>
              )}
              {displayedUsage && (
                <section aria-label="模型用量" className="run-metadata">
                  <span>
                    {displayedUsage.usage
                      ? running
                        ? '已知用量 · 运行尚未结束'
                        : displayedUsage.usageComplete === false
                          ? runInfo
                            ? '部分模型轮次未返回用量'
                            : '已知用量 · 本次统计不完整'
                          : displayedUsage.usageComplete === true && !runInfo
                            ? '已知用量 · 已发出请求均已统计'
                            : '供应商已返回 usage'
                      : runInfo
                        ? displayedUsage.provider === 'demo'
                          ? '未调用模型'
                          : '供应商未返回用量'
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
                        const previous = p.notes[selectedLesson.id] || '';
                        const next = `${previous}${previous ? '\n\n' : ''}${noteEntry}`;
                        if (previous.includes(noteMarker) || next.length > 10000) return p;
                        return { ...p, notes: { ...p.notes, [selectedLesson.id]: next } };
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
