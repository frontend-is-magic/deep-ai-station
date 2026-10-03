import { latestProgress, toggleBookmark } from './state';
import type { FeedItem, Progress } from './types';

export const RESET_NOTICE =
  '学习记录已被导入或清空，本次旧页面内容未保存；请检查当前记录后重新填写或运行。';

// Re-read before an edit; this is not an atomic transaction across browser tabs.
export function editLatestProgress(
  previous: Progress,
  change: (current: Progress) => Progress,
  context?: { resetId: Progress['history_reset_id'] },
): { progress: Progress; reset: boolean } {
  const current = latestProgress(previous);
  if (context && context.resetId !== current.history_reset_id) {
    return { progress: current, reset: true };
  }
  return { progress: change(current), reset: false };
}

export function setBookmarkSaved(current: Progress, item: FeedItem, saved: boolean): Progress {
  return current.bookmarks.includes(item.id) === saved ? current : toggleBookmark(current, item);
}

type CapacityEdit = { progress: Progress; capacityReached: boolean };

export function setLessonNote(current: Progress, lessonId: string, value: string): CapacityEdit {
  const exists = Object.prototype.hasOwnProperty.call(current.notes, lessonId);
  if (value === '') {
    if (!exists) return { progress: current, capacityReached: false };
    const notes = { ...current.notes };
    delete notes[lessonId];
    return { progress: { ...current, notes }, capacityReached: false };
  }
  if (!exists && Object.keys(current.notes).length >= 1000) {
    return { progress: current, capacityReached: true };
  }
  if (exists && current.notes[lessonId] === value) {
    return { progress: current, capacityReached: false };
  }
  return {
    progress: { ...current, notes: { ...current.notes, [lessonId]: value } },
    capacityReached: false,
  };
}

export function setLessonCompleted(
  current: Progress,
  lessonId: string,
  completed: boolean,
): CapacityEdit {
  if (current.completed.includes(lessonId) === completed) {
    return { progress: current, capacityReached: false };
  }
  if (completed && current.completed.length >= 2000) {
    return { progress: current, capacityReached: true };
  }
  return {
    progress: {
      ...current,
      completed: completed
        ? [...current.completed, lessonId]
        : current.completed.filter((id) => id !== lessonId),
    },
    capacityReached: false,
  };
}
