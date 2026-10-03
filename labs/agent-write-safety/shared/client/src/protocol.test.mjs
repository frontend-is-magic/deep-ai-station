import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
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
  parseDocument,
  parseDocuments,
  parseExecution,
  parseOperation,
  parsePrincipal,
  preparePayload,
  rememberOperation,
  requestJson,
  sameDraft,
} from './protocol.mjs';
const id = '12345678-1234-4123-8123-123456789abc';
const nextId = '12345678-1234-4123-9123-123456789abd';
const fixture = { owner_id: 'alice', requester_id: 'agent-alice', token: 'fixture-alice-agent' };
const principal = {
  owner_id: 'alice',
  requester_id: 'agent-alice',
  capabilities: ['read', 'prepare', 'execute'],
};
const document = { id: 'agent-summary', title: '练习摘要', content: '发布前', version: 1 };
const draft = {
  document_id: 'agent-summary',
  expected_version: 1,
  content: '<script>alert(1)</script>\n新摘要 & 原样文本',
};
function operation(status = 'prepared') {
  const approved = ['approved', 'applied', 'expired'].includes(status);
  const result = {
    operation_id: id,
    owner_id: 'alice',
    requester_id: 'agent-alice',
    tool: 'publish_revision',
    ...draft,
    before_content: document.content,
    intent_hash: 'a'.repeat(64),
    status,
    prepared_at: 1700000000,
    approved_at: approved ? 1700000001 : null,
    expires_at: approved ? 1700000061 : null,
    receipt: null,
  };
  if (status === 'applied')
    result.receipt = {
      operation_id: id,
      document_id: draft.document_id,
      version: 2,
      content: draft.content,
      intent_hash: result.intent_hash,
      applied_at: 1700000002,
    };
  return result;
}
test('public client identity fixtures contain no document contents and match the shared sessions', () => {
  const client = JSON.parse(readFileSync(new URL('./fixtures.json', import.meta.url)));
  const shared = JSON.parse(readFileSync(new URL('../../fixtures.json', import.meta.url)));
  assert.deepEqual(client, shared.sessions);
  assert.equal(
    client.some((item) => 'content' in item || 'documents' in item),
    false,
  );
});
test('principal must belong to the selected fixture, with unique known capabilities', () => {
  assert.deepEqual(parsePrincipal(principal, fixture), principal);
  for (const value of [
    { ...principal, owner_id: 'bob' },
    { ...principal, requester_id: 'another-agent' },
    { ...principal, capabilities: ['read', 'read'] },
    { ...principal, capabilities: ['admin'] },
    { ...principal, token: 'do-not-trust' },
  ])
    assert.throws(() => parsePrincipal(value, fixture), LabError);
});
test('document content remains exact plain text and lists reject duplicate/unsorted IDs', () => {
  const raw = { ...document, content: draft.content };
  assert.equal(parseDocument(raw).content, draft.content);
  assert.deepEqual(parseDocuments({ items: [raw] }), [raw]);
  for (const items of [
    [raw, raw],
    [
      { ...raw, id: 'z' },
      { ...raw, id: 'a' },
    ],
  ]) {
    assert.throws(() => parseDocuments({ items }), LabError);
  }
  assert.throws(() => parseDocument({ ...raw, version: 1.1 }), LabError);
  assert.throws(() => parseDocument({ ...raw, version: 2147483648 }), LabError);
  assert.throws(() => parseDocument({ ...raw, content: '\ud800' }), LabError);
});
for (const status of ['prepared', 'approved', 'applied', 'revoked', 'expired']) {
  test(`reads the actual ${status} operation shape`, () => {
    const value = operation(status);
    assert.deepEqual(parseOperation(value, 'alice', id), value);
  });
}
for (const [label, change] of [
  ['owner', { owner_id: 'bob' }],
  ['operation ID', { operation_id: nextId }],
  ['tool', { tool: 'delete_everything' }],
  ['extra field', { secret: 'never render' }],
  ['Unicode surrogate', { content: '\ud800' }],
  ['NUL', { before_content: 'a\0b' }],
  ['hash', { intent_hash: 'A'.repeat(64) }],
  ['fractional version', { expected_version: 1.1 }],
  ['version max', { expected_version: 2147483648 }],
  ['infinite time', { prepared_at: Infinity }],
  ['missing approval', { status: 'approved' }],
  ['non-applied receipt', { receipt: {} }],
]) {
  test(`rejects mismatched ${label} before any operation is shown`, () => {
    assert.throws(() => parseOperation({ ...operation(), ...change }, 'alice', id), LabError);
  });
}
test('revocation can retain its previous approval window', () => {
  assert.equal(
    parseOperation({ ...operation('approved'), status: 'revoked' }, 'alice', id).status,
    'revoked',
  );
});
test('receipt is bound to the operation and replay is a boolean, never a new success', () => {
  const original = operation('applied');
  for (const replayed of [true, false])
    assert.deepEqual(parseExecution({ operation: original, replayed }, 'alice', id), {
      operation: original,
      replayed,
    });
  for (const patch of [
    { content: 'different' },
    { version: 3 },
    { intent_hash: 'b'.repeat(64) },
    { operation_id: nextId },
    { applied_at: NaN },
  ]) {
    assert.throws(
      () =>
        parseOperation({ ...original, receipt: { ...original.receipt, ...patch } }, 'alice', id),
      LabError,
    );
  }
  assert.throws(
    () => parseExecution({ operation: operation('approved'), replayed: false }, 'alice', id),
    LabError,
  );
  assert.throws(
    () => parseExecution({ operation: original, replayed: 'true' }, 'alice', id),
    LabError,
  );
});
test('draft matching and permission gate prohibit using approval for edited drafts or peers', () => {
  const approved = operation('approved');
  assert.equal(canExecute(principal, approved, draft), true);
  for (const value of [
    null,
    { ...draft, content: draft.content + ' ' },
    { ...draft, expected_version: 2 },
    { ...draft, document_id: 'other' },
  ]) {
    assert.equal(sameDraft(value, approved), false);
    assert.equal(canExecute(principal, approved, value), false);
  }
  for (const person of [
    null,
    { ...principal, owner_id: 'bob' },
    { ...principal, requester_id: 'agent-alice-peer' },
    { ...principal, capabilities: ['read', 'approve'] },
  ]) {
    assert.equal(canExecute(person, approved, draft), false);
    assert.equal(canReplay(person, operation('applied')), false);
  }
  assert.equal(canExecute(principal, operation(), draft), false);
  assert.equal(canExecute(principal, operation('expired'), draft), false);
  assert.equal(canReplay(principal, operation('applied')), true);
  assert.equal(canReplay(principal, approved), false);
});
test('draft extraction uses server versions and never mutates original documents or intents', () => {
  const source = Object.freeze(document);
  const before = operation('approved');
  const extracted = draftFromOperation(before);
  extracted.content = 'edited';
  assert.equal(before.content, draft.content);
  assert.deepEqual(draftFromDocument(source), {
    document_id: document.id,
    expected_version: 1,
    content: document.content,
  });
});
test('prepare preserves exact Unicode and rejects byte overflow including JSON overhead', () => {
  assert.equal(draftProblem({ ...draft, content: ' ' }), '');
  assert.equal(draftProblem({ ...draft, content: 'x'.repeat(2000) }), '');
  assert.notEqual(draftProblem({ ...draft, content: 'x'.repeat(2001) }), '');
  assert.notEqual(draftProblem({ ...draft, content: '😀'.repeat(2000) }), '');
  assert.notEqual(draftProblem({ ...draft, content: '\udfff' }), '');
  assert.notEqual(draftProblem({ ...draft, content: 'nul\0' }), '');
  assert.notEqual(draftProblem({ ...draft, content: '' }), '');
  const payload = preparePayload(id, { ...draft, content: ' e\u0301\n中 😀 ' });
  assert.equal(payload.arguments.content, ' e\u0301\n中 😀 ');
  const encoder = new TextEncoder();
  const overhead = encoder.encode(
    JSON.stringify(preparePayload(id, { ...draft, content: '' })),
  ).byteLength;
  const base = '中'.repeat(Math.floor((4096 - overhead) / 3));
  const exact = base + 'a'.repeat(4096 - overhead - encoder.encode(base).byteLength);
  assert.equal(
    encoder.encode(JSON.stringify(preparePayload(id, { ...draft, content: exact }))).byteLength,
    4096,
  );
  assert.equal(draftProblem({ ...draft, content: exact }), '');
  assert.match(draftProblem({ ...draft, content: exact + 'a' }), /4096/);
});
test('operation IDs reject uppercase, newline, wrong UUID version and variant', () => {
  assert.equal(isOperationId(id), true);
  for (const value of [
    id.toUpperCase(),
    id + '\n',
    id.replace('-4123-', '-1123-'),
    id.replace('-8123-', '-7123-'),
    '',
  ])
    assert.equal(isOperationId(value), false);
});
test('recovery retains IDs for both owners, updates uncertainty immutably and never copies content', () => {
  const first = rememberOperation({}, 'alice', id, true);
  const second = rememberOperation(first, 'bob', nextId, false);
  const third = rememberOperation(second, 'alice', id, false);
  assert.deepEqual(first, { alice: [{ id, uncertain: true }] });
  assert.deepEqual(third, {
    alice: [{ id, uncertain: false }],
    bob: [{ id: nextId, uncertain: false }],
  });
  assert.equal(rememberOperation(third, 'alice', 'invalid', true), third);
});
test('late responses cannot update another identity even when transport ignores abort', async () => {
  const gate = createRequestGate();
  let release;
  const delayed = new Promise((resolve) => {
    release = resolve;
  });
  const alice = gate.begin('fixture-alice-agent');
  let view = 'empty';
  const work = delayed.then(() => {
    if (gate.isCurrent(alice)) view = 'alice-private';
  });
  const bob = gate.begin('fixture-bob-agent');
  assert.equal(alice.controller.signal.aborted, true);
  if (gate.isCurrent(bob)) view = 'bob-private';
  release();
  await work;
  assert.equal(view, 'bob-private');
  gate.finish(alice);
  assert.equal(gate.isCurrent(bob), true);
});
test('overlapping requests and unmount invalidate generations within one owner', () => {
  const gate = createRequestGate();
  const first = gate.begin('fixture-alice-agent');
  const second = gate.begin('fixture-alice-agent');
  assert.equal(gate.isLatest(first), false);
  assert.equal(gate.isCurrent(second), true);
  second.controller.abort();
  assert.equal(gate.isLatest(second), true);
  assert.equal(gate.isCurrent(second), false);
  gate.cancel();
  assert.equal(gate.isLatest(second), false);
});
test('HTTP uses a relative proxy and fixed JSON headers; response errors never expose raw detail', async (context) => {
  const calls = [];
  context.mock.method(globalThis, 'fetch', async (path, options) => {
    calls.push({ path, options });
    return new Response(JSON.stringify(operation()), { status: 200 });
  });
  const gate = createRequestGate();
  const ticket = gate.begin(fixture.token);
  const result = await requestJson(
    ticket,
    '/operations',
    (value) => parseOperation(value, 'alice', id),
    preparePayload(id, draft),
  );
  assert.equal(result.operation_id, id);
  assert.equal(calls[0].path, '/api/operations');
  assert.equal(calls[0].options.headers.Authorization, `Bearer ${fixture.token}`);
  assert.equal(calls[0].options.headers['Content-Type'], 'application/json');
  assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(calls[0].options.signal, ticket.controller.signal);
  await assert.rejects(
    requestJson(ticket, 'https://elsewhere.example/operations', parseDocument),
    LabError,
  );
  assert.equal(calls.length, 1);
  globalThis.fetch.mock.mockImplementation(
    async () => new Response('sensitive transport detail', { status: 503 }),
  );
  await assert.rejects(
    requestJson(ticket, '/me', (value) => value),
    (error) =>
      error instanceof LabError && !error.message.includes('sensitive') && error.status === 503,
  );
  globalThis.fetch.mock.mockImplementation(
    async () =>
      new Response(JSON.stringify({ error: 'sensitive transport detail' }), { status: 500 }),
  );
  await assert.rejects(
    requestJson(ticket, '/me', (value) => value),
    (error) => error.code === 'unexpected_response' && !error.message.includes('sensitive'),
  );
  globalThis.fetch.mock.mockImplementation(
    async () => new Response(JSON.stringify({ error: 'result_unconfirmed' }), { status: 503 }),
  );
  await assert.rejects(
    requestJson(ticket, '/me', (value) => value),
    (error) => error.message === UNCONFIRMED_MESSAGE && error.code === 'result_unconfirmed',
  );
});

test('querying an unrelated ID cannot unlock new preparation while an owner has an unknown mutation', () => {
  const pending = rememberOperation({}, 'alice', id, true);
  const unrelated404 = rememberOperation(pending, 'alice', nextId, false);
  assert.equal(hasUnconfirmedOperations(unrelated404, 'alice'), true);
  assert.equal(hasUnconfirmedOperations(unrelated404, 'bob'), false);
  const resolvedOriginal = rememberOperation(unrelated404, 'alice', id, false);
  assert.equal(hasUnconfirmedOperations(resolvedOriginal, 'alice'), false);
  assert.deepEqual(pending.alice, [{ id, uncertain: true }]);
});
