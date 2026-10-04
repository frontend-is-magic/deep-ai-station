import { courseLabFor, type CourseLab } from './course-labs';
import { practiceFor } from './practice';
import type { Language, Lesson, Progress, Track, TrackId } from './types';

export interface LabPracticeGroup {
  readonly lab: CourseLab;
  readonly language: Language;
  readonly lessons: readonly {
    readonly lesson: Lesson;
    readonly href: string;
    readonly practiceRecorded: boolean;
  }[];
}

function isLanguage(value: unknown): value is Language {
  return value === 'typescript' || value === 'go' || value === 'python';
}

export function labPracticeGroups(
  tracks: Track[],
  trackId: TrackId,
  progress: Progress,
): LabPracticeGroup[] {
  if (
    (trackId !== 'agent' && trackId !== 'fullstack') ||
    !progress ||
    !Array.isArray(tracks) ||
    Array.from(tracks).some((track) => !track || !Array.isArray(track.lessons))
  )
    return [];
  const parents = tracks.filter((track) => track.id === trackId);
  if (parents.length !== 1) return [];
  const track = parents[0];
  const language = trackId === 'agent' ? 'python' : progress.language;
  if (
    !isLanguage(language) ||
    !Array.isArray(track.languages) ||
    !Array.from(track.languages).every(isLanguage) ||
    !track.languages.includes(language)
  )
    return [];

  // Route lookup spans all tracks, including entries that cannot themselves form links.
  const occurrences = new Map<string, number>();
  for (const parent of tracks) {
    for (const lesson of parent.lessons) {
      if (typeof lesson?.id === 'string')
        occurrences.set(lesson.id, (occurrences.get(lesson.id) ?? 0) + 1);
    }
  }
  const groups = new Map<
    CourseLab['id'],
    { lab: CourseLab; language: Language; lessons: LabPracticeGroup['lessons'][number][] }
  >();
  for (const lesson of track.lessons) {
    if (
      !lesson ||
      typeof lesson.id !== 'string' ||
      lesson.id.length > 100 ||
      /^(agent|fullstack)-[a-z0-9]+(?:-[a-z0-9]+)*$/.exec(lesson.id)?.[0] !== lesson.id ||
      !lesson.id.startsWith(`${trackId}-`) ||
      lesson.track !== trackId ||
      occurrences.get(lesson.id) !== 1 ||
      !lesson.snippets ||
      typeof lesson.snippets !== 'object' ||
      Array.isArray(lesson.snippets) ||
      !Object.hasOwn(lesson.snippets, language)
    )
      continue;
    const snippet = lesson.snippets[language];
    if (typeof snippet !== 'string' || !snippet.trim()) continue;
    const lab = courseLabFor(lesson.id, language);
    if (!lab) continue;
    let group = groups.get(lab.id);
    if (!group) {
      group = { lab, language, lessons: [] };
      groups.set(lab.id, group);
    }
    group.lessons.push({
      lesson,
      href:
        `/lesson/${lesson.id}` +
        (trackId === 'fullstack' ? `?language=${language}` : '') +
        '#course-lab',
      practiceRecorded: Boolean(practiceFor(progress, lesson.id, language)),
    });
  }
  return [...groups.values()];
}
