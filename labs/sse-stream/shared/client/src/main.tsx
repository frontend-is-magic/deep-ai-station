import { StrictMode, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { atom, useAtom } from 'jotai';
import { Button } from './components/ui/button';
import { consumeSse, ProtocolError, type Scenario, type StreamEvent } from './stream.mjs';
import './style.css';

type RunState = { status: string; text: string; runId: string; events: string[]; running: boolean };
const empty: RunState = { status: '等待开始', text: '', runId: '', events: [], running: false };
const panels = { A: atom<RunState>(empty), B: atom<RunState>(empty) };
const scenarios: [Scenario, string][] = [
  ['success', '正常完成'],
  ['error', '中途故障'],
  ['timeout', '总时限超时'],
  ['hold', '等待中手动停止'],
];

function RunPanel({ id }: { id: 'A' | 'B' }) {
  const [state, setState] = useAtom(panels[id]);
  const [scenario, setScenario] = useState<Scenario>('success');
  const active = useRef<AbortController | undefined>(undefined);
  useEffect(
    () => () => {
      const previous = active.current;
      active.current = undefined;
      previous?.abort();
    },
    [],
  );

  async function start() {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setState({ ...empty, status: '正在连接', running: true });
    let timedOut = false;
    const watchdog = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, 10000);
    function update(event: StreamEvent) {
      if (active.current !== controller || controller.signal.aborted) return;
      setState((value) => ({
        ...value,
        runId: event.data.run_id,
        status: '接收中',
        text: event.event === 'delta' ? value.text + event.data.text : value.text,
        events: [
          ...value.events,
          `${event.event}${event.event === 'delta' ? ` #${event.data.seq}` : ''}`,
        ],
      }));
    }
    try {
      const response = await fetch(`/api/stream?scenario=${scenario}`, {
        signal: controller.signal,
        headers: { Accept: 'text/event-stream' },
        cache: 'no-store',
      });
      const result = await consumeSse(response, scenario, update);
      if (active.current !== controller || controller.signal.aborted) return;
      setState((value) => ({
        ...value,
        running: false,
        status: result.kind === 'done' ? '已完成' : result.message,
      }));
    } catch (error) {
      if (active.current !== controller) return;
      const status = timedOut
        ? '客户端等待超时，已停止'
        : controller.signal.aborted
          ? '已停止，保留已收到的文字'
          : error instanceof ProtocolError
            ? error.message
            : '连接中断，运行未完成';
      setState((value) => ({ ...value, status, running: false }));
    } finally {
      window.clearTimeout(watchdog);
      if (active.current === controller) active.current = undefined;
    }
  }
  function stop() {
    const previous = active.current;
    active.current = undefined;
    previous?.abort();
    setState((value) => ({ ...value, status: '已停止，保留已收到的文字', running: false }));
  }
  return (
    <section
      aria-label={`运行 ${id}`}
      className="min-w-0 rounded-2xl border border-stone-200 bg-white p-6 shadow-sm"
    >
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-xl font-semibold">运行 {id}</h2>
        <span className="text-xs text-stone-500">独立连接</span>
      </div>
      <label className="mt-5 block text-sm font-medium">
        实验场景
        <select
          aria-label="实验场景"
          value={scenario}
          onChange={(e) => setScenario(e.target.value as Scenario)}
          className="mt-2 block w-full rounded-lg border border-stone-300 bg-white p-3"
        >
          {scenarios.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </label>
      <div className="my-4 flex flex-wrap gap-3">
        <Button onClick={() => void start()}>{state.running ? '重新运行' : '开始运行'}</Button>
        <Button className="bg-stone-100" disabled={!state.running} onClick={stop}>
          停止
        </Button>
      </div>
      <p role="status" className="min-h-6 text-sm font-medium text-stone-700">
        {state.status}
      </p>
      <p className="mt-2 break-all font-mono text-xs text-stone-500">
        run_id: {state.runId || '尚未建立'}
      </p>
      <div
        role="region"
        aria-label="增量文字"
        className="my-5 min-h-28 whitespace-pre-wrap break-words rounded-xl bg-lime-50 p-5 text-xl leading-relaxed"
      >
        {state.text || (
          <span className="text-sm text-stone-500">
            收到 delta 后逐步显示。失败或停止时保留已有文字。
          </span>
        )}
      </div>
      <h3 className="text-xs font-semibold uppercase tracking-widest text-stone-500">事件顺序</h3>
      <ol aria-label="事件顺序" className="mt-3 flex flex-wrap gap-2 text-xs">
        {state.events.map((name, index) => (
          <li key={index} className="rounded border border-stone-200 px-2 py-1 font-mono">
            {name}
          </li>
        ))}
      </ol>
    </section>
  );
}

function App() {
  return (
    <main className="mx-auto max-w-5xl px-5 py-12 sm:px-8">
      <p className="text-xs font-semibold uppercase tracking-[0.25em] text-lime-800">
        Deep AI Station / Streaming Lab
      </p>
      <h1 className="mt-4 text-3xl font-semibold tracking-tight sm:text-4xl">
        让每一段响应，都有明确归宿。
      </h1>
      <p className="mt-5 max-w-3xl leading-7 text-stone-600">
        观察相同协议如何传递增量、完成和故障。先运行 A，再启动 B；停止 A，检查 B
        是否仍正常完成。固定教学数据，无模型调用。
      </p>
      <div className="my-8 grid gap-5 md:grid-cols-2">
        <RunPanel id="A" />
        <RunPanel id="B" />
      </div>
      <aside className="rounded-xl border border-stone-200 p-5 text-sm leading-7 text-stone-600">
        服务端总时限为 3
        秒。网络分块不等于事件，客户端仅在完整事件到达后更新文字。页面停止只说明当前客户端已取消；服务端是否释放资源由独立真实
        HTTP 测试证明。将命令、成功与失败证据记入 EVIDENCE.md。
      </aside>
    </main>
  );
}
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
