import assert from 'node:assert/strict';
import test from 'node:test';
import { consumeSse, ProtocolError } from './stream.mjs';

const run_id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const encoder = new TextEncoder();
const frame = (event, value) =>
  `event: ${event}\ndata: ${JSON.stringify({ run_id, ...value })}\n\n`;
const start = (scenario = 'success') => frame('start', { scenario });
const delta = (seq = 1, text = '理解 ') => frame('delta', { seq, text });
const done = (seq = 3) => frame('done', { seq });
const deltas = () => delta(1, '理解 ') + delta(2, '流式 ') + delta(3, '响应 🌱');
function response(chunks, options = {}) {
  let i = 0;
  return new Response(
    new ReadableStream({
      pull(controller) {
        if (i < chunks.length) controller.enqueue(chunks[i++]);
        else if (!options.hold) controller.close();
      },
      cancel() {
        options.cancel?.();
      },
    }),
    { headers: { 'Content-Type': options.type || 'text/event-stream; charset=utf-8' } },
  );
}
const bytes = (text) => [encoder.encode(text)];
const receive = (source, scenario = 'success') => {
  const events = [];
  return { events, result: consumeSse(source, scenario, (event) => events.push(event)) };
};

for (const newline of ['\n', '\r\n', '\r']) {
  test(`UTF-8 single-byte chunks and ${JSON.stringify(newline)} framing`, async () => {
    const wire = (': heartbeat\n\n' + start() + deltas() + done()).replaceAll('\n', newline);
    const chunks = [...encoder.encode(wire)].map((value) => Uint8Array.of(value));
    const { events, result } = receive(response(chunks));
    assert.equal((await result).kind, 'done');
    assert.deepEqual(
      events.map((e) => e.event),
      ['start', 'delta', 'delta', 'delta', 'done'],
    );
    assert.equal(
      events
        .filter((event) => event.event === 'delta')
        .map((event) => event.data.text)
        .join(''),
      '理解 流式 响应 🌱',
    );
  });
}
test('all events in one network chunk and multiple data lines', async () => {
  const multiline = `event: delta\ndata: {"run_id":"${run_id}",\ndata: "seq":1,"text":"你好"}\n\n`;
  const { events, result } = receive(
    response(bytes(start() + multiline + delta(2, ' ') + delta(3, '🌱') + done())),
  );
  assert.equal((await result).kind, 'done');
  assert.equal(events[1].data.text, '你好');
});
test('partial JSON across three chunks only dispatches complete events', async () => {
  const wire = encoder.encode(start() + deltas() + done());
  const { result, events } = receive(
    response([wire.slice(0, 17), wire.slice(17, 153), wire.slice(153)]),
  );
  await result;
  assert.equal(events.length, 5);
});
for (const code of ['producer_failed', 'deadline_exceeded']) {
  test(`${code} retains deltas and replaces arbitrary server error text`, async () => {
    const scenario = code === 'producer_failed' ? 'error' : 'timeout';
    const wire =
      start(scenario) +
      delta() +
      frame('error', { code, message: 'raw internal detail must stay hidden' });
    const { result, events } = receive(response(bytes(wire)), scenario);
    const terminal = await result;
    assert.equal(terminal.kind, 'error');
    assert.equal(terminal.code, code);
    assert.equal(events[1].data.text, '理解 ');
    assert.ok(!JSON.stringify(events).includes('internal'));
    assert.ok(!terminal.message.includes('internal'));
  });
}
for (const [name, wire] of [
  ['EOF without terminal', start() + delta()],
  ['EOF inside a terminal frame', start() + deltas() + done().slice(0, -1)],
  ['delta before start', delta() + done()],
  ['duplicate start', start() + start() + deltas() + done()],
  [
    'mismatched run',
    start() + frame('delta', { run_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', seq: 1, text: 'x' }),
  ],
  ['out-of-order seq', start() + delta(2) + done(2)],
  ['duplicate seq', start() + delta() + delta() + done()],
  ['terminal count mismatch', start() + delta() + done(2)],
  ['duplicate terminal', start() + deltas() + done() + done()],
  ['delta after terminal', start() + deltas() + done() + delta(2)],
  ['unknown event', start() + frame('surprise', {})],
  ['malformed JSON', start() + 'event: delta\ndata: {\n\n'],
  ['non-string run ID', frame('start', { run_id: [run_id], scenario: 'success' })],
  ['unknown error code', start() + frame('error', { code: '__proto__' })],
  ['non-string error code', start() + frame('error', { code: ['producer_failed'] })],
  ['wrong scenario', start('hold') + delta() + done()],
]) {
  test(`reject ${name}`, async () => {
    await assert.rejects(
      consumeSse(response(bytes(wire)), 'success', () => {}),
      ProtocolError,
    );
  });
}
test('protocol failure cancels the underlying reader', async () => {
  let cancelled = 0;
  await assert.rejects(
    consumeSse(
      response(bytes(delta()), { hold: true, cancel: () => cancelled++ }),
      'success',
      () => {},
    ),
    ProtocolError,
  );
  assert.equal(cancelled, 1);
});
test('wrong media type cancels unread response body', async () => {
  let cancelled = 0;
  await assert.rejects(
    consumeSse(
      response(bytes('{}'), { type: 'application/json', hold: true, cancel: () => cancelled++ }),
      'success',
      () => {},
    ),
    ProtocolError,
  );
  assert.equal(cancelled, 1);
});
test('malformed UTF-8 and oversized unfinished frame are bounded', async () => {
  await assert.rejects(
    consumeSse(response([Uint8Array.of(0xff)]), 'success', () => {}),
    ProtocolError,
  );
  await assert.rejects(
    consumeSse(response(bytes('data: ' + 'x'.repeat(17000))), 'success', () => {}),
    ProtocolError,
  );
  await assert.rejects(
    consumeSse(response(bytes(':'.repeat(65537))), 'success', () => {}),
    ProtocolError,
  );
});
test('extra delta cannot grow fixed teaching output unboundedly', async () => {
  const wire =
    start() + Array.from({ length: 16 }, (_, i) => delta(i + 1, 'x')).join('') + done(16);
  const { events, result } = receive(response(bytes(wire)));
  await assert.rejects(result, ProtocolError);
  assert.equal(events.length, 4);
});

for (const scenario of ['success', 'error', 'timeout', 'hold']) {
  test(`reject premature or wrong-scenario done for ${scenario}`, async () => {
    const body =
      start(scenario) +
      (scenario === 'success' ? delta() : deltas()) +
      done(scenario === 'success' ? 1 : 3);
    await assert.rejects(
      consumeSse(response(bytes(body)), scenario, () => {}),
      ProtocolError,
    );
  });
}
