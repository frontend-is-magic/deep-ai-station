import { describe, expect, it } from 'vitest';
import { incompleteRunUsage, updateRunUsage } from '../src/lib/run-usage';
import { emptyProgress, validateProgress } from '../src/lib/state';

describe('cumulative known usage snapshots', () => {
  it('replaces repeated snapshots instead of adding or retaining omitted fields', () => {
    const event = {
      usage: { prompt_tokens: 7, completion_tokens: 5, total_tokens: 12 },
      usage_complete: false,
    };
    const first = updateRunUsage(null, event);
    expect(updateRunUsage(first, event)).toEqual(first);
    expect(updateRunUsage(first, { usage: { total_tokens: 18 } })).toEqual({
      usage: { total_tokens: 18 },
      usageComplete: false,
    });
    expect(first.usage).toEqual(event.usage);
  });
  it('keeps explicit zero while absent usage stays unknown', () => {
    expect(updateRunUsage(null, {})).toEqual({ usage: null, usageComplete: false });
    expect(updateRunUsage(null, { usage: { total_tokens: 0 } })).toEqual({
      usage: { total_tokens: 0 },
      usageComplete: false,
    });
    expect(updateRunUsage(null, { usage: { prompt_tokens: 0 } }).usage).toEqual({
      prompt_tokens: 0,
    });
  });
  it('preserves an earlier snapshot when a legacy ending omits usage, while explicit null clears it', () => {
    const previous = updateRunUsage(null, { usage: { total_tokens: 12 } });
    expect(updateRunUsage(previous, {}, true)).toEqual(previous);
    expect(updateRunUsage(previous, { usage: null }, true).usage).toBeNull();
  });
  it('does not claim completeness during an active run even when a provider round was complete', () => {
    expect(updateRunUsage(null, { usage: { total_tokens: 12 }, usage_complete: true })).toEqual({
      usage: { total_tokens: 12 },
      usageComplete: false,
    });
  });
  it('supports complete terminal usage even when the run ends with a quota error', () => {
    expect(
      updateRunUsage(
        null,
        { usage: { total_tokens: 12 }, usage_complete: true, message: 'quota exhausted' },
        true,
      ),
    ).toEqual({ usage: { total_tokens: 12 }, usageComplete: true });
  });
  it('marks cancellation and missing terminal frames incomplete without discarding known counts', () => {
    const previous = updateRunUsage(
      null,
      { usage: { total_tokens: 0 }, usage_complete: true },
      true,
    );
    expect(incompleteRunUsage(previous)).toEqual({
      usage: { total_tokens: 0 },
      usageComplete: false,
    });
    expect(incompleteRunUsage(null)).toEqual({ usage: null, usageComplete: false });
    expect(previous.usageComplete).toBe(true);
  });
  it.each([
    { total_tokens: -1 },
    { total_tokens: 0.5 },
    { total_tokens: Infinity },
    { total_tokens: 300_000_001 },
    { total_tokens: '12' },
    { unknown: 2 },
    {},
  ])('does not replace received counts or certify an invalid snapshot %j', (usage) => {
    const previous = updateRunUsage(null, { usage: { total_tokens: 12 } });
    expect(updateRunUsage(previous, { usage, usage_complete: true }, true)).toEqual(previous);
  });
  it('retains legacy successful done payloads without inventing a completeness flag', () => {
    expect(updateRunUsage(null, { usage: { input_tokens: 0, output_tokens: 3 } }, true)).toEqual({
      usage: { input_tokens: 0, output_tokens: 3 },
      usageComplete: undefined,
    });
  });
  it('uses the existing 300M cumulative ceiling so exported successful runs remain valid', () => {
    const snapshot = updateRunUsage(
      null,
      { usage: { total_tokens: 300_000_000 }, usage_complete: false },
      true,
    );
    expect(snapshot.usage).toEqual({ total_tokens: 300_000_000 });
    expect(
      validateProgress({
        ...emptyProgress,
        runs: [
          {
            id: 'known-usage',
            prompt: 'task',
            answer: 'answer',
            provider: 'deepseek',
            track: 'agent',
            date: '2026-10-04T00:00:00.000Z',
            duration_ms: 10,
            usage: snapshot.usage,
            usage_complete: snapshot.usageComplete,
          },
        ],
      }),
    ).toBe(true);
  });
});
