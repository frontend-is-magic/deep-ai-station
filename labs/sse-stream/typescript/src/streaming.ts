import { readFileSync } from 'node:fs';

export type Scenario = 'success' | 'error' | 'timeout' | 'hold';
export interface Fixtures {
  texts: string[];
  deadline_ms: number;
  step_ms: number;
}
function readFixtures(): Fixtures {
  // Downloaded labs place fixtures beside package.json; the repository keeps
  // the same fixed file in the language-independent shared directory.
  try {
    return JSON.parse(readFileSync(new URL('../fixtures.json', import.meta.url), 'utf8'));
  } catch (error) {
    if (!(error instanceof Error && 'code' in error && error.code === 'ENOENT')) throw error;
  }
  return JSON.parse(readFileSync(new URL('../../shared/fixtures.json', import.meta.url), 'utf8'));
}
export const fixtures: Fixtures = readFixtures();

export interface ProducerContext {
  scenario: Scenario;
  runId: string;
  signal: AbortSignal;
}
export interface Producer {
  next(): Promise<IteratorResult<string, void>>;
  cleanup(): void | Promise<void>;
}
export type ProducerFactory = (context: ProducerContext) => Producer;
export type ScheduleDeadline = (expire: () => void, milliseconds: number) => () => void;

export const scheduleDeadline: ScheduleDeadline = (expire, milliseconds) => {
  const timer = setTimeout(expire, milliseconds);
  return () => clearTimeout(timer);
};

export function wait(milliseconds: number | null, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    signal.throwIfAborted();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const finish = () => {
      if (timer !== undefined) clearTimeout(timer);
      signal.removeEventListener('abort', abort);
    };
    const abort = () => {
      finish();
      reject(signal.reason);
    };
    signal.addEventListener('abort', abort, { once: true });
    if (milliseconds !== null) {
      timer = setTimeout(() => {
        finish();
        resolve();
      }, milliseconds);
    }
  });
}

export function teachingProducer(stepMs = fixtures.step_ms): ProducerFactory {
  return ({ scenario, signal }) => {
    const iterator = (async function* (): AsyncGenerator<string, void> {
      signal.throwIfAborted();
      yield fixtures.texts[0];
      if (scenario === 'error') throw new Error('fixed teaching producer failure');
      if (scenario === 'hold' || scenario === 'timeout') {
        await wait(null, signal);
        return;
      }
      for (const text of fixtures.texts.slice(1)) {
        await wait(stepMs, signal);
        signal.throwIfAborted();
        yield text;
      }
    })();
    return {
      next: () => iterator.next(),
      cleanup: async () => {
        await iterator.return();
      },
    };
  };
}
