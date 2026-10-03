import { serve } from '@hono/node-server';
import { createApp } from './app.js';

const configuredPort = process.env.PORT ?? '8023';
const port = Number(configuredPort);
if (!/^\d+$/.test(configuredPort) || !Number.isInteger(port) || port < 1 || port > 65535) {
  throw new Error('PORT must be an integer between 1 and 65535');
}
const server = serve({ fetch: createApp().fetch, hostname: '127.0.0.1', port });
for (const signal of ['SIGINT', 'SIGTERM'] as const) {
  process.on(signal, () => server.close());
}
