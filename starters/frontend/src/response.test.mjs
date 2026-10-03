import assert from 'node:assert/strict';
import test from 'node:test';
import { parseAnswer, parseHealth, responseError, safeHttpsUrl } from './response.ts';

function ordinary() {
  return {
    run_id: 'test-run',
    mode: 'demo',
    answer: '固定资料说明。',
    sources: [{ id: 'api', title: 'API 契约', url: 'https://example.com/course' }],
    usage: null,
  };
}
function agent() {
  return {
    ...ordinary(),
    sources: [{ ...ordinary().sources[0], kind: 'course-excerpt' }],
    workflow: 'research-agent',
    outcome: 'complete',
    model_calls: 0,
    tool_calls: 2,
    usage_complete: true,
    trace: [{ id: 'read-1', title: '读取资料', detail: '读取 api', status: 'success' }],
    citations: [{ document_id: 'api', quote: '固定资料说明。' }],
  };
}
function conflict() {
  return {
    ...agent(),
    outcome: 'conflicting_evidence',
    sources: [
      { id: 'cancel-billed', title: '取消仍可能计费', url: null, kind: 'conflict-fixture' },
      { id: 'cancel-free', title: '取消不会计费', url: null, kind: 'conflict-fixture' },
    ],
    citations: [
      { document_id: 'cancel-billed', quote: '取消仍可能计费。' },
      { document_id: 'cancel-free', quote: '取消不会计费。' },
    ],
  };
}

test('ordinary three-framework answers keep the existing contract', () => {
  assert.deepEqual(parseAnswer(ordinary()), ordinary());
  assert.equal(parseHealth({ status: 'ok', framework: 'hono', documents: 3 }), 'knowledge');
  assert.equal(
    parseHealth({ status: 'ok', framework: 'fastapi', documents: 5, workflow: 'research-agent' }),
    'agent',
  );
});

test('demo research keeps actual zero model calls and bounded tool calls', () => {
  assert.deepEqual(parseAnswer(agent()), agent());
});

test('real insufficient evidence can have actual model calls and unknown usage', () => {
  const payload = {
    ...agent(),
    mode: 'openai',
    outcome: 'insufficient_evidence',
    model_calls: 2,
    tool_calls: 1,
    usage_complete: false,
    citations: [],
    sources: [],
  };
  assert.deepEqual(parseAnswer(payload), payload);
});

test('partial known usage stays partial instead of filling unknown fields with zero', () => {
  const payload = {
    ...agent(),
    mode: 'openai',
    model_calls: 3,
    usage_complete: false,
    usage: { prompt_tokens: 12 },
  };
  assert.deepEqual(parseAnswer(payload).usage, { prompt_tokens: 12 });
  assert.equal(parseAnswer(payload).usage_complete, false);
});

test('conflict fixtures have no invented source link', () => {
  const payload = conflict();
  assert.equal(parseAnswer(payload).sources[0].url, null);
  assert.throws(() =>
    parseAnswer({
      ...payload,
      sources: [{ ...payload.sources[0], url: 'https://example.com/official' }, payload.sources[1]],
    }),
  );
});

test('no-evidence is reserved for zero-model insufficient evidence runs', () => {
  const payload = {
    ...agent(),
    mode: 'no-evidence',
    outcome: 'insufficient_evidence',
    sources: [],
    citations: [],
    tool_calls: 1,
  };
  assert.deepEqual(parseAnswer(payload), payload);
  assert.throws(() => parseAnswer({ ...payload, model_calls: 1, usage_complete: false }));
  assert.throws(() => parseAnswer({ ...agent(), mode: 'no-evidence' }));
});

test('insufficient evidence cannot retain sources or citations', () => {
  assert.throws(() => parseAnswer({ ...agent(), outcome: 'insufficient_evidence' }));
  assert.throws(() => parseAnswer({ ...agent(), outcome: 'insufficient_evidence', citations: [] }));
  assert.throws(() => parseAnswer({ ...agent(), outcome: 'insufficient_evidence', sources: [] }));
});

test('completed evidence needs a citation for every reported source', () => {
  const payload = {
    ...agent(),
    sources: [
      agent().sources[0],
      { id: 'tools', title: '工具权限', url: 'https://example.com/tools', kind: 'course-excerpt' },
    ],
    citations: [...agent().citations, { document_id: 'tools', quote: '只读工具独立校验参数。' }],
  };
  assert.deepEqual(parseAnswer(payload), payload);
  assert.throws(() => parseAnswer({ ...payload, citations: agent().citations }));
  assert.throws(() => parseAnswer({ ...agent(), sources: [], citations: [] }));
  assert.throws(() => parseAnswer({ ...agent(), citations: [] }));
});

test('conflicting evidence requires at least two cited conflict fixture sources', () => {
  const payload = conflict();
  assert.deepEqual(parseAnswer(payload), payload);
  assert.throws(() =>
    parseAnswer({ ...payload, sources: [payload.sources[0]], citations: [payload.citations[0]] }),
  );
  assert.throws(() => parseAnswer({ ...payload, citations: [payload.citations[0]] }));
  assert.throws(() => parseAnswer({ ...agent(), outcome: 'conflicting_evidence' }));
});

test('complete provider usage requires all three actual token counters', () => {
  const payload = {
    ...agent(),
    mode: 'openai',
    model_calls: 3,
    usage: { prompt_tokens: 12, completion_tokens: 0, total_tokens: 12 },
  };
  assert.deepEqual(parseAnswer(payload), payload);
  for (const field of ['prompt_tokens', 'completion_tokens', 'total_tokens']) {
    const usage = { ...payload.usage };
    delete usage[field];
    assert.throws(() => parseAnswer({ ...payload, usage }));
  }
  assert.throws(() => parseAnswer({ ...payload, usage: null }));
});

test('links require absolute HTTPS without credentials', () => {
  assert.equal(safeHttpsUrl('https://example.com/source'), 'https://example.com/source');
  for (const url of [
    'javascript:alert(1)',
    'http://example.com',
    '//example.com',
    'https://user:password@example.com',
    ' https://example.com',
    null,
  ]) {
    assert.equal(safeHttpsUrl(url), null);
    if (url !== null)
      assert.throws(() =>
        parseAnswer({ ...ordinary(), sources: [{ id: 'api', title: 'API', url }] }),
      );
  }
});

test('malformed responses never produce a history record', () => {
  for (const payload of [
    null,
    [],
    { ...ordinary(), mode: { toString: () => 'demo' } },
    { ...ordinary(), usage: { total_tokens: -1 } },
    { ...ordinary(), usage: { total_tokens: 1.5 } },
    { ...ordinary(), usage: { unknown: 1 } },
    { ...ordinary(), workflow: 'other-workflow' },
    { ...ordinary(), sources: [{ ...ordinary().sources[0], url: null }] },
    { ...ordinary(), sources: [ordinary().sources[0], ordinary().sources[0]] },
    { ...agent(), model_calls: 4 },
    { ...agent(), tool_calls: 3 },
    { ...agent(), model_calls: true },
    { ...agent(), outcome: { toString: () => 'complete' } },
    { ...agent(), sources: ordinary().sources },
    { ...agent(), mode: 'demo', model_calls: 1 },
    { ...agent(), trace: [{ id: 'read-1', title: '读取资料', detail: '资料', status: 'running' }] },
    { ...agent(), citations: [{ document_id: 'unread', quote: '引用' }] },
    { ...agent(), citations: [{ document_id: 'api', quote: '' }] },
    { ...agent(), citations: [agent().citations[0], agent().citations[0]] },
    { ...agent(), usage_complete: undefined },
    { ...agent(), trace: undefined },
  ])
    assert.throws(() => parseAnswer(payload));
});

test('plain text remains plain and malformed errors are not stringified', () => {
  const payload = { ...ordinary(), answer: '<script>alert(1)</script>' };
  assert.equal(parseAnswer(payload).answer, payload.answer);
  assert.equal(responseError({ error: { secret: 'fixture' } }), '请求失败，请稍后重试');
  assert.equal(responseError({ error: '访问码未配置' }), '访问码未配置');
});
