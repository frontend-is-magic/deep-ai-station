import assert from 'node:assert/strict';
import test from 'node:test';
import { createApp } from './app.js';
import { MemoryLessonRepository, readLabJson, type LessonRepository } from './repository.js';

interface ContractCase {
  id: string;
  method: string;
  path: string;
  status: number;
  expected: unknown;
  json?: unknown;
  raw?: string;
  content_type?: string;
  repeat_body?: { character: string; count: number };
}

const cases = readLabJson<ContractCase[]>('contract-cases.json');
assert.ok(cases.length >= 24, 'The shared contract must retain its baseline cases');

function requestFor(item: ContractCase): RequestInit {
  const body =
    'json' in item
      ? JSON.stringify(item.json)
      : item.repeat_body
        ? item.repeat_body.character.repeat(item.repeat_body.count)
        : item.raw;
  const headers: Record<string, string> = {};
  if (item.content_type) headers['Content-Type'] = item.content_type;
  else if ('json' in item) headers['Content-Type'] = 'application/json';
  return { method: item.method, headers, body };
}

for (const item of cases) {
  test('shared contract: ' + item.id, async () => {
    const response = await createApp().request(item.path, requestFor(item));
    assert.equal(response.status, item.status);
    assert.match(response.headers.get('content-type') ?? '', /^application\/json/);
    assert.deepEqual(await response.json(), item.expected);
  });
}

test('empty repository can be injected without public switches', async () => {
  const app = createApp(new MemoryLessonRepository([]));
  const found = await app.request('/lessons/tools');
  assert.equal(found.status, 404);
  assert.deepEqual(await found.json(), { error: 'lesson_not_found' });
  const search = await app.request('/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question: '工具' }),
  });
  assert.equal(search.status, 200);
  assert.deepEqual(await search.json(), { question: '工具', items: [] });
});

test('repository failures return stable 503 without internal messages', async () => {
  const repository: LessonRepository = {
    async find() {
      throw new Error('private-repository-location');
    },
    async search() {
      throw new Error('private-repository-location');
    },
  };
  const app = createApp(repository);
  for (const [path, options] of [
    ['/lessons/tools', undefined],
    [
      '/search',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: '工具' }),
      },
    ],
  ] as const) {
    const response = await app.request(path, options);
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { error: 'repository_unavailable' });
  }
});

test('every invalid shared case is rejected before querying the repository', async () => {
  let queries = 0;
  const app = createApp({
    async find() {
      queries++;
      return null;
    },
    async search() {
      queries++;
      return [];
    },
  });
  for (const item of cases.filter((item) => item.method === 'POST' && item.status >= 400)) {
    const response = await app.request(item.path, requestFor(item));
    assert.equal(response.status, item.status, item.id);
  }
  assert.equal(queries, 0);
});

test('invalid UTF-8 is rejected instead of silently replaced', async () => {
  let queries = 0;
  const app = createApp({
    async find() {
      throw new Error('unexpected call');
    },
    async search() {
      queries++;
      return [];
    },
  });
  const response = await app.request('/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: Buffer.concat([Buffer.from('{"question":"'), Buffer.from([0xff]), Buffer.from('"}')]),
  });
  assert.equal(response.status, 422);
  assert.deepEqual(await response.json(), { error: 'invalid_input' });
  assert.equal(queries, 0);
});

test('actual chunked bytes take precedence over media and false Content-Length', async () => {
  let queries = 0;
  let cancelled = false;
  const app = createApp({
    async find() {
      return null;
    },
    async search() {
      queries++;
      return [];
    },
  });
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new Uint8Array(2048).fill(120));
      controller.enqueue(new Uint8Array(2049).fill(120));
    },
    cancel() {
      cancelled = true;
    },
  });
  const request = new Request('http://localhost/search', {
    method: 'POST',
    headers: { 'Content-Type': 'text/plain', 'Content-Length': '1' },
    body,
    duplex: 'half',
  } as RequestInit & { duplex: 'half' });
  const response = await app.fetch(request);
  assert.equal(response.status, 413);
  assert.deepEqual(await response.json(), { error: 'request_too_large' });
  assert.equal(queries, 0);
  assert.equal(cancelled, true);
});

test('4096 real bytes are allowed even with a false oversized Content-Length', async () => {
  const body = JSON.stringify({ question: '工具' });
  const padded = body + ' '.repeat(4096 - Buffer.byteLength(body));
  const response = await createApp().request('/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Content-Length': '999999' },
    body: padded,
  });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), {
    question: '工具',
    items: [{ id: 'tools', title: '工具契约' }],
  });
});

test('size limits count UTF-8 bytes, not JavaScript string units', async () => {
  const response = await createApp().request('/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '界'.repeat(1366),
  });
  assert.equal(response.status, 413);
  assert.deepEqual(await response.json(), { error: 'request_too_large' });
});

test('service projects injected records and repository preserves source order', async () => {
  const app = createApp(
    new MemoryLessonRepository([
      { id: 'second', title: 'Match two', body: 'private content' },
      { id: 'first', title: 'Match one', body: 'private content' },
    ]),
  );
  const search = await app.request('/search', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question: 'MATCH' }),
  });
  assert.deepEqual(await search.json(), {
    question: 'MATCH',
    items: [
      { id: 'second', title: 'Match two' },
      { id: 'first', title: 'Match one' },
    ],
  });
  const found = await app.request('/lessons/first');
  assert.deepEqual(await found.json(), { id: 'first', title: 'Match one' });
});

test('title and body are searched separately, not concatenated into false matches', async () => {
  const repository = new MemoryLessonRepository([{ id: 'one', title: 'ab', body: 'cd' }]);
  assert.deepEqual(await repository.search('bc'), []);
});

test('isolated surrogate code points are rejected before repository queries', async () => {
  let queries = 0;
  const app = createApp({
    async find() {
      return null;
    },
    async search() {
      queries++;
      return [];
    },
  });
  for (const question of ['\ud800', '\udfff', '工具\ud800资料', '\udfff\ud800']) {
    const response = await app.request('/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    });
    assert.equal(response.status, 422);
    assert.deepEqual(await response.json(), { error: 'invalid_input' });
  }
  assert.equal(queries, 0);
});

test('exact Unicode White_Space is removed before validation and searching', async () => {
  const whitespace = String.fromCodePoint(
    0x09,
    0x0a,
    0x0b,
    0x0c,
    0x0d,
    0x20,
    0x85,
    0xa0,
    0x1680,
    ...Array.from({ length: 11 }, (_, index) => 0x2000 + index),
    0x2028,
    0x2029,
    0x202f,
    0x205f,
    0x3000,
  );
  const app = createApp();
  for (const [question, status, expected] of [
    [
      whitespace + '工具' + whitespace,
      200,
      { question: '工具', items: [{ id: 'tools', title: '工具契约' }] },
    ],
    [whitespace, 422, { error: 'invalid_input' }],
    [whitespace + '🙂'.repeat(500) + whitespace, 200, { question: '🙂'.repeat(500), items: [] }],
  ] as const) {
    const response = await app.request('/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    });
    assert.equal(response.status, status);
    assert.deepEqual(await response.json(), expected);
  }
});

test('BOM and C0 separators outside Unicode White_Space remain part of the question', async () => {
  for (const codePoint of [0xfeff, 0x1c, 0x1d, 0x1e, 0x1f]) {
    const separator = String.fromCodePoint(codePoint);
    const question = separator + '工具' + separator;
    const response = await createApp().request('/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    });
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { question, items: [] });
  }
});
