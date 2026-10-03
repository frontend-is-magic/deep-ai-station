import { atomWithStorage, createJSONStorage } from 'jotai/utils';
import type { FeedItem, Progress } from './types';

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
const boundedText = (value: unknown, max: number): value is string =>
  typeof value === 'string' && value.length > 0 && value.length <= max;
function safeURL(value: unknown): value is string {
  if (!boundedText(value, 2000)) return false;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password;
  } catch {
    return false;
  }
}
function validFeedItem(item: unknown): item is FeedItem {
  if (!item || typeof item !== 'object') return false;
  const x = item as FeedItem;
  return (
    Object.keys(x).every((key) =>
      ['id', 'title', 'summary', 'source', 'url', 'track', 'tags', 'kind', 'published'].includes(
        key,
      ),
    ) &&
    boundedText(x.id, 3000) &&
    boundedText(x.title, 250) &&
    boundedText(x.summary, 1200) &&
    boundedText(x.source, 100) &&
    safeURL(x.url) &&
    ['agent', 'fullstack'].includes(x.track) &&
    ['guide', 'news'].includes(x.kind) &&
    Array.isArray(x.tags) &&
    x.tags.length <= 10 &&
    x.tags.every((tag) => boundedText(tag, 100)) &&
    (x.published === null ||
      (typeof x.published === 'string' && Number.isFinite(Date.parse(x.published))))
  );
}
export function toggleBookmark(progress: Progress, item: FeedItem): Progress {
  const saved = progress.bookmarks.includes(item.id);
  if (!saved && (progress.bookmarks.length >= 200 || !validFeedItem(item))) return progress;
  return {
    ...progress,
    bookmarks: saved
      ? progress.bookmarks.filter((id) => id !== item.id)
      : [...progress.bookmarks, item.id],
    savedItems: saved
      ? (progress.savedItems || []).filter((x) => x.id !== item.id)
      : [...(progress.savedItems || []), item],
  };
}
export function validateProgress(value: unknown): value is Progress {
  if (!value || typeof value !== 'object') return false;
  const p = value as Progress;
  return (
    Object.keys(p).every((key) =>
      ['version', 'completed', 'bookmarks', 'savedItems', 'notes', 'language', 'runs'].includes(
        key,
      ),
    ) &&
    p.version === 1 &&
    safeStrings(p.completed) &&
    safeStrings(p.bookmarks) &&
    (p.savedItems === undefined ||
      (Array.isArray(p.savedItems) &&
        p.savedItems.length <= 200 &&
        p.savedItems.every((x) => validFeedItem(x) && p.bookmarks.includes(x.id)) &&
        new Set(p.savedItems.map((x) => x.id)).size === p.savedItems.length)) &&
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
        Object.keys(x).every((key) =>
          ['id', 'prompt', 'answer', 'provider', 'track', 'date', 'duration_ms'].includes(key),
        ) &&
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
let storageIssue: string | null = null;
const storageListeners = new Set<() => void>();
export const getStorageIssue = () => storageIssue;
export function subscribeStorageIssue(listener: () => void) {
  storageListeners.add(listener);
  return () => {
    storageListeners.delete(listener);
  };
}
function notifyStorageIssue(issue: string | null) {
  storageIssue = issue;
  storageListeners.forEach((listener) => listener());
}
const guardedStorage = {
  getItem(key: string, fallback: Progress) {
    try {
      const value = storage.getItem(key, fallback);
      return validateProgress(value) ? value : fallback;
    } catch {
      return fallback;
    }
  },
  setItem(key: string, value: Progress) {
    try {
      storage.setItem(key, value);
      notifyStorageIssue(null);
    } catch {
      notifyStorageIssue('浏览器存储不可用，本次记录暂存在内存。请导出学习记录备份。');
    }
  },
  removeItem(key: string) {
    try {
      storage.removeItem(key);
    } catch {
      notifyStorageIssue('浏览器存储不可用，清空操作尚未保存。');
    }
  },
  subscribe(key: string, callback: (value: Progress) => void, fallback: Progress) {
    return (
      storage.subscribe?.(
        key,
        (value) => callback(validateProgress(value) ? value : fallback),
        fallback,
      ) || (() => {})
    );
  },
};
export const progressAtom = atomWithStorage<Progress>(
  'deep-ai-station:v1',
  emptyProgress,
  guardedStorage,
  { getOnInit: true },
);
