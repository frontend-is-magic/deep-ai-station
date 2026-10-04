import { serve } from '@hono/node-server';
import { createApp } from './app.js';
import { SqliteRepository } from './sqlite-repository.js';
import { StorageError, storageErrorCode, storageMode } from './storage.js';

process.stdout.on('error', () => {
  process.exitCode = 1;
});
function output(value: unknown): void {
  try {
    process.stdout.write(`${JSON.stringify(value)}\n`, (error) => {
      if (error) process.exitCode = 1;
    });
  } catch {
    process.exitCode = 1;
  }
}
try {
  const mode = storageMode(process.argv.slice(2));
  if (mode === 'help') {
    process.stdout.write('Usage: node dist/server.js [init | serve --storage sqlite | --help]\n');
  } else if (mode === 'init') {
    output(new SqliteRepository().initialize());
  } else {
    const configuredPort = process.env.PORT ?? '8023';
    const port = Number(configuredPort);
    if (!/^\d+$/.test(configuredPort) || !Number.isInteger(port) || port < 1 || port > 65535)
      throw new StorageError('invalid_input');
    const repository = mode === 'sqlite' ? new SqliteRepository() : undefined;
    repository?.check();
    const server = serve({ fetch: createApp({ repository }).fetch, hostname: '127.0.0.1', port });
    server.on('error', () => {
      output({ error: 'repository_unavailable' });
      process.exitCode = 1;
      server.close();
    });
    for (const signal of ['SIGINT', 'SIGTERM'] as const) process.on(signal, () => server.close());
  }
} catch (error) {
  output({ error: storageErrorCode(error) });
  process.exitCode = 1;
}
