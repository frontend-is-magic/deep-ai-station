// Bounded teaching protocol over standard SSE framing; no automatic reconnect.
export class ProtocolError extends Error {
  constructor() {
    super('流协议无效或未完成');
    this.name = 'ProtocolError';
  }
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const messages = {
  producer_failed: '教学数据源失败',
  deadline_exceeded: '教学运行超时',
};

export async function consumeSse(response, scenario, onEvent) {
  const reader = response.body?.getReader();
  if (!reader) throw new ProtocolError();
  let eof = false;
  let line = '';
  let skipLF = false;
  let event = '';
  let data = [];
  let frameSize = 0;
  let receivedBytes = 0;
  let eventCount = 0;
  let runId;
  let seq = 0;
  let terminal;
  const decoder = new TextDecoder('utf-8', { fatal: true });

  function dispatch() {
    if (!data.length) return;
    if (terminal || ++eventCount > 16) throw new ProtocolError();
    let value;
    try {
      value = JSON.parse(data.join('\n'));
    } catch {
      throw new ProtocolError();
    }
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new ProtocolError();
    if (event === 'start') {
      if (
        runId ||
        typeof value.run_id !== 'string' ||
        !uuid.test(value.run_id) ||
        value.scenario !== scenario
      ) {
        throw new ProtocolError();
      }
      runId = value.run_id;
    } else {
      if (!runId || value.run_id !== runId) throw new ProtocolError();
      if (event === 'delta') {
        if (
          value.seq !== seq + 1 ||
          value.seq > 3 ||
          typeof value.text !== 'string' ||
          !value.text.length
        ) {
          throw new ProtocolError();
        }
        seq = value.seq;
      } else if (event === 'done') {
        if (scenario !== 'success' || value.seq !== 3 || seq !== 3) throw new ProtocolError();
        terminal = { kind: 'done', runId };
      } else if (event === 'error') {
        if (typeof value.code !== 'string' || !Object.hasOwn(messages, value.code))
          throw new ProtocolError();
        value = { run_id: runId, code: value.code, message: messages[value.code] };
        terminal = { kind: 'error', runId, code: value.code, message: value.message };
      } else {
        throw new ProtocolError();
      }
    }
    onEvent({ event, data: value });
  }

  function completeLine() {
    if (line === '') {
      dispatch();
      event = '';
      data = [];
      frameSize = 0;
    } else if (!line.startsWith(':')) {
      const colon = line.indexOf(':');
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? '' : line.slice(colon + 1);
      if (value.startsWith(' ')) value = value.slice(1);
      if (field === 'event') event = value;
      if (field === 'data') data.push(value);
    }
    line = '';
  }

  function accept(text) {
    for (const char of text) {
      if (skipLF) {
        skipLF = false;
        if (char === '\n') continue;
      }
      if (++frameSize > 16384) throw new ProtocolError();
      if (char === '\r' || char === '\n') {
        completeLine();
        skipLF = char === '\r';
      } else {
        line += char;
      }
    }
  }

  try {
    if (
      !response.ok ||
      response.headers.get('content-type')?.split(';')[0] !== 'text/event-stream'
    ) {
      throw new ProtocolError();
    }
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        eof = true;
        try {
          accept(decoder.decode());
        } catch {
          throw new ProtocolError();
        }
        if (!terminal || line || data.length || event) throw new ProtocolError();
        return terminal;
      }
      receivedBytes += value.byteLength;
      if (receivedBytes > 65536) throw new ProtocolError();
      let text;
      try {
        text = decoder.decode(value, { stream: true });
      } catch {
        throw new ProtocolError();
      }
      accept(text);
    }
  } finally {
    if (!eof) await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
