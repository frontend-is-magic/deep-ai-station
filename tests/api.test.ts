import { afterEach, expect, it, vi } from 'vitest';
import { streamRun } from '../src/lib/api';

afterEach(() => vi.unstubAllGlobals());

it('closes an active response when an invalid event stops parsing', async () => {
  const cancel = vi.fn();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode('event: delta\ndata: invalid-json\n\n'));
    },
    cancel,
  });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(body)));
  await expect(streamRun({}, '', new AbortController().signal, () => undefined)).rejects.toThrow();
  expect(cancel).toHaveBeenCalledOnce();
  expect(body.locked).toBe(false);
});
