import { expect, it } from 'vitest';
import {
  normalizeQuestion,
  parseResponseBody,
  ProtocolError,
  readResponseBody,
  validLessonId,
  type ApiRequest,
} from './protocol';

const search: ApiRequest = { method: 'POST', path: '/api/search', body: { question: '工具' } };
const detail: ApiRequest = { method: 'GET', path: '/api/lessons/tools', body: null };
const item = { id: 'tools', title: '工具契约' };
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

it('normalizes only the frozen White_Space set and preserves valid scalars', () => {
  expect(normalizeQuestion('\u0085\u3000工具\u00a0')).toBe('工具');
  expect(normalizeQuestion('\ufeff')).toBe('\ufeff');
  expect(normalizeQuestion('\u001c')).toBe('\u001c');
  expect(normalizeQuestion('😀'.repeat(500))).toBe('😀'.repeat(500));
  for (const invalid of ['', ' \n', '😀'.repeat(501), '\ud800', '\udfff', null])
    expect(normalizeQuestion(invalid)).toBeNull();
  expect(validLessonId('tools\n')).toBe(false);
});
it('parses real search, empty and detail schemas without inventing a body', () => {
  expect(parseResponseBody({ question: '工具', items: [item] }, 200, search)).toEqual({
    body: { question: '工具', items: [item] },
    outcome: 'success',
  });
  expect(parseResponseBody({ question: '工具', items: [] }, 200, search)).toEqual({
    body: { question: '工具', items: [] },
    outcome: 'empty',
  });
  expect(parseResponseBody(item, 200, detail)).toEqual({ body: item, outcome: 'success' });
});
it.each([
  [detail, 404, 'lesson_not_found', 'not_found'],
  [search, 422, 'invalid_input', 'invalid_input'],
  [search, 413, 'request_too_large', 'http_error'],
  [search, 415, 'unsupported_media_type', 'http_error'],
  [search, 503, 'repository_unavailable', 'http_error'],
  [detail, 503, 'repository_unavailable', 'http_error'],
] as const)(
  'accepts only the matched status/code for %o / %i',
  (request, status, error, outcome) => {
    expect(parseResponseBody({ error }, status, request)).toEqual({ body: { error }, outcome });
  },
);
it('rejects cross-request responses, shape drift and status/code mismatches', () => {
  const invalid: [unknown, number, ApiRequest][] = [
    [{ question: 'other', items: [] }, 200, search],
    [{ question: '工具', items: [item, item] }, 200, search],
    [{ question: '工具', items: [{ ...item, body: 'not part of API' }] }, 200, search],
    [{ question: '工具', items: [{ id: 'tools', title: '\ud800' }] }, 200, search],
    [{ id: 'http', title: 'HTTP API' }, 200, detail],
    [{ error: 'lesson_not_found' }, 404, search],
    [{ error: 'repository_unavailable' }, 422, search],
    [{ error: 'invalid_input', detail: 'private-input-fixture' }, 422, search],
    [{ error: 'request_failed' }, 500, detail],
  ];
  for (const [body, status, request] of invalid)
    expect(() => parseResponseBody(body, status, request)).toThrow(ProtocolError);
});
it('reads actual UTF-8 bytes across chunk boundaries', async () => {
  const bytes = new TextEncoder().encode(JSON.stringify({ question: '工具', items: [item] }));
  const response = new Response(
    new ReadableStream({
      start(controller) {
        for (const byte of bytes) controller.enqueue(Uint8Array.of(byte));
        controller.close();
      },
    }),
    { headers: { 'content-type': 'Application/JSON; charset=utf-8', 'content-length': '1' } },
  );
  expect(await readResponseBody(response, new AbortController().signal)).toEqual({
    question: '工具',
    items: [item],
  });
});
it('rejects actual bytes over 32 KiB without awaiting an uncooperative cancellation', async () => {
  let cancelled = false;
  const response = new Response(
    new ReadableStream({
      start(controller) {
        controller.enqueue(new Uint8Array(32769));
      },
      cancel() {
        cancelled = true;
        return new Promise(() => {});
      },
    }),
    { headers: { 'content-type': 'application/json', 'content-length': '1' } },
  );
  await expect(readResponseBody(response, new AbortController().signal)).rejects.toThrow(
    ProtocolError,
  );
  expect(cancelled).toBe(true);
  expect(response.body!.locked).toBe(false);
});
it('rejects non-JSON media, malformed JSON, BOM and illegal UTF-8', async () => {
  for (const response of [
    new Response('{}'),
    new Response('{', { headers: { 'content-type': 'application/json' } }),
    new Response(Uint8Array.of(0x80), { headers: { 'content-type': 'application/json' } }),
    new Response('\ufeff{}', { headers: { 'content-type': 'application/json' } }),
  ]) {
    await expect(readResponseBody(response, new AbortController().signal)).rejects.toThrow(
      ProtocolError,
    );
  }
  expect(
    await readResponseBody(json({ error: 'invalid_input' }, 422), new AbortController().signal),
  ).toEqual({ error: 'invalid_input' });
});
it('abort cancels a pending body read and releases its reader', async () => {
  let cancelled = false;
  const response = new Response(
    new ReadableStream({
      cancel() {
        cancelled = true;
      },
    }),
    { headers: { 'content-type': 'application/json' } },
  );
  const abort = new AbortController();
  const pending = readResponseBody(response, abort.signal);
  abort.abort();
  await expect(pending).rejects.toBeDefined();
  expect(cancelled).toBe(true);
  expect(response.body!.locked).toBe(false);
});
