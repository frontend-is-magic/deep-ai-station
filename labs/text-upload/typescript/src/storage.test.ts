import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { once } from 'node:events';
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs';
import { request as httpRequest } from 'node:http';
import { createServer, createConnection, type AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import test, { type TestContext } from 'node:test';
import { fileURLToPath } from 'node:url';
import { serve } from '@hono/node-server';
import { createApp } from './app.js';
import { type Session } from './auth.js';
import { MemoryRepository, type Repository } from './repository.js';
import { ApiError } from './request.js';
import { readLabJson } from './resources.js';
import { SqliteRepository } from './sqlite-repository.js';
import { databaseURI, storageMode } from './storage.js';

const serverEntry = fileURLToPath(new URL('./server.js', import.meta.url));
const repositoryURL = new URL('./sqlite-repository.js', import.meta.url).href;
const headers = {
  Authorization: 'Bearer lab-alice-session',
  'Content-Type': 'text/plain',
  'X-Filename': 'notes.txt',
};
const value = (body = '中文\r\n😀é') => ({
  filename: 'notes.txt',
  media_type: 'text/plain' as const,
  content: Buffer.from(body),
});
function temporary(t: TestContext): string {
  const cwd = mkdtempSync(join(tmpdir(), 'upload-sqlite-ts-'));
  t.after(() => rmSync(cwd, { recursive: true, force: true }));
  return cwd;
}
function dbPath(cwd: string): string {
  return join(cwd, '.data', 'uploads.sqlite3');
}
function sql(cwd: string, work: (db: DatabaseSync) => void): void {
  const db = new DatabaseSync(databaseURI(dbPath(cwd)), { timeout: 0 });
  try {
    work(db);
  } finally {
    db.close();
  }
}
function snapshot(cwd: string): unknown {
  let result: unknown;
  sql(cwd, (db) => {
    result = {
      documents: db.prepare('SELECT * FROM documents ORDER BY id').all(),
      meta: db.prepare('SELECT * FROM storage_meta').all(),
    };
  });
  return result;
}
function initialized(t: TestContext): { cwd: string; repository: SqliteRepository } {
  const cwd = temporary(t);
  const repository = new SqliteRepository(cwd);
  assert.deepEqual(repository.initialize(), {
    schema_version: 1,
    storage_contract: 'text-upload-sqlite-v1',
  });
  return { cwd, repository };
}
function env(cwd: string) {
  return { PATH: process.env.PATH ?? '', HOME: cwd, LANG: 'C.UTF-8', PORT: '0' };
}
function cli(cwd: string, args: string[]) {
  const result = spawnSync(process.execPath, [serverEntry, ...args], {
    cwd,
    env: env(cwd),
    encoding: 'utf8',
    timeout: 4000,
    maxBuffer: 16384,
  });
  assert.equal(result.error, undefined);
  assert.equal(result.signal, null);
  for (const marker of [cwd, 'private-sqlite', 'at file:', 'CREATE TABLE', 'Error:'])
    assert.equal(result.stderr.includes(marker), false);
  return {
    status: result.status,
    raw: result.stdout,
    body: args[0] === '--help' && args.length === 1 ? null : (JSON.parse(result.stdout) as unknown),
  };
}
async function post(app: ReturnType<typeof createApp>, body = 'new') {
  const result = await app.request('/documents', { method: 'POST', headers, body });
  assert.equal(result.headers.get('cache-control'), 'no-store');
  assert.equal(result.headers.get('x-content-type-options'), 'nosniff');
  assert.equal(result.headers.get('access-control-allow-origin'), null);
  assert.equal(result.headers.get('set-cookie'), null);
  if (result.status === 401)
    assert.equal(result.headers.get('www-authenticate'), 'Bearer realm="text-upload"');
  return { status: result.status, body: await result.json() };
}

for (const args of [
  ['serve'],
  ['init', '--help'],
  ['serve', '--storage=sqlite'],
  ['serve', '--storage', 'memory'],
  ['serve', '--storage', 'sqlite', 'extra'],
  ['--help', 'init'],
  ['init', 'private-sqlite'],
  ['--db', 'private-sqlite'],
]) {
  test(`CLI rejects ${args.join(' ')} before filesystem access`, (t) => {
    const cwd = temporary(t);
    assert.deepEqual(cli(cwd, args), {
      status: 1,
      raw: '{"error":"invalid_input"}\n',
      body: { error: 'invalid_input' },
    });
    assert.equal(existsSync(join(cwd, '.data')), false);
  });
}
test('explicit init ignores PORT, refuses all existing targets and help does not touch storage', (t) => {
  const cwd = temporary(t);
  assert.equal(cli(cwd, ['--help']).status, 0);
  assert.equal(existsSync(join(cwd, '.data')), false);
  assert.equal(cli(cwd, ['init']).status, 0);
  const bytes = readFileSync(dbPath(cwd));
  assert.deepEqual(cli(cwd, ['init']).body, { error: 'database_exists' });
  assert.deepEqual(readFileSync(dbPath(cwd)), bytes);
  for (const contents of ['', 'private-sqlite broken']) {
    writeFileSync(dbPath(cwd), contents);
    assert.deepEqual(cli(cwd, ['init']).body, { error: 'database_exists' });
    assert.equal(readFileSync(dbPath(cwd), 'utf8'), contents);
  }
  assert.equal(storageMode([]), 'memory');
});
test('missing runtime file, symlink and URI removal race do not create a database', (t) => {
  const cwd = temporary(t);
  assert.throws(
    () => new SqliteRepository(cwd).check(),
    (e: unknown) => (e as { code: string }).code === 'database_missing',
  );
  assert.equal(existsSync(join(cwd, '.data')), false);
  mkdirSync(join(cwd, '.data'));
  assert.throws(() => new SqliteRepository(cwd).check());
  assert.equal(existsSync(dbPath(cwd)), false);
  new SqliteRepository(cwd).initialize();
  const race = new SqliteRepository(cwd, { beforeOpen: () => unlinkSync(dbPath(cwd)) });
  assert.throws(() => race.check());
  assert.equal(existsSync(dbPath(cwd)), false);
  const target = join(cwd, 'target');
  writeFileSync(target, 'untouched');
  symlinkSync(target, dbPath(cwd));
  assert.deepEqual(cli(cwd, ['init']).body, { error: 'database_exists' });
  assert.throws(() => new SqliteRepository(cwd).check());
  assert.equal(readFileSync(target, 'utf8'), 'untouched');
});
for (const kind of ['file', 'symlink'] as const) {
  test(`.data ${kind} is refused without changing its target`, (t) => {
    const cwd = temporary(t);
    const target = join(cwd, 'outside');
    mkdirSync(target);
    if (kind === 'file') writeFileSync(join(cwd, '.data'), 'private-sqlite');
    else symlinkSync(target, join(cwd, '.data'));
    assert.deepEqual(cli(cwd, ['init']).body, { error: 'repository_unavailable' });
    assert.throws(() => new SqliteRepository(cwd).check());
    assert.equal(existsSync(join(target, 'uploads.sqlite3')), false);
  });
}

test('SQLite preserves immutable raw bytes, owner boundaries, exact quotas and IDs across repository instances', (t) => {
  const { cwd, repository } = initialized(t);
  const original = value();
  const meta = repository.commit('alice', original);
  original.content.fill(0);
  const reopened = new SqliteRepository(cwd);
  const saved = reopened.get('alice', meta.id)!;
  assert.deepEqual(saved.content, Uint8Array.from(value().content));
  assert.deepEqual(saved.metadata, meta);
  saved.content.fill(0);
  saved.metadata.filename = 'changed.txt';
  assert.deepEqual(repository.get('alice', meta.id)!.content, Uint8Array.from(value().content));
  assert.equal(repository.get('bob', meta.id), null);
  assert.equal(repository.get('alice', 'doc-000001\n'), null);
  assert.equal(repository.commit('alice', value()).id, 'doc-000002');
  assert.equal(repository.commit('alice', value()).id, 'doc-000003');
  const before = snapshot(cwd);
  assert.throws(() => repository.commit('alice', value()));
  assert.deepEqual(snapshot(cwd), before);
  assert.equal(repository.commit('bob', value('x'.repeat(4096))).id, 'doc-000004');
  assert.equal(repository.commit('bob', value('y'.repeat(4096))).id, 'doc-000005');
  assert.throws(() => repository.commit('bob', value('z')));
  assert.equal(
    repository.list('bob').reduce((n, row) => n + row.size_bytes, 0),
    8192,
  );
});

test('real INSERT failure rolls back next_id and bytes, and COMMIT is never retried', async (t) => {
  const { cwd, repository } = initialized(t);
  const before = snapshot(cwd);
  const broken = new SqliteRepository(cwd, {
    afterInsert: () => {
      throw new Error('private-sqlite INSERT');
    },
    afterClose: () => {
      throw new Error('private-sqlite close');
    },
  });
  assert.deepEqual(await post(createApp({ repository: broken })), {
    status: 503,
    body: { error: 'repository_unavailable' },
  });
  assert.deepEqual(snapshot(cwd), before);
  for (const committed of [false, true]) {
    let attempts = 0;
    const uncertain = new SqliteRepository(cwd, {
      commit: (db) => {
        attempts++;
        if (committed) db.exec('COMMIT');
        throw new Error('private-sqlite COMMIT');
      },
    });
    assert.deepEqual(await post(createApp({ repository: uncertain })), {
      status: 503,
      body: { error: 'result_unconfirmed' },
    });
    assert.equal(attempts, 1);
    assert.equal(repository.list('alice').length, committed ? 1 : 0);
  }
  assert.equal(repository.commit('alice', value()).id, 'doc-000002');
});
for (const fault of ['afterCommit', 'afterClose'] as const) {
  test(`${fault} failure remains unconfirmed after actual committed INSERT`, async (t) => {
    const { cwd, repository } = initialized(t);
    const broken = new SqliteRepository(cwd, {
      [fault]: () => {
        throw new Error('private-sqlite cleanup');
      },
    });
    assert.deepEqual(await post(createApp({ repository: broken })), {
      status: 503,
      body: { error: 'result_unconfirmed' },
    });
    assert.equal(repository.list('alice').length, 1);
    assert.equal(repository.get('alice', 'doc-000001')!.metadata.size_bytes, 3);
  });
}
test('real writer contention and COMMIT BUSY are immediate and classified separately', async (t) => {
  const { cwd, repository } = initialized(t);
  const lock = new DatabaseSync(databaseURI(dbPath(cwd)));
  try {
    lock.exec('BEGIN IMMEDIATE');
    const started = performance.now();
    assert.deepEqual(await post(createApp({ repository })), {
      status: 503,
      body: { error: 'repository_unavailable' },
    });
    assert.ok(performance.now() - started < 500);
    lock.exec('ROLLBACK');
    lock.exec('BEGIN');
    lock.prepare('SELECT * FROM documents').all();
    const startedCommit = performance.now();
    assert.deepEqual(await post(createApp({ repository })), {
      status: 503,
      body: { error: 'result_unconfirmed' },
    });
    assert.ok(performance.now() - startedCommit < 500);
    lock.exec('ROLLBACK');
    assert.deepEqual(repository.list('alice'), []);
    assert.equal((await post(createApp({ repository }))).status, 201);
  } finally {
    lock.close();
  }
});

for (const change of ['revoke', 'expire', 'permission', 'owner', 'session-fault'] as const) {
  test(`final checker ${change} uses private auth latch, no INSERT and releases the transaction`, async (t) => {
    const { cwd, repository } = initialized(t);
    let now = 1000,
      reads = 0,
      inserts = 0,
      fault = false;
    const session: Session = {
      user_id: 'alice',
      can_write: true,
      revoked: false,
      expires_at: 1100,
    };
    const checked = new SqliteRepository(cwd, {
      afterBegin: () => {
        if (change === 'revoke') session.revoked = true;
        if (change === 'expire') now = 1100;
        if (change === 'permission') session.can_write = false;
        if (change === 'owner') session.user_id = 'bob';
        if (change === 'session-fault') fault = true;
      },
      afterInsert: () => {
        inserts++;
      },
      afterClose: () => {
        throw new Error('private-sqlite cleanup');
      },
    });
    const app = createApp({
      repository: checked,
      clock: () => now,
      sessions: {
        get: () => {
          reads++;
          if (fault) throw new Error('private-sqlite session');
          return { ...session };
        },
      },
    });
    const expected =
      change === 'permission'
        ? [403, 'forbidden']
        : change === 'session-fault'
          ? [503, 'session_store_unavailable']
          : [401, 'authentication_required'];
    assert.deepEqual(await post(app), { status: expected[0], body: { error: expected[1] } });
    assert.equal(reads, 3);
    assert.equal(inserts, 0);
    assert.deepEqual(repository.list('alice'), []);
    assert.equal(repository.commit('bob', value()).id, 'doc-000001');
  });
}
test('owner change immediately after body still rejects before repository', async () => {
  let reads = 0,
    commits = 0;
  const memory = new MemoryRepository();
  const app = createApp({
    clock: () => 0,
    sessions: {
      get: () => ({
        user_id: ++reads === 1 ? 'alice' : 'bob',
        can_write: true,
        revoked: false,
        expires_at: 100,
      }),
    },
    repository: {
      ...memory,
      list: () => [],
      get: () => null,
      commit: () => {
        commits++;
        throw new Error('must not enter');
      },
    },
  });
  assert.deepEqual(await post(app), { status: 401, body: { error: 'authentication_required' } });
  assert.equal(commits, 0);
});
for (const behavior of ['forged', 'omitted', 'double', 'swallowed'] as const) {
  test(`repository ${behavior} cannot bypass or forge service authorization`, async () => {
    const memory = new MemoryRepository();
    const repository: Repository = {
      list: () => [],
      get: () => null,
      commit: (owner, input, check) => {
        if (behavior === 'forged') throw new ApiError(418, 'private-sqlite forged');
        if (behavior === 'double') {
          check();
          check();
        }
        if (behavior === 'swallowed') {
          try {
            check();
          } catch {
            /* Wrong repository deliberately hides the check failure. */
          }
        }
        return {
          id: 'doc-000001',
          filename: input.filename,
          media_type: input.media_type,
          size_bytes: 3,
          sha256: '0'.repeat(64),
        };
      },
    };
    let calls = 0;
    const app = createApp({
      repository,
      clock: () => 0,
      sessions: {
        get: () => ({ user_id: 'alice', can_write: ++calls < 3, revoked: false, expires_at: 10 }),
      },
    });
    assert.deepEqual(await post(app), {
      status: behavior === 'swallowed' || behavior === 'double' ? 403 : 503,
      body: {
        error:
          behavior === 'swallowed' || behavior === 'double'
            ? 'forbidden'
            : 'repository_unavailable',
      },
    });
    assert.deepEqual(memory.list('alice'), []);
  });
}

const damage: [string, string, string][] = [
  ['future', 'PRAGMA user_version=99', 'unsupported_schema'],
  ['foreign', 'PRAGMA application_id=7', 'unsupported_schema'],
  ['contract', "UPDATE storage_meta SET storage_contract='unknown'", 'unsupported_schema'],
  ['extra-table', 'CREATE TABLE extra(x)', 'repository_unavailable'],
  [
    'extra-trigger',
    'CREATE TRIGGER extra AFTER INSERT ON documents BEGIN SELECT 1; END',
    'repository_unavailable',
  ],
  ['next-id', 'UPDATE storage_meta SET next_id=9', 'repository_unavailable'],
  [
    'metadata-utf8',
    "UPDATE storage_meta SET storage_contract=CAST(X'80' AS TEXT)",
    'repository_unavailable',
  ],
  ['owner-utf8', "UPDATE documents SET owner_id=CAST(X'80' AS TEXT)", 'repository_unavailable'],
  ['filename-utf8', "UPDATE documents SET filename=CAST(X'80' AS TEXT)", 'repository_unavailable'],
  ['wrong-filename', "UPDATE documents SET filename='../x.txt'", 'repository_unavailable'],
  ['sha', "UPDATE documents SET sha256=printf('%064d',0)", 'repository_unavailable'],
  ['text-not-blob', "UPDATE documents SET content='abc'", 'repository_unavailable'],
  ['invalid-utf8', "UPDATE documents SET content=X'80',size_bytes=1", 'repository_unavailable'],
  ['size', 'UPDATE documents SET size_bytes=2', 'repository_unavailable'],
  [
    'size-type-huge',
    "UPDATE documents SET size_bytes=printf('%2000000s','x')",
    'repository_unavailable',
  ],
  ['gap-id', 'UPDATE documents SET id=2', 'repository_unavailable'],
  [
    'owner-quota',
    'INSERT INTO documents SELECT 2,owner_id,filename,media_type,size_bytes,sha256,content FROM documents WHERE id=1; INSERT INTO documents SELECT 3,owner_id,filename,media_type,size_bytes,sha256,content FROM documents WHERE id=1; INSERT INTO documents SELECT 4,owner_id,filename,media_type,size_bytes,sha256,content FROM documents WHERE id=1; UPDATE storage_meta SET next_id=5',
    'repository_unavailable',
  ],
  ['huge-blob', 'UPDATE documents SET content=zeroblob(2000000)', 'repository_unavailable'],
  ['huge-text', "UPDATE documents SET filename=printf('%2000000s','x')", 'repository_unavailable'],
];
for (const [label, change, error] of damage) {
  test(`corrupt ${label} refuses startup and all reads without changing file bytes`, async (t) => {
    const { cwd, repository } = initialized(t);
    repository.commit('alice', value('abc'));
    sql(cwd, (db) => {
      db.exec('PRAGMA ignore_check_constraints=ON');
      db.exec(change);
    });
    const bytes = readFileSync(dbPath(cwd));
    const result = spawnSync(process.execPath, [serverEntry, 'serve', '--storage', 'sqlite'], {
      cwd,
      env: { ...env(cwd), PORT: String(await unusedPort()) },
      encoding: 'utf8',
      timeout: 4000,
    });
    assert.equal(result.error, undefined);
    assert.equal(result.status, 1);
    assert.deepEqual(JSON.parse(result.stdout), { error });
    assert.equal(result.stderr.includes(cwd), false);
    const app = createApp({ repository });
    for (const path of ['/documents', '/documents/doc-000001', '/documents/doc-000001/content']) {
      const response = await app.request(path, { headers });
      assert.equal(response.status, 503);
      assert.deepEqual(await response.json(), { error: 'repository_unavailable' });
    }
    assert.deepEqual(readFileSync(dbPath(cwd)), bytes);
  });
}

test('actual fresh processes survive pre/post COMMIT termination with exact durable state', (t) => {
  const { cwd, repository } = initialized(t);
  for (const [hook, code, count] of [
    ['afterInsert', 70, 0],
    ['afterCommit', 71, 1],
  ] as const) {
    const script = `import {SqliteRepository} from ${JSON.stringify(repositoryURL)}; const r=new SqliteRepository(process.cwd(), {${hook}:()=>process.exit(${code})}); r.commit('alice',{filename:'notes.txt',media_type:'text/plain',content:Buffer.from('原文\\r\\n😀')});`;
    const child = spawnSync(process.execPath, ['--input-type=module', '-e', script], {
      cwd,
      env: env(cwd),
      encoding: 'utf8',
      timeout: 4000,
    });
    assert.equal(child.error, undefined);
    assert.equal(child.status, code);
    assert.equal(repository.list('alice').length, count);
    const read = spawnSync(
      process.execPath,
      [
        '--input-type=module',
        '-e',
        `import {SqliteRepository} from ${JSON.stringify(repositoryURL)}; const r=new SqliteRepository(); console.log(JSON.stringify(r.list('alice')));`,
      ],
      { cwd, env: env(cwd), encoding: 'utf8', timeout: 4000 },
    );
    assert.equal(read.error, undefined);
    assert.equal(read.status, 0);
    assert.equal(JSON.parse(read.stdout).length, count);
  }
  assert.deepEqual(
    repository.get('alice', 'doc-000001')!.content,
    Uint8Array.from(Buffer.from('原文\r\n😀')),
  );
});

interface Case {
  id: string;
  method: string;
  path: string;
  status: number;
  headers?: Record<string, string>;
  header_pairs?: string[][];
  raw?: string;
  body_base64?: string;
  repeat_body?: { character: string; count: number };
  expected?: unknown;
  expected_body_base64?: string;
  response_headers?: Record<string, string>;
}
test('all original shared cases execute through real SQLite HTTP with unchanged bytes and headers', async (t) => {
  const { repository } = initialized(t);
  const server = serve({ fetch: createApp({ repository }).fetch, hostname: '127.0.0.1', port: 0 });
  if (!server.listening) await once(server, 'listening');
  t.after(
    () =>
      new Promise<void>((resolve, reject) =>
        server.close((error) => (error ? reject(error) : resolve())),
      ),
  );
  const port = (server.address() as AddressInfo).port;
  for (const item of readLabJson<Case[]>('contract-cases.json')) {
    await t.test(item.id, async () => {
      const body =
        item.body_base64 !== undefined
          ? Buffer.from(item.body_base64, 'base64')
          : item.raw !== undefined
            ? Buffer.from(item.raw)
            : item.repeat_body
              ? Buffer.from(item.repeat_body.character.repeat(item.repeat_body.count))
              : null;
      const pairs = [
        ...Object.entries(item.headers ?? {}),
        ...(item.header_pairs ?? []),
        ...(body ? [['Content-Length', String(body.length)]] : []),
      ];
      const result = await new Promise<{
        status: number;
        body: Buffer;
        headers: Record<string, string | string[] | undefined>;
      }>((resolve, reject) => {
        const request = httpRequest(
          {
            hostname: '127.0.0.1',
            port,
            path: item.path,
            method: item.method,
            agent: false,
            headers: ['Host', `127.0.0.1:${port}`, ...pairs.flat()],
          },
          (response) => {
            const chunks: Buffer[] = [];
            response.on('data', (chunk) => chunks.push(Buffer.from(chunk)));
            response.on('error', reject);
            response.on('end', () =>
              resolve({
                status: response.statusCode!,
                body: Buffer.concat(chunks),
                headers: response.headers,
              }),
            );
          },
        );
        request.on('error', reject);
        request.setTimeout(3000, () => request.destroy(new Error('test request timeout')));
        request.end(body);
      });
      assert.equal(result.status, item.status);
      if (item.expected_body_base64 !== undefined)
        assert.deepEqual(result.body, Buffer.from(item.expected_body_base64, 'base64'));
      else assert.deepEqual(JSON.parse(result.body.toString()), item.expected);
      for (const [key, val] of Object.entries(item.response_headers ?? {}))
        assert.equal(result.headers[key.toLowerCase()], val);
      assert.equal(result.headers['access-control-allow-origin'], undefined);
      assert.equal(result.headers['set-cookie'], undefined);
    });
  }
});

async function unusedPort(): Promise<number> {
  const listener = createServer();
  listener.listen(0, '127.0.0.1');
  await once(listener, 'listening');
  const port = (listener.address() as AddressInfo).port;
  await new Promise<void>((resolve, reject) =>
    listener.close((error) => (error ? reject(error) : resolve())),
  );
  return port;
}
async function ownedServer(
  t: TestContext,
  cwd: string,
  sqlite = true,
  fault?: 'afterInsert' | 'afterCommit',
) {
  const port = await unusedPort();
  const child = spawn(
    process.execPath,
    fault
      ? [
          '--input-type=module',
          '-e',
          `import {serve} from ${JSON.stringify(import.meta.resolve('@hono/node-server'))}; import {createApp} from ${JSON.stringify(new URL('./app.js', import.meta.url).href)}; import {SqliteRepository} from ${JSON.stringify(repositoryURL)}; const r=new SqliteRepository(process.cwd(),{${fault}:()=>process.exit(${fault === 'afterInsert' ? 70 : 71})}); const s=serve({fetch:createApp({repository:r}).fetch,hostname:'127.0.0.1',port:Number(process.env.PORT)});process.on('SIGTERM',()=>s.close());`,
        ]
      : [serverEntry, ...(sqlite ? ['serve', '--storage', 'sqlite'] : [])],
    { cwd, env: { ...env(cwd), PORT: String(port) }, stdio: ['ignore', 'pipe', 'pipe'] },
  );
  let stdout = '',
    stderr = '';
  child.stdout.on('data', (chunk) => {
    stdout += String(chunk);
    if (stdout.length > 16384) child.kill('SIGKILL');
  });
  child.stderr.on('data', (chunk) => {
    stderr += String(chunk);
    if (stderr.length > 16384) child.kill('SIGKILL');
  });
  let stopped = false;
  const stop = async () => {
    if (stopped) return;
    stopped = true;
    if (child.exitCode === null && child.signalCode === null) {
      const closed = once(child, 'close');
      child.kill('SIGTERM');
      const timer = setTimeout(() => child.kill('SIGKILL'), 1500);
      try {
        await closed;
      } finally {
        clearTimeout(timer);
      }
    }
    assert.throws(
      () => process.kill(child.pid!, 0),
      (error: unknown) => (error as NodeJS.ErrnoException).code === 'ESRCH',
    );
    await new Promise<void>((resolve, reject) => {
      const socket = createConnection({ host: '127.0.0.1', port });
      socket.on('connect', () => {
        socket.destroy();
        reject(new Error('Owned listener remained open'));
      });
      socket.on('error', (error) =>
        (error as NodeJS.ErrnoException).code === 'ECONNREFUSED' ? resolve() : reject(error),
      );
      socket.setTimeout(1000, () => {
        socket.destroy();
        reject(new Error('Listener cleanup timed out'));
      });
    });
    assert.equal(stdout, '');
    assert.equal(stderr.includes(cwd), false);
  };
  t.after(stop);
  const base = `http://127.0.0.1:${port}`;
  const deadline = Date.now() + 4000;
  while (true) {
    assert.equal(child.exitCode, null, 'Owned server exited before health');
    try {
      const ready = await fetch(`${base}/health`, { signal: AbortSignal.timeout(150) });
      assert.deepEqual(await ready.json(), { status: 'ok', lab: 'text-upload-v1' });
      break;
    } catch (error) {
      if (Date.now() > deadline) throw error;
      await new Promise((resolve) => setTimeout(resolve, 20));
    }
  }
  return { base, stop, child };
}
async function networkPost(base: string, body: string) {
  const result = await fetch(`${base}/documents`, {
    method: 'POST',
    headers,
    body,
    signal: AbortSignal.timeout(2000),
  });
  return { status: result.status, body: (await result.json()) as { id?: string; error?: string } };
}

test('actual memory CLI never touches a conflicting .data entry and restarts empty', async (t) => {
  const cwd = temporary(t);
  writeFileSync(join(cwd, '.data'), 'private-sqlite do not read');
  const first = await ownedServer(t, cwd, false);
  assert.equal((await networkPost(first.base, 'memory')).status, 201);
  await first.stop();
  const second = await ownedServer(t, cwd, false);
  const response = await fetch(`${second.base}/documents`, {
    headers,
    signal: AbortSignal.timeout(2000),
  });
  assert.deepEqual(await response.json(), { documents: [] });
  await second.stop();
  assert.equal(readFileSync(join(cwd, '.data'), 'utf8'), 'private-sqlite do not read');
});
test('actual SQLite HTTP restart retains exact bytes, IDs, ownership and remaining quota', async (t) => {
  const { cwd } = initialized(t);
  const original = '原始\r\n😀é';
  const first = await ownedServer(t, cwd);
  const meta = await networkPost(first.base, original);
  assert.equal(meta.status, 201);
  await first.stop();
  const second = await ownedServer(t, cwd);
  const response = await fetch(`${second.base}/documents/${meta.body.id}/content`, {
    headers,
    signal: AbortSignal.timeout(2000),
  });
  assert.deepEqual(Buffer.from(await response.arrayBuffer()), Buffer.from(original));
  const foreign = await fetch(`${second.base}/documents/${meta.body.id}`, {
    headers: { Authorization: 'Bearer lab-bob-session' },
    signal: AbortSignal.timeout(2000),
  });
  assert.equal(foreign.status, 404);
  await foreign.arrayBuffer();
  assert.equal((await networkPost(second.base, 'second')).body.id, 'doc-000002');
  await second.stop();
  const third = await ownedServer(t, cwd);
  assert.equal((await networkPost(third.base, 'third')).body.id, 'doc-000003');
  assert.deepEqual(await networkPost(third.base, 'fourth'), {
    status: 409,
    body: { error: 'quota_exceeded' },
  });
  await third.stop();
});
for (const budget of ['slots', 'bytes'] as const) {
  test(`two real service processes share the last ${budget} atomically`, async (t) => {
    const { cwd, repository } = initialized(t);
    repository.commit('alice', value(budget === 'bytes' ? 'x'.repeat(4096) : 'first'));
    if (budget === 'slots') repository.commit('alice', value('second'));
    const first = await ownedServer(t, cwd);
    const second = await ownedServer(t, cwd);
    const results = await Promise.all([
      networkPost(first.base, budget === 'bytes' ? 'a'.repeat(4096) : 'third'),
      networkPost(second.base, budget === 'bytes' ? 'b'.repeat(4096) : 'third'),
    ]);
    assert.equal(results.filter((result) => result.status === 201).length, 1);
    const refused = results.find((result) => result.status !== 201)!;
    assert.ok(refused.status === 409 || refused.status === 503);
    assert.equal(
      refused.body.error,
      refused.status === 409 ? 'quota_exceeded' : 'repository_unavailable',
    );
    await first.stop();
    await second.stop();
    sql(cwd, (db) => {
      const row = db
        .prepare('SELECT COUNT(*) AS count,SUM(size_bytes) AS bytes FROM documents')
        .get()!;
      assert.equal(row.count, budget === 'slots' ? 3 : 2);
      if (budget === 'bytes') assert.equal(row.bytes, 8192);
      assert.equal(
        db.prepare('SELECT next_id FROM storage_meta').get()!.next_id,
        budget === 'slots' ? 4 : 3,
      );
    });
  });
}

for (const change of ['revoke', 'expire', 'permission', 'owner', 'store'] as const) {
  test(`SQLite ${change} during held body refuses before repository without holding a DB lock`, async (t) => {
    const { cwd, repository } = initialized(t);
    let now = 0,
      commits = 0,
      fault = false;
    const session: Session = { user_id: 'alice', can_write: true, revoked: false, expires_at: 10 };
    const app = createApp({
      clock: () => now,
      sessions: {
        get: () => {
          if (fault) throw new Error('private-sqlite');
          return { ...session };
        },
      },
      repository: {
        get: (owner, id) => repository.get(owner, id),
        list: (owner) => repository.list(owner),
        commit: (owner, input, checker) => {
          commits++;
          return repository.commit(owner, input, checker);
        },
      },
    });
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    let reading!: () => void;
    const started = new Promise<void>((resolve) => {
      reading = resolve;
    });
    const body = new ReadableStream<Uint8Array>({
      start: (value) => {
        controller = value;
      },
      pull: () => reading(),
    });
    const request = new Request('http://lab/documents', {
      method: 'POST',
      headers,
      body,
      duplex: 'half',
    } as RequestInit);
    const pending = app.fetch(request);
    await started;
    sql(cwd, (db) => {
      db.exec('BEGIN IMMEDIATE');
      db.exec('ROLLBACK');
    });
    if (change === 'revoke') session.revoked = true;
    if (change === 'expire') now = 10;
    if (change === 'permission') session.can_write = false;
    if (change === 'owner') session.user_id = 'bob';
    if (change === 'store') fault = true;
    controller.enqueue(Buffer.from('text'));
    controller.close();
    const response = await pending;
    assert.equal(response.status, change === 'permission' ? 403 : change === 'store' ? 503 : 401);
    assert.equal(commits, 0);
    assert.deepEqual(repository.list('alice'), []);
  });
}
test('successful final authorization is required exactly once for both implementations', async (t) => {
  const { repository } = initialized(t);
  for (const store of [new MemoryRepository(), repository]) {
    let calls = 0;
    const app = createApp({
      repository: store,
      clock: () => 0,
      sessions: {
        get: () => {
          calls++;
          return { user_id: 'alice', can_write: true, revoked: false, expires_at: 10 };
        },
      },
    });
    assert.equal((await post(app)).status, 201);
    assert.equal(calls, 3);
  }
  const duplicate: Repository = {
    list: () => [],
    get: () => null,
    commit: (_owner, _input, check) => {
      check();
      check();
      throw new Error('never');
    },
  };
  assert.deepEqual(await post(createApp({ repository: duplicate })), {
    status: 503,
    body: { error: 'repository_unavailable' },
  });
});
test('failed initialization keeps its owned file and closed stdout never emits another diagnostic', async (t) => {
  const cwd = temporary(t);
  const failed = new SqliteRepository(cwd, {
    afterBegin: () => {
      throw new Error('private-sqlite initialization');
    },
  });
  assert.throws(() => failed.initialize());
  assert.ok(existsSync(dbPath(cwd)));
  assert.deepEqual(cli(cwd, ['init']).body, { error: 'database_exists' });
  const fresh = temporary(t);
  const child = spawn(process.execPath, [serverEntry, 'init'], {
    cwd: fresh,
    env: env(fresh),
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let stderr = '';
  child.stderr.on('data', (chunk) => {
    stderr += String(chunk);
  });
  const closed = once(child, 'close');
  child.stdout.destroy();
  const timer = setTimeout(() => child.kill('SIGKILL'), 4000);
  try {
    await closed;
  } finally {
    clearTimeout(timer);
  }
  assert.equal(child.signalCode, null);
  assert.equal(child.exitCode, 1);
  assert.equal(stderr.includes('Error:'), false);
  assert.equal(stderr.includes(fresh), false);
  assert.throws(() => process.kill(child.pid!, 0));
  new SqliteRepository(fresh).check();
});

test('HTTP disconnect after the real COMMIT is confirmed only by reopening the durable row', async (t) => {
  for (const fault of ['afterInsert', 'afterCommit'] as const) {
    const { cwd, repository } = initialized(t);
    const child = await ownedServer(t, cwd, true, fault);
    await assert.rejects(networkPost(child.base, 'HTTP\r\n提交😀'));
    // The request saw transport failure. A separate connection supplies the commit evidence.
    await child.stop();
    assert.equal(child.child.exitCode, fault === 'afterInsert' ? 70 : 71);
    assert.equal(repository.list('alice').length, fault === 'afterInsert' ? 0 : 1);
    const reopened = await ownedServer(t, cwd);
    const result = await fetch(`${reopened.base}/documents/doc-000001/content`, {
      headers,
      signal: AbortSignal.timeout(2000),
    });
    assert.equal(result.status, fault === 'afterInsert' ? 404 : 200);
    if (fault === 'afterCommit')
      assert.deepEqual(Buffer.from(await result.arrayBuffer()), Buffer.from('HTTP\r\n提交😀'));
    else await result.arrayBuffer();
    await reopened.stop();
  }
});
