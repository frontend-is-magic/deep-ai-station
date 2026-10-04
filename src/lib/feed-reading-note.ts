import { feedCourseFor } from './feed-course';
import { setLessonNote } from './progress-write';
import { validHistoryResetId } from './run-history';
import { validateProgress } from './state';
import type { FeedItem, Language, Progress, Track } from './types';

export const FEED_READING_UNDERSTANDING_LIMIT = 1500;
export const FEED_READING_QUESTION_LIMIT = 500;
const NOTE_LIMIT = 10000;
const NOTE_KEYS_LIMIT = 1000;

export type FeedReadingDraft = Readonly<{
  id: string;
  createdAt: string;
  resetId: Progress['history_reset_id'];
  item: FeedItem;
  lessonId: string;
  language: Language;
}>;

export type PreparedFeedReadingNote = {
  status: 'ready' | 'saved' | 'missing-fields' | 'too-long' | 'invalid';
  entry: string;
  nextNote: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function boundedText(value: unknown, limit: number): value is string {
  return typeof value === 'string' && value.length <= limit && value.trim().length > 0;
}

function validDate(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    value.length === 24 &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value) &&
    Number.isFinite(Date.parse(value)) &&
    new Date(value).toISOString() === value
  );
}

function validSource(value: unknown): value is FeedItem {
  // The controlled mapping checks identity; only display fields need extra bounds here.
  return (
    isRecord(value) &&
    boundedText(value.title, 250) &&
    boundedText(value.source, 100) &&
    Array.isArray(value.tags)
  );
}

function validNotes(progress: unknown): progress is Progress {
  return (
    isRecord(progress) &&
    isRecord(progress.notes) &&
    Reflect.ownKeys(progress.notes).length === Object.keys(progress.notes).length &&
    validateProgress(progress)
  );
}

function plainText(value: string): string {
  const longest = Math.max(0, ...Array.from(value.matchAll(/`+/g), (match) => match[0].length));
  const fence = '`'.repeat(Math.max(3, longest + 1));
  return `${fence}text\n${value}\n${fence}`;
}

export function createFeedReadingDraft(
  item: FeedItem,
  tracks: Track[],
  progress: Progress,
  id: string,
  createdAt: string,
): FeedReadingDraft | undefined {
  if (
    !validNotes(progress) ||
    !validSource(item) ||
    !validHistoryResetId(id) ||
    !validDate(createdAt)
  )
    return undefined;
  const course = feedCourseFor(item, tracks, progress);
  if (!course || !boundedText(course.lesson.title, 300)) return undefined;
  return {
    id,
    createdAt,
    resetId: progress.history_reset_id,
    item: { ...item, tags: [...item.tags] },
    lessonId: course.lesson.id,
    language: course.language,
  };
}

export function prepareFeedReadingNote(
  progress: Progress,
  tracks: Track[],
  draft: FeedReadingDraft,
  understanding: string,
  question: string,
): PreparedFeedReadingNote {
  const invalid: PreparedFeedReadingNote = { status: 'invalid', entry: '', nextNote: '' };
  if (
    !validNotes(progress) ||
    !isRecord(draft) ||
    !validHistoryResetId(draft.id) ||
    !validDate(draft.createdAt) ||
    !validSource(draft.item) ||
    draft.resetId !== progress.history_reset_id ||
    !['typescript', 'python', 'go'].includes(draft.language)
  )
    return invalid;
  // Retain the reference language chosen when opening the draft, including for Jotai.
  const course = feedCourseFor(draft.item, tracks, { ...progress, language: draft.language });
  if (
    !course ||
    course.lesson.id !== draft.lessonId ||
    course.language !== draft.language ||
    !boundedText(course.lesson.title, 300)
  )
    return invalid;
  const previous = Object.hasOwn(progress.notes, draft.lessonId)
    ? progress.notes[draft.lessonId]
    : '';
  const unchanged = { entry: '', nextNote: previous };
  const marker = `### 阅读摘记 ${draft.id}`;
  if (previous.split(/\r\n?|\n/).includes(marker)) return { ...unchanged, status: 'saved' };
  if (typeof understanding !== 'string' || typeof question !== 'string')
    return { ...unchanged, status: 'invalid' };
  if (!understanding.trim() || !question.trim()) return { ...unchanged, status: 'missing-fields' };
  if (
    understanding.length > FEED_READING_UNDERSTANDING_LIMIT ||
    question.length > FEED_READING_QUESTION_LIMIT
  )
    return { ...unchanged, status: 'too-long' };
  const entry = [
    marker,
    plainText(
      [
        `资料：${draft.item.title}`,
        `来源：${draft.item.source}`,
        `原文 URL：${draft.item.url}`,
        `课程：${course.lesson.title}`,
        `课程 ID：${course.lesson.id}`,
        `学习路线：${course.lesson.track}`,
        `参考语言：${draft.language}${course.sharedFrontend ? '（公共前端）' : ''}`,
        `记录时间：${draft.createdAt}`,
      ].join('\n'),
    ),
    '### 我的理解',
    plainText(understanding),
    '### 准备验证的问题',
    plainText(question),
    '个人阅读摘记；不代表平台核验或实践完成。',
  ].join('\n\n');
  const nextNote = previous ? `${previous}\n\n${entry}` : entry;
  if (
    nextNote.length > NOTE_LIMIT ||
    (!Object.hasOwn(progress.notes, draft.lessonId) &&
      Object.keys(progress.notes).length >= NOTE_KEYS_LIMIT)
  )
    return { status: 'too-long', entry, nextNote };
  return { status: 'ready', entry, nextNote };
}

export function saveFeedReadingNote(
  progress: Progress,
  tracks: Track[],
  draft: FeedReadingDraft,
  understanding: string,
  question: string,
): Progress {
  const prepared = prepareFeedReadingNote(progress, tracks, draft, understanding, question);
  if (prepared.status !== 'ready') return progress;
  return setLessonNote(progress, draft.lessonId, prepared.nextNote).progress;
}
