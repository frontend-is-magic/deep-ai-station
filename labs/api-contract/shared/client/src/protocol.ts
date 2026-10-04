export interface LessonSummary {
  id: string;
  title: string;
}
export interface SearchResponse {
  question: string;
  items: LessonSummary[];
}
export interface ApiFailure {
  error: string;
}
export interface ApiRequest {
  method: 'POST' | 'GET';
  path: string;
  body: { question: string } | null;
}
export type ResponseBody = SearchResponse | LessonSummary | ApiFailure;
export type Outcome =
  | 'loading'
  | 'success'
  | 'empty'
  | 'not_found'
  | 'invalid_input'
  | 'http_error'
  | 'network_error'
  | 'timeout'
  | 'protocol_error';
export interface RequestRecord {
  request: ApiRequest;
  response: { status: number | null; body: ResponseBody | null };
  outcome: Outcome;
}
export const MAX_RESPONSE_BYTES = 32768;
export const REQUEST_DEADLINE_MS = 5000;
export class ProtocolError extends Error {
  constructor() {
    super('Invalid API contract response');
  }
}

function scalarString(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    !Array.from(value).some((char) => {
      const point = char.codePointAt(0)!;
      return point >= 0xd800 && point <= 0xdfff;
    })
  );
}
export function validLessonId(value: unknown): value is string {
  return typeof value === 'string' && /^[a-z0-9][a-z0-9-]{0,63}$/.test(value);
}
export function normalizeQuestion(value: unknown): string | null {
  if (!scalarString(value)) return null;
  const question = value.replace(/^\p{White_Space}+|\p{White_Space}+$/gu, '');
  const count = Array.from(question).length;
  if (count < 1 || count > 500) return null;
  if (new TextEncoder().encode(JSON.stringify({ question })).byteLength > 4096) return null;
  return question;
}
function exact(value: unknown, keys: string[]): value is Record<string, unknown> {
  return (
    value !== null &&
    typeof value === 'object' &&
    !Array.isArray(value) &&
    Object.keys(value).length === keys.length &&
    keys.every((key) => Object.hasOwn(value, key))
  );
}
function summary(value: unknown): LessonSummary {
  if (
    !exact(value, ['id', 'title']) ||
    !validLessonId(value.id) ||
    !scalarString(value.title) ||
    !value.title.length
  )
    throw new ProtocolError();
  return { id: value.id, title: value.title };
}
export function parseResponseBody(
  value: unknown,
  status: number,
  request: ApiRequest,
): { body: ResponseBody; outcome: Outcome } {
  if (status === 200) {
    if (request.method === 'POST') {
      if (
        !exact(value, ['question', 'items']) ||
        value.question !== request.body?.question ||
        !Array.isArray(value.items)
      )
        throw new ProtocolError();
      const items = Array.from(value.items, summary);
      if (new Set(items.map((item) => item.id)).size !== items.length) throw new ProtocolError();
      return {
        body: { question: request.body!.question, items },
        outcome: items.length ? 'success' : 'empty',
      };
    }
    const item = summary(value);
    if (request.path !== `/api/lessons/${item.id}`) throw new ProtocolError();
    return { body: item, outcome: 'success' };
  }
  if (!exact(value, ['error'])) throw new ProtocolError();
  const code = value.error;
  if (request.method === 'GET' && status === 404 && code === 'lesson_not_found')
    return { body: { error: code }, outcome: 'not_found' };
  if (request.method === 'POST') {
    if (status === 422 && code === 'invalid_input')
      return { body: { error: code }, outcome: 'invalid_input' };
    if (
      (status === 413 && code === 'request_too_large') ||
      (status === 415 && code === 'unsupported_media_type')
    )
      return { body: { error: code }, outcome: 'http_error' };
  }
  if (status === 503 && code === 'repository_unavailable')
    return { body: { error: code }, outcome: 'http_error' };
  throw new ProtocolError();
}

export function discardResponse(response: Response): void {
  void response.body?.cancel().catch(() => {});
}
export async function readResponseBody(response: Response, signal: AbortSignal): Promise<unknown> {
  const media = (response.headers.get('content-type') ?? '')
    .split(';', 1)[0]
    .replace(/^[ \t]+|[ \t]+$/g, '')
    .replace(/[A-Z]/g, (char) => char.toLowerCase());
  if (response.redirected || media !== 'application/json' || !response.body) {
    discardResponse(response);
    throw new ProtocolError();
  }
  const reader = response.body.getReader();
  let released = false;
  const release = () => {
    if (released) return;
    released = true;
    void reader.cancel().catch(() => {});
    try {
      reader.releaseLock();
    } catch {
      /* The pending read will settle after cancellation. */
    }
  };
  signal.addEventListener('abort', release, { once: true });
  try {
    if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
    const chunks: Uint8Array[] = [];
    let length = 0;
    while (true) {
      const chunk = await reader.read();
      if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
      if (chunk.done) break;
      length += chunk.value.byteLength;
      if (length > MAX_RESPONSE_BYTES) throw new ProtocolError();
      chunks.push(chunk.value);
    }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    try {
      const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes);
      return JSON.parse(text) as unknown;
    } catch {
      throw new ProtocolError();
    }
  } finally {
    signal.removeEventListener('abort', release);
    release();
  }
}

export function recordNotice(record: RequestRecord): string {
  switch (record.outcome) {
    case 'loading':
      return record.request.method === 'POST' ? '正在查询…' : '正在读取资料摘要…';
    case 'success':
      return record.response.body && 'items' in record.response.body
        ? `找到 ${record.response.body.items.length} 条资料`
        : '资料摘要已读取。';
    case 'empty':
      return '没有匹配资料；这是合法的空结果。';
    case 'not_found':
      return '资料不存在（404）。';
    case 'invalid_input':
      return '服务端拒绝了输入（422），请修改问题后重新提交。';
    case 'network_error':
      return '连接失败，请检查本地 API 后重试。';
    case 'timeout':
      return '请求超时，请重试。';
    case 'protocol_error':
      return '服务响应不符合实验契约。';
    case 'http_error':
      if (record.response.status === 413) return '请求超过服务端字节上限（413）。';
      if (record.response.status === 415) return '服务端不接受此请求格式（415）。';
      return '资料服务暂不可用（503），请稍后重试。';
  }
}
