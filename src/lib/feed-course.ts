import { practiceFor } from './practice';
import type { FeedItem, Language, Lesson, Progress, Track, TrackId } from './types';

export interface FeedCourseLink {
  readonly lesson: Lesson;
  readonly language: Language;
  readonly href: string;
  readonly practiceRecorded: boolean;
  readonly sharedFrontend: boolean;
}

type CuratedCourse = Readonly<{
  id: string;
  url: string;
  track: TrackId;
  lessonId: string;
  language: Language | 'preference';
}>;

// Deliberately match the original CURATED URLs in backend/feed.py, without redirect normalization.
const curatedCourses: readonly CuratedCourse[] = [
  {
    id: 'agents-sdk',
    url: 'https://openai.github.io/openai-agents-python/',
    track: 'agent',
    lessonId: 'agent-agent-loop',
    language: 'python',
  },
  {
    id: 'mcp-guide',
    url: 'https://modelcontextprotocol.io/docs/getting-started/intro',
    track: 'agent',
    lessonId: 'agent-mcp',
    language: 'python',
  },
  {
    id: 'langgraph',
    url: 'https://docs.langchain.com/oss/python/langgraph/overview',
    track: 'agent',
    lessonId: 'agent-state-machine',
    language: 'python',
  },
  {
    id: 'evals',
    url: 'https://platform.openai.com/docs/guides/evals',
    track: 'agent',
    lessonId: 'agent-datasets',
    language: 'python',
  },
  {
    id: 'fastapi-guide',
    url: 'https://fastapi.tiangolo.com/tutorial/',
    track: 'fullstack',
    lessonId: 'fullstack-routing',
    language: 'python',
  },
  {
    id: 'hono-guide',
    url: 'https://hono.dev/docs/',
    track: 'fullstack',
    lessonId: 'fullstack-routing',
    language: 'typescript',
  },
  {
    id: 'go-guide',
    url: 'https://go.dev/blog/context',
    track: 'fullstack',
    lessonId: 'fullstack-async',
    language: 'go',
  },
  {
    id: 'jotai-guide',
    url: 'https://jotai.org/docs',
    track: 'fullstack',
    lessonId: 'fullstack-jotai',
    language: 'preference',
  },
];

function isLanguage(value: unknown): value is Language {
  return value === 'typescript' || value === 'python' || value === 'go';
}

export function feedCourseFor(
  item: FeedItem,
  tracks: Track[],
  progress: Progress,
): FeedCourseLink | undefined {
  if (!item || item.kind !== 'guide' || !Array.isArray(tracks) || !progress) return undefined;
  const mapping = curatedCourses.find(
    (entry) => entry.id === item.id && entry.url === item.url && entry.track === item.track,
  );
  if (!mapping) return undefined;
  const language = mapping.language === 'preference' ? progress.language : mapping.language;
  if (!isLanguage(language)) return undefined;

  // Lesson routes resolve across all tracks. Ambiguous IDs must not choose the first match.
  if (tracks.some((track) => !track || !Array.isArray(track.lessons))) return undefined;
  const owners = tracks.filter((track) => track.id === mapping.track);
  const matches = tracks.flatMap((track) =>
    track.lessons
      .filter((lesson) => lesson?.id === mapping.lessonId)
      .map((lesson) => ({ track, lesson })),
  );
  if (owners.length !== 1 || matches.length !== 1) return undefined;
  const { track, lesson } = matches[0];
  if (
    track !== owners[0] ||
    lesson.track !== mapping.track ||
    !Array.isArray(track.languages) ||
    !track.languages.every(isLanguage) ||
    !track.languages.includes(language) ||
    !lesson.snippets ||
    typeof lesson.snippets !== 'object' ||
    Array.isArray(lesson.snippets) ||
    !Object.hasOwn(lesson.snippets, language)
  )
    return undefined;
  const snippet = lesson.snippets[language];
  if (typeof snippet !== 'string' || !snippet.trim()) return undefined;

  const sharedFrontend = mapping.language === 'preference';
  return {
    lesson,
    language,
    href:
      `/lesson/${lesson.id}` +
      (mapping.track === 'fullstack' && !sharedFrontend ? `?language=${language}` : ''),
    practiceRecorded: Boolean(practiceFor(progress, lesson.id, language)),
    sharedFrontend,
  };
}
