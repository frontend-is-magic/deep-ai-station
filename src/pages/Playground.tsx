import { useEffect, useRef, useState } from 'react';
import { useAtom } from 'jotai';
import { Link, useSearchParams } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import {
  Bot,
  CheckCircle2,
  ChevronDown,
  Code2,
  FlaskConical,
  History,
  Loader2,
  Play,
  RotateCcw,
  Square,
  Terminal,
  XCircle,
} from 'lucide-react';
import { api, streamRun } from '@/lib/api';
import { progressAtom } from '@/lib/state';
import { languageNames } from '@/lib/utils';
import type { Capabilities, Language, Track, TrackId } from '@/lib/types';
import { PageHeading } from '@/components/common';
import { Button } from '@/components/ui/button';

interface Trace {
  title: string;
  detail: string;
  status: string;
}
interface CheckResult {
  mode: string;
  executed: boolean;
  checks: { title: string; passed: boolean }[];
  passed: boolean;
  notice: string;
}

export default function Playground({ tracks }: { tracks: Track[] }) {
  const [params, setParams] = useSearchParams();
  const [progress, setProgress] = useAtom(progressAtom);
  const [trackId, setTrackId] = useState<TrackId>(
    params.get('track') === 'fullstack' ? 'fullstack' : 'agent',
  );
  const [mode, setMode] = useState(params.get('mode') === 'code' ? 'code' : 'agent');
  const [provider, setProvider] = useState('demo');
  const [prompt, setPrompt] = useState('如何设计一个有工具调用和停止条件的 AI Agent？');
  const [system, setSystem] = useState(
    '你是一位严谨的 AI 工程导师。给出可验证的步骤，明确不确定性。',
  );
  const [temperature, setTemperature] = useState(0.3);
  const [token, setToken] = useState('');
  const [cap, setCap] = useState<Capabilities | null>(null);
  const [running, setRunning] = useState(false);
  const [trace, setTrace] = useState<Trace[]>([]);
  const [output, setOutput] = useState('');
  const [error, setError] = useState('');
  const [runInfo, setRunInfo] = useState<{ id: string; duration: number; usage: unknown } | null>(
    null,
  );
  const [history, setHistory] = useState(false);
  const [language, setLanguage] = useState<Language>(
    trackId === 'agent' ? 'python' : progress.language,
  );
  const selectedLesson = tracks
    .flatMap((t) => t.lessons)
    .find((x) => x.id === params.get('lesson'));
  const track = tracks.find((x) => x.id === trackId)!;
  const initialCode =
    selectedLesson?.snippets[language] || track.lessons[0].snippets[language] || '';
  const [code, setCode] = useState(initialCode);
  const [checkResult, setCheckResult] = useState<CheckResult | null>(null);
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
      controller.current?.abort();
    };
  }, []);
  useEffect(() => {
    const lesson = tracks.flatMap((t) => t.lessons).find((x) => x.id === params.get('lesson'));
    const current = tracks.find((x) => x.id === trackId)!;
    setCode(lesson?.snippets[language] || current.lessons[0].snippets[language] || '');
    setCheckResult(null);
  }, [language, trackId, tracks, params]);
  function switchMode(next: string) {
    setMode(next);
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
    setTrackId(value);
    setLanguage(value === 'agent' ? 'python' : progress.language);
    setParams({ track: value, mode }, { replace: true });
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
  async function run() {
    if (running || !prompt.trim()) return;
    const c = new AbortController();
    controller.current = c;
    setRunning(true);
    setOutput('');
    setTrace([]);
    setError('');
    setRunInfo(null);
    let answer = '';
    let id = '';
    let completed = false;
    try {
      await streamRun(
        { prompt, system, provider, track: trackId, temperature },
        token,
        c.signal,
        (event) => {
          if (c.signal.aborted || !mounted.current) return;
          if (event.event === 'start') id = String(event.data.run_id);
          if (event.event === 'trace') setTrace((x) => [...x, event.data as unknown as Trace]);
          if (event.event === 'delta') {
            answer += String(event.data.text);
            setOutput(answer);
          }
          if (event.event === 'error') {
            setError(String(event.data.message));
          }
          if (event.event === 'done') {
            completed = true;
            const duration = Number(event.data.duration_ms);
            setRunInfo({ id, duration, usage: event.data.usage });
            setProgress((p) => ({
              ...p,
              runs: [
                {
                  id,
                  prompt,
                  answer,
                  provider,
                  track: trackId,
                  date: new Date().toISOString(),
                  duration_ms: duration,
                },
                ...p.runs,
              ].slice(0, 20),
            }));
          }
        },
      );
      if (!completed && !c.signal.aborted && mounted.current)
        setError((previous) => previous || '运行未完成，请重试');
    } catch (e) {
      if (mounted.current)
        setError(
          c.signal.aborted
            ? '运行已停止，部分结果未计入历史。'
            : e instanceof Error
              ? e.message
              : '运行失败',
        );
    } finally {
      if (mounted.current) setRunning(false);
      if (controller.current === c) controller.current = null;
    }
  }
  async function check() {
    setRunning(true);
    setError('');
    const c = new AbortController();
    controller.current = c;
    try {
      const result = await api<CheckResult>('/playground/check', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ language, code }),
        signal: c.signal,
      });
      if (mounted.current) setCheckResult(result);
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : '检查失败');
    } finally {
      if (mounted.current) setRunning(false);
      controller.current = null;
    }
  }
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
        </div>
        <label className="track-select">
          学习方向
          <select
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
      {history && (
        <div className="run-history">
          <h3>最近运行 · 当前浏览器</h3>
          {progress.runs.length ? (
            progress.runs.map((record) => (
              <button
                key={record.id}
                disabled={running}
                onClick={() => {
                  setPrompt(record.prompt);
                  setOutput(record.answer);
                  setTrace([]);
                  setRunInfo({ id: record.id, duration: record.duration_ms, usage: null });
                  setError('');
                  setHistory(false);
                }}
              >
                <span>{record.provider === 'demo' ? '教学演示' : record.provider}</span>
                <strong>{record.prompt}</strong>
                <small>{new Date(record.date).toLocaleString('zh-CN')}</small>
              </button>
            ))
          ) : (
            <p>完成一次运行后，结果会保存到这里。</p>
          )}
        </div>
      )}
      {mode === 'agent' ? (
        <>
          <div className={`mode-notice ${provider === 'demo' ? '' : 'live'}`}>
            <FlaskConical size={18} />
            <span>
              {provider === 'demo'
                ? '教学演示：确定性课程检索，不调用模型、不产生模型费用。'
                : '真实模型：服务端调用供应商，生成完成后逐段展示。需要实验访问码，调用会产生费用。'}
            </span>
          </div>
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
                  onChange={(e) => setProvider(e.target.value)}
                >
                  <option value="demo">教学演示 · 无需密钥</option>
                  {(cap?.providers || [])
                    .filter((x) => x.id !== 'demo')
                    .map((p) => (
                      <option key={p.id} disabled={!p.enabled} value={p.id}>
                        {p.name}
                        {p.enabled ? ` · ${p.model}` : ' · 未启用'}
                      </option>
                    ))}
                </select>
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
                  <Button variant="dark" onClick={() => controller.current?.abort()}>
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
                  {running ? '实验进行中' : runInfo ? '实验已完成' : '等待你的第一次运行'}
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
                    <div key={i}>
                      <CheckCircle2 size={17} />
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
                      a: ({ children, ...props }) => (
                        <a {...props} target="_blank" rel="noreferrer">
                          {children}
                        </a>
                      ),
                    }}
                  >
                    {output}
                  </ReactMarkdown>
                </div>
              )}
              {runInfo && (
                <div className="run-metadata">
                  <span>完成 · {(runInfo.duration / 1000).toFixed(2)}s</span>
                  <span>{runInfo.usage ? '供应商已返回 usage' : '无模型 token 用量'}</span>
                  <code>run / {runInfo.id.slice(0, 8)}</code>
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
              检查基础文本结构，业务行为仍需独立测试。
            </span>
          </div>
          {selectedLesson && (
            <p className="linked-lesson">
              来自课程：<Link to={`/lesson/${selectedLesson.id}`}>{selectedLesson.title}</Link>
            </p>
          )}
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
                    onChange={(e) => setLanguage(e.target.value as Language)}
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
                    setCode(initialCode);
                    setCheckResult(null);
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
                onChange={(e) => setCode(e.target.value)}
              />
              <div className="code-editor-footer">
                <span>
                  {code.split('\n').length} 行 · {code.length} 字符
                </span>
                <Button onClick={() => void check()} disabled={running || !code.trim()}>
                  {running ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}
                  检查代码
                </Button>
              </div>
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
              {checkResult ? (
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
                    这个环境不会执行用户代码。
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
