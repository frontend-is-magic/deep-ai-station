import { Hono } from 'hono';
import { Authenticator, MemorySessionStore, type Clock, type SessionStore } from './auth.js';
import { MemoryRepository, type Repository } from './repository.js';
import { ApiError, asciiLower, readBody, validateUpload, type HeaderValues } from './request.js';
import { UploadService } from './service.js';

interface Bindings {
  incoming?: { rawHeaders: string[] };
}
export interface AppOptions {
  clock?: Clock;
  sessions?: SessionStore;
  repository?: Repository;
}
export function createApp(options: AppOptions = {}) {
  const clock = options.clock ?? (() => Date.now() / 1000);
  const auth = new Authenticator(options.sessions ?? new MemorySessionStore(clock()), clock);
  const service = new UploadService(options.repository ?? new MemoryRepository());
  const app = new Hono<{ Bindings: Bindings }>();
  app.use('*', async (c, next) => {
    c.header('Cache-Control', 'no-store');
    c.header('X-Content-Type-Options', 'nosniff');
    await next();
  });
  app.onError((error, c) => {
    const known = error instanceof ApiError ? error : new ApiError(500, 'request_failed');
    c.header('Cache-Control', 'no-store');
    c.header('X-Content-Type-Options', 'nosniff');
    if (known.status === 401) c.header('WWW-Authenticate', 'Bearer realm="text-upload"');
    return c.json({ error: known.code }, known.status);
  });
  app.all('*', async (c) => {
    const path = c.req.path;
    const document = /^\/documents\/([^/]+)(\/content)?$/.exec(path);
    const allowed =
      path === '/health'
        ? ['GET']
        : path === '/documents'
          ? ['GET', 'POST']
          : document
            ? ['GET']
            : [];
    if (allowed.length === 0) throw new ApiError(404, 'route_not_found');
    if (!allowed.includes(c.req.method)) throw new ApiError(405, 'method_not_allowed');
    if (path === '/health') return c.json({ status: 'ok', lab: 'text-upload-v1' });
    const rawHeaders = c.env?.incoming?.rawHeaders;
    const headers: HeaderValues = (name) => {
      if (!rawHeaders) {
        const value = c.req.raw.headers.get(name);
        return value === null ? [] : [value];
      }
      const values: string[] = [];
      for (let i = 0; i < rawHeaders.length; i += 2)
        if (asciiLower(rawHeaders[i]) === name) values.push(rawHeaders[i + 1]);
      return values;
    };
    let principal = auth.authenticate(headers);
    if (new URL(c.req.url).search.length > 0) throw new ApiError(422, 'invalid_input');
    if (c.req.method === 'POST') {
      auth.requireWrite(principal);
      const bytes = await readBody(c.req.raw);
      const upload = validateUpload(headers, bytes);
      // Bind the final authorization to the original token after body streaming yields.
      principal = auth.resolve(principal.token);
      auth.requireWrite(principal);
      return c.json(service.commit(principal, upload), 201);
    }
    if (path === '/documents') return c.json(service.list(principal));
    const found = service.find(principal, document![1]);
    if (!document![2]) return c.json(found.metadata);
    const extension = found.metadata.media_type === 'text/plain' ? 'txt' : 'md';
    c.header('Content-Type', 'application/octet-stream');
    c.header(
      'Content-Disposition',
      `attachment; filename="upload-${found.metadata.id}.${extension}"`,
    );
    return c.body(Buffer.from(found.content));
  });
  return app;
}
