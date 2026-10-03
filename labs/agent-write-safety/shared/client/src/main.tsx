import { StrictMode, useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { atom, useAtom } from 'jotai';
import { Button } from './components/ui/button';
import fixtureData from './fixtures.json';
import {
  LabError,
  UNCONFIRMED_MESSAGE,
  canExecute,
  canReplay,
  createRequestGate,
  draftFromDocument,
  draftFromOperation,
  draftProblem,
  hasUnconfirmedOperations,
  isOperationId,
  parseDocuments,
  parseExecution,
  parseOperation,
  parsePrincipal,
  preparePayload,
  rememberOperation,
  requestJson,
  sameDraft,
  type Document,
  type Draft,
  type Fixture,
  type Operation,
  type Principal,
  type RecoveryRecords,
  type Ticket,
} from './protocol.mjs';
import './style.css';

const fixtures: Fixture[] = fixtureData;
// Only owner-scoped operation IDs and uncertainty flags survive a fixture switch, in memory.
const recoveryAtom = atom<RecoveryRecords>({});
type View = {
  token: string;
  principal: Principal | null;
  documents: Document[];
  draft: Draft | null;
  operation: Operation | null;
  operationId: string;
  queryId: string;
  feedback: string;
  running: boolean;
  replayed: boolean | null;
};
function emptyView(token: string, operationId = ''): View {
  return {
    token,
    principal: null,
    documents: [],
    draft: null,
    operation: null,
    operationId,
    queryId: operationId,
    feedback: '正在读取教学身份…',
    running: false,
    replayed: null,
  };
}
const statusNames = {
  prepared: '待批准',
  approved: '已批准',
  applied: '已执行',
  revoked: '已撤销',
  expired: '已过期',
};
const roleNames: Record<string, string> = {
  'fixture-alice-agent': 'Alice · Agent 申请者',
  'fixture-alice-approver': 'Alice · 审批者',
  'fixture-alice-peer': 'Alice · 另一位 Agent',
  'fixture-alice-reader': 'Alice · 只读观察者',
  'fixture-bob-agent': 'Bob · Agent 申请者',
  'fixture-bob-approver': 'Bob · 审批者',
  'fixture-expired': 'Alice · 已过期会话',
  'fixture-revoked': 'Alice · 已撤销会话',
};
function PlainText({ label, content }: { label: string; content: string }) {
  return (
    <div className="min-w-0">
      <h3 className="mb-2 text-sm font-semibold">{label}</h3>
      <pre
        aria-label={label}
        className="min-w-0 whitespace-pre-wrap break-words rounded-lg bg-stone-50 p-4 font-sans text-sm leading-7 [overflow-wrap:anywhere]"
      >
        {content}
      </pre>
    </div>
  );
}
function sameIntent(a: Operation, b: Operation) {
  return (
    a.operation_id === b.operation_id &&
    a.owner_id === b.owner_id &&
    a.requester_id === b.requester_id &&
    a.tool === b.tool &&
    a.document_id === b.document_id &&
    a.expected_version === b.expected_version &&
    a.before_content === b.before_content &&
    a.content === b.content &&
    a.intent_hash === b.intent_hash &&
    a.prepared_at === b.prepared_at
  );
}
type RequestOptions = {
  operationId?: string;
  mutation?: boolean;
  query?: boolean;
  message: string;
};
type Result = { patch: Partial<View>; confirmed?: boolean };

function App() {
  const [fixture, setFixture] = useState(fixtures[0]);
  const [recovery, setRecovery] = useAtom(recoveryAtom);
  const [view, setView] = useState<View>(() => emptyView(fixtures[0].token));
  const gate = useRef(createRequestGate());
  const active = useRef<RequestOptions | null>(null);
  const current = view.token === fixture.token ? view : emptyView(fixture.token);
  const ownerRecords = recovery[fixture.owner_id] || [];
  const ownerUncertain = hasUnconfirmedOperations(recovery, fixture.owner_id);
  const uncertain = ownerRecords.some((item) => item.id === current.operationId && item.uncertain);
  const principal = current.principal;
  const operation = current.operation;
  const document = current.documents.find((item) => item.id === current.draft?.document_id);
  const mayPrepare = !!principal?.capabilities.includes('prepare');
  const mayApprove = !!principal?.capabilities.includes('approve');
  const mayRead = !!principal?.capabilities.includes('read');
  const problem = draftProblem(current.draft);
  const draftChanged = !!operation && !sameDraft(current.draft, operation);

  function record(id: string, value: boolean) {
    setRecovery((previous) => rememberOperation(previous, fixture.owner_id, id, value));
  }
  async function perform(
    options: RequestOptions,
    work: (ticket: Ticket) => Promise<Result | null>,
  ) {
    const ticket = gate.current.begin(fixture.token);
    active.current = options;
    if (options.mutation && options.operationId) record(options.operationId, true);
    setView((previous) => ({ ...previous, running: true, feedback: options.message }));
    const timeout = window.setTimeout(() => ticket.controller.abort(), 10000);
    try {
      const result = await work(ticket);
      if (!gate.current.isLatest(ticket)) return;
      if (ticket.controller.signal.aborted) throw new DOMException('Aborted', 'AbortError');
      if (!result) return;
      if (options.operationId && result.confirmed) record(options.operationId, false);
      setView((previous) => ({ ...previous, ...result.patch, running: false }));
    } catch (error) {
      if (!gate.current.isLatest(ticket)) return;
      const knownFailure =
        error instanceof LabError &&
        error.status >= 400 &&
        error.status < 500 &&
        error.code !== 'unexpected_response';
      if (
        options.operationId &&
        knownFailure &&
        (options.mutation || (options.query && error.code === 'operation_not_found'))
      ) {
        record(options.operationId, false);
      }
      const feedback =
        options.mutation && !knownFailure
          ? UNCONFIRMED_MESSAGE
          : ticket.controller.signal.aborted
            ? '读取等待已结束。可以使用原操作 ID 重新查询。'
            : error instanceof LabError
              ? error.message
              : '连接中断。请使用原操作 ID 重新查询。';
      setView((previous) => ({ ...previous, running: false, feedback }));
    } finally {
      window.clearTimeout(timeout);
      if (gate.current.isLatest(ticket)) {
        active.current = null;
        gate.current.finish(ticket);
      }
    }
  }
  useEffect(() => {
    const remembered = (recovery[fixture.owner_id] || []).at(-1)?.id || '';
    setView(emptyView(fixture.token, remembered));
    void perform(
      { message: '正在读取教学身份与当前文档…', operationId: remembered, query: true },
      async (ticket) => {
        const me = await requestJson(ticket, '/me', (value) => parsePrincipal(value, fixture));
        if (!gate.current.isCurrent(ticket)) return null;
        const documents = await requestJson(ticket, '/documents', parseDocuments);
        if (!gate.current.isCurrent(ticket)) return null;
        const patch: Partial<View> = {
          principal: me,
          documents,
          draft: documents[0] ? draftFromDocument(documents[0]) : null,
          feedback: '教学身份已读取。准备意图不会发布内容。',
        };
        if (!remembered) return { patch };
        try {
          const saved = await requestJson(ticket, `/operations/${remembered}`, (value) =>
            parseOperation(value, fixture.owner_id, remembered),
          );
          return {
            patch: {
              ...patch,
              operation: saved,
              draft: draftFromOperation(saved),
              feedback: '已查询本 owner 的原操作，请审阅服务端保存的意图。',
            },
            confirmed: true,
          };
        } catch (error) {
          return {
            patch: {
              ...patch,
              feedback:
                error instanceof LabError
                  ? error.message
                  : '原操作暂时无法确认，请保留 ID 后重新查询。',
            },
            confirmed: error instanceof LabError && error.code === 'operation_not_found',
          };
        }
      },
    );
    return () => {
      gate.current.cancel();
      active.current = null;
    };
  }, [fixture.token]);

  function switchIdentity(token: string) {
    const next = fixtures.find((item) => item.token === token);
    if (!next) return;
    gate.current.cancel();
    active.current = null;
    setView(emptyView(next.token, (recovery[next.owner_id] || []).at(-1)?.id || ''));
    setFixture(next);
  }
  function stop() {
    const mutation = active.current?.mutation;
    gate.current.cancel();
    active.current = null;
    setView((previous) => ({
      ...previous,
      running: false,
      feedback: mutation ? UNCONFIRMED_MESSAGE : '已停止读取。可以使用原操作 ID 重新查询。',
    }));
  }
  function prepare() {
    const draft = current.draft;
    if (
      !mayPrepare ||
      !principal ||
      !draft ||
      draftProblem(draft) ||
      current.running ||
      ownerUncertain
    )
      return;
    const operationId = crypto.randomUUID();
    setView((previous) => ({
      ...previous,
      operationId,
      queryId: operationId,
      operation: null,
      replayed: null,
    }));
    void perform(
      { operationId, mutation: true, message: '正在准备新意图；尚未请求发布。' },
      async (ticket) => {
        const saved = await requestJson(
          ticket,
          '/operations',
          (value) => {
            const parsed = parseOperation(value, fixture.owner_id, operationId);
            if (parsed.requester_id !== principal.requester_id || !sameDraft(draft, parsed))
              throw new LabError();
            return parsed;
          },
          preparePayload(operationId, draft),
        );
        return {
          patch: {
            operation: saved,
            feedback:
              saved.status === 'prepared'
                ? '意图已准备，尚未发布。'
                : `已读到原操作，当前状态：${statusNames[saved.status]}。`,
          },
          confirmed: true,
        };
      },
    );
  }
  function query() {
    const id = current.queryId;
    if (!mayRead || !isOperationId(id)) return;
    setView((previous) => ({ ...previous, operationId: id, operation: null, replayed: null }));
    void perform({ operationId: id, query: true, message: '正在查询原操作…' }, async (ticket) => {
      const saved = await requestJson(ticket, `/operations/${id}`, (value) =>
        parseOperation(value, fixture.owner_id, id),
      );
      return {
        patch: {
          operation: saved,
          feedback:
            saved.status === 'applied'
              ? '查询已确认：操作已执行，原回执如下。'
              : '原操作已查询，请审阅当前状态与意图。',
        },
        confirmed: true,
      };
    });
  }
  function act(action: 'approve' | 'revoke' | 'execute') {
    if (!operation || !principal || current.running || uncertain) return;
    if (
      action === 'execute'
        ? !(canExecute(principal, operation, current.draft) || canReplay(principal, operation))
        : !mayApprove
    )
      return;
    const id = operation.operation_id;
    void perform(
      { operationId: id, mutation: true, message: '正在提交当前意图，请保留操作 ID…' },
      async (ticket) => {
        let saved: Operation;
        let replayed: boolean | null = null;
        if (action === 'execute') {
          const result = await requestJson(
            ticket,
            `/operations/${id}/execute`,
            (value) => parseExecution(value, fixture.owner_id, id),
            { intent_hash: operation.intent_hash },
          );
          saved = result.operation;
          replayed = result.replayed;
        } else {
          saved = await requestJson(
            ticket,
            `/operations/${id}/${action}`,
            (value) => parseOperation(value, fixture.owner_id, id),
            { intent_hash: operation.intent_hash },
          );
        }
        if (
          !sameIntent(saved, operation) ||
          (action === 'approve' && saved.status !== 'approved') ||
          (action === 'revoke' && saved.status !== 'revoked')
        )
          throw new LabError();
        const feedback =
          action === 'approve'
            ? '当前意图已批准，请切回原申请者执行。'
            : action === 'revoke'
              ? '当前意图已撤销，不能再执行。'
              : replayed
                ? '已返回原回执，本次没有重复发布。'
                : '发布已确认。请回读文档核对新版本。';
        return { patch: { operation: saved, replayed, feedback }, confirmed: true };
      },
    );
  }
  function refresh() {
    if (!mayRead) return;
    void perform({ message: '正在回读当前文档…' }, async (ticket) => {
      const documents = await requestJson(ticket, '/documents', parseDocuments);
      return { patch: { documents, feedback: '已回读服务端当前文档；编辑草稿保持原样。' } };
    });
  }
  return (
    <main className="mx-auto max-w-6xl px-5 py-10 sm:px-8">
      <p className="text-xs font-semibold uppercase tracking-[0.2em] text-lime-800">
        Deep AI Station / Write Safety Lab
      </p>
      <h1 className="mt-4 text-3xl font-semibold tracking-tight">让写操作先被审阅，再被执行。</h1>
      <p className="mt-4 max-w-3xl text-sm leading-7 text-stone-600">
        使用公开假会话观察授权、精确意图和原操作重放。发布只写入本地练习
        SQLite，不调用模型、不访问外部发布平台。这些身份不是生产登录或真人审批。
      </p>
      <section aria-label="当前身份" className="panel mt-7">
        <label className="block text-sm font-semibold">
          教学身份
          <select
            value={fixture.token}
            onChange={(event) => switchIdentity(event.target.value)}
            className="field mt-2"
          >
            {fixtures.map((item) => (
              <option key={item.token} value={item.token}>
                {roleNames[item.token]}
              </option>
            ))}
          </select>
        </label>
        <p className="mt-3 break-words text-sm text-stone-600">
          {principal
            ? `服务端身份：owner=${principal.owner_id} / requester=${principal.requester_id} / capabilities=${principal.capabilities.join(', ')}`
            : '正在等待服务端确认当前身份；尚无可用权限。'}
        </p>
      </section>
      <div className="my-5 flex flex-wrap items-start gap-3">
        <p role="status" className="min-w-0 flex-1 break-words text-sm font-medium leading-7">
          {current.feedback}
        </p>
        <Button className="bg-stone-200" disabled={!current.running} onClick={stop}>
          停止等待
        </Button>
      </div>
      <section aria-label="操作恢复" className="panel mb-6">
        <h2 className="text-lg font-semibold">保留原操作 ID</h2>
        <p className="mt-2 text-sm leading-6 text-stone-600">
          写请求中断不代表已回滚。查询或重放必须使用原 ID。这里只按 owner 临时保留
          ID，刷新页面会清空；请先复制需要恢复的 ID。
        </p>
        <label className="mt-4 block text-sm font-medium">
          当前操作 ID
          <input readOnly value={current.operationId} className="field mt-2 font-mono text-xs" />
        </label>
        {ownerUncertain && (
          <p className="mt-3 text-sm font-medium text-amber-800">
            本 owner 仍有未确认结果，暂不允许准备新操作。请从下方选择标记“未确认”的原操作 ID
            后查询。
          </p>
        )}
        {ownerRecords.length > 0 && (
          <label className="mt-4 block text-sm font-medium">
            本 owner 的操作 ID
            <select
              className="field mt-2 font-mono text-xs"
              value={
                ownerRecords.some((item) => item.id === current.queryId) ? current.queryId : ''
              }
              onChange={(event) =>
                setView((previous) => ({ ...previous, queryId: event.target.value }))
              }
            >
              <option value="">选择需要回读的操作</option>
              {ownerRecords.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.id}
                  {item.uncertain ? ' · 未确认' : ''}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="mt-4 block text-sm font-medium">
          查询操作 ID
          <input
            value={current.queryId}
            onChange={(event) =>
              setView((previous) => ({ ...previous, queryId: event.target.value }))
            }
            className="field mt-2 font-mono text-xs"
            placeholder="粘贴小写 UUIDv4"
          />
        </label>
        <Button
          className="mt-3"
          disabled={!mayRead || !isOperationId(current.queryId)}
          onClick={query}
        >
          查询原操作
        </Button>
      </section>
      <div className="grid min-w-0 gap-6 lg:grid-cols-2">
        <section aria-label="当前文档" className="panel min-w-0">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-semibold">当前文档</h2>
            <Button disabled={!mayRead} onClick={refresh}>
              刷新文档
            </Button>
          </div>
          {document ? (
            <div className="mt-5 space-y-4">
              <h3 className="font-semibold">{document.title}</h3>
              <p className="break-all text-sm">
                文档：{document.id} · 当前版本：{document.version}
              </p>
              <PlainText label="服务端当前内容" content={document.content} />
            </div>
          ) : (
            <p className="mt-4 text-sm text-stone-600">尚未读取当前 owner 的文档。</p>
          )}
        </section>
        <section aria-label="编辑草稿" className="panel min-w-0">
          <h2 className="text-lg font-semibold">编辑草稿</h2>
          <p className="mt-2 text-sm leading-6 text-stone-600">
            草稿不会修改已经准备的意图。改动后须明确准备新操作，旧批准不能用于新内容。
          </p>
          <label className="mt-4 block text-sm font-medium">
            目标文档
            <select
              className="field mt-2"
              disabled={!mayPrepare || current.running}
              value={current.draft?.document_id || ''}
              onChange={(event) => {
                const selected = current.documents.find((item) => item.id === event.target.value);
                if (selected)
                  setView((previous) => ({ ...previous, draft: draftFromDocument(selected) }));
              }}
            >
              <option value="" disabled>
                选择文档
              </option>
              {current.documents.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title} / {item.id}
                </option>
              ))}
            </select>
          </label>
          <p className="mt-3 text-sm">
            草稿预期版本：{current.draft?.expected_version ?? '尚未读取'}
          </p>
          <label className="mt-4 block text-sm font-medium">
            新摘要内容
            <textarea
              className="field mt-2 min-h-40 resize-y"
              disabled={!mayPrepare || current.running || !current.draft}
              value={current.draft?.content || ''}
              onChange={(event) => {
                const content = event.target.value;
                setView((previous) => ({
                  ...previous,
                  draft: previous.draft ? { ...previous.draft, content } : null,
                }));
              }}
            />
          </label>
          <p className="mt-2 text-xs text-stone-500">
            {Array.from(current.draft?.content || '').length} / 2000 个 Unicode 字符；完整 JSON
            正文另受 4096 字节上限约束。
          </p>
          {problem && <p className="mt-2 text-sm text-amber-800">{problem}</p>}
          <div className="mt-4 flex flex-wrap gap-3">
            <Button
              disabled={!mayPrepare || current.running || !!problem || ownerUncertain}
              onClick={prepare}
            >
              准备新操作
            </Button>
            <Button
              className="bg-stone-100"
              disabled={!mayPrepare || !document || current.running}
              onClick={() =>
                document &&
                setView((previous) => ({ ...previous, draft: draftFromDocument(document) }))
              }
            >
              载入文档当前版本
            </Button>
          </div>
        </section>
      </div>
      <section aria-label="待审阅意图" className="panel mt-6 min-w-0">
        <h2 className="text-lg font-semibold">待审阅意图</h2>
        {!operation ? (
          <p className="mt-3 text-sm text-stone-600">
            先准备或查询操作，服务端保存的完整意图会显示在这里。
          </p>
        ) : (
          <>
            <dl className="mt-4 grid min-w-0 gap-3 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-stone-500">状态</dt>
                <dd>
                  {statusNames[operation.status]} / {operation.status}
                </dd>
              </div>
              <div>
                <dt className="text-stone-500">工具</dt>
                <dd>{operation.tool}</dd>
              </div>
              <div>
                <dt className="text-stone-500">目标文档</dt>
                <dd className="break-all">{operation.document_id}</dd>
              </div>
              <div>
                <dt className="text-stone-500">预期版本</dt>
                <dd>{operation.expected_version}</dd>
              </div>
              <div>
                <dt className="text-stone-500">owner / 原申请者</dt>
                <dd className="break-all">
                  {operation.owner_id} / {operation.requester_id}
                </dd>
              </div>
              <div>
                <dt className="text-stone-500">批准期限（服务端 Unix 秒）</dt>
                <dd>{operation.expires_at ?? '尚未批准'}</dd>
              </div>
              <div className="min-w-0 sm:col-span-2">
                <dt className="text-stone-500">完整意图 hash</dt>
                <dd className="break-all font-mono text-xs leading-6">{operation.intent_hash}</dd>
              </div>
            </dl>
            <div className="mt-5 grid min-w-0 gap-5 sm:grid-cols-2">
              <PlainText label="发布前内容" content={operation.before_content} />
              <PlainText label="待发布内容" content={operation.content} />
            </div>
            {draftChanged && (
              <p className="mt-4 text-sm font-medium text-amber-800">
                草稿与此意图不同，旧批准不能执行当前草稿。请准备新操作，或明确载入原意图草稿。
              </p>
            )}
            <div className="mt-5 flex flex-wrap gap-3">
              <Button
                disabled={
                  !mayApprove ||
                  current.running ||
                  uncertain ||
                  !['prepared', 'approved'].includes(operation.status)
                }
                onClick={() => act('approve')}
              >
                批准当前意图
              </Button>
              <Button
                className="bg-stone-200"
                disabled={
                  !mayApprove ||
                  current.running ||
                  uncertain ||
                  !['prepared', 'approved', 'revoked'].includes(operation.status)
                }
                onClick={() => act('revoke')}
              >
                撤销当前批准
              </Button>
              <Button
                disabled={
                  current.running || uncertain || !canExecute(principal, operation, current.draft)
                }
                onClick={() => act('execute')}
              >
                执行已批准操作
              </Button>
              <Button
                className="bg-stone-200"
                disabled={current.running || uncertain || !canReplay(principal, operation)}
                onClick={() => act('execute')}
              >
                重放原操作
              </Button>
              <Button
                className="bg-stone-100"
                disabled={!mayPrepare || current.running}
                onClick={() =>
                  setView((previous) => ({ ...previous, draft: draftFromOperation(operation) }))
                }
              >
                载入当前意图草稿
              </Button>
            </div>
            <p className="mt-3 text-xs leading-6 text-stone-500">
              批准仅针对上面的保存内容和
              hash。服务端仍会在执行时检查身份、原申请者、期限与文档版本。重放只返回原回执，不发布编辑草稿。
            </p>
          </>
        )}
      </section>
      <section aria-label="操作回执" className="panel mt-6 min-w-0">
        <h2 className="text-lg font-semibold">操作回执</h2>
        {operation?.receipt ? (
          <>
            <p className="mt-3 text-sm">
              {current.replayed === true ? '同键重放的原回执' : '服务端已确认的持久回执'}
            </p>
            <pre className="mt-4 whitespace-pre-wrap break-words rounded-lg bg-lime-50 p-4 text-xs leading-6 [overflow-wrap:anywhere]">
              {JSON.stringify(operation.receipt, null, 2)}
            </pre>
          </>
        ) : (
          <p className="mt-3 text-sm text-stone-600">
            尚无服务端确认的回执。请求失败或停止等待不能证明已回滚。
          </p>
        )}
      </section>
      <aside className="mt-6 text-sm leading-7 text-stone-600">
        默认批准窗口为 60
        秒，以服务端检查为准。角色切换只清空页面私有内容，不撤销数据库里的操作。请把真实命令、回执和失败边界记录到包内
        EVIDENCE.md。
      </aside>
    </main>
  );
}
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
