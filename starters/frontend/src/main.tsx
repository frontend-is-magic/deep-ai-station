import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { atom, useAtom } from 'jotai';
import { Button } from './components/ui/button';
import { parseAnswer, parseHealth, responseError, safeHttpsUrl, type Answer } from './response';
import './style.css';

const outcomes = {
  complete: '整理完成',
  insufficient_evidence: '证据不足',
  conflicting_evidence: '证据冲突',
};
function resultTitle(result: Answer) {
  if (result.workflow === 'research-agent')
    return `${result.model_calls ? '真实研究助手' : '教学演示'} · ${outcomes[result.outcome]}`;
  return result.mode === 'demo'
    ? '教学演示'
    : result.mode === 'no-evidence'
      ? '证据不足 · 未调用模型'
      : '真实模型回答';
}
const recordsAtom = atom<Answer[]>([]);
function App() {
  const [question, setQuestion] = useState('API 超时后，前端应该怎样显示错误？');
  const [mode, setMode] = useState<'demo' | 'openai'>('demo');
  const [token, setToken] = useState('');
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<Answer | null>(null);
  const [capability, setCapability] = useState<'loading' | 'unknown' | 'knowledge' | 'agent'>(
    'loading',
  );
  const [records, setRecords] = useAtom(recordsAtom);
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    const health = new AbortController();
    async function loadCapability() {
      try {
        const response = await fetch('/api/health', {
          signal: AbortSignal.any([health.signal, AbortSignal.timeout(5000)]),
        });
        if (!response.ok) throw new Error('API 未就绪');
        const next = parseHealth(await response.json());
        if (mounted.current && !health.signal.aborted) setCapability(next);
      } catch {
        if (mounted.current && !health.signal.aborted) setCapability('unknown');
      }
    }
    void loadCapability();
    return () => {
      mounted.current = false;
      health.abort();
      controller.current?.abort();
    };
  }, []);
  async function ask() {
    if (running || !question.trim()) return;
    const active = new AbortController();
    controller.current = active;
    setRunning(true);
    setError('');
    setResult(null);
    try {
      const response = await fetch('/api/ask', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(mode === 'openai' ? { 'X-Playground-Token': token } : {}),
        },
        body: JSON.stringify({ prompt: question, mode }),
        signal: active.signal,
      });
      const payload: unknown = await response.json();
      if (!response.ok) throw new Error(responseError(payload));
      const answer = parseAnswer(payload);
      if (!mounted.current || active.signal.aborted) return;
      setResult(answer);
      setRecords((previous) => [answer, ...previous].slice(0, 20));
    } catch (e) {
      if (mounted.current)
        setError(
          active.signal.aborted
            ? '已取消；输入保留，供应商可能已经计费。'
            : e instanceof Error
              ? e.message
              : '请求失败',
        );
    } finally {
      if (mounted.current) setRunning(false);
      if (controller.current === active) controller.current = null;
    }
  }
  return (
    <main className="mx-auto max-w-3xl space-y-6 px-5 py-10">
      <header>
        <p className="text-sm text-lime-800">DEEP AI STATION / CAPSTONE</p>
        <h1 className="mt-2 text-3xl font-semibold">
          {capability === 'agent' ? 'Agent 研究助手' : 'AI 知识工作台'}
        </h1>
        <p className="mt-3 text-slate-600">
          同一 React 前端，连接 Python / TypeScript / Go 的共同 API 契约，并展示后端支持的研究能力。
        </p>
        {capability === 'agent' && (
          <p className="mt-3 text-sm text-slate-600">
            只读固定课程资料与冲突练习集。每次运行最多 3 次模型请求、2
            次工具请求；工具仅提供检索和资料读取，不访问外部页面，不执行代码。
          </p>
        )}
        {capability === 'unknown' && (
          <p role="status" className="mt-3 text-sm text-amber-800">
            API 健康信息未确认；请先启动对应后端。
          </p>
        )}
      </header>
      <section className="space-y-4 rounded-xl border border-lime-200 bg-white p-5">
        <label className="block">
          运行模式
          <select
            aria-label="运行模式"
            disabled={running}
            value={mode}
            onChange={(e) => setMode(e.target.value as typeof mode)}
            className="mt-2 block w-full max-w-full rounded border p-2"
          >
            <option value="demo">教学演示 · 无模型费用</option>
            <option value="openai">真实 OpenAI · 需要托管配置</option>
          </select>
        </label>
        <p className="text-sm text-slate-600">
          {mode === 'demo'
            ? capability === 'agent'
              ? '预设检索、读取和整理顺序，没有模型决策，也没有调用模型。'
              : '固定资料检索与整理，没有调用模型。'
            : '真实调用需要服务端密钥、访问码与供应商预算，调用会产生费用。'}
        </p>
        {mode === 'openai' && (
          <label className="block">
            实验访问码
            <input
              aria-label="实验访问码"
              type="password"
              autoComplete="off"
              disabled={running}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              className="mt-2 block w-full rounded border p-2"
            />
          </label>
        )}
        <label className="block">
          问题
          <textarea
            aria-label="问题"
            value={question}
            maxLength={1000}
            disabled={running}
            onChange={(e) => setQuestion(e.target.value)}
            rows={4}
            className="mt-2 block w-full rounded border p-3"
          />
        </label>
        {capability === 'agent' && (
          <div role="group" aria-label="示例问题" className="space-y-2">
            <p className="text-sm text-slate-600">示例只填入问题；点击“提问”才运行。</p>
            <div className="flex flex-wrap gap-2">
              {['API 超时', 'zzzz unmatched', '取消计费冲突'].map((example) => (
                <Button
                  key={example}
                  type="button"
                  className="border bg-white text-sm"
                  disabled={running}
                  onClick={() => setQuestion(example)}
                >
                  {example}
                </Button>
              ))}
            </div>
          </div>
        )}
        <div className="flex flex-wrap gap-3">
          <Button
            disabled={running || !question.trim() || (mode === 'openai' && !token)}
            onClick={ask}
          >
            提问
          </Button>
          {running && <Button onClick={() => controller.current?.abort()}>取消请求</Button>}
        </div>
        {running && <p role="status">正在检索与请求服务…</p>}
        {error && (
          <p role="alert" className="break-words text-red-800">
            {error}
          </p>
        )}
      </section>
      {result && (
        <section aria-label="回答" className="space-y-4 rounded-xl border bg-white p-5">
          <p className="text-sm text-lime-800">{resultTitle(result)}</p>
          {result.workflow === 'research-agent' && (
            <p className="text-sm text-slate-600">
              实际请求：模型 {result.model_calls} 次 · 工具 {result.tool_calls} 次
            </p>
          )}
          <pre className="whitespace-pre-wrap break-words font-sans leading-7">{result.answer}</pre>
          <ul className="space-y-2">
            {result.sources.map((source) => (
              <li key={source.id} className="break-words">
                {safeHttpsUrl(source.url) ? (
                  <a
                    className="underline"
                    href={safeHttpsUrl(source.url)!}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {source.title}
                  </a>
                ) : (
                  <span>
                    {source.title} ·{' '}
                    {source.kind === 'conflict-fixture' ? '冲突练习资料，无外链' : '无外链'}
                  </span>
                )}
              </li>
            ))}
          </ul>
          {result.workflow === 'research-agent' && (
            <>
              <section aria-label="引用摘录" className="space-y-3">
                <h2 className="font-semibold">引用摘录</h2>
                <p className="text-sm text-slate-600">
                  摘录来自本地课程与练习资料，来源链接供延伸阅读，没有获取对应网页。后端仅允许引用本次实际读取的资料，并校验摘录是正文子串。这是机械存在性校验，摘要结论仍需核对。
                </p>
                {result.citations.length ? (
                  result.citations.map((citation, index) => (
                    <blockquote
                      key={`${citation.document_id}-${index}`}
                      className="space-y-2 border-l-2 border-lime-600 pl-3"
                    >
                      <p className="whitespace-pre-wrap break-words text-sm">{citation.quote}</p>
                      <cite className="block break-words text-xs text-slate-500 not-italic">
                        {result.sources.find((source) => source.id === citation.document_id)?.title}
                      </cite>
                    </blockquote>
                  ))
                ) : (
                  <p className="text-sm text-slate-600">本次没有通过校验的引用摘录。</p>
                )}
              </section>
              <section aria-label="运行轨迹" className="space-y-3">
                <h2 className="font-semibold">有界运行轨迹</h2>
                <ol className="space-y-3">
                  {result.trace.map((step, index) => (
                    <li key={step.id} className="rounded border border-slate-200 p-3">
                      <p className="break-words text-sm font-medium">
                        {index + 1}. {step.title} · {step.status === 'success' ? '完成' : '失败'}
                      </p>
                      <p className="mt-1 whitespace-pre-wrap break-words text-sm text-slate-600">
                        {step.detail}
                      </p>
                    </li>
                  ))}
                </ol>
              </section>
            </>
          )}
          <p className="break-words text-xs text-slate-500">
            run / {result.run_id} ·{' '}
            {result.workflow === 'research-agent'
              ? result.model_calls === 0
                ? '未请求模型'
                : result.usage_complete
                  ? '已知用量 ' + JSON.stringify(result.usage)
                  : result.usage
                    ? '部分已知用量 ' + JSON.stringify(result.usage) + '；其余未报告，不能视为 0'
                    : '供应商用量未报告，不能视为 0'
              : result.usage
                ? '已知用量 ' + JSON.stringify(result.usage)
                : '没有供应商用量'}{' '}
            · 引用是实际检索来源，仍需核对回答事实。
          </p>
        </section>
      )}
      <section>
        <h2 className="font-semibold">本次页面会话 · 最近 {records.length} 次完整运行</h2>
        <p className="my-2 text-sm text-slate-600">
          刷新清空，不保存访问码。这里只提供只读固定资料集；上传、数据库、账号和生产发布是后续实践任务。
        </p>
        {records.map((record) => (
          <button
            key={record.run_id}
            className="mb-2 block w-full rounded border bg-white p-3 text-left break-words"
            disabled={running}
            onClick={() => setResult(record)}
          >
            {resultTitle(record)} · {record.answer.slice(0, 80)}
          </button>
        ))}
      </section>
    </main>
  );
}
createRoot(document.getElementById('root')!).render(<App />);
