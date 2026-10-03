import { atomWithStorage, createJSONStorage } from 'jotai/utils';
import type { Progress } from './types';

export const emptyProgress: Progress = {
  version: 1,
  completed: [],
  bookmarks: [],
  notes: {},
  language: 'typescript',
  runs: [],
};
const safeStrings = (x: unknown): x is string[] =>
  Array.isArray(x) && x.length <= 2000 && x.every((v) => typeof v === 'string' && v.length <= 3000);
export function validateProgress(value: unknown): value is Progress {
  if (!value || typeof value !== 'object') return false;
  const p = value as Progress;
  return (
    p.version === 1 &&
    safeStrings(p.completed) &&
    safeStrings(p.bookmarks) &&
    ['typescript', 'go', 'python'].includes(p.language) &&
    typeof p.notes === 'object' &&
    p.notes !== null &&
    !Array.isArray(p.notes) &&
    Object.keys(p.notes).length <= 1000 &&
    Object.values(p.notes).every((x) => typeof x === 'string' && x.length <= 10000) &&
    Array.isArray(p.runs) &&
    p.runs.length <= 20 &&
    p.runs.every(
      (x) =>
        x &&
        typeof x.id === 'string' &&
        typeof x.prompt === 'string' &&
        x.prompt.length <= 4000 &&
        typeof x.answer === 'string' &&
        x.answer.length <= 50000 &&
        ['agent', 'fullstack'].includes(x.track) &&
        typeof x.provider === 'string' &&
        typeof x.date === 'string' &&
        Number.isFinite(Date.parse(x.date)) &&
        Number.isFinite(x.duration_ms),
    )
  );
}
const storage = createJSONStorage<Progress>(() => localStorage);
const guardedStorage = {
  getItem(key: string, fallback: Progress) {
    try {
      const value = storage.getItem(key, fallback);
      return validateProgress(value) ? value : fallback;
    } catch {
      return fallback;
    }
  },
  setItem: storage.setItem,
  removeItem: storage.removeItem,
};
export const progressAtom = atomWithStorage<Progress>(
  'deep-ai-station:v1',
  emptyProgress,
  guardedStorage,
);
