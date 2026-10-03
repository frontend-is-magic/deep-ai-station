import type { LearningPosition, Lesson, Progress, ResumeState, Track, TrackId } from './types';

const trackIds = ['agent', 'fullstack'] as const;
function isRecord(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}
function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return (
    Reflect.ownKeys(value).length === keys.length &&
    Object.keys(value).length === keys.length &&
    keys.every((key) => Object.hasOwn(value, key))
  );
}
function validPosition(value: unknown): value is LearningPosition {
  if (!isRecord(value) || !exactKeys(value, ['lesson_id', 'visited_at'])) return false;
  if (
    typeof value.lesson_id !== 'string' ||
    value.lesson_id.length > 100 ||
    /^[a-z][a-z0-9-]*$/.exec(value.lesson_id)?.[0] !== value.lesson_id ||
    typeof value.visited_at !== 'string' ||
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.exec(value.visited_at)?.[0] !== value.visited_at
  )
    return false;
  const date = new Date(value.visited_at);
  return Number.isFinite(date.getTime()) && date.toISOString() === value.visited_at;
}
export function validResume(value: unknown): value is ResumeState {
  if (
    !isRecord(value) ||
    !exactKeys(value, ['last_track', 'positions']) ||
    !trackIds.some((id) => id === value.last_track) ||
    !isRecord(value.positions)
  )
    return false;
  const positions = value.positions;
  const keys = Reflect.ownKeys(positions);
  return (
    keys.length >= 1 &&
    keys.length <= 2 &&
    keys.every(
      (key) =>
        typeof key === 'string' &&
        trackIds.some((id) => id === key) &&
        Object.prototype.propertyIsEnumerable.call(positions, key) &&
        validPosition(positions[key]),
    ) &&
    Object.hasOwn(positions, value.last_track as TrackId)
  );
}
export function recordLessonVisit(
  progress: Progress,
  lesson: Pick<Lesson, 'id' | 'track'>,
  visitedAt: string,
): Progress {
  const position = { lesson_id: lesson.id, visited_at: visitedAt };
  if (
    !trackIds.includes(lesson.track) ||
    !validPosition(position) ||
    (progress.resume !== undefined && !validResume(progress.resume))
  )
    return progress;
  const previous = progress.resume?.positions[lesson.track];
  if (
    progress.resume?.last_track === lesson.track &&
    previous?.lesson_id === lesson.id &&
    previous.visited_at === visitedAt
  )
    return progress;
  return {
    ...progress,
    resume: {
      last_track: lesson.track,
      positions: { ...progress.resume?.positions, [lesson.track]: position },
    },
  };
}
export function resumeLesson(
  progress: Progress,
  tracks: Track[],
  trackId?: TrackId,
): Lesson | undefined {
  if (!validResume(progress.resume)) return undefined;
  const positions = progress.resume.positions;
  const find = (id: TrackId) =>
    tracks
      .find((track) => track.id === id)
      ?.lessons.find((lesson) => lesson.id === positions[id]?.lesson_id && lesson.track === id);
  if (trackId !== undefined) return find(trackId);
  // Navigation order is explicit: equal or inaccurate client clocks cannot reorder visits.
  return find(progress.resume.last_track) || trackIds.map(find).find(Boolean);
}
export function nextIncompleteLesson(progress: Progress, lessons: Lesson[]): Lesson | undefined {
  return lessons.find((lesson) => !progress.completed.includes(lesson.id));
}
