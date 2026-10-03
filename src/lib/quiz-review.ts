import type { Lesson, Progress, QuizReviewRecord, Track } from './types';

export const MAX_QUIZ_REVIEWS = 64;

function validLessonId(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    value.length <= 100 &&
    /^(agent|fullstack)-[a-z0-9]+(?:-[a-z0-9]+)*$/.exec(value)?.[0] === value
  );
}

function validRecord(value: unknown): value is QuizReviewRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) return false;
  if (
    Reflect.ownKeys(value).length !== 2 ||
    Object.keys(value).length !== 2 ||
    !Object.hasOwn(value, 'lesson_id') ||
    !Object.hasOwn(value, 'added_at')
  )
    return false;
  const record = value as QuizReviewRecord;
  if (
    !validLessonId(record.lesson_id) ||
    typeof record.added_at !== 'string' ||
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.exec(record.added_at)?.[0] !== record.added_at
  )
    return false;
  const date = new Date(record.added_at);
  return Number.isFinite(date.getTime()) && date.toISOString() === record.added_at;
}

export function validQuizReviewRecords(value: unknown): value is QuizReviewRecord[] {
  return (
    Array.isArray(value) &&
    value.length <= MAX_QUIZ_REVIEWS &&
    Array.from(value).every(validRecord) &&
    new Set(value.map((record) => record.lesson_id)).size === value.length
  );
}

export function addQuizReview(progress: Progress, lessonId: string, addedAt: string): Progress {
  const record = { lesson_id: lessonId, added_at: addedAt };
  const previous = progress.quizReview === undefined ? [] : progress.quizReview;
  if (!validRecord(record) || !validQuizReviewRecords(previous)) return progress;
  if (previous.some((item) => item.lesson_id === lessonId) || previous.length >= MAX_QUIZ_REVIEWS)
    return progress;
  return { ...progress, quizReview: [...previous, record] };
}

export function removeQuizReview(progress: Progress, lessonId: string): Progress {
  const previous = progress.quizReview === undefined ? [] : progress.quizReview;
  if (!validLessonId(lessonId) || !validQuizReviewRecords(previous)) return progress;
  const quizReview = previous.filter((record) => record.lesson_id !== lessonId);
  return quizReview.length === previous.length ? progress : { ...progress, quizReview };
}

export function resolveQuizReviews(
  progress: Progress,
  tracks: Track[],
): {
  available: Array<{ record: QuizReviewRecord; lesson: Lesson }>;
  unavailable: QuizReviewRecord[];
} {
  const previous = progress.quizReview === undefined ? [] : progress.quizReview;
  const available: Array<{ record: QuizReviewRecord; lesson: Lesson }> = [];
  const unavailable: QuizReviewRecord[] = [];
  if (!validQuizReviewRecords(previous)) return { available, unavailable };

  const parents = new Map<string, number>();
  const lessons = new Map<string, Array<{ lesson: Lesson; parent: string }>>();
  for (const track of tracks) {
    parents.set(track.id, (parents.get(track.id) ?? 0) + 1);
    for (const lesson of track.lessons) {
      const matches = lessons.get(lesson.id) ?? [];
      matches.push({ lesson, parent: track.id });
      lessons.set(lesson.id, matches);
    }
  }
  const ordered = [...previous].sort((a, b) => {
    const time = Date.parse(a.added_at) - Date.parse(b.added_at);
    return time || (a.lesson_id < b.lesson_id ? -1 : a.lesson_id > b.lesson_id ? 1 : 0);
  });
  for (const record of ordered) {
    const matches = lessons.get(record.lesson_id) ?? [];
    const match = matches[0];
    if (
      matches.length === 1 &&
      match &&
      (match.parent === 'agent' || match.parent === 'fullstack') &&
      parents.get(match.parent) === 1 &&
      match.lesson.track === match.parent &&
      record.lesson_id.startsWith(`${match.parent}-`)
    )
      available.push({ record, lesson: match.lesson });
    else unavailable.push(record);
  }
  return { available, unavailable };
}
