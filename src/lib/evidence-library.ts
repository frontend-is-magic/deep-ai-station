import { courseLabFor } from './course-labs';
import { evidenceFor, validEvidenceRecords } from './evidence';
import type { EvidenceRecord, Language, Lesson, Progress, Track, TrackId } from './types';

export interface EvidenceLibraryEntry {
  record: EvidenceRecord;
  track: TrackId;
  lesson?: Lesson;
  href?: string;
  editable: boolean;
  hasContent: boolean;
  hasPending: boolean;
}

export interface EvidenceLibraryFilters {
  track?: TrackId | 'all';
  language?: Language | 'all';
  pendingOnly?: boolean;
}

const contentFields = ['revision', 'command', 'success', 'failure', 'pending'] as const;
const languages: readonly Language[] = ['typescript', 'go', 'python'];
const compareText = (left: string, right: string) => (left < right ? -1 : left > right ? 1 : 0);

export function resolveEvidenceEntries(
  progress: Progress,
  tracks: Track[],
): EvidenceLibraryEntry[] {
  const records = progress.evidence ?? [];
  if (!validEvidenceRecords(records)) return [];
  const parents = new Map<string, number>();
  const lessons = new Map<string, Array<{ lesson: Lesson; parent: Track }>>();
  // A broken catalog disables navigation, never discards valid saved records.
  if (Array.isArray(tracks) && tracks.every((track) => track && Array.isArray(track.lessons))) {
    for (const track of tracks) {
      parents.set(track.id, (parents.get(track.id) ?? 0) + 1);
      for (const lesson of track.lessons) {
        if (!lesson) continue;
        const matches = lessons.get(lesson.id) ?? [];
        matches.push({ lesson, parent: track });
        lessons.set(lesson.id, matches);
      }
    }
  }
  return [...records]
    .sort(
      (left, right) =>
        Date.parse(right.updated_at) - Date.parse(left.updated_at) ||
        compareText(left.lesson_id, right.lesson_id) ||
        compareText(left.language, right.language),
    )
    .map((record) => {
      const track: TrackId = record.lesson_id.startsWith('agent-') ? 'agent' : 'fullstack';
      const entry: EvidenceLibraryEntry = {
        record,
        track,
        editable: false,
        hasContent: contentFields.some((field) => Boolean(record[field].trim())),
        hasPending: Boolean(record.pending.trim()),
      };
      const matches = lessons.get(record.lesson_id) ?? [];
      const match = matches[0];
      if (
        // This checks only link eligibility; legacy IDs stay in the index and exports.
        /^(agent|fullstack)-[a-z0-9]+(?:-[a-z0-9]+)*$/.exec(record.lesson_id)?.[0] !==
          record.lesson_id ||
        matches.length !== 1 ||
        !match ||
        parents.get(track) !== 1 ||
        match.parent.id !== track ||
        match.lesson.track !== track ||
        !Array.isArray(match.parent.languages) ||
        !match.parent.languages.every((language) => languages.includes(language)) ||
        !match.parent.languages.includes(record.language) ||
        !match.lesson.snippets ||
        !Object.hasOwn(match.lesson.snippets, record.language) ||
        typeof match.lesson.snippets[record.language] !== 'string' ||
        !match.lesson.snippets[record.language]?.trim()
      )
        return entry;
      return {
        ...entry,
        lesson: match.lesson,
        href:
          `/lesson/${encodeURIComponent(record.lesson_id)}` +
          (track === 'fullstack' ? `?language=${record.language}` : ''),
        editable:
          Boolean(courseLabFor(record.lesson_id, record.language)) ||
          (track === 'fullstack' && match.lesson.stage === 'ship') ||
          (track === 'agent' && match.lesson.stage === 'capstone'),
      };
    });
}

export function filterEvidenceEntries(
  entries: readonly EvidenceLibraryEntry[],
  filters: EvidenceLibraryFilters = {},
): EvidenceLibraryEntry[] {
  return entries.filter(
    (entry) =>
      (!filters.track || filters.track === 'all' || entry.track === filters.track) &&
      (!filters.language ||
        filters.language === 'all' ||
        entry.record.language === filters.language) &&
      (!filters.pendingOnly || entry.hasPending),
  );
}

// The caller supplies its just-read latest Progress. This function never reads storage.
export function evidenceRecordForExport(
  current: Progress,
  lessonId: string,
  language: Language,
): EvidenceRecord | undefined {
  const record = evidenceFor(current, lessonId, language);
  return record ? { ...record } : undefined;
}
