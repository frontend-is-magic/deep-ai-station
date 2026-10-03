import { randomUUID } from 'node:crypto';
import { Hono } from 'hono';
import { stream } from 'hono/streaming';
import {
  fixtures,
  scheduleDeadline,
  teachingProducer,
  type Producer,
  type ProducerFactory,
  type Scenario,
  type ScheduleDeadline,
} from './streaming.js';

export interface AppOptions {
  producerFactory?: ProducerFactory;
  deadlineMs?: number;
  stepMs?: number;
  scheduleDeadline?: ScheduleDeadline;
}

class DeadlineExceeded extends Error {}
const scenarios = new Set<Scenario>(['success', 'error', 'timeout', 'hold']);

export function createApp(options: AppOptions = {}) {
  const createProducer = options.producerFactory ?? teachingProducer(options.stepMs);
  const deadlineMs = options.deadlineMs ?? fixtures.deadline_ms;
  const schedule = options.scheduleDeadline ?? scheduleDeadline;
  const app = new Hono();
  app.use('*', async (c, next) => {
    c.header('Cache-Control', 'no-store');
    c.header('X-Content-Type-Options', 'nosniff');
    await next();
  });
  app.get('/health', (c) => c.json({ ok: true, lab: 'sse-stream' }));
  app.get('/stream', (c) => {
    const entries = [...new URL(c.req.url).searchParams];
    const scenario = entries[0]?.[1] as Scenario;
    if (entries.length !== 1 || entries[0][0] !== 'scenario' || !scenarios.has(scenario)) {
      return c.json(
        { error: { code: 'invalid_request', message: '仅支持一个有效的 scenario 参数' } },
        400,
      );
    }
    // Hono handles HEAD via GET unless it is rejected before starting the producer.
    if (c.req.method !== 'GET') return c.body(null, 405);
    c.header('Content-Type', 'text/event-stream; charset=utf-8');
    c.header('X-Accel-Buffering', 'no');
    const requestSignal = c.req.raw.signal;
    return stream(c, async (output) => {
      const controller = new AbortController();
      const runId = randomUUID();
      let disconnected = false;
      let producer: Producer | undefined;
      let cancelDeadline = () => {};
      const disconnect = () => {
        disconnected = true;
        controller.abort(new Error('client_disconnected'));
      };
      const isDisconnected = () => disconnected || requestSignal.aborted || output.aborted;
      const emit = async (event: string, payload: Record<string, unknown>) => {
        if (isDisconnected()) return;
        await output.write(
          `event: ${event}\ndata: ${JSON.stringify({ run_id: runId, ...payload })}\n\n`,
        );
      };
      // node-server aborts Request.signal on an unfinished response close. Hono's
      // onAbort also covers direct ReadableStream cancellation in other consumers.
      requestSignal.addEventListener('abort', disconnect, { once: true });
      output.onAbort(disconnect);
      try {
        if (isDisconnected()) {
          disconnect();
          return;
        }
        cancelDeadline = schedule(() => controller.abort(new DeadlineExceeded()), deadlineMs);
        await emit('start', { scenario });
        if (isDisconnected()) return;
        producer = createProducer({ scenario, runId, signal: controller.signal });
        let seq = 0;
        while (!isDisconnected()) {
          controller.signal.throwIfAborted();
          const item = await producer.next();
          if (isDisconnected()) return;
          controller.signal.throwIfAborted();
          if (item.done) {
            await emit('done', { seq });
            return;
          }
          await emit('delta', { seq: ++seq, text: item.value });
        }
      } catch {
        if (!isDisconnected()) {
          const deadline = controller.signal.reason instanceof DeadlineExceeded;
          controller.abort(new Error('producer_stopped'));
          await emit('error', {
            code: deadline ? 'deadline_exceeded' : 'producer_failed',
            message: deadline ? '教学运行超时' : '教学数据源失败',
          });
        }
      } finally {
        cancelDeadline();
        requestSignal.removeEventListener('abort', disconnect);
        // This single owner runs for success, failure, deadline and disconnect.
        // Cleanup failures must not leak details or append a second terminal frame.
        try {
          await producer?.cleanup();
        } catch {}
      }
    });
  });
  return app;
}
