import { describe, expect, it } from 'vitest';
import { feedCourseFor } from '../src/lib/feed-course';
import { emptyProgress, validateProgress } from '../src/lib/state';
import type { FeedItem, Language, Lesson, Progress, Track, TrackId } from '../src/lib/types';

const cases = [
  [
    'agents-sdk',
    'https://openai.github.io/openai-agents-python/',
    'agent',
    'agent-agent-loop',
    'python',
  ],
  [
    'mcp-guide',
    'https://modelcontextprotocol.io/docs/getting-started/intro',
    'agent',
    'agent-mcp',
    'python',
  ],
  [
    'langgraph',
    'https://docs.langchain.com/oss/python/langgraph/overview',
    'agent',
    'agent-state-machine',
    'python',
  ],
  ['evals', 'https://platform.openai.com/docs/guides/evals', 'agent', 'agent-datasets', 'python'],
  [
    'fastapi-guide',
    'https://fastapi.tiangolo.com/tutorial/',
    'fullstack',
    'fullstack-routing',
    'python',
  ],
  ['hono-guide', 'https://hono.dev/docs/', 'fullstack', 'fullstack-routing', 'typescript'],
  ['go-guide', 'https://go.dev/blog/context', 'fullstack', 'fullstack-async', 'go'],
  ['jotai-guide', 'https://jotai.org/docs', 'fullstack', 'fullstack-jotai', 'go'],
] as const;

function item(id = 'fastapi-guide'): FeedItem {
  const row = cases.find((entry) => entry[0] === id)!;
  return {
    id: row[0],
    url: row[1],
    track: row[2],
    title: '固定精选资料',
    summary: '官方入门文档',
    source: '官方文档',
    tags: ['学习'],
    kind: 'guide',
    published: null,
  };
}

function lesson(id: string, track: TrackId): Lesson {
  return {
    id,
    track,
    stage: 'basics',
    title: id,
    objective: '课程目标',
    minutes: 15,
    level: '入门',
    body: ['正文'],
    steps: ['练习'],
    criteria: ['解释结果'],
    resources: [],
    quiz: { question: '问题', options: ['选项'], answer: 0, explanation: '解释' },
    snippets:
      track === 'agent'
        ? { python: 'print("agent")' }
        : { python: 'print("api")', typescript: 'console.log("api")', go: 'package main' },
  };
}

function tracks(): Track[] {
  return (['agent', 'fullstack'] as const).map((id) => ({
    id,
    title: id,
    description: '课程路线',
    languages: id === 'agent' ? ['python'] : ['typescript', 'go', 'python'],
    stages: [],
    lessons: [...new Set(cases.filter((row) => row[2] === id).map((row) => row[3]))].map((key) =>
      lesson(key, id),
    ),
  }));
}

function progress(): Progress {
  return { ...emptyProgress, language: 'go', notes: {}, completed: [], bookmarks: [], runs: [] };
}

function freeze<T>(value: T): T {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}

const completedAt = '2026-10-04T00:00:00.000Z';

describe('curated feed course links', () => {
  it.each(cases)(
    'links %s to its exact lesson and language',
    (id, _url, trackId, lessonId, language) => {
      const current = tracks();
      const result = feedCourseFor(item(id), current, progress());
      const sharedFrontend = id === 'jotai-guide';
      expect(result).toEqual({
        lesson: current
          .find((track) => track.id === trackId)!
          .lessons.find((entry) => entry.id === lessonId),
        language,
        href:
          `/lesson/${lessonId}` +
          (trackId === 'fullstack' && !sharedFrontend ? `?language=${language}` : ''),
        practiceRecorded: false,
        sharedFrontend,
      });
      expect(result?.lesson).toBe(
        current
          .find((track) => track.id === trackId)!
          .lessons.find((entry) => entry.id === lessonId),
      );
    },
  );

  it.each(['typescript', 'python', 'go'] as const)(
    'uses %s preference only for shared Jotai material',
    (language) => {
      const current = { ...progress(), language };
      expect(feedCourseFor(item('jotai-guide'), tracks(), current)).toMatchObject({
        language,
        href: '/lesson/fullstack-jotai',
        sharedFrontend: true,
      });
      expect(feedCourseFor(item('agents-sdk'), tracks(), current)).toMatchObject({
        language: 'python',
        href: '/lesson/agent-agent-loop',
        sharedFrontend: false,
      });
      expect(feedCourseFor(item('fastapi-guide'), tracks(), current)?.language).toBe('python');
      expect(current.language).toBe(language);
    },
  );

  it.each([
    { id: 'unknown-guide' },
    { id: 'python-123456789' },
    { kind: 'news' },
    { track: 'agent' },
    { url: 'https://fastapi.tiangolo.com/tutorial' },
    { url: 'https://fastapi.tiangolo.com/tutorial/?source=feed' },
    { url: 'https://fastapi.tiangolo.com/tutorial/#first-steps' },
    { url: 'https://FASTAPI.tiangolo.com/tutorial/' },
    { url: 'https://fastapi.tiangolo.com:443/tutorial/' },
    { url: 'https://example.com/tutorial/' },
    { url: 'http://fastapi.tiangolo.com/tutorial/' },
    { url: 'https://fastapi.tiangolo.com/tutorial/\n' },
    { id: 'fastapi-guide\n' },
  ])('rejects non-curated or changed identity %j', (change) => {
    expect(
      feedCourseFor({ ...item(), ...change } as FeedItem, tracks(), progress()),
    ).toBeUndefined();
  });

  it('does not infer a link from title, tags or source', () => {
    const source = {
      ...item(),
      id: 'unrelated',
      title: 'FastAPI',
      tags: ['Python'],
      source: 'FastAPI',
    };
    expect(feedCourseFor(source, tracks(), progress())).toBeUndefined();
    expect(
      feedCourseFor(
        { ...item(), title: '已更新的标题', tags: ['Go'], source: '备用显示名称' },
        tracks(),
        progress(),
      )?.language,
    ).toBe('python');
  });

  it('uses a saved copy when the live feed is unavailable without changing the v1 backup', () => {
    const old: Progress = {
      ...progress(),
      bookmarks: ['fastapi-guide'],
      savedItems: [item()],
      completed: ['fullstack-routing'],
      notes: { 'fullstack-routing': '已有笔记' },
    };
    const restored = JSON.parse(JSON.stringify(old)) as Progress;
    const before = JSON.stringify(restored);
    expect(validateProgress(restored)).toBe(true);
    expect(feedCourseFor(restored.savedItems![0], tracks(), restored)).toMatchObject({
      language: 'python',
      practiceRecorded: false,
    });
    expect(JSON.stringify(restored)).toBe(before);
    expect(restored).not.toHaveProperty('practice');
  });

  it('reads only the exact practice pair and never treats completion or evidence as practice', () => {
    const current: Progress = {
      ...progress(),
      completed: ['fullstack-routing'],
      practice: [{ lesson_id: 'fullstack-routing', language: 'go', completed_at: completedAt }],
      evidence: [
        {
          lesson_id: 'fullstack-routing',
          language: 'python',
          revision: 'v1',
          command: 'pytest',
          success: '通过',
          failure: '拒绝',
          pending: '部署',
          updated_at: completedAt,
        },
      ],
    };
    expect(feedCourseFor(item(), tracks(), current)?.practiceRecorded).toBe(false);
    expect(feedCourseFor(item('hono-guide'), tracks(), current)?.practiceRecorded).toBe(false);
    expect(feedCourseFor(item('go-guide'), tracks(), current)?.practiceRecorded).toBe(false);
    current.practice!.push({
      lesson_id: 'fullstack-routing',
      language: 'python',
      completed_at: completedAt,
    });
    expect(feedCourseFor(item(), tracks(), current)?.practiceRecorded).toBe(true);
    expect(feedCourseFor(item('hono-guide'), tracks(), current)?.practiceRecorded).toBe(false);
  });

  it('reads Jotai practice for the current preference, including after JSON restore', () => {
    const original: Progress = {
      ...progress(),
      practice: [{ lesson_id: 'fullstack-jotai', language: 'go', completed_at: completedAt }],
    };
    const restored = JSON.parse(JSON.stringify(original)) as Progress;
    expect(feedCourseFor(item('jotai-guide'), tracks(), restored)?.practiceRecorded).toBe(true);
    expect(
      feedCourseFor(item('jotai-guide'), tracks(), { ...restored, language: 'python' })
        ?.practiceRecorded,
    ).toBe(false);
  });

  it('does not mutate frozen progress, catalogue, or feed data', () => {
    const current = freeze({
      ...progress(),
      practice: [
        {
          lesson_id: 'agent-agent-loop',
          language: 'python' as Language,
          completed_at: completedAt,
        },
      ],
    });
    const catalogue = freeze(tracks());
    const source = freeze(item('agents-sdk'));
    const before = JSON.stringify([current, catalogue, source]);
    expect(feedCourseFor(source, catalogue, current)?.practiceRecorded).toBe(true);
    expect(JSON.stringify([current, catalogue, source])).toBe(before);
    expect(Object.keys(current).sort()).toEqual(Object.keys(progress()).concat('practice').sort());
  });

  it('does not trust malformed practice records', () => {
    const current = {
      ...progress(),
      practice: [{ lesson_id: 'fullstack-routing', language: 'python', completed_at: 'bad date' }],
    } as Progress;
    expect(feedCourseFor(item(), tracks(), current)?.practiceRecorded).toBe(false);
  });
});

describe('current course availability', () => {
  it('rejects a missing lesson or catalogue', () => {
    const current = tracks();
    current[1].lessons = current[1].lessons.filter((entry) => entry.id !== 'fullstack-routing');
    expect(feedCourseFor(item(), current, progress())).toBeUndefined();
    expect(feedCourseFor(item(), [], progress())).toBeUndefined();
  });

  it('rejects duplicate lesson routes within or across tracks', () => {
    for (const destination of [0, 1]) {
      const current = tracks();
      current[destination].lessons.push({ ...current[1].lessons[0] });
      expect(feedCourseFor(item(), current, progress())).toBeUndefined();
    }
  });

  it('rejects duplicate track IDs even when only one contains the lesson', () => {
    const current = tracks();
    current.push({ ...current[1], lessons: [] });
    expect(feedCourseFor(item(), current, progress())).toBeUndefined();
  });

  it('rejects a route moved to the wrong track or a mismatched lesson track', () => {
    const moved = tracks();
    moved[0].lessons.push(moved[1].lessons.shift()!);
    expect(feedCourseFor(item(), moved, progress())).toBeUndefined();
    const mismatched = tracks();
    mismatched[1].lessons[0].track = 'agent';
    expect(feedCourseFor(item(), mismatched, progress())).toBeUndefined();
  });

  it.each([undefined, null, {}, ['go'], ['python', 'rust']])(
    'rejects unavailable or invalid track languages %j',
    (languages) => {
      const current = tracks();
      current[1].languages = languages as Language[];
      expect(feedCourseFor(item(), current, progress())).toBeUndefined();
    },
  );

  it.each([undefined, null, '', ' \n\t', 123])(
    'rejects missing or empty corresponding snippet %j',
    (snippet) => {
      const current = tracks();
      current[1].lessons[0].snippets.python = snippet as string;
      expect(feedCourseFor(item(), current, progress())).toBeUndefined();
    },
  );

  it('does not use another language snippet or an inherited property as fallback', () => {
    const current = tracks();
    delete current[1].lessons[0].snippets.python;
    expect(feedCourseFor(item(), current, progress())).toBeUndefined();
    current[1].lessons[0].snippets = Object.create({ python: 'print("inherited")' });
    expect(feedCourseFor(item(), current, progress())).toBeUndefined();
  });

  it.each(['rust', '', null, ['go']])(
    'rejects an invalid Jotai language preference %j',
    (language) => {
      expect(
        feedCourseFor(item('jotai-guide'), tracks(), { ...progress(), language } as Progress),
      ).toBeUndefined();
    },
  );

  it('also requires the selected Jotai language to be available', () => {
    const current = tracks();
    delete current[1].lessons.find((entry) => entry.id === 'fullstack-jotai')!.snippets.go;
    expect(feedCourseFor(item('jotai-guide'), current, progress())).toBeUndefined();
  });

  it.each([null, {}, [null], [{ id: 'fullstack', lessons: null }]])(
    'fails closed for malformed catalogue containers %j',
    (value) => {
      expect(feedCourseFor(item(), value as Track[], progress())).toBeUndefined();
    },
  );
});
