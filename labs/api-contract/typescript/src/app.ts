import { Hono } from 'hono';
import { asciiLower, MemoryLessonRepository, type LessonRepository } from './repository.js';
import { ApiError, LessonService } from './service.js';

const MAX_BODY_BYTES = 4096;

async function readLimitedBody(request: Request): Promise<Uint8Array> {
  const reader = request.body?.getReader();
  if (!reader) return new Uint8Array();
  const chunks: Uint8Array[] = [];
  let bytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > MAX_BODY_BYTES) throw new ApiError(413, 'request_too_large');
      chunks.push(value);
    }
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(422, 'invalid_input');
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
  const body = new Uint8Array(bytes);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}

export function createApp(repository: LessonRepository = new MemoryLessonRepository()) {
  const service = new LessonService(repository);
  const app = new Hono();
  app.onError((error, c) =>
    error instanceof ApiError
      ? c.json({ error: error.code }, error.status)
      : c.json({ error: 'request_failed' }, 500),
  );
  app.notFound((c) => c.json({ error: 'route_not_found' }, 404));
  app.get('/health', (c) => c.json({ status: 'ok', lab: 'api-contract-v1' }));
  app.get('/lessons/:id', async (c) => c.json(await service.find(c.req.param('id'))));
  app.post('/search', async (c) => {
    // Limit the real stream before inspecting media type or decoding JSON.
    const bytes = await readLimitedBody(c.req.raw);
    const media = asciiLower(
      (c.req.header('content-type') ?? '').split(';', 1)[0].replace(/^[ \t]+|[ \t]+$/g, ''),
    );
    if (media !== 'application/json') throw new ApiError(415, 'unsupported_media_type');
    let input: unknown;
    try {
      input = JSON.parse(new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes));
    } catch {
      throw new ApiError(422, 'invalid_input');
    }
    return c.json(await service.search(input));
  });
  return app;
}
