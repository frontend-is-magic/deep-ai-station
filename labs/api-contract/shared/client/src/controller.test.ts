import { afterEach, expect, it, vi } from 'vitest';
import { createRequestController } from './controller';
import { parseLocation, type ValidTarget } from './navigation';
import { recordNotice } from './protocol';

const target = (question: string) =>
  parseLocation({
    pathname: '/search',
    search: '?' + new URLSearchParams({ q: question }),
    hash: '',
  }) as ValidTarget;
const response = (question: string) =>
  new Response(JSON.stringify({ question, items: [] }), {
    headers: { 'Content-Type': 'application/json' },
  });
const flush = async () => {
  for (let index = 0; index < 16; index++) await Promise.resolve();
};
afterEach(() => {
  vi.useRealTimers();
});

it('StrictMode setup-dispose-setup dispatches only the still-owned request', async () => {
  const fetcher = vi.fn(async () => response('工具'));
  const first = createRequestController({ fetch: fetcher });
  first.run(target('工具'));
  first.dispose();
  const second = createRequestController({ fetch: fetcher });
  second.run(target('工具'));
  await flush();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(first.getSnapshot()).toEqual({ record: null });
  expect(second.getSnapshot().record?.outcome).toBe('empty');
  second.dispose();
});
it('creates the real fixed request and stable snapshots, then explicit same-query rerun', async () => {
  const fetcher = vi.fn(async () => response('工具'));
  const controller = createRequestController({ fetch: fetcher });
  expect(controller.getSnapshot()).toBe(controller.getSnapshot());
  controller.run(target('工具'));
  await flush();
  expect(fetcher.mock.calls[0]).toMatchObject([
    '/api/search',
    {
      method: 'POST',
      credentials: 'omit',
      redirect: 'error',
      body: '{"question":"工具"}',
      headers: { 'Content-Type': 'application/json' },
    },
  ]);
  expect(controller.getSnapshot().record).toEqual({
    request: { method: 'POST', path: '/api/search', body: { question: '工具' } },
    response: { status: 200, body: { question: '工具', items: [] } },
    outcome: 'empty',
  });
  controller.run(target('工具'));
  await flush();
  expect(fetcher).toHaveBeenCalledTimes(2);
  controller.dispose();
});
it('a late fetch ignoring abort cannot replace a newer query', async () => {
  let release!: (response: Response) => void;
  let oldSignal!: AbortSignal;
  const fetcher = vi.fn<typeof fetch>((_input, options) => {
    if (fetcher.mock.calls.length === 1) {
      oldSignal = options!.signal!;
      return new Promise((resolve) => {
        release = resolve;
      });
    }
    return Promise.resolve(response('HTTP'));
  });
  const controller = createRequestController({ fetch: fetcher });
  controller.run(target('工具'));
  await flush();
  controller.run(target('HTTP'));
  await flush();
  const newer = controller.getSnapshot();
  expect(oldSignal.aborted).toBe(true);
  release(response('工具'));
  await flush();
  expect(controller.getSnapshot()).toBe(newer);
  expect(newer.record?.request.body).toEqual({ question: 'HTTP' });
  controller.dispose();
});
it('navigation during a pending body read keeps new loading and releases the old reader', async () => {
  let cancelled = false;
  let releaseNew!: (value: Response) => void;
  const old = new Response(
    new ReadableStream({
      cancel() {
        cancelled = true;
      },
    }),
    { headers: { 'Content-Type': 'application/json' } },
  );
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(old)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          releaseNew = resolve;
        }),
    );
  const controller = createRequestController({ fetch: fetcher });
  controller.run(target('工具'));
  await flush();
  expect(old.body!.locked).toBe(true);
  controller.run(target('HTTP'));
  await flush();
  expect(cancelled).toBe(true);
  expect(old.body!.locked).toBe(false);
  expect(controller.getSnapshot().record?.outcome).toBe('loading');
  expect(controller.getSnapshot().record?.request.body).toEqual({ question: 'HTTP' });
  releaseNew(response('HTTP'));
  await flush();
  expect(controller.getSnapshot().record?.outcome).toBe('empty');
  controller.dispose();
});
it('the total deadline releases loading even if fetch ignores its signal', async () => {
  vi.useFakeTimers();
  let release!: (value: Response) => void;
  const controller = createRequestController({
    fetch: () =>
      new Promise((resolve) => {
        release = resolve;
      }),
    now: Date.now,
  });
  controller.run(target('工具'));
  await flush();
  await vi.advanceTimersByTimeAsync(5000);
  const expired = controller.getSnapshot();
  expect(expired.record).toMatchObject({
    outcome: 'timeout',
    response: { status: null, body: null },
  });
  expect(vi.getTimerCount()).toBe(0);
  release(response('工具'));
  await flush();
  expect(controller.getSnapshot()).toBe(expired);
  controller.dispose();
});
it('one deadline includes body reading and retains only actually seen HTTP status', async () => {
  vi.useFakeTimers();
  let cancelled = false;
  const body = new Response(
    new ReadableStream({
      cancel() {
        cancelled = true;
      },
    }),
    { headers: { 'content-type': 'application/json' } },
  );
  const controller = createRequestController({ fetch: async () => body, now: Date.now });
  controller.run(target('工具'));
  await flush();
  await vi.advanceTimersByTimeAsync(5000);
  expect(controller.getSnapshot().record).toMatchObject({
    outcome: 'timeout',
    response: { status: 200, body: null },
  });
  expect(cancelled).toBe(true);
  expect(vi.getTimerCount()).toBe(0);
  controller.dispose();
});
it('success, rejection and disposal clear owned deadline timers', async () => {
  vi.useFakeTimers();
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(response('工具'))
    .mockRejectedValueOnce(new Error('private-input-fixture'))
    .mockImplementationOnce(() => new Promise(() => {}));
  const controller = createRequestController({ fetch: fetcher, now: Date.now });
  controller.run(target('工具'));
  await flush();
  expect(vi.getTimerCount()).toBe(0);
  controller.run(target('HTTP'));
  await flush();
  expect(controller.getSnapshot().record).toMatchObject({
    outcome: 'network_error',
    response: { status: null, body: null },
  });
  expect(JSON.stringify(controller.getSnapshot())).not.toContain('private-input-fixture');
  expect(recordNotice(controller.getSnapshot().record!)).toBe('连接失败，请检查本地 API 后重试。');
  expect(vi.getTimerCount()).toBe(0);
  controller.run(target('工具'));
  await flush();
  expect(vi.getTimerCount()).toBe(1);
  controller.dispose();
  expect(vi.getTimerCount()).toBe(0);
  expect(controller.getSnapshot()).toEqual({ record: null });
});
it('an unsubmitted target clears the record and sends nothing', async () => {
  const fetcher = vi.fn(async () => response('工具'));
  const controller = createRequestController({ fetch: fetcher });
  controller.run({ kind: 'search', question: null, canonical: '/search' });
  await flush();
  expect(fetcher).not.toHaveBeenCalled();
  expect(controller.getSnapshot()).toEqual({ record: null });
  controller.dispose();
});
it('detail 404 is an observed API result and bad bodies remain untrusted', async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(
      new Response('{"error":"lesson_not_found"}', {
        status: 404,
        headers: { 'content-type': 'application/json' },
      }),
    )
    .mockResolvedValueOnce(
      new Response('{"private":"private-input-fixture"}', {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    );
  const controller = createRequestController({ fetch: fetcher });
  controller.run({ kind: 'detail', id: 'missing', question: null, canonical: '/lessons/missing' });
  await flush();
  expect(controller.getSnapshot().record).toMatchObject({
    outcome: 'not_found',
    response: { status: 404, body: { error: 'lesson_not_found' } },
  });
  controller.run(target('工具'));
  await flush();
  expect(controller.getSnapshot().record).toMatchObject({
    outcome: 'protocol_error',
    response: { status: 200, body: null },
  });
  expect(JSON.stringify(controller.getSnapshot())).not.toContain('private-input-fixture');
  controller.dispose();
});
