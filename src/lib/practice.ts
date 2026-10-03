import type { Language, PracticeRecord, Progress } from './types';

export const MAX_PRACTICE_RECORDS = 256;
const fields = ['lesson_id', 'language', 'completed_at'] as const;

function validPracticeKey(lessonId: unknown, language: unknown): boolean {
  return (
    typeof lessonId === 'string' &&
    lessonId.length <= 100 &&
    /^(agent|fullstack)-[a-z0-9]+(?:-[a-z0-9]+)*$/.test(lessonId) &&
    typeof language === 'string' &&
    ['typescript', 'go', 'python'].includes(language) &&
    (!lessonId.startsWith('agent-') || language === 'python')
  );
}

export function validPracticeRecord(value: unknown): value is PracticeRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const keys = Reflect.ownKeys(value);
  if (
    keys.length !== fields.length ||
    Object.keys(value).length !== fields.length ||
    !keys.every((key) => typeof key === 'string' && fields.includes(key as (typeof fields)[number]))
  )
    return false;
  const record = value as PracticeRecord;
  if (
    !validPracticeKey(record.lesson_id, record.language) ||
    typeof record.completed_at !== 'string' ||
    record.completed_at.length > 40
  )
    return false;
  const date = new Date(record.completed_at);
  return Number.isFinite(date.getTime()) && date.toISOString() === record.completed_at;
}

export function validPracticeRecords(value: unknown): value is PracticeRecord[] {
  return (
    Array.isArray(value) &&
    value.length <= MAX_PRACTICE_RECORDS &&
    Array.from(value).every(validPracticeRecord) &&
    new Set(value.map((record) => `${record.lesson_id}:${record.language}`)).size === value.length
  );
}

export function practiceFor(
  progress: Progress,
  lessonId: string,
  language: Language,
): PracticeRecord | undefined {
  const previous = progress.practice === undefined ? [] : progress.practice;
  if (!validPracticeKey(lessonId, language) || !validPracticeRecords(previous)) return undefined;
  return previous.find((record) => record.lesson_id === lessonId && record.language === language);
}

export function savePractice(progress: Progress, record: PracticeRecord): Progress {
  const previous = progress.practice === undefined ? [] : progress.practice;
  if (!validPracticeRecord(record) || !validPracticeRecords(previous)) return progress;
  if (
    previous.some(
      (item) => item.lesson_id === record.lesson_id && item.language === record.language,
    ) ||
    previous.length >= MAX_PRACTICE_RECORDS
  )
    return progress;
  return { ...progress, practice: [...previous, { ...record }] };
}

export function removePractice(progress: Progress, lessonId: string, language: Language): Progress {
  const previous = progress.practice === undefined ? [] : progress.practice;
  if (!validPracticeKey(lessonId, language) || !validPracticeRecords(previous)) return progress;
  const practice = previous.filter(
    (record) => record.lesson_id !== lessonId || record.language !== language,
  );
  return practice.length === previous.length ? progress : { ...progress, practice };
}
