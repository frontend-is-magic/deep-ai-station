import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import test, { type TestContext } from 'node:test';
import { fileURLToPath } from 'node:url';
import { LabError, parseCommand, publicError } from './request.js';
import { Repository } from './repository.js';

const at = '2026-10-03T01:02:03.004Z';
const later = '2026-10-03T02:03:04.005Z';
const main = fileURLToPath(new URL('./main.js', import.meta.url));
const migrations = existsSync(new URL('../migrations/', import.meta.url))
  ? new URL('../migrations/', import.meta.url)
  : new URL('../../shared/migrations/', import.meta.url);

function temporaryPath(t: TestContext): string {
  const directory = mkdtempSync(join(tmpdir(), 'sqlite-storage-ts-'));
  t.after(() => rmSync(directory, { force: true, recursive: true }));
  return join(directory, 'progress.sqlite');
}
function change(owner_id = 'alice', lesson_id = 'database', completed = true, time = at) {
  return { op: 'set_progress' as const, owner_id, lesson_id, completed, at: time };
}
function list(owner_id = 'alice', cursor = 0, limit = 50) {
  return { op: 'list_progress' as const, owner_id, cursor, limit };
}
function cli(path: string, input: string | Uint8Array, args: string[] = [path]) {
  const result = spawnSync(process.execPath, [main, ...args], { input, encoding: 'utf8' });
  assert.equal(result.error, undefined);
  assert.equal(result.signal, null);
  assert.equal(result.stdout.trim().split('\n').length, 1);
  return { ...result, body: JSON.parse(result.stdout) as unknown };
}
function concurrentCli(path: string, request: unknown): Promise<{ status: number; body: unknown }> {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [main, path], { stdio: ['pipe', 'pipe', 'pipe'] });
    let stdout = '';
    child.stdout.on('data', (data) => (stdout += String(data)));
    child.stderr.resume();
    child.on('error', reject);
    child.on('close', (status) => {
      try {
        assert.notEqual(status, null);
        resolve({ status: status!, body: JSON.parse(stdout) as unknown });
      } catch (error) {
        reject(error);
      }
    });
    child.stdin.end(JSON.stringify(request));
  });
}
function initializeV1(path: string): void {
  const db = new DatabaseSync(path);
  try {
    db.exec(readFileSync(new URL('001.sql', migrations), 'utf8'));
    db.exec('PRAGMA user_version = 1');
    db.prepare(
      'INSERT INTO progress (id, owner_id, lesson_id, completed, created_at) VALUES (?, ?, ?, ?, ?)',
    ).run(42, 'alice', 'database', 1, at);
    db.prepare('INSERT INTO audit (id, progress_id, completed, at) VALUES (?, ?, ?, ?)').run(
      73,
      42,
      1,
      at,
    );
  } finally {
    db.close();
  }
}
function snapshot(path: string) {
  const db = new DatabaseSync(path);
  try {
    return {
      version: db.prepare('PRAGMA user_version').get(),
      schema: db.prepare('SELECT name, sql FROM sqlite_master ORDER BY name').all(),
      progress: db.prepare('SELECT * FROM progress ORDER BY id').all(),
      audit: db.prepare('SELECT * FROM audit ORDER BY id').all(),
    };
  } finally {
    db.close();
  }
}

test('fresh processes persist data and same-state writes preserve original timestamps and audit', (t) => {
  const path = temporaryPath(t);
  assert.deepEqual(cli(path, JSON.stringify(change())).body, { error: 'unsupported_schema' });
  assert.deepEqual(cli(path, '{"op":"migrate"}').body, { schema_version: 2 });
  const item = {
    id: 1,
    owner_id: 'alice',
    lesson_id: 'database',
    completed: true,
    created_at: at,
    updated_at: at,
  };
  const written = cli(path, JSON.stringify(change()));
  assert.equal(written.status, 0);
  assert.deepEqual(written.body, { item, changed: true });
  assert.deepEqual(cli(path, JSON.stringify(change('alice', 'database', true, later))).body, {
    item,
    changed: false,
  });
  assert.deepEqual(cli(path, JSON.stringify(list())).body, { items: [item], next_cursor: null });
  assert.deepEqual(cli(path, JSON.stringify(change('alice', 'database', false, later))).body, {
    item: { ...item, completed: false, updated_at: later },
    changed: true,
  });
  assert.deepEqual(cli(path, JSON.stringify({ ...list(), op: 'list_audit' })).body, {
    items: [
      { id: 1, progress_id: 1, completed: true, at },
      { id: 2, progress_id: 1, completed: false, at: later },
    ],
    next_cursor: null,
  });
});

test('v1 migration preserves IDs and audit, backfills timestamps, and is idempotent', (t) => {
  const path = temporaryPath(t);
  initializeV1(path);
  const before = snapshot(path);
  assert.deepEqual(cli(path, JSON.stringify(list())).body, { error: 'unsupported_schema' });
  assert.deepEqual(snapshot(path), before);
  const repository = new Repository(path);
  try {
    assert.deepEqual(repository.migrate(), { schema_version: 2 });
    assert.deepEqual(repository.listProgress(list()), {
      items: [
        {
          id: 42,
          owner_id: 'alice',
          lesson_id: 'database',
          completed: true,
          created_at: at,
          updated_at: at,
        },
      ],
      next_cursor: null,
    });
    assert.deepEqual(snapshot(path).audit, before.audit);
    const migrated = snapshot(path);
    repository.migrate();
    assert.deepEqual(snapshot(path), migrated);
  } finally {
    repository.close();
  }
});

test('future schema refuses all operations without changing any database bytes', (t) => {
  const path = temporaryPath(t);
  initializeV1(path);
  const db = new DatabaseSync(path);
  db.exec('PRAGMA user_version = 99');
  db.close();
  const original = readFileSync(path);
  for (const request of [{ op: 'migrate' }, change(), list(), { ...list(), op: 'list_audit' }]) {
    const result = cli(path, JSON.stringify(request));
    assert.equal(result.status, 1);
    assert.deepEqual(result.body, { error: 'unsupported_schema' });
    assert.deepEqual(readFileSync(path), original);
  }
});

test('failed v2 backfill rolls back both ALTER TABLE and user_version', (t) => {
  const path = temporaryPath(t);
  initializeV1(path);
  const db = new DatabaseSync(path);
  db.exec(
    "CREATE TRIGGER fail_backfill BEFORE UPDATE ON progress BEGIN SELECT RAISE(ABORT, 'private migration detail'); END",
  );
  db.close();
  const before = snapshot(path);
  const result = cli(path, '{"op":"migrate"}');
  assert.equal(result.status, 1);
  assert.deepEqual(result.body, { error: 'storage_failure' });
  assert.equal((result.stdout + result.stderr).includes('private migration detail'), false);
  assert.deepEqual(snapshot(path), before);
});

test('failures after INSERT and UPDATE roll back progress, timestamps and audit; later writes recover', (t) => {
  const path = temporaryPath(t);
  const good = new Repository(path);
  const faulty = new Repository(path, {
    afterProgressWrite: () => {
      throw new Error('private audit failure');
    },
  });
  try {
    good.migrate();
    const empty = snapshot(path);
    assert.throws(() => faulty.setProgress(change()), /private audit failure/);
    assert.deepEqual(snapshot(path), empty);
    good.setProgress(change());
    const before = snapshot(path);
    assert.throws(() => faulty.setProgress(change('alice', 'database', false, later)));
    assert.deepEqual(snapshot(path), before);
    assert.equal(good.setProgress(change('alice', 'database', false, later)).changed, true);
    assert.equal(good.listAudit(list()).items.length, 2);
  } finally {
    faulty.close();
    good.close();
  }
});

test('SQLite audit failure is atomic and CLI hides SQL, paths and exception bodies', (t) => {
  const path = temporaryPath(t);
  assert.equal(cli(path, '{"op":"migrate"}').status, 0);
  const db = new DatabaseSync(path);
  db.exec(
    "CREATE TRIGGER fail_audit BEFORE INSERT ON audit BEGIN SELECT RAISE(ABORT, 'secret audit message'); END",
  );
  db.close();
  const before = snapshot(path);
  const result = cli(path, JSON.stringify(change()));
  assert.equal(result.status, 1);
  assert.deepEqual(result.body, { error: 'storage_failure' });
  for (const forbidden of [path, 'secret audit message', 'INSERT INTO']) {
    assert.equal((result.stdout + result.stderr).includes(forbidden), false);
  }
  assert.deepEqual(snapshot(path), before);
});

test('two connections classify writer contention as busy and recover after rollback', (t) => {
  const path = temporaryPath(t);
  const repository = new Repository(path);
  repository.migrate();
  const locker = new DatabaseSync(path);
  try {
    locker.exec('BEGIN IMMEDIATE');
    for (const operation of [() => repository.migrate(), () => repository.setProgress(change())]) {
      assert.throws(operation, (error) => publicError(error) === 'database_busy');
    }
    assert.deepEqual(repository.listProgress(list()).items, []);
    locker.exec('ROLLBACK');
    assert.equal(repository.setProgress(change()).changed, true);
    assert.equal(repository.listAudit(list()).items.length, 1);
  } finally {
    locker.close();
    repository.close();
  }
});

test('concurrent CLI writers serialize the same logical write and append exactly one audit', async (t) => {
  const path = temporaryPath(t);
  assert.equal(cli(path, '{"op":"migrate"}').status, 0);
  const results = await Promise.all([concurrentCli(path, change()), concurrentCli(path, change())]);
  assert.deepEqual(
    results.map((result) => result.status),
    [0, 0],
  );
  assert.deepEqual(results.map((result) => (result.body as { changed: boolean }).changed).sort(), [
    false,
    true,
  ]);
  assert.equal(snapshot(path).progress.length, 1);
  assert.equal(snapshot(path).audit.length, 1);
});

test('stable ID cursors isolate interleaved owners and end with next_cursor null', (t) => {
  const path = temporaryPath(t);
  const repository = new Repository(path);
  try {
    repository.migrate();
    repository.setProgress(change('alice', 'first'));
    repository.setProgress(change('bob', 'first'));
    repository.setProgress(change('alice', 'second'));
    repository.setProgress(change('alice', 'first', false, later));
    const first = repository.listProgress(list('alice', 0, 1));
    assert.deepEqual(
      first.items.map((item) => item.id),
      [1],
    );
    assert.equal(first.next_cursor, 1);
    repository.setProgress(change('alice', 'third'));
    const second = repository.listProgress(list('alice', first.next_cursor!, 1));
    assert.deepEqual(
      second.items.map((item) => item.id),
      [3],
    );
    assert.equal(second.next_cursor, 3);
    const third = repository.listProgress(list('alice', second.next_cursor!, 1));
    assert.deepEqual(
      third.items.map((item) => item.id),
      [4],
    );
    assert.equal(third.next_cursor, null);
    assert.deepEqual(repository.listProgress(list('alice', 99, 1)), {
      items: [],
      next_cursor: null,
    });
    assert.deepEqual(
      repository.listProgress(list('bob')).items.map((item) => item.id),
      [2],
    );
    const auditIds: number[] = [];
    let cursor: number | null = 0;
    do {
      const result = repository.listAudit(list('alice', cursor, 1));
      auditIds.push(...result.items.map((item) => item.id));
      cursor = result.next_cursor;
    } while (cursor !== null);
    assert.deepEqual(auditIds, [1, 3, 4, 5]);
    assert.deepEqual(
      repository.listAudit(list('bob')).items.map((item) => item.id),
      [2],
    );
  } finally {
    repository.close();
  }
});

test('repository binds SQL values even for strings rejected by the CLI', (t) => {
  const path = temporaryPath(t);
  const repository = new Repository(path);
  try {
    repository.migrate();
    repository.setProgress(change());
    const literal = "alice' OR 1=1; DROP TABLE audit; --";
    assert.deepEqual(repository.listProgress(list(literal)), { items: [], next_cursor: null });
    repository.setProgress(change(literal, 'literal'));
    assert.equal(repository.listProgress(list(literal)).items[0].owner_id, literal);
    assert.equal(repository.listProgress(list()).items.length, 1);
    assert.equal(repository.listAudit(list()).items.length, 1);
  } finally {
    repository.close();
  }
});

test('real database enforces unique owner/lesson, boolean CHECK and audit foreign key', (t) => {
  const path = temporaryPath(t);
  const repository = new Repository(path);
  repository.migrate();
  repository.setProgress(change());
  repository.close();
  const db = new DatabaseSync(path);
  try {
    db.exec('PRAGMA foreign_keys = ON');
    assert.throws(() => db.prepare('UPDATE progress SET completed = ?').run(2));
    assert.throws(() =>
      db.prepare('INSERT INTO audit (progress_id, completed, at) VALUES (?, ?, ?)').run(999, 1, at),
    );
    assert.throws(() =>
      db
        .prepare(
          'INSERT INTO progress (owner_id, lesson_id, completed, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
        )
        .run('alice', 'database', 1, at, at),
    );
  } finally {
    db.close();
  }
});

const invalid: [string, string | Uint8Array][] = [
  ['empty', ''],
  ['null', 'null'],
  ['array', '[]'],
  ['second JSON', '{"op":"migrate"}\n{"op":"migrate"}'],
  ['unknown field', '{"op":"migrate","fail":true}'],
  ['BOM', Buffer.concat([Buffer.from([0xef, 0xbb, 0xbf]), Buffer.from('{"op":"migrate"}')])],
  ['invalid UTF-8', Buffer.from([0xff])],
  ['overlong UTF-8', Buffer.from([0xc0, 0xaf])],
  ['4097 bytes', '{"op":"migrate"}'.padEnd(4097, ' ')],
  ['missing cursor', JSON.stringify({ op: 'list_progress', owner_id: 'alice', limit: 1 })],
  ...[true, -1, 0.5, 9007199254740992].map((cursor): [string, string] => [
    `cursor ${cursor}`,
    JSON.stringify({ ...list(), cursor }),
  ]),
  ...[true, 0, 51, 1.5].map((limit): [string, string] => [
    `limit ${limit}`,
    JSON.stringify({ ...list(), limit }),
  ]),
  ['uppercase owner', JSON.stringify(change('Alice'))],
  ['SQL owner', JSON.stringify(change("alice' OR 1=1 --"))],
  ['empty lesson', JSON.stringify(change('alice', ''))],
  ['long lesson', JSON.stringify(change('alice', 'a'.repeat(65)))],
  ['integer boolean', JSON.stringify({ ...change(), completed: 1 })],
  ['surrogate', JSON.stringify(change('\ud800'))],
  ...['\n', '\r', '\u2028', '\u2029'].flatMap((ending): [string, string][] => [
    [`owner ending ${JSON.stringify(ending)}`, JSON.stringify(change(`alice${ending}`))],
    [
      `lesson ending ${JSON.stringify(ending)}`,
      JSON.stringify(change('alice', `database${ending}`)),
    ],
  ]),
  ...[
    '0999-01-01T00:00:00.000Z',
    '2023-02-29T00:00:00.000Z',
    '2024-04-31T00:00:00.000Z',
    '2024-01-01T24:00:00.000Z',
    '2024-01-01T00:00:60.000Z',
    '2024-01-01T00:00:00.000+00:00',
    '2024-01-01T00:00:00Z',
    '2024-01-01T00:00:00.00Z',
  ].map((time): [string, string] => [
    `timestamp ${time}`,
    JSON.stringify(change('alice', 'database', true, time)),
  ]),
];
for (const [label, raw] of invalid) {
  test(`invalid input cannot create a database: ${label}`, (t) => {
    const path = temporaryPath(t);
    const result = cli(path, raw);
    assert.equal(result.status, 1);
    assert.deepEqual(result.body, { error: 'invalid_input' });
    assert.equal(existsSync(path), false);
  });
}

test('exactly 4096 bytes including final newline and trailing JSON whitespace are accepted', (t) => {
  const path = temporaryPath(t);
  const exact = '{"op":"migrate"}'.padEnd(4095, ' ') + '\n';
  assert.equal(Buffer.byteLength(exact), 4096);
  assert.equal(cli(path, exact).status, 0);
});

test('JSON 1.0 integers, maximum safe cursor and canonical leap dates are accepted', () => {
  assert.deepEqual(
    parseCommand(Buffer.from('{"op":"list_progress","owner_id":"alice","cursor":1.0,"limit":1.0}')),
    list('alice', 1, 1),
  );
  assert.deepEqual(
    parseCommand(Buffer.from(JSON.stringify(list('alice', 9007199254740991, 50)))),
    list('alice', 9007199254740991, 50),
  );
  for (const time of [
    '1000-01-01T00:00:00.000Z',
    '2024-02-29T12:34:56.789Z',
    '9999-12-31T23:59:59.999Z',
  ]) {
    assert.deepEqual(
      parseCommand(Buffer.from(JSON.stringify(change('alice', 'database', true, time)))),
      change('alice', 'database', true, time),
    );
  }
});

test('path arguments are required; public errors contain no arbitrary exception details', (t) => {
  const path = temporaryPath(t);
  for (const args of [[], [path, 'extra'], ['']]) {
    const result = cli(path, '{"op":"migrate"}', args);
    assert.equal(result.status, 1);
    assert.deepEqual(result.body, { error: 'invalid_input' });
  }
  assert.equal(existsSync(path), false);
  assert.equal(publicError(new Error('secret')), 'storage_failure');
  assert.equal(publicError(new LabError('unsupported_schema')), 'unsupported_schema');
});
