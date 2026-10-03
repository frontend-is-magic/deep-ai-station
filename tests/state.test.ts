import { describe, expect, it } from 'vitest';
import { createSSEParser } from '../src/lib/api';
import { emptyProgress, validateProgress } from '../src/lib/state';

describe('stream framing', () => {
  it('buffers frames and UTF-8 JSON payloads across arbitrary delivery boundaries', () => {
    const events: unknown[] = [];
    const parse = createSSEParser((event) => events.push(event));
    parse('event: delta\nda');
    parse('ta: {"text":"你好"}\n');
    expect(events).toHaveLength(0);
    parse('\nevent: done\ndata: {"run_id":"1"}\n\n');
    expect(events).toEqual([
      { event: 'delta', data: { text: '你好' } },
      { event: 'done', data: { run_id: '1' } },
    ]);
  });
});
describe('import boundary', () => {
  it('accepts a versioned progress snapshot', () =>
    expect(validateProgress(emptyProgress)).toBe(true));
  it.each([
    null,
    {},
    { ...emptyProgress, language: 'rust' },
    { ...emptyProgress, notes: null },
    { ...emptyProgress, completed: [1] },
    { ...emptyProgress, runs: [{ id: 'x' }] },
  ])('rejects malformed persisted data: %s', (data) => expect(validateProgress(data)).toBe(false));
});
