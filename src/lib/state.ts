import { atomWithStorage, createJSONStorage } from 'jotai/utils';
import type { FeedItem, Progress } from './types';
import { validEvidenceRecords } from './evidence';
import { validPracticeRecords } from './practice';

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
      [
        'version',
        'completed',
        'bookmarks',
        'savedItems',
        'notes',
        'language',
        'runs',
        'evidence',
        'practice',
      ].includes(key),
    ) &&
    p.version === 1 &&
    (p.evidence === undefined || validEvidenceRecords(p.evidence)) &&
    (p.practice === undefined || validPracticeRecords(p.practice)) &&
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
          [
            'id',
            'prompt',
            'answer',
            'provider',
            'track',
            'lesson_id',
            'workflow',
            'trace',
            'usage',
            'usage_complete',
            'steps',
            'tool_count',
            'date',
            'duration_ms',
          ].includes(key),
        ) &&
        typeof x.id === 'string' &&
        typeof x.prompt === 'string' &&
        x.prompt.length <= 4000 &&
        typeof x.answer === 'string' &&
        x.answer.length <= 50000 &&
        ['agent', 'fullstack'].includes(x.track) &&
        (x.lesson_id === undefined ||
          (typeof x.lesson_id === 'string' &&
            x.lesson_id.length <= 100 &&
            x.lesson_id.startsWith(`${x.track}-`) &&
            /^[a-z0-9-]+$/.test(x.lesson_id))) &&
        (x.workflow === undefined || ['retrieval', 'agent'].includes(x.workflow)) &&
        (x.usage === undefined || x.usage === null || validRunUsage(x.usage)) &&
        (x.usage_complete === undefined || typeof x.usage_complete === 'boolean') &&
        (x.steps === undefined || (Number.isInteger(x.steps) && x.steps >= 1 && x.steps <= 3)) &&
        (x.tool_count === undefined ||
          (Number.isInteger(x.tool_count) && x.tool_count >= 0 && x.tool_count <= 2)) &&
        (x.trace === undefined ||
          (Array.isArray(x.trace) &&
            x.trace.length <= 12 &&
            x.trace.every(
              (step) =>
                step &&
                Object.keys(step).every((key) =>
                  ['id', 'title', 'detail', 'status'].includes(key),
                ) &&
                boundedText(step.title, 100) &&
                boundedText(step.detail, 500) &&
                ['running', 'success', 'error'].includes(step.status) &&
                (step.id === undefined || boundedText(step.id, 100)),
            ))) &&
        typeof x.provider === 'string' &&
        typeof x.date === 'string' &&
        Number.isFinite(Date.parse(x.date)) &&
        Number.isFinite(x.duration_ms),
    )
  );
}
const usageFields = new Set([
  'prompt_tokens',
  'completion_tokens',
  'total_tokens',
  'input_tokens',
  'output_tokens',
  'cache_creation_input_tokens',
  'cache_read_input_tokens',
]);
function validRunUsage(value: unknown): value is Record<string, number> {
  return (
    typeof value === 'object' &&
    value !== null &&
    !Array.isArray(value) &&
    Object.keys(value).length > 0 &&
    Object.entries(value).every(
      ([key, count]) =>
        usageFields.has(key) && Number.isInteger(count) && count >= 0 && count <= 300_000_000,
    )
  );
}
export function readRunUsage(value: unknown): Record<string, number> | null {
  return validRunUsage(value) ? value : null;
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
