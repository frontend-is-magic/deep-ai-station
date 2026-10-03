import assert from 'node:assert/strict';
import { request as httpRequest } from 'node:http';
import type { AddressInfo } from 'node:net';
import test, { type TestContext } from 'node:test';
import { serve } from '@hono/node-server';
import { createApp } from './app.js';
import { MemorySessionStore, sessionCookie, type SessionStore } from './auth.js';
import { MemoryRepository, readLabJson, type Repository } from './repository.js';

const alice = 'Bearer lab-alice-session';
const cookie = '__Host-lab_session=lab-alice-session';
const origin = 'https://lab.example.test';
const jsonHeaders = { Authorization: alice, 'Content-Type': 'application/json' };
const cookieHeaders = {
  Cookie: cookie,
  Origin: origin,
  'X-CSRF-Token': 'lab-csrf-a',
  'Content-Type': 'application/json',
};

async function response(
  app: ReturnType<typeof createApp>,
  path: string,
  options: RequestInit = {},
) {
  const result = await app.request(path, options);
  assert.equal(result.headers.get('cache-control'), 'no-store');
  assert.match(result.headers.get('content-type') ?? '', /^application\/json/);
  if (result.status === 401)
    assert.equal(result.headers.get('www-authenticate'), 'Bearer realm="session-authorization"');
  return { status: result.status, body: await result.json(), headers: result.headers };
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
    body: unknown;
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
        result.on('end', () => {
          try {
            resolve({
              status: result.statusCode!,
              body: JSON.parse(Buffer.concat(received).toString('utf8')),
              headers: result.headers,
            });
          } catch (error) {
            reject(error);
          }
        });
      },
    );
    request.on('error', reject);
    for (const chunk of chunks) request.write(chunk);
    request.end();
  });
}
function assertCookie(value: string, clear = false) {
  const parts = value.split(';').map((part) => part.trim().toLowerCase());
  assert.ok(parts[0].startsWith('__host-lab_session='));
  for (const attribute of ['secure', 'httponly', 'samesite=strict', 'path=/'])
    assert.ok(parts.includes(attribute), attribute);
  assert.equal(
    parts.some((part) => part.startsWith('domain=')),
    false,
  );
  if (clear) {
    assert.equal(parts[0], '__host-lab_session=');
    assert.ok(parts.includes('max-age=0'));
  }
}

interface ContractCase {
  id: string;
  method: string;
  path: string;
  status: number;
  expected: unknown;
  headers?: Record<string, string>;
  header_pairs?: string[][];
  json?: unknown;
  raw?: string;
  repeat_body?: { character: string; count: number };
  content_type?: string;
  response_headers?: Record<string, string>;
  set_cookie?: boolean;
}
test('shared contract runs in order against one real HTTP app', async (t) => {
  const cases = readLabJson<ContractCase[]>('contract-cases.json');
  assert.ok(cases.length > 0);
  const port = await listen(t);
  for (const item of cases) {
    await t.test(item.id, async () => {
      const pairs = [
        ...Object.entries(item.headers ?? {}),
        ...(item.header_pairs ?? []).map((pair) => [...pair]),
      ];
      if (item.content_type) pairs.push(['Content-Type', item.content_type]);
      else if ('json' in item && !pairs.some(([name]) => name.toLowerCase() === 'content-type'))
        pairs.push(['Content-Type', 'application/json']);
      const body =
        'json' in item
          ? JSON.stringify(item.json)
          : item.repeat_body
            ? item.repeat_body.character.repeat(item.repeat_body.count)
            : item.raw;
      const result = await send(
        port,
        item.method,
        item.path,
        pairs,
        body === undefined ? [] : [Buffer.from(body)],
      );
      assert.equal(result.status, item.status);
      assert.deepEqual(result.body, item.expected);
      assert.equal(result.headers['cache-control'], 'no-store');
      for (const [name, value] of Object.entries(item.response_headers ?? {}))
        assert.equal(result.headers[name.toLowerCase()], value);
      if (item.set_cookie) assertCookie(result.headers['set-cookie']?.[0] ?? '', true);
      if (item.set_cookie === false) assert.equal(result.headers['set-cookie'], undefined);
    });
  }
});

test('injected clock enforces the precise expiration boundary without sleeping', async () => {
  let now = 1000;
  const app = createApp({ clock: () => now });
  now += 3600 * 1000 - 1;
  assert.equal((await response(app, '/me', { headers: { Authorization: alice } })).status, 200);
  now += 1;
  assert.deepEqual((await response(app, '/me', { headers: { Authorization: alice } })).body, {
    error: 'authentication_required',
  });
});

test('SessionStore get and revoke failures become private 503 errors', async () => {
  const base = new MemorySessionStore(Date.now());
  const failedGet: SessionStore = {
    get() {
      throw new Error('private-store-secret');
    },
    revoke() {
      throw new Error('unexpected');
    },
  };
  const result = await response(createApp({ sessions: failedGet }), '/documents', {
    headers: { Authorization: alice },
  });
  assert.equal(result.status, 503);
  assert.deepEqual(result.body, { error: 'session_store_unavailable' });
  const failedRevoke: SessionStore = {
    get: (token) => base.get(token),
    revoke() {
      throw new Error('private-revoke-secret');
    },
  };
  const logout = await response(createApp({ sessions: failedRevoke }), '/logout', {
    method: 'POST',
    headers: jsonHeaders,
    body: '{}',
  });
  assert.equal(logout.status, 503);
  assert.deepEqual(logout.body, { error: 'session_store_unavailable' });
});

test('repository list/find/update errors are private 503 responses and unexpected clock errors are 500', async () => {
  const normal = new MemoryRepository();
  for (const operation of ['list', 'find', 'update'] as const) {
    const repository: Repository = {
      list: (owner) => normal.list(owner),
      find: (owner, id) => normal.find(owner, id),
      update: (owner, id, archived) => normal.update(owner, id, archived),
    };
    repository[operation] = () => {
      throw new Error('private-repository-secret');
    };
    const result = await response(
      createApp({ repository }),
      operation === 'list' ? '/documents' : '/documents/alice-notes',
      operation === 'update'
        ? { method: 'PATCH', headers: jsonHeaders, body: '{"archived":true}' }
        : { headers: { Authorization: alice } },
    );
    assert.equal(result.status, 503);
    assert.deepEqual(result.body, { error: 'repository_unavailable' });
  }
  let broken = false;
  const app = createApp({
    clock: () => {
      if (broken) throw new Error('private-clock-secret');
      return 1000;
    },
  });
  broken = true;
  const result = await response(app, '/me', { headers: { Authorization: alice } });
  assert.equal(result.status, 500);
  assert.deepEqual(result.body, { error: 'request_failed' });
});

test('invalid authentication, body, query and CSRF cannot query documents or revoke sessions', async () => {
  let queries = 0;
  let revocations = 0;
  const base = new MemorySessionStore(Date.now());
  const repository: Repository = {
    list() {
      queries++;
      return [];
    },
    find() {
      queries++;
      return null;
    },
    update() {
      queries++;
      return null;
    },
  };
  const app = createApp({
    repository,
    sessions: {
      get: (token) => base.get(token),
      revoke() {
        revocations++;
      },
    },
  });
  const attempts: [string, RequestInit, number][] = [
    ['/documents', {}, 401],
    ['/documents', { headers: { Authorization: 'Bearer unknown' } }, 401],
    ['/documents?owner=bob', { headers: { Authorization: alice } }, 422],
    [
      '/documents/alice-notes',
      { method: 'PATCH', headers: jsonHeaders, body: '{"archived":1}' },
      422,
    ],
    [
      '/documents/alice-notes',
      {
        method: 'PATCH',
        headers: { Cookie: cookie, 'Content-Type': 'application/json' },
        body: '{"archived":true}',
      },
      403,
    ],
    [
      '/logout',
      { method: 'POST', headers: { ...cookieHeaders, 'X-CSRF-Token': 'lab-csrf-b' }, body: '{}' },
      403,
    ],
    [
      '/documents/alice-notes',
      { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: 'x'.repeat(4097) },
      401,
    ],
  ];
  for (const [path, options, status] of attempts)
    assert.equal((await response(app, path, options)).status, status);
  assert.equal(queries, 0);
  assert.equal(revocations, 0);
});

test('server identity is passed into each repository operation and no response exposes extra fields', async () => {
  const calls: unknown[] = [];
  const item = {
    id: 'alice-notes',
    title: 'Private',
    archived: false,
    owner: 'alice',
    secret: 'hidden',
  };
  const repository: Repository = {
    list(owner) {
      calls.push(['list', owner]);
      return [item];
    },
    find(owner, id) {
      calls.push(['find', owner, id]);
      return item;
    },
    update(owner, id, archived) {
      calls.push(['update', owner, id, archived]);
      return { ...item, archived };
    },
  };
  const app = createApp({ repository });
  const result = await response(app, '/documents/alice-notes', {
    method: 'PATCH',
    headers: { ...jsonHeaders, 'X-User-Id': 'bob', 'X-Role': 'admin' },
    body: '{"archived":true}',
  });
  assert.deepEqual(result.body, { id: 'alice-notes', title: 'Private', archived: true });
  assert.deepEqual(calls, [
    ['find', 'alice', 'alice-notes'],
    ['update', 'alice', 'alice-notes', true],
  ]);
  const listed = await response(app, '/documents', { headers: { Authorization: alice } });
  assert.deepEqual(listed.body, {
    items: [{ id: 'alice-notes', title: 'Private', archived: false }],
  });
});

test('strict JSON catches plain/escaped duplicate keys while allowing a single escaped key', async () => {
  const app = createApp();
  const invalid = [
    '{"archived":false,"archived":true}',
    '{"archived":false,"\\u0061rchived":true}',
    '{"archived":{"hidden":"a:b"},"archived":true}',
    '{"archived\\\"name":true,"archived":true}',
    '{"archived":"a:b"}',
    '{"archived":true,"owner":"bob"}',
    '[true]',
    'null',
    '{"archived":true} {}',
    '\ufeff{"archived":true}',
  ];
  for (const body of invalid)
    assert.equal(
      (
        await response(app, '/documents/alice-notes', {
          method: 'PATCH',
          headers: jsonHeaders,
          body,
        })
      ).status,
      422,
      body,
    );
  assert.equal(
    (
      await response(app, '/documents/alice-notes', {
        method: 'PATCH',
        headers: jsonHeaders,
        body: '{"\\u0061rchived":true}',
      })
    ).status,
    200,
  );
  assert.equal(
    (
      await response(app, '/logout', {
        method: 'POST',
        headers: jsonHeaders,
        body: '{"x":1,"x":2}',
      })
    ).status,
    422,
  );
});

test('allow_cookie=false refuses cookie identity and ambiguity still cannot fall back to Bearer', async () => {
  const app = createApp({ allow_cookie: false });
  assert.equal((await response(app, '/me', { headers: { Cookie: cookie } })).status, 401);
  assert.equal(
    (await response(app, '/me', { headers: { Cookie: cookie, Authorization: alice } })).status,
    400,
  );
  assert.equal(
    (await response(app, '/me', { headers: { Cookie: 'other=hello', Authorization: alice } }))
      .status,
    200,
  );
});

test('Cookie setting and clearing attributes use one framework serializer', async () => {
  assertCookie(sessionCookie('lab-alice-session'));
  assertCookie(sessionCookie('', true), true);
  const app = createApp();
  const result = await response(app, '/logout', {
    method: 'POST',
    headers: cookieHeaders,
    body: '{}',
  });
  assert.equal(result.status, 200);
  assertCookie(result.headers.get('set-cookie') ?? '', true);
  assert.equal((await response(app, '/me', { headers: { Cookie: cookie } })).status, 401);
});

test('revocation only affects one session and a fresh app resets fixtures independently', async () => {
  const app = createApp();
  await response(app, '/logout', { method: 'POST', headers: jsonHeaders, body: '{}' });
  assert.equal((await response(app, '/me', { headers: { Authorization: alice } })).status, 401);
  for (const token of ['lab-alice-second-session', 'lab-bob-session'])
    assert.equal(
      (await response(app, '/me', { headers: { Authorization: `Bearer ${token}` } })).status,
      200,
    );
  assert.equal(
    (await response(createApp(), '/me', { headers: { Authorization: alice } })).status,
    200,
  );
});

test('read-only owner is forbidden to write while other and absent resources are indistinguishable', async () => {
  const app = createApp();
  const headers = { ...jsonHeaders, Authorization: 'Bearer lab-alice-readonly-session' };
  for (const [id, status] of [
    ['alice-notes', 403],
    ['bob-notes', 404],
    ['missing', 404],
  ] as const) {
    const result = await response(app, `/documents/${id}`, {
      method: 'PATCH',
      headers,
      body: '{"archived":true}',
    });
    assert.equal(result.status, status);
    assert.deepEqual(result.body, { error: status === 403 ? 'forbidden' : 'document_not_found' });
  }
});

test('raw repeated Authorization/Cookie/Origin/CSRF headers remain distinct at the HTTP boundary', async (t) => {
  const port = await listen(t);
  const cases: [string[][], number, string][] = [
    [
      [
        ['Authorization', alice],
        ['authorization', alice],
      ],
      400,
      'ambiguous_credentials',
    ],
    [[['Authorization', alice + ', ' + alice]], 400, 'ambiguous_credentials'],
    [
      [
        ['Cookie', cookie],
        ['Cookie', cookie],
      ],
      400,
      'ambiguous_credentials',
    ],
    [
      [
        ['Authorization', alice],
        ['Cookie', '__Host-lab_session='],
      ],
      400,
      'ambiguous_credentials',
    ],
    [
      [
        ['Cookie', cookie],
        ['Origin', origin],
        ['Origin', origin],
        ['X-CSRF-Token', 'lab-csrf-a'],
      ],
      403,
      'csrf_failed',
    ],
    [
      [
        ['Cookie', cookie],
        ['Origin', origin],
        ['X-CSRF-Token', 'lab-csrf-a'],
        ['x-csrf-token', 'lab-csrf-a'],
      ],
      403,
      'csrf_failed',
    ],
  ];
  for (const [headers, status, error] of cases) {
    const result = await send(
      port,
      'PATCH',
      '/documents/alice-notes',
      [...headers, ['Content-Type', 'application/json']],
      [Buffer.from('{"archived":true}')],
    );
    assert.equal(result.status, status);
    assert.deepEqual(result.body, { error });
    assert.equal(result.headers['cache-control'], 'no-store');
  }
});

test('actual chunked HTTP body limits precede media/JSON without trusting Content-Length', async (t) => {
  const port = await listen(t);
  const result = await send(
    port,
    'PATCH',
    '/documents/alice-notes',
    [
      ['Authorization', alice],
      ['Content-Type', 'text/plain'],
    ],
    [Buffer.alloc(2048, 120), Buffer.alloc(2049, 120)],
  );
  assert.equal(result.status, 413);
  assert.deepEqual(result.body, { error: 'request_too_large' });
  const unauthorized = await send(
    port,
    'PATCH',
    '/documents/alice-notes',
    [['Content-Type', 'application/json']],
    [Buffer.alloc(4097, 120)],
  );
  assert.equal(unauthorized.status, 401);
  const exact = '{"archived":true}'.padEnd(4096, ' ');
  assert.equal(
    (
      await send(port, 'PATCH', '/documents/alice-notes', Object.entries(jsonHeaders), [
        Buffer.from(exact),
      ])
    ).status,
    200,
  );
});

test('strict UTF-8, actual bytes and media rules apply after authentication', async () => {
  const app = createApp();
  assert.equal(
    (
      await response(app, '/documents/alice-notes', {
        method: 'PATCH',
        headers: jsonHeaders,
        body: Buffer.from([0xff]),
      })
    ).status,
    422,
  );
  assert.equal(
    (
      await response(app, '/documents/alice-notes', {
        method: 'PATCH',
        headers: jsonHeaders,
        body: '界'.repeat(1366),
      })
    ).status,
    413,
  );
  assert.equal(
    (
      await response(app, '/documents/alice-notes', {
        method: 'PATCH',
        headers: { ...jsonHeaders, 'Content-Type': 'Application/JSON; broken' },
        body: '{"archived":true}',
      })
    ).status,
    200,
  );
  const exact = '{"archived":false}'.padEnd(4096, ' ');
  assert.equal(
    (
      await response(app, '/documents/alice-notes', {
        method: 'PATCH',
        headers: { ...jsonHeaders, 'Content-Length': '999999' },
        body: exact,
      })
    ).status,
    200,
  );
});

test('logout during body streaming invalidates the suspended request before repository access', async () => {
  let release: (() => void) | undefined;
  let ready: (() => void) | undefined;
  const waiting = new Promise<void>((resolve) => {
    ready = resolve;
  });
  let queries = 0;
  const normal = new MemoryRepository();
  const repository: Repository = {
    list: (owner) => normal.list(owner),
    find(owner, id) {
      queries++;
      return normal.find(owner, id);
    },
    update(owner, id, archived) {
      queries++;
      return normal.update(owner, id, archived);
    },
  };
  const app = createApp({ repository });
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      ready?.();
      return new Promise<void>((resolve) => {
        release = () => {
          controller.enqueue(Buffer.from('{"archived":true}'));
          controller.close();
          resolve();
        };
      });
    },
  });
  const request = new Request('http://localhost/documents/alice-notes', {
    method: 'PATCH',
    headers: jsonHeaders,
    body,
    duplex: 'half',
  } as RequestInit & { duplex: 'half' });
  const pending = app.fetch(request);
  await waiting;
  assert.equal(
    (await response(app, '/logout', { method: 'POST', headers: jsonHeaders, body: '{}' })).status,
    200,
  );
  release!();
  const result = await pending;
  assert.equal(result.status, 401);
  assert.deepEqual(await result.json(), { error: 'authentication_required' });
  assert.equal(queries, 0);
});

for (const [method, path, payload] of [
  ['PATCH', '/documents/alice-notes', '{"archived":true}'],
  ['POST', '/logout', '{}'],
] as const) {
  test(`expiration while reading ${method} body rejects before document access or revocation`, async () => {
    let now = 1000;
    let reads = 0;
    let queries = 0;
    let revoked = 0;
    let release: (() => void) | undefined;
    let ready: (() => void) | undefined;
    const waiting = new Promise<void>((resolve) => {
      ready = resolve;
    });
    const sessions = new MemorySessionStore(now);
    const app = createApp({
      clock: () => now,
      sessions: {
        get(token) {
          reads++;
          return sessions.get(token);
        },
        revoke() {
          revoked++;
        },
      },
      repository: {
        list() {
          queries++;
          return [];
        },
        find() {
          queries++;
          return null;
        },
        update() {
          queries++;
          return null;
        },
      },
    });
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        ready?.();
        return new Promise<void>((resolve) => {
          release = () => {
            controller.enqueue(Buffer.from(payload));
            controller.close();
            resolve();
          };
        });
      },
    });
    const request = new Request(`http://localhost${path}`, {
      method,
      headers: jsonHeaders,
      body,
      duplex: 'half',
    } as RequestInit & { duplex: 'half' });
    const pending = app.fetch(request);
    await waiting;
    assert.equal(reads, 1, 'The initial authentication passed before the body paused');
    assert.equal(
      (await response(app, '/health')).status,
      200,
      'A stalled body does not lock the app',
    );
    now += 3600 * 1000;
    release!();
    const result = await pending;
    assert.equal(result.status, 401);
    assert.equal(result.headers.get('cache-control'), 'no-store');
    assert.deepEqual(await result.json(), { error: 'authentication_required' });
    assert.equal(reads, 2, 'The same session is checked again after body decoding');
    assert.equal(queries, 0);
    assert.equal(revoked, 0);
  });
}
