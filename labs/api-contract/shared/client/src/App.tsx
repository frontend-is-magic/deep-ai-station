import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { atom, useAtom } from 'jotai';
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { Button } from './components/ui/button';
import { createRequestController } from './controller';
import { detailHref, parseLocation, searchHref, type ValidTarget } from './navigation';
import { normalizeQuestion, recordNotice, type RequestRecord } from './protocol';

type Controller = ReturnType<typeof createRequestController>;
type Snapshot = ReturnType<Controller['getSnapshot']>;

function RequestFeedback({ record, retry }: { record: RequestRecord; retry: () => void }) {
  const isError = !['loading', 'success', 'empty'].includes(record.outcome);
  const canRetry = isError && record.outcome !== 'not_found';
  return (
    <div className="space-y-3">
      <p
        role={isError ? 'alert' : 'status'}
        className={`plain-text text-sm leading-7 ${isError ? 'text-amber-900' : 'text-stone-600'}`}
      >
        {recordNotice(record)}
      </p>
      {canRetry && (
        <Button type="button" className="secondary-button" onClick={retry}>
          重试请求
        </Button>
      )}
    </div>
  );
}

function RequestPanel({ record }: { record: RequestRecord | null }) {
  if (!record) return null;
  return (
    <details className="panel">
      <summary className="cursor-pointer text-sm font-semibold">查看本次请求</summary>
      <p className="mt-3 text-sm leading-6 text-stone-500">
        仅保留当前尝试。null 表示尚未观察到状态，或没有通过校验的响应内容。
      </p>
      <pre
        aria-label="本次请求记录"
        tabIndex={0}
        className="plain-text mt-4 max-h-96 overflow-auto rounded-xl bg-stone-950 p-4 font-mono text-xs leading-6 text-stone-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-lime-700"
      >
        {JSON.stringify(record, null, 2)}
      </pre>
    </details>
  );
}

function RequestPage({ target }: { target: ValidTarget }) {
  const navigate = useNavigate();
  const [snapshotAtom] = useState(() => atom<Snapshot>({ record: null }));
  const [snapshot, setSnapshot] = useAtom(snapshotAtom);
  const controller = useRef<Controller | null>(null);
  const [draft, setDraft] = useState(target.question ?? '');
  const [invalidDraft, setInvalidDraft] = useState(false);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const record = snapshot.record;

  useEffect(() => {
    // A StrictMode setup owns its controller; a disposed instance is never reused.
    const current = createRequestController();
    controller.current = current;
    setSnapshot(current.getSnapshot());
    const unsubscribe = current.subscribe((next) => setSnapshot(next));
    if (target.kind === 'detail' || target.question !== null) current.run(target);
    return () => {
      unsubscribe();
      if (controller.current === current) controller.current = null;
      current.dispose();
    };
  }, [setSnapshot, target]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const question = normalizeQuestion(draft);
    if (question === null) {
      setInvalidDraft(true);
      textarea.current?.focus();
      return;
    }
    setInvalidDraft(false);
    setDraft(question);
    if (target.kind === 'search' && question === target.question) {
      controller.current?.run(target);
    } else {
      navigate(searchHref(question));
    }
  }

  function retry() {
    controller.current?.run(target);
  }

  const body = record?.response.body;
  const results =
    target.kind === 'search' && record?.outcome === 'success' && body && 'items' in body
      ? body.items
      : [];
  const detail =
    target.kind === 'detail' && record?.outcome === 'success' && body && 'id' in body ? body : null;

  return (
    <div className="space-y-5">
      {target.kind === 'search' ? (
        <div className="grid min-w-0 gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <section className="panel space-y-5" aria-label="查询表单">
            <div className="space-y-2">
              <h2 className="text-xl font-semibold">把查询留在地址中</h2>
              <p className="text-sm leading-7 text-stone-600">
                编辑只改变草稿。提交后，地址、查询结果和请求记录才会一起更新。
              </p>
            </div>
            <form onSubmit={submit} className="space-y-3">
              <label htmlFor="search-question" className="block text-sm font-semibold">
                检索问题
              </label>
              <textarea
                id="search-question"
                ref={textarea}
                className="field plain-text"
                rows={4}
                value={draft}
                aria-invalid={invalidDraft}
                aria-describedby={invalidDraft ? 'question-help question-error' : 'question-help'}
                onChange={(event) => {
                  setDraft(event.target.value);
                  setInvalidDraft(false);
                }}
                onKeyDown={(event) => {
                  if (
                    event.key === 'Enter' &&
                    !event.shiftKey &&
                    !event.nativeEvent.isComposing &&
                    event.nativeEvent.keyCode !== 229
                  ) {
                    event.preventDefault();
                    event.currentTarget.form?.requestSubmit();
                  }
                }}
              />
              <p id="question-help" className="text-xs leading-6 text-stone-500">
                1–500 个 Unicode 码点。Enter 搜索，Shift+Enter 换行；可从“工具”或“HTTP”开始。
              </p>
              {invalidDraft && (
                <p id="question-error" role="alert" className="text-sm text-amber-900">
                  请输入 1–500 个 Unicode 码点的问题。
                </p>
              )}
              <Button type="submit">搜索</Button>
            </form>
            <div className="min-w-0 border-t border-stone-200 pt-4">
              <p className="mb-2 text-xs font-semibold text-stone-500">已提交查询</p>
              <output aria-label="已提交查询" className="plain-text block text-sm leading-7">
                {target.question ?? '尚未提交'}
              </output>
              {target.question !== null && draft !== target.question && (
                <p role="status" className="mt-3 text-sm leading-6 text-stone-600">
                  草稿尚未提交；当前结果仍对应上次查询。
                </p>
              )}
            </div>
          </section>
          <section className="panel space-y-4" aria-label="查询结果">
            <h2 className="text-xl font-semibold">查询结果</h2>
            {record ? (
              <RequestFeedback record={record} retry={retry} />
            ) : (
              <p role="status" className="text-sm leading-7 text-stone-600">
                填写问题后点击搜索。
              </p>
            )}
            <div className="space-y-3">
              {results.map((item) => (
                <article
                  key={item.id}
                  aria-label={item.id}
                  data-lesson-id={item.id}
                  className="min-w-0 rounded-xl border border-stone-200 p-4"
                >
                  <h3 className="plain-text font-semibold">{item.title}</h3>
                  <p className="plain-text mt-1 font-mono text-xs leading-6 text-stone-500">
                    {item.id}
                  </p>
                  <Link
                    className="text-link mt-3 inline-block"
                    to={detailHref(item.id, target.question)}
                  >
                    查看资料：{item.title}
                  </Link>
                </article>
              ))}
            </div>
          </section>
        </div>
      ) : (
        <section className="panel space-y-5" aria-label="资料摘要">
          <div className="space-y-2">
            <h2 className="text-xl font-semibold">资料摘要</h2>
            <p className="text-sm leading-7 text-stone-600">
              接口只返回资料 ID 和标题。这里没有原文正文，也没有生成回答。
            </p>
          </div>
          {record && <RequestFeedback record={record} retry={retry} />}
          {detail && (
            <div className="min-w-0 rounded-xl border border-stone-200 p-4">
              <h3 className="plain-text text-lg font-semibold">{detail.title}</h3>
              <dl className="mt-3 grid min-w-0 grid-cols-[auto_minmax(0,1fr)] gap-3 text-sm">
                <dt className="text-stone-500">资料 ID</dt>
                <dd className="plain-text font-mono">{detail.id}</dd>
              </dl>
            </div>
          )}
          <Link className="text-link inline-block" to={searchHref(target.question)}>
            {target.question === null ? '返回查询页' : '返回搜索结果'}
          </Link>
        </section>
      )}
      <RequestPanel record={record} />
    </div>
  );
}

export default function App() {
  const location = useLocation();
  const target = useMemo(
    () => parseLocation(location),
    [location.pathname, location.search, location.hash],
  );
  const canonical = target.kind === 'search' || target.kind === 'detail' ? target.canonical : null;
  const currentHref = location.pathname + location.search + location.hash;

  return (
    <main className="mx-auto max-w-5xl space-y-6 px-4 py-8 sm:px-6 sm:py-12">
      <header className="space-y-3">
        <p className="text-sm font-semibold tracking-wide text-lime-800">
          本地实验 · URL 与真实 API
        </p>
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">可导航 API 检索实验</h1>
        <p className="max-w-3xl text-sm leading-7 text-stone-600 sm:text-base">
          公共 React
          客户端连接你选择的本地后端；不调用模型，不保存学习记录。查询会进入地址和浏览器历史，请只使用公开教学问题。
        </p>
      </header>
      {canonical !== null && canonical !== currentHref ? (
        <Navigate to={canonical} replace />
      ) : target.kind === 'search' || target.kind === 'detail' ? (
        <RequestPage key={location.key} target={target} />
      ) : (
        <section className="panel space-y-4" aria-label="地址反馈">
          <h2 className="text-xl font-semibold">
            {target.kind === 'invalid' ? '地址参数无效' : '页面不存在'}
          </h2>
          <p role="alert" className="text-sm leading-7 text-stone-600">
            当前地址没有发起 API 请求。返回查询页后重新提交公开教学问题。
          </p>
          <Link className="text-link inline-block" to={searchHref(null)}>
            返回查询页
          </Link>
        </section>
      )}
      <footer className="text-xs leading-6 text-stone-500">
        刷新和历史导航会重新读取当前地址对应的数据。这个 POST
        是无副作用检索，请勿把自动重放规则用于写入操作。
      </footer>
    </main>
  );
}
