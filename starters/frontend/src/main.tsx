import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { atom, useAtom } from 'jotai';
import { Button } from './components/ui/button';
import './style.css';

interface Answer {
  run_id: string;
  mode: 'demo' | 'openai' | 'no-evidence';
  answer: string;
  sources: { id: string; title: string; url: string }[];
  usage: Record<string, number> | null;
}
const recordsAtom = atom<Answer[]>([]);
function App() {
  const [question, setQuestion] = useState('API 超时后，前端应该怎样显示错误？');
  const [mode, setMode] = useState<'demo' | 'openai'>('demo');
  const [token, setToken] = useState('');
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<Answer | null>(null);
  const [records, setRecords] = useAtom(recordsAtom);
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  useEffect(
    () => () => {
      mounted.current = false;
      controller.current?.abort();
    },
    [],
  );
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
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || '请求失败，请稍后重试');
      if (!mounted.current || active.signal.aborted) return;
      setResult(payload);
      setRecords((previous) => [payload, ...previous].slice(0, 20));
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
        <h1 className="mt-2 text-3xl font-semibold">AI 知识工作台</h1>
        <p className="mt-3 text-slate-600">
          同一 React 前端，连接 Python / TypeScript / Go 的共同 API 契约。
        </p>
      </header>
      <section className="space-y-4 rounded-xl border border-lime-200 bg-white p-5">
        <label className="block">
          运行模式
          <select
            aria-label="运行模式"
            disabled={running}
            value={mode}
            onChange={(e) => setMode(e.target.value as typeof mode)}
            className="ml-3 rounded border p-2"
          >
            <option value="demo">教学演示 · 无模型费用</option>
            <option value="openai">真实 OpenAI · 需要托管配置</option>
          </select>
        </label>
        <p className="text-sm text-slate-600">
          {mode === 'demo'
            ? '固定资料检索与整理，没有调用模型。'
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
              className="ml-3 max-w-full rounded border p-2"
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
        <div className="flex gap-3">
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
          <p role="alert" className="text-red-800">
            {error}
          </p>
        )}
      </section>
      {result && (
        <section aria-label="回答" className="space-y-4 rounded-xl border bg-white p-5">
          <p className="text-sm text-lime-800">
            {result.mode === 'demo'
              ? '教学演示'
              : result.mode === 'no-evidence'
                ? '证据不足 · 未调用模型'
                : '真实模型回答'}
          </p>
          <pre className="whitespace-pre-wrap break-words font-sans leading-7">{result.answer}</pre>
          <ul className="space-y-2">
            {result.sources.map((source) => (
              <li key={source.id}>
                <a className="underline" href={source.url} target="_blank" rel="noreferrer">
                  {source.title}
                </a>
              </li>
            ))}
          </ul>
          <p className="text-xs text-slate-500">
            run / {result.run_id} ·{' '}
            {result.usage ? '已知用量 ' + JSON.stringify(result.usage) : '没有供应商用量'} ·
            引用是实际检索来源，仍需核对回答事实。
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
            className="mb-2 block w-full rounded border bg-white p-3 text-left"
            disabled={running}
            onClick={() => setResult(record)}
          >
            {record.mode} · {record.answer.slice(0, 80)}
          </button>
        ))}
      </section>
    </main>
  );
}
createRoot(document.getElementById('root')!).render(<App />);
