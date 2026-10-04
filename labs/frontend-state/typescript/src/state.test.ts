import { describe, expect, it, vi } from 'vitest';
import { createLabState, parseFacts, STORAGE_KEY, type Facts } from './state';

function memoryStorage(raw: string | null = null) {
  const records = new Map<string, string>([['platform-progress', 'leave-this-alone']]);
  if (raw !== null) records.set(STORAGE_KEY, raw);
  return {
    records,
    getItem: vi.fn((key: string) => records.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      records.set(key, value);
    }),
  };
}
const blank: Facts = { version: 1, completedIds: [], favoriteIds: [] };

describe('facts and real derived Jotai atoms', () => {
  it('starts with three cards and zero global statistics without writing storage', () => {
    const storage = memoryStorage();
    const state = createLabState(storage);
    expect(state.store.get(state.factsAtom)).toEqual(blank);
    expect(state.store.get(state.visibleCardsAtom).map((card) => card.id)).toEqual([
      'lesson-1',
      'lesson-2',
      'lesson-3',
    ]);
    expect(state.store.get(state.completedCountAtom)).toBe(0);
    expect(state.store.get(state.favoriteCountAtom)).toBe(0);
    expect(state.store.get(state.completionPercentAtom)).toBe(0);
    expect(storage.setItem).not.toHaveBeenCalled();
  });
  it('completes all three cards, treats repeated intent as a no-op, and explicitly revokes', () => {
    const storage = memoryStorage();
    const state = createLabState(storage);
    state.store.set(state.setCompletedAtom, { id: 'lesson-3', value: true });
    state.store.set(state.setCompletedAtom, { id: 'lesson-1', value: true });
    state.store.set(state.setCompletedAtom, { id: 'lesson-1', value: true });
    expect(state.store.get(state.factsAtom).completedIds).toEqual(['lesson-1', 'lesson-3']);
    expect(storage.setItem).toHaveBeenCalledTimes(2);
    state.store.set(state.setCompletedAtom, { id: 'lesson-2', value: true });
    expect(state.store.get(state.completedCountAtom)).toBe(3);
    expect(state.store.get(state.completionPercentAtom)).toBe(100);
    state.store.set(state.setCompletedAtom, { id: 'lesson-1', value: false });
    state.store.set(state.setCompletedAtom, { id: 'lesson-1', value: false });
    expect(state.store.get(state.completedCountAtom)).toBe(2);
    expect(state.store.get(state.completionPercentAtom)).toBe(67);
    expect(storage.setItem).toHaveBeenCalledTimes(4);
  });
  it('favorites independently and filters the real visible atom while statistics keep denominator three', () => {
    const state = createLabState(memoryStorage());
    state.store.set(state.setCompletedAtom, { id: 'lesson-1', value: true });
    state.store.set(state.setFavoriteAtom, { id: 'lesson-2', value: true });
    state.store.set(state.filterAtom, 'pending');
    expect(state.store.get(state.visibleCardsAtom).map((card) => card.id)).toEqual([
      'lesson-2',
      'lesson-3',
    ]);
    expect(state.store.get(state.completionPercentAtom)).toBe(33);
    state.store.set(state.filterAtom, 'favorites');
    expect(state.store.get(state.visibleCardsAtom).map((card) => card.id)).toEqual(['lesson-2']);
    expect(state.store.get(state.completedCountAtom)).toBe(1);
    expect(state.store.get(state.favoriteCountAtom)).toBe(1);
    expect(state.store.get(state.completionPercentAtom)).toBe(33);
    state.store.set(state.setFavoriteAtom, { id: 'lesson-2', value: false });
    expect(state.store.get(state.visibleCardsAtom)).toEqual([]);
    expect(state.store.get(state.completionPercentAtom)).toBe(33);
  });
  it('notifies derived subscribers when facts change, with no manual counter synchronization', () => {
    const state = createLabState(memoryStorage());
    const seen: number[] = [];
    const stop = state.store.sub(state.completedCountAtom, () =>
      seen.push(state.store.get(state.completedCountAtom)),
    );
    state.store.set(state.setCompletedAtom, { id: 'lesson-2', value: true });
    state.store.set(state.setCompletedAtom, { id: 'lesson-2', value: true });
    state.store.set(state.setCompletedAtom, { id: 'lesson-2', value: false });
    stop();
    expect(seen).toEqual([1, 0]);
  });
  it('persists only canonical facts and restores them with filter reset to all', () => {
    const storage = memoryStorage();
    const state = createLabState(storage);
    state.store.set(state.setFavoriteAtom, { id: 'lesson-3', value: true });
    state.store.set(state.setFavoriteAtom, { id: 'lesson-1', value: true });
    state.store.set(state.setCompletedAtom, { id: 'lesson-2', value: true });
    state.store.set(state.filterAtom, 'favorites');
    expect(storage.records.get(STORAGE_KEY)).toBe(
      '{"version":1,"completedIds":["lesson-2"],"favoriteIds":["lesson-1","lesson-3"]}',
    );
    expect(storage.setItem.mock.calls.every(([key]) => key === STORAGE_KEY)).toBe(true);
    expect(storage.records.get('platform-progress')).toBe('leave-this-alone');
    const restored = createLabState(storage);
    expect(restored.store.get(restored.factsAtom)).toEqual(state.store.get(state.factsAtom));
    expect(restored.store.get(restored.filterAtom)).toBe('all');
    expect(restored.store.get(restored.visibleCardsAtom)).toHaveLength(3);
    expect(restored.store.get(restored.completionPercentAtom)).toBe(33);
    expect(storage.setItem).toHaveBeenCalledTimes(3);
  });
  it('does not persist filter changes or unchanged favorite intent', () => {
    const storage = memoryStorage();
    const state = createLabState(storage);
    state.store.set(state.filterAtom, 'favorites');
    state.store.set(state.filterAtom, 'all');
    state.store.set(state.setFavoriteAtom, { id: 'lesson-1', value: false });
    expect(storage.setItem).not.toHaveBeenCalled();
    state.store.set(state.setFavoriteAtom, { id: 'lesson-1', value: true });
    state.store.set(state.setFavoriteAtom, { id: 'lesson-1', value: true });
    expect(storage.setItem).toHaveBeenCalledTimes(1);
  });
});

describe('minimum local fact storage boundary', () => {
  it('normalizes valid unordered sets on load without an automatic write', () => {
    const storage = memoryStorage(
      '{"version":1,"completedIds":["lesson-3","lesson-1"],"favoriteIds":[]}',
    );
    const state = createLabState(storage);
    expect(state.store.get(state.factsAtom).completedIds).toEqual(['lesson-1', 'lesson-3']);
    expect(state.store.get(state.storageIssueAtom)).toBeNull();
    expect(storage.setItem).not.toHaveBeenCalled();
  });
  it('rejects wrong version, extra derived values, unknown or duplicate IDs, and invalid shapes as whole records', () => {
    const invalid = [
      '{broken',
      'null',
      '[]',
      JSON.stringify({ ...blank, version: 2 }),
      JSON.stringify({ ...blank, completedCount: 0 }),
      JSON.stringify({ ...blank, completedIds: ['unknown'] }),
      JSON.stringify({ ...blank, favoriteIds: ['lesson-1', 'lesson-1'] }),
      JSON.stringify({ version: 1, completedIds: [] }),
      JSON.stringify({ ...blank, completedIds: [1] }),
    ];
    for (const raw of invalid) {
      expect(parseFacts(raw)).toBeNull();
      const storage = memoryStorage(raw);
      const state = createLabState(storage);
      expect(state.store.get(state.factsAtom)).toEqual(blank);
      expect(state.store.get(state.storageIssueAtom)).toBe('invalid');
      expect(storage.setItem).not.toHaveBeenCalled();
    }
  });
  it('lets one explicit fact change replace bad storage and clears the notice', () => {
    const storage = memoryStorage('private-invalid-marker');
    const state = createLabState(storage);
    state.store.set(state.setFavoriteAtom, { id: 'lesson-2', value: true });
    expect(state.store.get(state.storageIssueAtom)).toBeNull();
    expect(JSON.parse(storage.records.get(STORAGE_KEY)!)).toEqual({
      ...blank,
      favoriteIds: ['lesson-2'],
    });
  });
  it('uses memory when reading is unavailable and can save a later explicit operation', () => {
    const storage = memoryStorage();
    storage.getItem.mockImplementation(() => {
      throw new Error('private-reader-error');
    });
    const state = createLabState(storage);
    expect(state.store.get(state.storageIssueAtom)).toBe('unavailable');
    state.store.set(state.setCompletedAtom, { id: 'lesson-1', value: true });
    expect(state.store.get(state.completedCountAtom)).toBe(1);
    expect(state.store.get(state.storageIssueAtom)).toBeNull();
  });
  it('retains changed facts and derived values in memory after a write failure', () => {
    const storage = memoryStorage();
    storage.setItem.mockImplementation(() => {
      throw new Error('private-write-error');
    });
    const state = createLabState(storage);
    state.store.set(state.setCompletedAtom, { id: 'lesson-2', value: true });
    state.store.set(state.setFavoriteAtom, { id: 'lesson-2', value: true });
    expect(state.store.get(state.factsAtom)).toEqual({
      version: 1,
      completedIds: ['lesson-2'],
      favoriteIds: ['lesson-2'],
    });
    expect(state.store.get(state.completedCountAtom)).toBe(1);
    expect(state.store.get(state.favoriteCountAtom)).toBe(1);
    expect(state.store.get(state.storageIssueAtom)).toBe('write-failed');
    expect(storage.records.has(STORAGE_KEY)).toBe(false);
  });
  it('still supports explicit local intents when no storage object is available', () => {
    const state = createLabState(null);
    state.store.set(state.setCompletedAtom, { id: 'lesson-3', value: true });
    state.store.set(state.filterAtom, 'pending');
    expect(state.store.get(state.visibleCardsAtom).map((card) => card.id)).toEqual([
      'lesson-1',
      'lesson-2',
    ]);
    expect(state.store.get(state.completionPercentAtom)).toBe(33);
    expect(state.store.get(state.storageIssueAtom)).toBe('unavailable');
  });
});
