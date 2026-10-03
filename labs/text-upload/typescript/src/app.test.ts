import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { request as httpRequest } from 'node:http';
import type { AddressInfo } from 'node:net';
import test, { type TestContext } from 'node:test';
import { serve } from '@hono/node-server';
import { createApp } from './app.js';
import { type Session, type SessionStore } from './auth.js';
import { MemoryRepository, type Repository } from './repository.js';
import { readLabJson } from './resources.js';

const alice = 'Bearer lab-alice-session';
const uploadHeaders = {
  Authorization: alice,
  'Content-Type': 'text/plain',
  'X-Filename': 'notes.txt',
};
function assertSecurity(headers: Headers) {
  assert.equal(headers.get('cache-control'), 'no-store');
  assert.equal(headers.get('x-content-type-options'), 'nosniff');
  assert.equal(headers.get('access-control-allow-origin'), null);
  assert.equal(headers.get('set-cookie'), null);
}
async function response(
  app: ReturnType<typeof createApp>,
  path = '/documents',
  options: RequestInit = { headers: { Authorization: alice } },
) {
  const result = await app.request(path, options);
  assertSecurity(result.headers);
  if (result.status === 401)
    assert.equal(result.headers.get('www-authenticate'), 'Bearer realm="text-upload"');
  return { status: result.status, body: await result.json(), headers: result.headers };
}
function upload(app: ReturnType<typeof createApp>, body = 'hello', filename = 'notes.txt') {
  return response(app, '/documents', {
    method: 'POST',
    headers: { ...uploadHeaders, 'X-Filename': filename },
    body,
  });
}
async function listen(t: TestContext, app = createApp()) {
  const server = serve({ fetch: app.fetch, hostname: '127.0.0.1', port: 0 });
  if (!server.listening) await new Promise<void>((resolve) => server.once('listening', resolve));
  t.after(
    () =>
      new Promise<void>((resolve, reject) =>
        server.close((error) => (error ? reject(error) : resolve())),
      ),
  );
  return (server.address() as AddressInfo).port;
}
function send(
  port: number,
  method: string,
  path: string,
  pairs: string[][] = [],
  chunks: Uint8Array[] = [],
) {
  return new Promise<{
    status: number;
    body: Buffer;
    headers: Record<string, string | string[] | undefined>;
  }>((resolve, reject) => {
    const request = httpRequest(
      {
        hostname: '127.0.0.1',
        port,
        path,
        method,
        headers: ['Host', `127.0.0.1:${port}`, ...pairs.flat()],
        agent: false,
      },
      (result) => {
        const received: Buffer[] = [];
        result.on('data', (chunk) => received.push(Buffer.from(chunk)));
        result.on('error', reject);
        result.on('end', () =>
          resolve({
            status: result.statusCode!,
            body: Buffer.concat(received),
            headers: result.headers,
          }),
        );
      },
    );
    request.on('error', reject);
    for (const chunk of chunks) request.write(chunk);
    request.end();
  });
}
interface ContractCase {
  id: string;
  method: string;
  path: string;
  status: number;
  expected?: unknown;
  expected_body_base64?: string;
  headers?: Record<string, string>;
  header_pairs?: string[][];
  raw?: string;
  body_base64?: string;
  repeat_body?: { character: string; count: number };
  response_headers?: Record<string, string>;
}
test('shared cases run in order through real HTTP, preserving repeated headers and bytes', async (t) => {
  const cases = readLabJson<ContractCase[]>('contract-cases.json');
  assert.ok(cases.length > 0);
  const port = await listen(t);
  for (const item of cases) {
    await t.test(item.id, async () => {
      const pairs = [
        ...Object.entries(item.headers ?? {}),
        ...(item.header_pairs ?? []).map((pair) => [...pair]),
      ];
      const body =
        item.body_base64 !== undefined
          ? Buffer.from(item.body_base64, 'base64')
          : item.repeat_body
            ? Buffer.from(item.repeat_body.character.repeat(item.repeat_body.count))
            : item.raw === undefined
              ? undefined
              : Buffer.from(item.raw);
      const result = await send(port, item.method, item.path, pairs, body ? [body] : []);
      assert.equal(result.status, item.status);
      if (item.expected_body_base64 !== undefined)
        assert.equal(result.body.toString('base64'), item.expected_body_base64);
      else assert.deepEqual(JSON.parse(result.body.toString('utf8')), item.expected);
      assert.equal(result.headers['cache-control'], 'no-store');
      assert.equal(result.headers['x-content-type-options'], 'nosniff');
      assert.equal(result.headers['access-control-allow-origin'], undefined);
      assert.equal(result.headers['set-cookie'], undefined);
      for (const [name, value] of Object.entries(item.response_headers ?? {}))
        assert.equal(result.headers[name.toLowerCase()], value);
    });
  }
});

function suspendedBody(bytes = Buffer.from('hello')) {
  let ready!: () => void;
  const reading = new Promise<void>((resolve) => (ready = resolve));
  let release!: () => void;
  const stream = new ReadableStream<Uint8Array>(
    {
      pull(controller) {
        ready();
        return new Promise<void>((resolve) => {
          release = () => {
            controller.enqueue(bytes);
            controller.close();
            resolve();
          };
        });
      },
    },
    { highWaterMark: 0 },
  );
  return { stream, reading, release: () => release() };
}
function streamingRequest(body: ReadableStream<Uint8Array>, headers = uploadHeaders) {
  return new Request('http://localhost/documents', {
    method: 'POST',
    headers,
    body,
    duplex: 'half',
  } as RequestInit);
}

test('injected clock uses seconds and rejects at the exact expiry boundary', async () => {
  let now = 1000;
  const app = createApp({ clock: () => now });
  now = 4599.999;
  assert.equal((await response(app)).status, 200);
  now = 4600;
  const result = await response(app);
  assert.equal(result.status, 401);
  assert.deepEqual(result.body, { error: 'authentication_required' });
});

test('session and repository faults remain private stable errors', async () => {
  const fault = () => {
    throw new Error('private-storage-location-and-session-detail');
  };
  const sessions: SessionStore = { get: fault };
  const sessionFailure = await response(createApp({ sessions }));
  assert.equal(sessionFailure.status, 503);
  assert.deepEqual(sessionFailure.body, { error: 'session_store_unavailable' });
  const repository: Repository = { commit: fault, list: fault, get: fault };
  const app = createApp({ repository });
  for (const result of [
    await response(app),
    await response(app, '/documents/doc-000001'),
    await upload(app),
  ]) {
    assert.equal(result.status, 503);
    assert.deepEqual(result.body, { error: 'repository_unavailable' });
  }
  const clockFailure = createApp({
    clock: fault,
    sessions: {
      get: () => ({ user_id: 'alice', can_write: true, expires_at: 10, revoked: false }),
    },
  });
  assert.deepEqual((await response(clockFailure)).body, { error: 'request_failed' });
});

test('authentication, query and initial permission failures do not read the body or repository', async () => {
  let reads = 0;
  let calls = 0;
  const forbiddenCall = () => {
    calls++;
    throw new Error('unexpected repository access');
  };
  const app = createApp({
    repository: { commit: forbiddenCall, list: forbiddenCall, get: forbiddenCall },
  });
  for (const [authorization, query, expected] of [
    ['Bearer unknown', '', 401],
    [alice, '?owner=bob', 422],
    ['Bearer lab-alice-readonly-session', '', 403],
  ] as const) {
    const body = new ReadableStream<Uint8Array>(
      {
        pull: () => {
          reads++;
        },
      },
      { highWaterMark: 0 },
    );
    const request = new Request(`http://localhost/documents${query}`, {
      method: 'POST',
      headers: { ...uploadHeaders, Authorization: authorization },
      body,
      duplex: 'half',
    } as RequestInit);
    assert.equal((await app.fetch(request)).status, expected);
  }
  assert.equal(reads, 0);
  assert.equal(calls, 0);
  const invalid = await upload(app, '\u0000');
  assert.equal(invalid.status, 422);
  assert.equal(calls, 0);
});

for (const change of ['revoke', 'expire', 'remove permission'] as const) {
  test(`${change} while reading the body prevents commit without blocking reads`, async () => {
    let now = 1000;
    const session: Session = {
      user_id: 'alice',
      can_write: true,
      expires_at: 1100,
      revoked: false,
    };
    const repository = new MemoryRepository();
    let commits = 0;
    const app = createApp({
      clock: () => now,
      sessions: { get: () => ({ ...session }) },
      repository: {
        list: (owner) => repository.list(owner),
        get: (owner, id) => repository.get(owner, id),
        commit: (owner, value) => {
          commits++;
          return repository.commit(owner, value);
        },
      },
    });
    const body = suspendedBody();
    const pending = Promise.resolve(app.fetch(streamingRequest(body.stream)));
    await body.reading;
    assert.deepEqual((await response(app)).body, { documents: [] });
    if (change === 'revoke') session.revoked = true;
    if (change === 'expire') now = 1100;
    if (change === 'remove permission') session.can_write = false;
    body.release();
    const result = await pending;
    assert.equal(result.status, change === 'remove permission' ? 403 : 401);
    assert.deepEqual(await result.json(), {
      error: change === 'remove permission' ? 'forbidden' : 'authentication_required',
    });
    assertSecurity(result.headers);
    assert.equal(commits, 0);
    assert.deepEqual(repository.list('alice'), []);
  });
}

test('body read failure has no write and returns a private request error', async () => {
  const repository = new MemoryRepository();
  const app = createApp({ repository });
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      controller.error(new Error('private-body-stream-error'));
    },
  });
  const result = await app.fetch(streamingRequest(body));
  assert.equal(result.status, 500);
  assert.deepEqual(await result.json(), { error: 'request_failed' });
  assert.deepEqual(repository.list('alice'), []);
});

test('failure immediately before publishing leaves bytes, quotas and ID unchanged', async () => {
  let fail = false;
  const repository = new MemoryRepository({
    beforeCommit() {
      if (fail) throw new Error('private commit failure');
    },
  });
  const app = createApp({ repository });
  assert.equal((await upload(app, 'old')).status, 201);
  const before = repository.get('alice', 'doc-000001');
  fail = true;
  const failure = await upload(app, 'a'.repeat(4096));
  assert.equal(failure.status, 503);
  assert.deepEqual(failure.body, { error: 'repository_unavailable' });
  assert.deepEqual(repository.get('alice', 'doc-000001'), before);
  assert.equal(repository.list('alice').length, 1);
  assert.equal(repository.get('alice', 'doc-000002'), null);
  fail = false;
  assert.equal((await upload(app, 'a'.repeat(4096))).body.id, 'doc-000002');
  assert.equal((await upload(app, 'b'.repeat(4093))).body.id, 'doc-000003');
  assert.equal(
    repository.list('alice').reduce((size, item) => size + item.size_bytes, 0),
    8192,
  );
});

for (const quota of ['last document slot', 'last bytes'] as const) {
  test(`concurrent bodies compete atomically for the ${quota}`, async () => {
    const repository = new MemoryRepository();
    const app = createApp({ repository });
    const byteQuota = quota === 'last bytes';
    await upload(app, byteQuota ? 's'.repeat(4096) : 'first');
    if (!byteQuota) await upload(app, 'second');
    const old = repository.get('alice', 'doc-000001');
    const bodies = [
      suspendedBody(Buffer.from('a'.repeat(byteQuota ? 4096 : 1))),
      suspendedBody(Buffer.from('b'.repeat(byteQuota ? 4096 : 1))),
    ];
    const pending = bodies.map((body) => Promise.resolve(app.fetch(streamingRequest(body.stream))));
    await Promise.all(bodies.map((body) => body.reading));
    for (const body of bodies) body.release();
    const results = await Promise.all(pending);
    assert.deepEqual(results.map((result) => result.status).sort(), [201, 409]);
    assert.deepEqual(await results.find((result) => result.status === 409)!.json(), {
      error: 'quota_exceeded',
    });
    const saved = repository.list('alice');
    assert.equal(saved.length, byteQuota ? 2 : 3);
    if (byteQuota)
      assert.equal(
        saved.reduce((size, item) => size + item.size_bytes, 0),
        8192,
      );
    assert.deepEqual(repository.get('alice', 'doc-000001'), old);
    const bob = await response(app, '/documents', {
      method: 'POST',
      headers: { ...uploadHeaders, Authorization: 'Bearer lab-bob-session' },
      body: 'bob',
    });
    assert.equal(bob.status, 201);
    assert.equal(bob.body.id, byteQuota ? 'doc-000003' : 'doc-000004');
    assert.equal(repository.list('bob').length, 1);
  });
}

test('repository owns immutable byte copies and does not overwrite equal filenames', () => {
  const repository = new MemoryRepository();
  const original = Buffer.from('first');
  const first = repository.commit('alice', {
    filename: 'same.txt',
    media_type: 'text/plain',
    content: original,
  });
  const expected = { ...first };
  original.fill(0);
  first.filename = 'changed.txt';
  repository.list('alice')[0].id = 'changed';
  const returned = repository.get('alice', 'doc-000001')!;
  returned.content.fill(0);
  returned.metadata.sha256 = 'changed';
  repository.commit('alice', {
    filename: 'same.txt',
    media_type: 'text/plain',
    content: Buffer.from('second'),
  });
  const actual = repository.get('alice', 'doc-000001')!;
  assert.deepEqual(actual.metadata, expected);
  assert.equal(Buffer.from(actual.content).toString(), 'first');
  assert.equal(actual.metadata.sha256, createHash('sha256').update('first').digest('hex'));
  assert.equal(Buffer.from(repository.get('alice', 'doc-000002')!.content).toString(), 'second');
  assert.equal(repository.get('bob', 'doc-000001'), null);
  assert.deepEqual(repository.list('bob'), []);
});

test('real chunked uploads enforce actual byte size before media validation', async (t) => {
  const port = await listen(t);
  const pairs = Object.entries(uploadHeaders);
  const oversized = await send(
    port,
    'POST',
    '/documents',
    [...pairs.filter(([name]) => name !== 'Content-Type'), ['Content-Type', 'image/png']],
    [Buffer.alloc(2048, 'a'), Buffer.alloc(2049, 'b')],
  );
  assert.equal(oversized.status, 413);
  assert.deepEqual(JSON.parse(oversized.body.toString()), { error: 'request_too_large' });
  const exact = await send(port, 'POST', '/documents', pairs, [
    Buffer.alloc(2048, 'a'),
    Buffer.alloc(2048, 'b'),
  ]);
  assert.equal(exact.status, 201);
  assert.equal(JSON.parse(exact.body.toString()).id, 'doc-000001');
  assert.equal(JSON.parse(exact.body.toString()).size_bytes, 4096);
});

test('application size limit does not trust a supplied Content-Length', async () => {
  const app = createApp();
  const oversized = await response(app, '/documents', {
    method: 'POST',
    headers: { ...uploadHeaders, 'Content-Length': '1' },
    body: 'a'.repeat(4097),
  });
  assert.equal(oversized.status, 413);
  const short = await response(app, '/documents', {
    method: 'POST',
    headers: { ...uploadHeaders, 'Content-Length': '99999' },
    body: 'abc',
  });
  assert.equal(short.status, 201);
  assert.equal(short.body.size_bytes, 3);
});

test('independent applications start empty without sharing repository state', async () => {
  const first = createApp();
  assert.equal((await upload(first)).body.id, 'doc-000001');
  const second = createApp();
  assert.deepEqual((await response(second)).body, { documents: [] });
  assert.equal((await upload(second)).body.id, 'doc-000001');
});
