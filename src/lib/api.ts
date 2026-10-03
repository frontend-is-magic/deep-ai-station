export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, options);
  if (!response.ok) {
    let detail = '请求失败，请稍后重试';
    try {
      const data = await response.json();
      if (typeof data.detail === 'string') detail = data.detail;
    } catch {
      /* a proxy may return HTML */
    }
    throw new Error(detail);
  }
  return response.json();
}
export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}
export function createSSEParser(onEvent: (event: SSEEvent) => void) {
  let buffer = '';
  return (chunk: string) => {
    buffer += chunk;
    let end: number;
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const frame = buffer.slice(0, end);
      buffer = buffer.slice(end + 2);
      const lines = frame.split('\n');
      const event =
        lines
          .find((line) => line.startsWith('event:'))
          ?.slice(6)
          .trim() || 'message';
      const payload = lines
        .filter((line) => line.startsWith('data:'))
        .map((line) => line.slice(5).trimStart())
        .join('\n');
      if (payload) onEvent({ event, data: JSON.parse(payload) });
    }
  };
}
export async function streamRun(
  body: unknown,
  token: string,
  signal: AbortSignal,
  onEvent: (event: SSEEvent) => void,
) {
  const response = await fetch('/api/playground/run', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { 'X-Playground-Token': token } : {}),
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(typeof data.detail === 'string' ? data.detail : '运行失败，请重试');
  }
  if (!response.body) throw new Error('浏览器不支持流式响应');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parse = createSSEParser(onEvent);
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      parse(decoder.decode(value, { stream: true }));
    }
    parse(decoder.decode());
  } finally {
    // Parser/render errors must also close the response, releasing the provider stream.
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}
