import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';
import { createUploadController } from './controller.mjs';

const bytes = new TextEncoder().encode('中文\r\n😀e\u0301\n<script>window.pwned=1</script>');
const hash = (value) => createHash('sha256').update(value).digest('hex');
const metadata = (id = 'doc-000001', value = bytes, name = 'notes.txt') => ({
  id,
  filename: name,
  media_type: name.endsWith('.md') ? 'text/markdown' : 'text/plain',
  size_bytes: value.length,
  sha256: hash(value),
});
const headers = {
  'Content-Type': 'application/json; charset=utf-8',
  'Cache-Control': 'no-store',
  'X-Content-Type-Options': 'nosniff',
};
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers });
const file = (name = 'notes.txt', value = bytes) =>
  new File([value], name, { type: 'application/x-untrusted' });
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
};
const drain = () => new Promise((resolve) => setImmediate(resolve));
async function until(predicate) {
  for (let step = 0; step < 30; step++) {
    if (predicate()) return;
    await drain();
  }
  assert.ok(predicate(), 'controlled request did not reach expected state');
}
function harness(overrides = {}) {
  const requests = [],
    downloads = [],
    timers = new Map();
  let timerId = 0;
  const deps = {
    fetch: async (url, init) => {
      requests.push({ url, init });
      if (overrides.respond) return overrides.respond(url, init);
      if (init.method === 'POST')
        return json(metadata('doc-000001', init.body, init.headers['X-Filename']), 201);
      return json({ documents: [] });
    },
    readFile: (value) => value.arrayBuffer(),
    sha256: async (value) => hash(value),
    download: (value, name) => downloads.push({ value: value.slice(), name }),
    setTimer: (callback) => {
      const id = ++timerId;
      timers.set(id, callback);
      return id;
    },
    clearTimer: (id) => timers.delete(id),
    ...overrides.dependencies,
  };
  const controller = createUploadController({
    dependencies: deps,
    initialIdentity: overrides.initialIdentity,
  });
  const snapshot = () => controller.getSnapshot();
  return {
    controller,
    snapshot,
    requests,
    downloads,
    timers,
    posts: () => requests.filter((entry) => entry.init.method === 'POST'),
    expire: () => {
      for (const callback of [...timers.values()]) callback();
    },
    start: async () => {
      controller.start();
      await until(() => snapshot().listing.phase !== 'loading');
    },
    upload: () => {
      controller.selectFile(file());
      controller.upload();
    },
  };
}

test('identity transition blocks synchronous abort callback from issuing an old-owner GET', async () => {
  const pending = deferred();
  let instance;
  const h = harness({
    respond: (_url, init) => {
      if (init.headers.Authorization.endsWith('lab-alice-session')) {
        init.signal.addEventListener('abort', () => instance.refreshList(), { once: true });
        return pending.promise;
      }
      return json({ documents: [] });
    },
  });
  instance = h.controller;
  try {
    h.controller.start();
    h.controller.setIdentity('bob');
    await until(() => h.snapshot().listing.phase === 'ready');
    assert.equal(
      h.requests.filter((r) => r.init.headers.Authorization.endsWith('lab-alice-session')).length,
      1,
    );
    assert.equal(h.requests.length, 2);
    assert.equal(h.timers.size, 0);
  } finally {
    h.controller.dispose();
    pending.resolve(json({ documents: [] }));
    await drain();
  }
});

test('dispose clears the cached private snapshot without notifying or retaining data', async () => {
  const h = harness({
    respond: (url) =>
      url.endsWith('/content')
        ? new Response(bytes, {
            headers: {
              ...headers,
              'Content-Type': 'application/octet-stream',
              'Content-Disposition': 'attachment; filename="upload-doc-000001.txt"',
            },
          })
        : json({ documents: [metadata()] }),
  });
  await h.start();
  h.controller.selectFile(file());
  h.controller.preview('doc-000001');
  await until(() => h.snapshot().content.phase === 'preview');
  let notifications = 0;
  h.controller.subscribe(() => notifications++);
  h.controller.dispose();
  assert.equal(notifications, 0);
  assert.equal(h.snapshot().selectedFile, null);
  assert.deepEqual(h.snapshot().listing.documents, []);
  assert.equal(h.snapshot().content.text, null);
  assert.equal(h.snapshot().upload.metadata, null);
  assert.equal(h.timers.size, 0);
});

test('construction/subscription are pure and start is idempotent with immutable snapshot', async () => {
  const h = harness();
  const original = h.snapshot();
  let calls = 0;
  const unsubscribe = h.controller.subscribe(() => calls++);
  assert.equal(h.requests.length, 0);
  assert.equal(calls, 0);
  assert.equal(h.snapshot(), original);
  assert.ok(Object.isFrozen(original));
  h.controller.start();
  h.controller.start();
  await until(() => h.snapshot().listing.phase === 'ready');
  assert.equal(h.requests.length, 1);
  unsubscribe();
  h.controller.dispose();
  h.controller.start();
  h.controller.refreshList();
  h.controller.selectFile(file());
  h.controller.upload();
  assert.equal(h.requests.length, 1);
  assert.equal(h.timers.size, 0);
});

test('upload keeps original bytes, ignores File.type, guards duplicate clicks and separates failed GET', async () => {
  const h = harness({
    respond: (_url, init) =>
      init.method === 'POST'
        ? json(metadata('doc-000001', init.body, init.headers['X-Filename']), 201)
        : json({ error: 'repository_unavailable' }, 503),
  });
  await h.start();
  h.upload();
  h.controller.upload();
  await until(
    () => h.snapshot().upload.phase === 'confirmed' && h.snapshot().listing.phase === 'error',
  );
  assert.equal(h.posts().length, 1);
  assert.deepEqual(h.posts()[0].init.body, bytes);
  const { init, url } = h.posts()[0];
  assert.equal(url, '/api/documents');
  assert.equal(init.credentials, 'omit');
  assert.equal(init.cache, 'no-store');
  assert.equal(init.redirect, 'error');
  assert.deepEqual(init.headers, {
    Authorization: 'Bearer lab-alice-session',
    'Content-Type': 'text/plain',
    'X-Filename': 'notes.txt',
  });
  assert.deepEqual(h.snapshot().upload.metadata, metadata());
  assert.equal(h.snapshot().selectedFile, null);
  assert.equal(h.snapshot().ownerHasUnconfirmedUpload, false);
  h.controller.dispose();
  assert.equal(h.timers.size, 0);
});

for (const [name, response, phase, notice] of [
  ['quota', () => json({ error: 'quota_exceeded' }, 409), 'rejected', 'quota_exceeded'],
  [
    'session',
    () => json({ error: 'session_store_unavailable' }, 503),
    'rejected',
    'session_store_unavailable',
  ],
  [
    'repository',
    () => json({ error: 'repository_unavailable' }, 503),
    'rejected',
    'repository_unavailable',
  ],
  [
    'unknown commit',
    () => json({ error: 'result_unconfirmed' }, 503),
    'unconfirmed',
    'upload_unconfirmed',
  ],
  [
    'internal 500',
    () => json({ error: 'request_failed' }, 500),
    'unconfirmed',
    'upload_unconfirmed',
  ],
  [
    'arbitrary diagnostic',
    () => json({ error: 'PRIVATE-DIAGNOSTIC' }, 409),
    'unconfirmed',
    'upload_unconfirmed',
  ],
  [
    'wrong 201 sha',
    () => json({ ...metadata(), sha256: 'a'.repeat(64) }, 201),
    'unconfirmed',
    'upload_unconfirmed',
  ],
  [
    'wrong 201 name',
    () => json({ ...metadata(), filename: 'other.txt' }, 201),
    'unconfirmed',
    'upload_unconfirmed',
  ],
  [
    'wrong 201 length',
    () => json({ ...metadata(), size_bytes: 1 }, 201),
    'unconfirmed',
    'upload_unconfirmed',
  ],
  [
    'HTML',
    () => new Response('<html>PRIVATE-DIAGNOSTIC</html>', { status: 502 }),
    'unconfirmed',
    'upload_unconfirmed',
  ],
])
  test(`POST classifies ${name} without persisting raw diagnostics`, async () => {
    const h = harness({
      respond: (_url, init) => (init.method === 'POST' ? response() : json({ documents: [] })),
    });
    await h.start();
    h.upload();
    await until(() => h.snapshot().upload.phase === phase);
    assert.equal(h.snapshot().upload.notice, notice);
    assert.equal(h.snapshot().ownerHasUnconfirmedUpload, phase === 'unconfirmed');
    assert.equal(JSON.stringify(h.snapshot()).includes('PRIVATE-DIAGNOSTIC'), false);
    h.controller.dispose();
  });

test('unknown survives same-owner identity, successful GET, acknowledgement, and a later 201', async () => {
  let number = 0;
  const h = harness({
    respond: (_url, init) => {
      if (init.method !== 'POST') return json({ documents: [] });
      number++;
      if (number === 1) throw new Error('PRIVATE response lost');
      return json(metadata(`doc-00000${number}`, init.body), 201);
    },
  });
  await h.start();
  h.upload();
  await until(() => h.snapshot().upload.phase === 'unconfirmed');
  assert.equal(h.snapshot().canUpload, false);
  h.controller.selectFile(file());
  h.controller.upload();
  assert.equal(h.posts().length, 1);
  h.controller.setIdentity('alice-second');
  await until(() => h.snapshot().listing.phase === 'ready');
  assert.equal(h.snapshot().ownerHasUnconfirmedUpload, true);
  h.controller.setIdentity('bob');
  await until(() => h.snapshot().listing.phase === 'ready');
  assert.equal(h.snapshot().ownerHasUnconfirmedUpload, false);
  assert.equal(JSON.stringify(h.snapshot()).includes('notes.txt'), false);
  h.controller.setIdentity('alice');
  await until(() => h.snapshot().listing.phase === 'ready');
  h.controller.selectFile(file());
  const resetKey = h.snapshot().fileInputResetKey;
  h.controller.allowAnotherPost();
  assert.equal(h.posts().length, 1);
  assert.equal(h.snapshot().selectedFile, null);
  assert.ok(h.snapshot().fileInputResetKey > resetKey);
  assert.equal(h.snapshot().onePostPermission, true);
  h.controller.upload();
  assert.equal(h.posts().length, 1);
  h.upload();
  await until(() => h.snapshot().upload.phase === 'confirmed');
  assert.equal(h.posts().length, 2);
  assert.equal(h.snapshot().ownerHasUnconfirmedUpload, true);
  assert.equal(h.snapshot().onePostPermission, false);
  h.controller.selectFile(file());
  h.controller.upload();
  assert.equal(h.posts().length, 2);
  h.controller.dispose();
  assert.equal(h.timers.size, 0);
});

for (const stage of ['reading', 'hashing', 'posting', 'body']) {
  for (const finish of ['stop', 'timeout'])
    test(`${finish} at ${stage} ends promptly despite ignored abort`, async () => {
      const held = deferred();
      let readerCancelled = 0;
      const delayedResponse = {
        status: 201,
        headers: new Headers(headers),
        redirected: false,
        body: {
          getReader: () => ({
            read: () => held.promise,
            cancel: () => {
              readerCancelled++;
              return new Promise(() => {});
            },
            releaseLock() {},
          }),
        },
      };
      const h = harness({
        dependencies:
          stage === 'reading'
            ? { readFile: () => held.promise }
            : stage === 'hashing'
              ? { sha256: () => held.promise }
              : {},
        respond: (_url, init) =>
          init.method !== 'POST'
            ? json({ documents: [] })
            : stage === 'posting'
              ? held.promise
              : stage === 'body'
                ? delayedResponse
                : json(metadata(), 201),
      });
      await h.start();
      h.upload();
      await until(() => h.snapshot().upload.phase === (stage === 'body' ? 'posting' : stage));
      await drain();
      if (finish === 'stop') h.controller.stopWaiting();
      else h.expire();
      const dispatched = stage === 'posting' || stage === 'body';
      assert.equal(h.snapshot().upload.phase, dispatched ? 'unconfirmed' : 'stopped');
      assert.equal(h.snapshot().ownerHasUnconfirmedUpload, dispatched);
      assert.equal(h.timers.size, 0);
      if (stage === 'reading') held.resolve(bytes.slice().buffer);
      if (stage === 'hashing') held.resolve(hash(bytes));
      if (stage === 'posting') held.resolve(json(metadata(), 201));
      if (stage === 'body') {
        assert.equal(readerCancelled, 1);
        held.resolve({ done: true });
      }
      await drain();
      await drain();
      assert.equal(h.snapshot().upload.phase, dispatched ? 'unconfirmed' : 'stopped');
      assert.equal(h.posts().length, dispatched ? 1 : 0);
      h.controller.dispose();
    });
}

for (const stage of ['reading', 'hashing'])
  test(`new file selection supersedes late ${stage} and old finally cannot stop new request`, async () => {
    const old = deferred(),
      fresh = deferred();
    let count = 0;
    const h = harness({
      dependencies:
        stage === 'reading'
          ? { readFile: () => (++count === 1 ? old.promise : fresh.promise) }
          : { sha256: () => (++count === 1 ? old.promise : fresh.promise) },
    });
    await h.start();
    h.upload();
    await until(() => h.snapshot().upload.phase === stage);
    h.controller.selectFile(file('new.txt'));
    h.controller.upload();
    await until(() => count === 2);
    old.resolve(stage === 'reading' ? bytes.slice().buffer : hash(bytes));
    await drain();
    assert.equal(h.snapshot().upload.phase, stage);
    assert.equal(h.snapshot().selectedFile.name, 'new.txt');
    assert.equal(h.posts().length, 0);
    fresh.resolve(stage === 'reading' ? bytes.slice().buffer : hash(bytes));
    await until(() => h.snapshot().upload.phase === 'confirmed');
    assert.equal(h.posts()[0].init.headers['X-Filename'], 'new.txt');
    h.controller.dispose();
  });

test('late Alice 201 cannot enter Bob view or release Bob pending request', async () => {
  const alice = deferred(),
    bob = deferred();
  const h = harness({
    respond: (_url, init) =>
      init.method !== 'POST'
        ? json({ documents: [] })
        : init.headers.Authorization.endsWith('lab-alice-session')
          ? alice.promise
          : bob.promise,
  });
  await h.start();
  h.upload();
  await until(() => h.posts().length === 1);
  h.controller.setIdentity('bob');
  await until(() => h.snapshot().listing.phase === 'ready');
  h.upload();
  await until(() => h.posts().length === 2);
  assert.equal(h.posts()[0].init.signal.aborted, true);
  alice.resolve(json(metadata(), 201));
  await drain();
  assert.equal(h.snapshot().identity.id, 'bob');
  assert.equal(h.snapshot().upload.phase, 'posting');
  assert.equal(h.snapshot().ownerHasUnconfirmedUpload, false);
  bob.resolve(json(metadata('doc-000002'), 201));
  await until(() => h.snapshot().upload.phase === 'confirmed');
  h.controller.setIdentity('alice');
  assert.equal(h.snapshot().ownerHasUnconfirmedUpload, true);
  assert.equal(h.snapshot().upload.metadata, null);
  h.controller.dispose();
});

test('same-identity old list response cannot replace a newer list', async () => {
  const old = deferred(),
    fresh = deferred();
  let calls = 0;
  const h = harness({ respond: () => (++calls === 1 ? old.promise : fresh.promise) });
  h.controller.start();
  h.controller.refreshList();
  fresh.resolve(json({ documents: [metadata('doc-000002')] }));
  await until(() => h.snapshot().listing.phase === 'ready');
  old.resolve(json({ documents: [metadata()] }));
  await drain();
  assert.equal(h.snapshot().listing.documents[0].id, 'doc-000002');
  assert.equal(h.timers.size, 0);
  h.controller.dispose();
});

function attachment(value = bytes, patch = {}) {
  return new Response(value, {
    headers: {
      ...headers,
      'Content-Type': 'application/octet-stream',
      'Content-Disposition': 'attachment; filename="upload-doc-000001.txt"',
      ...patch,
    },
  });
}
test('preview and explicit download verify original bytes and safe attachment metadata', async () => {
  const h = harness({
    respond: (url) => (url.endsWith('/content') ? attachment() : json({ documents: [metadata()] })),
  });
  await h.start();
  h.controller.preview('doc-000001');
  await until(() => h.snapshot().content.phase === 'preview');
  assert.equal(h.snapshot().content.text, new TextDecoder().decode(bytes));
  assert.equal(h.downloads.length, 0);
  h.controller.download('doc-000001');
  await until(() => h.snapshot().content.phase === 'downloaded');
  assert.deepEqual(h.downloads, [{ value: bytes, name: 'upload-doc-000001.txt' }]);
  const calls = h.requests.length;
  h.controller.download('../arbitrary');
  assert.equal(h.requests.length, calls);
  h.controller.dispose();
});
for (const bad of ['headers', 'bytes', 'oversize', 'digest'])
  test(`attachment ${bad} mismatch refuses preview/download`, async () => {
    const h = harness({
      respond: (url) =>
        url.endsWith('/content')
          ? bad === 'headers'
            ? attachment(bytes, { 'Content-Disposition': 'attachment; filename="PRIVATE"' })
            : bad === 'bytes'
              ? attachment(bytes.slice(1))
              : bad === 'oversize'
                ? attachment(new Uint8Array(4097))
                : attachment()
          : json({ documents: [metadata()] }),
      dependencies: bad === 'digest' ? { sha256: async () => 'a'.repeat(64) } : {},
    });
    await h.start();
    h.controller.download('doc-000001');
    await until(() => h.snapshot().content.phase === 'error');
    assert.equal(h.downloads.length, 0);
    assert.equal(h.snapshot().content.text, null);
    assert.equal(JSON.stringify(h.snapshot()).includes('PRIVATE'), false);
    h.controller.dispose();
  });

test('late download digest after identity change creates no download and cannot clear new content', async () => {
  const old = deferred();
  let hashes = 0;
  const h = harness({
    respond: (url) => (url.endsWith('/content') ? attachment() : json({ documents: [metadata()] })),
    dependencies: { sha256: () => (++hashes === 1 ? old.promise : Promise.resolve(hash(bytes))) },
  });
  await h.start();
  h.controller.download('doc-000001');
  await until(() => hashes === 1);
  h.controller.setIdentity('bob');
  await until(() => h.snapshot().listing.phase === 'ready');
  h.controller.preview('doc-000001');
  await until(() => h.snapshot().content.phase === 'preview');
  old.resolve(hash(bytes));
  await drain();
  assert.equal(h.snapshot().identity.id, 'bob');
  assert.equal(h.snapshot().content.phase, 'preview');
  assert.equal(h.downloads.length, 0);
  h.controller.dispose();
});

test('readonly and invalid selection are guarded by commands, not just disabled controls', async () => {
  const h = harness({ initialIdentity: 'alice-readonly' });
  await h.start();
  h.upload();
  assert.equal(h.posts().length, 0);
  assert.equal(h.snapshot().canUpload, false);
  h.controller.setIdentity('alice');
  await until(() => h.snapshot().listing.phase === 'ready');
  h.controller.selectFile(file('unsafe.txt\n'));
  h.controller.upload();
  assert.equal(h.posts().length, 0);
  assert.equal(h.snapshot().selectionNotice, 'file_name_not_supported');
  h.controller.dispose();
});

test('default download adapter revokes object URL even when click throws', async () => {
  const previousDocument = globalThis.document,
    previousCreate = URL.createObjectURL,
    previousRevoke = URL.revokeObjectURL;
  const created = [],
    revoked = [];
  URL.createObjectURL = (blob) => {
    created.push(blob);
    return 'blob:owned-fixture';
  };
  URL.revokeObjectURL = (url) => revoked.push(url);
  globalThis.document = {
    createElement: () => ({
      click() {
        throw new Error('PRIVATE click failure');
      },
    }),
  };
  // Omit the injected sink entirely to exercise the real browser adapter.
  const timers = new Map();
  let n = 0;
  const controller = createUploadController({
    dependencies: {
      fetch: async (url) =>
        url.endsWith('/content') ? attachment() : json({ documents: [metadata()] }),
      sha256: async (value) => hash(value),
      setTimer: (cb) => {
        timers.set(++n, cb);
        return n;
      },
      clearTimer: (id) => timers.delete(id),
    },
  });
  try {
    controller.start();
    await until(() => controller.getSnapshot().listing.phase === 'ready');
    controller.download('doc-000001');
    await until(() => controller.getSnapshot().content.phase === 'error');
    assert.equal(controller.getSnapshot().content.notice, 'download_failed');
    assert.equal(created.length, 1);
    assert.equal(created[0].type, 'application/octet-stream');
    assert.deepEqual(new Uint8Array(await created[0].arrayBuffer()), bytes);
    assert.deepEqual(revoked, ['blob:owned-fixture']);
  } finally {
    controller.dispose();
    globalThis.document = previousDocument;
    URL.createObjectURL = previousCreate;
    URL.revokeObjectURL = previousRevoke;
  }
});

test('one-post permission survives local stop but is consumed by a server rejection', async () => {
  const held = deferred();
  let hashes = 0,
    posts = 0;
  const h = harness({
    dependencies: {
      sha256: (value) => (++hashes === 2 ? held.promise : Promise.resolve(hash(value))),
    },
    respond: (_url, init) => {
      if (init.method !== 'POST') return json({ documents: [] });
      posts++;
      return posts === 1
        ? json({ error: 'result_unconfirmed' }, 503)
        : json({ error: 'quota_exceeded' }, 409);
    },
  });
  await h.start();
  h.upload();
  await until(() => h.snapshot().upload.phase === 'unconfirmed');
  h.controller.allowAnotherPost();
  h.upload();
  await until(() => hashes === 2);
  h.controller.stopWaiting();
  assert.equal(h.snapshot().onePostPermission, true);
  assert.equal(h.posts().length, 1);
  held.resolve(hash(bytes));
  await drain();
  h.controller.upload();
  await until(() => h.snapshot().upload.phase === 'rejected');
  assert.equal(h.posts().length, 2);
  assert.equal(h.snapshot().onePostPermission, false);
  assert.equal(h.snapshot().ownerHasUnconfirmedUpload, true);
  assert.equal(h.snapshot().canAllowAnotherPost, true);
  h.controller.dispose();
});

test('identity switch cancels unused acknowledgement without exposing another owner marker', async () => {
  const h = harness({
    respond: (_url, init) =>
      init.method === 'POST' ? json({ error: 'result_unconfirmed' }, 503) : json({ documents: [] }),
  });
  await h.start();
  h.upload();
  await until(() => h.snapshot().upload.phase === 'unconfirmed');
  h.controller.allowAnotherPost();
  assert.equal(h.snapshot().onePostPermission, true);
  h.controller.setIdentity('bob');
  assert.equal(h.snapshot().onePostPermission, false);
  assert.equal(h.snapshot().ownerHasUnconfirmedUpload, false);
  h.controller.setIdentity('alice-second');
  assert.equal(h.snapshot().onePostPermission, false);
  assert.equal(h.snapshot().ownerHasUnconfirmedUpload, true);
  h.controller.dispose();
});

test('local byte-length mismatch and failed digest never dispatch or mark unknown', async () => {
  for (const dependencies of [
    { readFile: async () => new ArrayBuffer(1) },
    { sha256: async () => 'PRIVATE wrong digest' },
  ]) {
    const h = harness({ dependencies });
    await h.start();
    h.upload();
    await until(() => h.snapshot().upload.phase === 'local_error');
    assert.equal(h.posts().length, 0);
    assert.equal(h.snapshot().ownerHasUnconfirmedUpload, false);
    assert.equal(JSON.stringify(h.snapshot()).includes('PRIVATE'), false);
    h.controller.dispose();
  }
});
