import { serve } from '@hono/node-server';
import { createApp } from './app.js';
const server = serve({ fetch: createApp().fetch, hostname: '127.0.0.1', port: 8010 });
for (const signal of ['SIGINT', 'SIGTERM'] as const) process.on(signal, () => server.close());
