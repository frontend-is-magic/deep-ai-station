import { Hono } from 'hono';
import {
  Authenticator,
  MemorySessionStore,
  sessionCookie,
  type Clock,
  type HeaderValues,
  type SessionStore,
} from './auth.js';
import { MemoryRepository, type Repository } from './repository.js';
import { ApiError, asciiLower, parseBody } from './request.js';
import { DocumentService } from './service.js';

interface Bindings {
  incoming?: { rawHeaders: string[] };
}
export interface AppOptions {
  clock?: Clock;
  sessions?: SessionStore;
  repository?: Repository;
  allow_cookie?: boolean;
  allowed_origin?: string;
}
export function createApp(options: AppOptions = {}) {
  const clock = options.clock ?? Date.now;
  const auth = new Authenticator(
    options.sessions ?? new MemorySessionStore(clock()),
    clock,
    options.allow_cookie ?? true,
    options.allowed_origin ?? 'https://lab.example.test',
  );
  const service = new DocumentService(options.repository ?? new MemoryRepository());
  const app = new Hono<{ Bindings: Bindings }>();
  app.use('*', async (c, next) => {
    c.header('Cache-Control', 'no-store');
    await next();
  });
  app.onError((error, c) => {
    const known = error instanceof ApiError ? error : new ApiError(500, 'request_failed');
    c.header('Cache-Control', 'no-store');
    if (known.status === 401) c.header('WWW-Authenticate', 'Bearer realm="session-authorization"');
    return c.json({ error: known.code }, known.status);
  });
  app.all('*', async (c) => {
    const path = c.req.path;
    const document = /^\/documents\/([^/]+)$/.exec(path);
    const allowed =
      path === '/health' || path === '/me' || path === '/documents' || path === '/csrf'
        ? ['GET']
        : path === '/logout'
          ? ['POST']
          : document
            ? ['GET', 'PATCH']
            : [];
    if (allowed.length === 0) throw new ApiError(404, 'route_not_found');
    if (!allowed.includes(c.req.method)) throw new ApiError(405, 'method_not_allowed');
    if (path === '/health') return c.json({ status: 'ok', lab: 'session-authorization-v1' });
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
    let body: { archived?: boolean } = {};
    if (c.req.method === 'PATCH' || c.req.method === 'POST') {
      body = await parseBody(c.req.raw, path === '/logout');
      // Reading a stream yields: a different request may revoke/expire this session meanwhile.
      principal = auth.authenticate(headers);
      auth.csrf(principal, headers);
    }
    if (path === '/me') return c.json({ user_id: principal.user_id });
    if (path === '/csrf') return c.json({ csrf_token: principal.csrf_token });
    if (path === '/documents') return c.json(service.list(principal));
    if (path === '/logout') {
      auth.logout(principal);
      if (principal.source === 'cookie') c.header('Set-Cookie', sessionCookie('', true));
      return c.json({ ok: true });
    }
    return c.json(
      c.req.method === 'PATCH'
        ? service.update(principal, document![1], body.archived!)
        : service.find(principal, document![1]),
    );
  });
  return app;
}
