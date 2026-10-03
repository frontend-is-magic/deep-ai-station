import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { request, type IncomingMessage } from 'node:http';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { StringDecoder } from 'node:string_decoder';
import test, { type TestContext } from 'node:test';
import { serve } from '@hono/node-server';
import { createApp, type AppOptions } from './app.js';
import {
  fixtures,
  teachingProducer,
  wait,
  type ProducerContext,
  type ProducerFactory,
  type ScheduleDeadline,
} from './streaming.js';

function deferred<T = void>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

async function listen(t: TestContext, options: AppOptions = {}) {
  const server = serve({ fetch: createApp(options).fetch, hostname: '127.0.0.1', port: 0 });
  t.after(async () => {
    const closed = new Promise<void>((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
    if ('closeAllConnections' in server) server.closeAllConnections();
    await closed;
    assert.equal(server.listening, false);
  });
  if (!server.listening) {
    await new Promise<void>((resolve, reject) => {
      server.once('listening', resolve);
      server.once('error', reject);
    });
  }
  return (server.address() as AddressInfo).port;
}

interface Frame {
  event: string;
  data: {
    run_id: string;
    scenario?: string;
    seq?: number;
    text?: string;
    code?: string;
    message?: string;
  };
}

async function openStream(port: number, scenario: string, signal?: AbortSignal) {
  const ready = deferred<IncomingMessage>();
  const outgoing = request(
    { hostname: '127.0.0.1', port, path: `/stream?scenario=${scenario}`, agent: false, signal },
    ready.resolve,
  );
  outgoing.on('error', ready.reject);
  outgoing.end();
  const response = await ready.promise;
  const frames: Frame[] = [];
  const closed = deferred();
  const readers: { count: number; resolve: () => void; reject: (error: Error) => void }[] = [];
  const decoder = new StringDecoder('utf8');
  let buffer = '';
  let ended = false;
  let finished = false;
  response.on('data', (bytes: Buffer) => {
    buffer += decoder.write(bytes);
    for (let boundary; (boundary = buffer.indexOf('\n\n')) !== -1;) {
      const frame = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const lines = frame.split('\n');
      assert.match(lines[0], /^event: /);
      assert.match(lines[1], /^data: /);
      frames.push({ event: lines[0].slice(7), data: JSON.parse(lines[1].slice(6)) });
    }
    for (const reader of [...readers]) {
      if (frames.length >= reader.count) {
        readers.splice(readers.indexOf(reader), 1);
        reader.resolve();
      }
    }
  });
  const finish = () => {
    finished = true;
    for (const reader of readers.splice(0))
      reader.reject(new Error('stream closed before expected frame'));
    closed.resolve();
  };
  response.on('end', () => {
    ended = true;
    finish();
  });
  response.on('error', finish);
  response.on('close', finish);
  return {
    frames,
    response,
    closed: closed.promise,
    get ended() {
      return ended;
    },
    waitFor(count: number) {
      if (frames.length >= count) return Promise.resolve();
      if (finished) return Promise.reject(new Error('stream already closed'));
      return new Promise<void>((resolve, reject) => readers.push({ count, resolve, reject }));
    },
    close() {
      response.destroy();
      outgoing.destroy();
    },
  };
}

function assertHeaders(response: IncomingMessage) {
  assert.equal(response.statusCode, 200);
  assert.match(String(response.headers['content-type']), /^text\/event-stream(?:;|$)/);
  assert.equal(response.headers['cache-control'], 'no-store');
  assert.equal(response.headers['x-content-type-options'], 'nosniff');
  assert.equal(response.headers['x-accel-buffering'], 'no');
  assert.equal(response.headers['access-control-allow-origin'], undefined);
  assert.equal(response.headers['set-cookie'], undefined);
}
function assertFrames(frames: Frame[], scenario: string, terminal: string) {
  const runId = frames[0].data.run_id;
  assert.match(runId, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.deepEqual(frames[0], { event: 'start', data: { run_id: runId, scenario } });
  assert.equal(frames.at(-1)?.event, terminal);
  assert.equal(frames.filter((frame) => frame.event === 'start').length, 1);
  assert.equal(frames.filter((frame) => ['done', 'error'].includes(frame.event)).length, 1);
  const deltas = frames.filter((frame) => frame.event === 'delta');
  for (const [index, delta] of deltas.entries()) {
    assert.deepEqual(delta.data, { run_id: runId, seq: index + 1, text: fixtures.texts[index] });
  }
  for (const frame of frames) assert.equal(frame.data.run_id, runId);
  return runId;
}
function manualDeadlines() {
  const alarms: { milliseconds: number; expire: () => void; cleared: number }[] = [];
  const schedule: ScheduleDeadline = (expire, milliseconds) => {
    const alarm = { milliseconds, expire, cleared: 0 };
    alarms.push(alarm);
    return () => {
      alarm.cleared++;
    };
  };
  return { alarms, schedule };
}
function trackedProducer(base: ProducerFactory = teachingProducer(0)) {
  const states: (ProducerContext & {
    calls: number;
    cleanups: number;
    aborts: number;
    cancelled: ReturnType<typeof deferred<void>>;
    cleaned: ReturnType<typeof deferred<void>>;
  })[] = [];
  const factory: ProducerFactory = (context) => {
    const state = {
      ...context,
      calls: 0,
      cleanups: 0,
      aborts: 0,
      cancelled: deferred(),
      cleaned: deferred(),
    };
    states.push(state);
    const abort = () => {
      state.aborts++;
      state.cancelled.resolve();
    };
    context.signal.addEventListener('abort', abort, { once: true });
    const producer = base(context);
    return {
      next() {
        state.calls++;
        return producer.next();
      },
      async cleanup() {
        state.cleanups++;
        context.signal.removeEventListener('abort', abort);
        await producer.cleanup();
        state.cleaned.resolve();
      },
    };
  };
  return { factory, states };
}
function passGate(promise: Promise<void>, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    signal.throwIfAborted();
    const abort = () => reject(signal.reason);
    signal.addEventListener('abort', abort, { once: true });
    promise.then(
      () => {
        signal.removeEventListener('abort', abort);
        resolve();
      },
      (error) => {
        signal.removeEventListener('abort', abort);
        reject(error);
      },
    );
  });
}

test(
  'health and invalid queries use real HTTP without starting a producer',
  { timeout: 5000 },
  async (t) => {
    let starts = 0;
    const port = await listen(t, {
      producerFactory: () => {
        starts++;
        throw new Error('must not start');
      },
    });
    const base = `http://127.0.0.1:${port}`;
    assert.deepEqual(await (await fetch(`${base}/health`)).json(), { ok: true, lab: 'sse-stream' });
    for (const query of [
      '',
      '?scenario=',
      '?scenario=unknown',
      '?scenario=SUCCESS',
      '?scenario=success&scenario=success',
      '?scenario=success&scenario=error',
      '?scenario=success&extra=1',
      '?extra=success',
    ]) {
      const response = await fetch(`${base}/stream${query}`);
      assert.equal(response.status, 400, query);
      assert.deepEqual(await response.json(), {
        error: { code: 'invalid_request', message: '仅支持一个有效的 scenario 参数' },
      });
    }
    assert.equal((await fetch(`${base}/stream?scenario=success`, { method: 'HEAD' })).status, 405);
    assert.equal(starts, 0);
  },
);

for (const scenario of ['success', 'error'] as const) {
  test(
    `${scenario} emits exact fixtures and one terminal, then cleans up once`,
    { timeout: 5000 },
    async (t) => {
      const tracked = trackedProducer();
      const deadlines = manualDeadlines();
      const port = await listen(t, {
        producerFactory: tracked.factory,
        scheduleDeadline: deadlines.schedule,
      });
      const client = await openStream(port, scenario);
      await client.closed;
      assert.ok(client.ended);
      assertHeaders(client.response);
      const id = assertFrames(client.frames, scenario, scenario === 'success' ? 'done' : 'error');
      assert.deepEqual(
        client.frames.map((frame) => frame.event),
        scenario === 'success'
          ? ['start', 'delta', 'delta', 'delta', 'done']
          : ['start', 'delta', 'error'],
      );
      assert.deepEqual(
        client.frames.at(-1)?.data,
        scenario === 'success'
          ? { run_id: id, seq: 3 }
          : { run_id: id, code: 'producer_failed', message: '教学数据源失败' },
      );
      await tracked.states[0].cleaned.promise;
      assert.equal(tracked.states[0].cleanups, 1);
      assert.equal(deadlines.alarms.length, 1);
      assert.equal(deadlines.alarms[0].milliseconds, fixtures.deadline_ms);
      assert.equal(deadlines.alarms[0].cleared, 1);
    },
  );
}

for (const scenario of ['timeout', 'hold'] as const) {
  test(
    `${scenario} shares one total deadline and cancels the waiting producer`,
    { timeout: 5000 },
    async (t) => {
      const tracked = trackedProducer();
      const deadlines = manualDeadlines();
      const port = await listen(t, {
        producerFactory: tracked.factory,
        deadlineMs: 77,
        scheduleDeadline: deadlines.schedule,
      });
      const client = await openStream(port, scenario);
      await client.waitFor(2);
      assert.equal(deadlines.alarms.length, 1);
      assert.equal(deadlines.alarms[0].milliseconds, 77);
      deadlines.alarms[0].expire();
      await tracked.states[0].cancelled.promise;
      await tracked.states[0].cleaned.promise;
      await client.closed;
      const id = assertFrames(client.frames, scenario, 'error');
      assert.deepEqual(
        client.frames.map((frame) => frame.event),
        ['start', 'delta', 'error'],
      );
      assert.deepEqual(client.frames.at(-1)?.data, {
        run_id: id,
        code: 'deadline_exceeded',
        message: '教学运行超时',
      });
      assert.equal(tracked.states[0].calls, 2);
      assert.equal(tracked.states[0].cleanups, 1);
      assert.equal(tracked.states[0].aborts, 1);
      assert.equal(deadlines.alarms[0].cleared, 1);
    },
  );
}

for (const disconnect of ['abort', 'close'] as const) {
  test(
    `real HTTP ${disconnect} after first frames cancels only A and cleans up exactly once`,
    { timeout: 5000 },
    async (t) => {
      const releaseB = deferred();
      const waitingB = deferred();
      const base = teachingProducer(0);
      const tracked = trackedProducer((context) => {
        const producer = base(context);
        let calls = 0;
        return {
          async next() {
            if (context.scenario === 'success' && ++calls === 2) {
              waitingB.resolve();
              await passGate(releaseB.promise, context.signal);
            }
            return producer.next();
          },
          cleanup: () => producer.cleanup(),
        };
      });
      const deadlines = manualDeadlines();
      const port = await listen(t, {
        producerFactory: tracked.factory,
        scheduleDeadline: deadlines.schedule,
      });
      const controllerA = new AbortController();
      const a = await openStream(port, 'hold', controllerA.signal);
      await a.waitFor(2);
      const b = await openStream(port, 'success');
      await b.waitFor(2);
      await waitingB.promise;
      const stateA = tracked.states.find((state) => state.scenario === 'hold')!;
      const stateB = tracked.states.find((state) => state.scenario === 'success')!;
      assert.notEqual(stateA.runId, stateB.runId);
      if (disconnect === 'abort') controllerA.abort();
      else a.close();
      // These are server-side signals; a client's local abort alone is insufficient.
      await stateA.cancelled.promise;
      await stateA.cleaned.promise;
      await a.closed;
      assert.equal(stateA.cleanups, 1);
      assert.equal(stateA.aborts, 1);
      assert.equal(stateA.calls, 2);
      assert.equal(stateB.signal.aborted, false);
      assert.equal(stateB.cleanups, 0);
      assert.deepEqual(
        a.frames.map((frame) => frame.event),
        ['start', 'delta'],
      );
      releaseB.resolve();
      await b.closed;
      await stateB.cleaned.promise;
      assertFrames(b.frames, 'success', 'done');
      assert.equal(stateB.cleanups, 1);
      assert.equal(stateB.aborts, 0);
      controllerA.abort();
      a.close();
      assert.equal(stateA.cleanups, 1);
      assert.equal(deadlines.alarms.length, 2);
      assert.ok(deadlines.alarms.every((alarm) => alarm.cleared === 1));
    },
  );
}

test(
  'producer and cleanup failures remain private and never append another terminal',
  { timeout: 5000 },
  async (t) => {
    let cleanups = 0;
    const port = await listen(t, {
      producerFactory: () => ({
        next: async () => {
          throw new Error('private-provider-detail');
        },
        cleanup: () => {
          cleanups++;
          throw new Error('private-cleanup-detail');
        },
      }),
    });
    const client = await openStream(port, 'success');
    await client.closed;
    assertFrames(client.frames, 'success', 'error');
    assert.equal(cleanups, 1);
    assert.doesNotMatch(JSON.stringify(client.frames), /private/);
  },
);

test('default wait rejects cancellation immediately and releases its timer', async () => {
  const controller = new AbortController();
  const pending = wait(60_000, controller.signal);
  controller.abort(new Error('cancelled'));
  await assert.rejects(pending, /cancelled/);
  await assert.rejects(wait(null, controller.signal), /cancelled/);
});

test(
  'closing after only the start frame cancels an injected producer before any delta',
  { timeout: 5000 },
  async (t) => {
    const waiting = deferred();
    const tracked = trackedProducer(({ signal }) => ({
      async next() {
        waiting.resolve();
        await wait(null, signal);
        return { done: true, value: undefined };
      },
      cleanup() {},
    }));
    const deadlines = manualDeadlines();
    const port = await listen(t, {
      producerFactory: tracked.factory,
      scheduleDeadline: deadlines.schedule,
    });
    const client = await openStream(port, 'hold');
    await client.waitFor(1);
    await waiting.promise;
    assert.deepEqual(
      client.frames.map((frame) => frame.event),
      ['start'],
    );
    client.close();
    await tracked.states[0].cancelled.promise;
    // A deadline already queued during disconnect must not append a terminal.
    deadlines.alarms[0].expire();
    await tracked.states[0].cleaned.promise;
    await client.closed;
    assert.equal(tracked.states[0].calls, 1);
    assert.equal(tracked.states[0].cleanups, 1);
    assert.equal(tracked.states[0].aborts, 1);
    assert.deepEqual(
      client.frames.map((frame) => frame.event),
      ['start'],
    );
  },
);

test(
  'producer construction failure still follows start then one private error',
  { timeout: 5000 },
  async (t) => {
    const port = await listen(t, {
      producerFactory: () => {
        throw new Error('private-constructor-detail');
      },
    });
    const client = await openStream(port, 'success');
    await client.closed;
    assertFrames(client.frames, 'success', 'error');
    assert.deepEqual(
      client.frames.map((frame) => frame.event),
      ['start', 'error'],
    );
    assert.doesNotMatch(JSON.stringify(client.frames), /private/);
  },
);

test('compiled producer loads fixed fixtures in extracted and repository layouts', () => {
  const temporary = mkdtempSync(join(tmpdir(), 'sse-stream-typescript-'));
  try {
    const extracted = join(temporary, 'package');
    const shared = join(temporary, 'shared');
    mkdirSync(join(extracted, 'dist'), { recursive: true });
    mkdirSync(shared);
    copyFileSync(
      new URL('./streaming.js', import.meta.url),
      join(extracted, 'dist', 'streaming.js'),
    );
    copyFileSync(new URL('../package.json', import.meta.url), join(extracted, 'package.json'));
    const encoded = JSON.stringify(fixtures);
    writeFileSync(join(extracted, 'fixtures.json'), encoded);
    // An unusable fallback proves that the extracted package's root wins.
    writeFileSync(join(shared, 'fixtures.json'), 'invalid fallback');
    const run = () =>
      JSON.parse(
        execFileSync(
          process.execPath,
          [
            '--input-type=module',
            '-e',
            `
      import { fixtures, teachingProducer } from './dist/streaming.js';
      const producer = teachingProducer(0)({
        scenario: 'success', runId: 'layout-test', signal: new AbortController().signal,
      });
      const texts = [];
      try {
        for (let item; !(item = await producer.next()).done;) texts.push(item.value);
      } finally {
        await producer.cleanup();
      }
      process.stdout.write(JSON.stringify({ fixtures, texts }));
    `,
          ],
          { cwd: extracted, encoding: 'utf8', timeout: 5000 },
        ),
      );
    assert.deepEqual(run(), { fixtures, texts: fixtures.texts });
    // The repository's dist folder has no local fixture and uses shared instead.
    rmSync(join(extracted, 'fixtures.json'));
    writeFileSync(join(shared, 'fixtures.json'), encoded);
    assert.deepEqual(run(), { fixtures, texts: fixtures.texts });
  } finally {
    rmSync(temporary, { recursive: true, force: true });
  }
});
