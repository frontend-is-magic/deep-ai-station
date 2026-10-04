import type { ValidTarget } from './navigation';
import {
  discardResponse,
  normalizeQuestion,
  parseResponseBody,
  ProtocolError,
  readResponseBody,
  REQUEST_DEADLINE_MS,
  validLessonId,
  type ApiRequest,
  type RequestRecord,
} from './protocol';

export interface RequestSnapshot {
  readonly record: RequestRecord | null;
}
export interface ControllerDependencies {
  fetch?: typeof globalThis.fetch;
  deadlineMs?: number;
  now?: () => number;
}
interface Attempt {
  controller: AbortController;
  timer: ReturnType<typeof setTimeout> | null;
  request: ApiRequest;
  status: number | null;
  expiresAt: number;
}
function requestFor(target: ValidTarget): ApiRequest | null {
  if (target.kind === 'search') {
    if (target.question === null) return null;
    if (normalizeQuestion(target.question) !== target.question) throw new ProtocolError();
    return { method: 'POST', path: '/api/search', body: { question: target.question } };
  }
  if (!validLessonId(target.id)) throw new ProtocolError();
  return { method: 'GET', path: `/api/lessons/${target.id}`, body: null };
}

export function createRequestController(dependencies: ControllerDependencies = {}) {
  const fetcher = dependencies.fetch ?? ((...args: Parameters<typeof fetch>) => fetch(...args));
  const now = dependencies.now ?? (() => performance.now());
  const deadline = dependencies.deadlineMs ?? REQUEST_DEADLINE_MS;
  let disposed = false;
  let active: Attempt | null = null;
  let snapshot: RequestSnapshot = { record: null };
  const listeners = new Set<(value: RequestSnapshot) => void>();
  const publish = (record: RequestRecord | null) => {
    snapshot = { record };
    for (const listener of listeners) listener(snapshot);
  };
  const retire = (attempt: Attempt | null) => {
    if (!attempt) return;
    if (attempt.timer !== null) clearTimeout(attempt.timer);
    attempt.controller.abort();
  };
  const current = (attempt: Attempt) => !disposed && active === attempt;
  const finish = (attempt: Attempt, record: RequestRecord) => {
    if (!current(attempt)) return;
    active = null;
    if (attempt.timer !== null) clearTimeout(attempt.timer);
    publish(record);
  };
  const timeout = (attempt: Attempt) => {
    if (!current(attempt)) return;
    finish(attempt, {
      request: attempt.request,
      response: { status: attempt.status, body: null },
      outcome: 'timeout',
    });
    attempt.controller.abort();
  };
  async function execute(attempt: Attempt) {
    // StrictMode's first setup can dispose before this deferred dispatch.
    await Promise.resolve();
    if (!current(attempt)) return;
    if (now() >= attempt.expiresAt) {
      timeout(attempt);
      return;
    }
    attempt.timer = setTimeout(() => timeout(attempt), Math.max(0, attempt.expiresAt - now()));
    try {
      const response = await fetcher(attempt.request.path, {
        method: attempt.request.method,
        credentials: 'omit',
        redirect: 'error',
        signal: attempt.controller.signal,
        ...(attempt.request.body
          ? {
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(attempt.request.body),
            }
          : {}),
      });
      if (!current(attempt)) {
        discardResponse(response);
        return;
      }
      attempt.status = response.status;
      publish({
        request: attempt.request,
        response: { status: response.status, body: null },
        outcome: 'loading',
      });
      if (!current(attempt)) {
        discardResponse(response);
        return;
      }
      const body = await readResponseBody(response, attempt.controller.signal);
      if (!current(attempt)) return;
      const parsed = parseResponseBody(body, response.status, attempt.request);
      if (now() >= attempt.expiresAt) {
        timeout(attempt);
        return;
      }
      finish(attempt, {
        request: attempt.request,
        response: { status: response.status, body: parsed.body },
        outcome: parsed.outcome,
      });
    } catch (error) {
      if (!current(attempt)) return;
      if (now() >= attempt.expiresAt) {
        timeout(attempt);
        return;
      }
      finish(attempt, {
        request: attempt.request,
        response: { status: attempt.status, body: null },
        outcome: error instanceof ProtocolError ? 'protocol_error' : 'network_error',
      });
    }
  }
  return {
    getSnapshot: () => snapshot,
    subscribe(listener: (value: RequestSnapshot) => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    run(target: ValidTarget) {
      if (disposed) return;
      const request = requestFor(target);
      const previous = active;
      const attempt: Attempt | null = request
        ? {
            controller: new AbortController(),
            timer: null,
            request,
            status: null,
            expiresAt: now() + deadline,
          }
        : null;
      active = attempt;
      publish(
        request ? { request, response: { status: null, body: null }, outcome: 'loading' } : null,
      );
      retire(previous);
      if (attempt) void execute(attempt);
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      const previous = active;
      active = null;
      listeners.clear();
      snapshot = { record: null };
      retire(previous);
    },
  };
}
