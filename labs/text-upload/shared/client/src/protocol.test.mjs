import assert from 'node:assert/strict';
import test from 'node:test';
import {
  attachmentName,
  contentHeadersMatch,
  decodePreview,
  inspectFile,
  noticeText,
  parseApiError,
  parseListing,
  parseMetadata,
  readBoundedBody,
  readProtocolJson,
} from './protocol.mjs';

const record = {
  id: 'doc-000001',
  filename: 'Guide.MD',
  media_type: 'text/markdown',
  size_bytes: 3,
  sha256: 'a'.repeat(64),
};
const headers = {
  'Content-Type': 'application/json; charset=utf-8',
  'Cache-Control': 'no-store',
  'X-Content-Type-Options': 'nosniff',
};
const signal = () => new AbortController().signal;

test('metadata is copied, frozen and matches exact wire shape', () => {
  const parsed = parseMetadata(record);
  assert.deepEqual(parsed, record);
  assert.notEqual(parsed, record);
  assert.ok(Object.isFrozen(parsed));
  assert.equal(attachmentName(parsed), 'upload-doc-000001.md');
});
for (const [name, patch] of Object.entries({
  zero_id: { id: 'doc-000000' },
  short_id: { id: 'doc-1' },
  trailing_id: { id: 'doc-000001\n' },
  filename_lf: { filename: 'Guide.MD\n' },
  path: { filename: '../a.md' },
  mismatch_media: { media_type: 'text/plain' },
  numeric_string: { size_bytes: '3' },
  bool_size: { size_bytes: true },
  fractional: { size_bytes: 1.5 },
  oversized: { size_bytes: 4097 },
  empty: { size_bytes: 0 },
  uppercase_sha: { sha256: 'A'.repeat(64) },
  sha_lf: { sha256: 'a'.repeat(64) + '\n' },
  extra: { owner: 'alice' },
}))
  test(`metadata rejects ${name}`, () =>
    assert.equal(parseMetadata({ ...record, ...patch }), null));

test('listing checks bounded totals and sorted unique ids', () => {
  assert.deepEqual(parseListing({ documents: [] }), []);
  const second = { ...record, id: 'doc-000003' };
  assert.equal(parseListing({ documents: [record, second] }).length, 2);
  for (const documents of [
    [record, record],
    [second, record],
    Array(4).fill(record),
    [
      { ...record, size_bytes: 4096 },
      { ...second, size_bytes: 4096 },
      { ...second, id: 'doc-000005', size_bytes: 1 },
    ],
  ]) {
    assert.equal(parseListing({ documents }), null);
  }
  assert.equal(parseListing({ documents: [], total: 0 }), null);
});

test('file inspection neither trusts MIME nor trims/renames the original name', () => {
  assert.deepEqual(inspectFile({ name: 'Notes.TxT', size: 4096, type: 'image/png' }), {
    ok: true,
    file: { name: 'Notes.TxT', size: 4096, mediaType: 'text/plain' },
  });
  assert.equal(inspectFile({ name: 'a.md', size: 0 }).ok, true);
  for (const name of ['a.txt\n', ' a.txt', 'a.txt ', '中文.txt', 'a.b.txt', 'a%2etxt', '/a.txt'])
    assert.equal(inspectFile({ name, size: 1 }).ok, false);
  for (const size of [4097, NaN, Infinity, -1, 1.2, '1'])
    assert.equal(inspectFile({ name: 'a.txt', size }).ok, false);
});

test('known errors require the exact status, shape and fixed code', () => {
  assert.deepEqual(parseApiError(503, { error: 'result_unconfirmed' }), {
    status: 503,
    code: 'result_unconfirmed',
  });
  assert.equal(parseApiError(409, { error: 'result_unconfirmed' }), null);
  assert.equal(parseApiError(503, { error: 'repository_unavailable', detail: 'PRIVATE' }), null);
  assert.equal(parseApiError(503, { error: 'PRIVATE' }), null);
  assert.equal(parseApiError(503, { error: '__proto__' }), null);
  assert.equal(noticeText('PRIVATE').includes('PRIVATE'), false);
});

test('content headers are tied to the exact selected id and media', () => {
  const valid = {
    ...headers,
    'Content-Type': 'application/octet-stream',
    'Content-Disposition': 'attachment; filename="upload-doc-000001.md"',
  };
  assert.equal(contentHeadersMatch(new Response('abc', { headers: valid }), record), true);
  for (const patch of [
    { 'Content-Disposition': 'attachment; filename="../../PRIVATE.md"' },
    { 'Content-Type': 'text/html' },
    { 'Cache-Control': 'public' },
    { 'X-Content-Type-Options': '' },
  ])
    assert.equal(
      contentHeadersMatch(new Response('abc', { headers: { ...valid, ...patch } }), record),
      false,
    );
  assert.equal(
    contentHeadersMatch({ headers: new Headers(valid), redirected: true }, record),
    false,
  );
});

test('JSON bytes are strict UTF-8, header checked and bounded before parsing', async () => {
  assert.deepEqual(
    await readProtocolJson(new Response('{"documents":[]}', { headers }), signal()),
    { documents: [] },
  );
  for (const response of [
    new Response(new Uint8Array([0x80]), { headers }),
    new Response('<html>PRIVATE</html>', { headers }),
    new Response(' '.repeat(4097), { headers }),
    new Response('{}', { headers: { ...headers, 'Content-Type': 'text/html' } }),
  ]) {
    await assert.rejects(
      readProtocolJson(response, signal()),
      (error) => error.message === 'invalid_response',
    );
  }
});

test('bounded body cancels an oversized stream without reading its remainder', async () => {
  let pulls = 0,
    cancels = 0;
  const body = new ReadableStream(
    {
      pull(controller) {
        pulls++;
        controller.enqueue(new Uint8Array(4097));
      },
      cancel() {
        cancels++;
      },
    },
    { highWaterMark: 0 },
  );
  await assert.rejects(readBoundedBody(new Response(body), 4096, signal()));
  assert.equal(pulls, 1);
  assert.equal(cancels, 1);
});

test('pending body abort triggers cancellation without awaiting a hanging cancel', async () => {
  let cancels = 0;
  const body = new ReadableStream({
    cancel() {
      cancels++;
      return new Promise(() => {});
    },
  });
  const abort = new AbortController();
  const pending = readBoundedBody(new Response(body), 4096, abort.signal);
  abort.abort();
  await assert.rejects(pending);
  assert.equal(cancels, 1);
});

test('preview preserves UTF-8 characters and CRLF while rejecting invalid bytes', () => {
  const source = '中文\r\n😀e\u0301<script>unsafe</script>[link](https://example.invalid)';
  assert.equal(decodePreview(new TextEncoder().encode(source)), source);
  assert.equal(decodePreview(new Uint8Array([0xc0, 0x80])), null);
});
