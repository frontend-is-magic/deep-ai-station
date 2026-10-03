import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createApp } from './app.js';
const cases: { name: string; body: unknown; status: number; mode?: string; source?: string }[] =
  JSON.parse(readFileSync(new URL('../contract-cases.json', import.meta.url), 'utf8'));
for (const fixture of cases)
  test(fixture.name, async () => {
    const app = createApp({
      env: {},
      fetcher: async () => {
        throw new Error('must not call provider');
      },
    });
    const response = await app.request('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fixture.body),
    });
    assert.equal(response.status, fixture.status);
    const result = await response.json();
    if (fixture.mode) {
      assert.equal(result.mode, fixture.mode);
      assert.ok(result.run_id);
      assert.equal(result.usage, null);
      if (fixture.source)
        assert.ok(result.sources.some((source: { id: string }) => source.id === fixture.source));
      else assert.deepEqual(result.sources, []);
    }
  });
test('bounded wire inputs', async () => {
  const app = createApp({ env: {} });
  for (const body of ['{', '[]', '"bad"', JSON.stringify({ prompt: 'x'.repeat(17000) })]) {
    const response = await app.request('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
    });
    assert.equal(response.status, Buffer.byteLength(body) > 16384 ? 413 : 422);
    assert.deepEqual(Object.keys(await response.json()), ['error']);
  }
});
test('live fixed endpoint, token budget, known usage and quota', async () => {
  let calls = 0;
  const app = createApp({
    env: { OPENAI_API_KEY: 'test-only', PLAYGROUND_ACCESS_TOKEN: 'test-access' },
    fetcher: async (url, options) => {
      calls++;
      assert.equal(url, 'https://api.openai.com/v1/chat/completions');
      assert.equal(JSON.parse(String(options?.body)).max_completion_tokens, 800);
      return Response.json({
        choices: [{ finish_reason: 'stop', message: { content: '实际 API 资料回答' } }],
        usage: { total_tokens: 12, unexpected: 'discard' },
      });
    },
  });
  for (let step = 0; step < 11; step++) {
    const response = await app.request('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Playground-Token': 'test-access' },
      body: JSON.stringify({ prompt: 'API', mode: 'openai' }),
    });
    assert.equal(response.status, step < 10 ? 200 : 429);
    if (step < 10) assert.deepEqual((await response.json()).usage, { total_tokens: 12 });
  }
  assert.equal(calls, 10);
});
test('provider invalid or incomplete responses are sanitized', async () => {
  for (const body of [
    '{}',
    '{"choices":[]}',
    JSON.stringify({ choices: [{ finish_reason: 'length', message: { content: 'partial' } }] }),
    'x'.repeat(1000001),
  ]) {
    const app = createApp({
      env: { OPENAI_API_KEY: 'test-only', PLAYGROUND_ACCESS_TOKEN: 'test-access' },
      fetcher: async () => new Response(body),
    });
    const response = await app.request('/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Playground-Token': 'test-access' },
      body: JSON.stringify({ prompt: 'API', mode: 'openai' }),
    });
    assert.equal(response.status, 502);
    assert.deepEqual(await response.json(), { error: 'provider_invalid_response' });
  }
});

test('cancel aborts the provider request', async () => {
  const controller = new AbortController();
  let entered!: () => void;
  const waiting = new Promise<void>((resolve) => {
    entered = resolve;
  });
  let providerSignal: AbortSignal | null = null;
  const app = createApp({
    env: { OPENAI_API_KEY: 'test-only', PLAYGROUND_ACCESS_TOKEN: 'test-access' },
    fetcher: async (_url, options) => {
      providerSignal = options!.signal!;
      entered();
      return new Promise((_resolve, reject) =>
        options!.signal!.addEventListener(
          'abort',
          () => reject(new DOMException('cancelled', 'AbortError')),
          { once: true },
        ),
      );
    },
  });
  const pending = app.request(
    new Request('http://local/api/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Playground-Token': 'test-access' },
      body: JSON.stringify({ prompt: 'API', mode: 'openai' }),
      signal: controller.signal,
    }),
  );
  await waiting;
  controller.abort();
  assert.equal((await pending).status, 499);
  assert.equal((providerSignal as AbortSignal | null)?.aborted, true);
});
