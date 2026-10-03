import { afterEach, describe, expect, it, vi } from 'vitest';
import { emptyProgress, latestProgress } from '../src/lib/state';

const memory = { ...emptyProgress, notes: { 'fullstack-database': 'current memory note' } };

afterEach(() => vi.unstubAllGlobals());

describe('latest valid progress before evidence edit or export', () => {
  it('reads external changes even when their storage event has not arrived', () => {
    const latest = { ...memory, notes: { ...memory.notes, 'fullstack-routing': 'other tab' } };
    const getItem = vi.fn(() => JSON.stringify(latest));
    vi.stubGlobal('localStorage', { getItem });
    expect(latestProgress(memory)).toEqual(latest);
    expect(getItem).toHaveBeenCalledWith('deep-ai-station:v1');
    expect(memory.notes).toEqual({ 'fullstack-database': 'current memory note' });
  });

  it('respects an actual deletion instead of reviving an older persisted snapshot', () => {
    vi.stubGlobal('localStorage', { getItem: () => null });
    expect(latestProgress(memory)).toBe(emptyProgress);
  });

  it.each(['{', 'null', '[]', JSON.stringify({ ...emptyProgress, version: 2 })])(
    'keeps current memory when external storage is invalid: %s',
    (raw) => {
      vi.stubGlobal('localStorage', { getItem: () => raw });
      expect(latestProgress(memory)).toBe(memory);
    },
  );

  it('keeps memory when storage cannot be accessed', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('storage disabled');
      },
    });
    expect(latestProgress(memory)).toBe(memory);
  });
});
