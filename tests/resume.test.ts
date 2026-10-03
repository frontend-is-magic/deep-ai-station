import { describe, expect, it } from 'vitest';
import {
  nextIncompleteLesson,
  recordLessonVisit,
  resumeLesson,
  validResume,
} from '../src/lib/resume';
import { emptyProgress, validateProgress } from '../src/lib/state';
import type { Lesson, Progress, ResumeState, Track, TrackId } from '../src/lib/types';

const visitedAt = '2026-10-03T12:34:56.000Z';
const later = '2026-10-04T01:02:03.004Z';

function position(lessonId = 'agent-tools', time = visitedAt) {
  return { lesson_id: lessonId, visited_at: time };
}

function resume(overrides: Partial<ResumeState> = {}): ResumeState {
  return {
    last_track: 'agent',
    positions: { agent: position() },
    ...overrides,
  };
}

function lesson(id: string, track: TrackId): Lesson {
  return {
    id,
    track,
    stage: 'basics',
    title: id,
    objective: '完成课程练习',
    minutes: 15,
    level: '入门',
    body: ['课程内容'],
    steps: ['阅读并练习'],
    criteria: ['解释运行结果'],
    resources: [],
    quiz: { question: '是否完成？', options: ['是', '否'], answer: 0, explanation: '完成练习' },
    snippets: { python: 'print("hello")' },
  };
}

const agentLessons = [lesson('agent-tools', 'agent'), lesson('agent-memory', 'agent')];
const fullstackLessons = [
  lesson('fullstack-routing', 'fullstack'),
  lesson('fullstack-storage', 'fullstack'),
];

function track(id: TrackId, lessons: Lesson[]): Track {
  return {
    id,
    title: id,
    description: '课程路线',
    languages: ['python'],
    stages: [
      {
        id: 'basics',
        number: 1,
        title: '基础',
        description: '课程基础',
        lessons: lessons.map((item) => item.id),
      },
    ],
    lessons,
  };
}

const tracks = [track('fullstack', fullstackLessons), track('agent', agentLessons)];

function existingProgress(): Progress {
  return {
    ...emptyProgress,
    completed: ['agent-tools'],
    bookmarks: ['saved-guide'],
    savedItems: [
      {
        id: 'saved-guide',
        title: '课程资料',
        summary: '已收藏的学习资料',
        source: '课程文档',
        url: 'https://example.com/guide',
        track: 'agent',
        tags: ['tools'],
        kind: 'guide',
        published: null,
      },
    ],
    language: 'go',
    notes: { 'agent-tools': '保留已有笔记' },
    runs: [
      {
        id: 'saved-run',
        prompt: '解释工具调用',
        answer: '已有运行结果',
        provider: 'demo',
        track: 'agent',
        date: visitedAt,
        duration_ms: 10,
      },
    ],
    practice: [{ lesson_id: 'fullstack-routing', language: 'go', completed_at: visitedAt }],
    evidence: [
      {
        lesson_id: 'fullstack-routing',
        language: 'go',
        revision: 'lesson-example',
        command: 'go test ./...',
        success: '本地通过',
        failure: '非法输入已拒绝',
        pending: '尚未部署',
        updated_at: visitedAt,
      },
    ],
  };
}

function expectOtherProgressUnchanged(result: Progress, original: Progress) {
  expect(result.version).toBe(original.version);
  expect(result.completed).toBe(original.completed);
  expect(result.bookmarks).toBe(original.bookmarks);
  expect(result.savedItems).toBe(original.savedItems);
  expect(result.language).toBe(original.language);
  expect(result.notes).toBe(original.notes);
  expect(result.runs).toBe(original.runs);
  expect(result.practice).toBe(original.practice);
  expect(result.evidence).toBe(original.evidence);
}

function expectInvalid(value: unknown) {
  expect(validResume(value)).toBe(false);
  expect(validateProgress({ ...emptyProgress, resume: value })).toBe(false);
}

describe('resume import validation', () => {
  it('accepts old version-one backups without inventing a last visit', () => {
    const old = JSON.parse(JSON.stringify(existingProgress()));
    const before = JSON.stringify(old);
    expect(validateProgress(old)).toBe(true);
    expect(old.version).toBe(1);
    expect(old.resume).toBeUndefined();
    expect(emptyProgress.resume).toBeUndefined();
    expect(validResume(undefined)).toBe(false);
    expect(validateProgress({ ...old, resume: undefined })).toBe(true);
    expect(resumeLesson(old, tracks)).toBeUndefined();
    expect(resumeLesson(old, tracks, 'agent')).toBeUndefined();
    expect(JSON.stringify(old)).toBe(before);
  });

  it('roundtrips both route positions and the selected route with the rest of version-one progress', () => {
    const original = existingProgress();
    const saved = recordLessonVisit(
      recordLessonVisit(original, agentLessons[1], visitedAt),
      fullstackLessons[0],
      later,
    );
    const restored = JSON.parse(JSON.stringify(saved));
    expect(validateProgress(restored)).toBe(true);
    expect(restored).toEqual(saved);
    expect(restored.version).toBe(1);
    expect(resumeLesson(restored, tracks)).toBe(fullstackLessons[0]);
    expect(resumeLesson(restored, tracks, 'agent')).toBe(agentLessons[1]);
    expectOtherProgressUnchanged(saved, original);
    expect(original.resume).toBeUndefined();
  });

  it('validates structure separately from course existence and route membership', () => {
    const saved = {
      ...existingProgress(),
      resume: resume({
        positions: {
          agent: position('unknown-course'),
          fullstack: position('agent-tools'),
        },
      }),
    };
    expect(validResume(saved.resume)).toBe(true);
    expect(validateProgress(saved)).toBe(true);
    expect(resumeLesson(saved, tracks)).toBeUndefined();
    expect(saved.completed).toEqual(['agent-tools']);
    expect(saved.notes['agent-tools']).toBe('保留已有笔记');
  });

  it.each(['a', 'a'.repeat(100), 'a-', 'a--b', 'unknown-course'])(
    'accepts bounded ASCII lesson id %s without requiring a known route prefix',
    (id) => expect(validResume(resume({ positions: { agent: position(id) } }))).toBe(true),
  );

  it.each(['0000-01-01T00:00:00.000Z', '2024-02-29T23:59:59.999Z', '9999-12-31T23:59:59.999Z'])(
    'accepts canonical four-digit UTC timestamp %s',
    (time) =>
      expect(validResume(resume({ positions: { agent: position('agent-tools', time) } }))).toBe(
        true,
      ),
  );

  it.each([
    ['null', null],
    ['array', []],
    ['string', 'agent'],
    ['number', 1],
    ['boolean', true],
    ['empty object', {}],
    ['missing last track', { positions: { agent: position() } }],
    ['missing positions', { last_track: 'agent' }],
    ['unknown last track', { ...resume(), last_track: 'other' }],
    ['extra key', { ...resume(), language: 'python' }],
    ['extra undefined key', { ...resume(), ignored: undefined }],
    ['extra symbol', { ...resume(), [Symbol('extra')]: true }],
    ['null positions', { ...resume(), positions: null }],
    ['array positions', { ...resume(), positions: [position()] }],
    ['empty positions', { ...resume(), positions: {} }],
    [
      'missing selected position',
      { ...resume(), positions: { fullstack: position('fullstack-routing') } },
    ],
    [
      'unknown position route',
      { ...resume(), positions: { agent: position(), other: position() } },
    ],
    [
      'three positions',
      {
        ...resume(),
        positions: {
          agent: position(),
          fullstack: position('fullstack-routing'),
          other: position(),
        },
      },
    ],
    [
      'symbol position route',
      { ...resume(), positions: { agent: position(), [Symbol('extra')]: position() } },
    ],
    ['undefined position', { ...resume(), positions: { agent: undefined } }],
    ['null position', { ...resume(), positions: { agent: null } }],
    ['array position', { ...resume(), positions: { agent: [] } }],
    ['missing lesson id', { ...resume(), positions: { agent: { visited_at: visitedAt } } }],
    ['missing time', { ...resume(), positions: { agent: { lesson_id: 'agent-tools' } } }],
    [
      'extra position key',
      { ...resume(), positions: { agent: { ...position(), completed: true } } },
    ],
    [
      'extra position symbol',
      { ...resume(), positions: { agent: { ...position(), [Symbol('extra')]: true } } },
    ],
  ])('rejects %s in resume state and imported progress', (_label, value) => expectInvalid(value));

  it.each([
    ['empty lesson', ''],
    ['oversized lesson', 'a'.repeat(101)],
    ['uppercase lesson', 'Agent-tools'],
    ['numeric first character', '1-agent-tools'],
    ['leading hyphen', '-agent-tools'],
    ['non-ASCII lesson', 'agent-工具'],
    ['path traversal', '../agent-tools'],
    ['path separator', 'agent/tools'],
    ['underscore', 'agent_tools'],
    ['leading whitespace', ' agent-tools'],
    ['trailing whitespace', 'agent-tools '],
    ['trailing newline', 'agent-tools\n'],
    ['embedded newline', 'agent\ntools'],
    ['numeric lesson', 1],
    ['undefined lesson', undefined],
  ])('rejects %s', (_label, id) => {
    expectInvalid({ ...resume(), positions: { agent: { ...position(), lesson_id: id } } });
  });

  it.each([
    ['undefined time', undefined],
    ['numeric time', 1791030896000],
    ['empty time', ''],
    ['invalid time', 'not-a-date'],
    ['date only', '2026-10-03'],
    ['missing milliseconds', '2026-10-03T12:34:56Z'],
    ['one millisecond digit', '2026-10-03T12:34:56.0Z'],
    ['extra millisecond digit', '2026-10-03T12:34:56.0000Z'],
    ['timezone offset', '2026-10-03T20:34:56.000+08:00'],
    ['lowercase zone', '2026-10-03T12:34:56.000z'],
    ['normalized impossible day', '2026-02-30T12:34:56.000Z'],
    ['invalid non-leap February', '2026-02-29T12:34:56.000Z'],
    ['normalized hour', '2026-10-03T24:00:00.000Z'],
    ['invalid month', '2026-13-03T12:34:56.000Z'],
    ['trailing newline', visitedAt + '\n'],
    ['negative year', '-000001-10-03T12:34:56.000Z'],
    ['extended year', '+010000-10-03T12:34:56.000Z'],
  ])('rejects %s', (_label, time) => {
    expectInvalid({ ...resume(), positions: { agent: { ...position(), visited_at: time } } });
  });

  it('rejects inherited fields and custom prototypes at every object level', () => {
    const inheritedState = Object.assign(Object.create({ last_track: 'agent' }), {
      positions: { agent: position() },
    });
    const inheritedPositions = Object.create({ agent: position() });
    const inheritedPosition = Object.assign(Object.create({ visited_at: visitedAt }), {
      lesson_id: 'agent-tools',
    });
    for (const value of [
      inheritedState,
      { ...resume(), positions: inheritedPositions },
      { ...resume(), positions: { agent: inheritedPosition } },
      Object.assign(Object.create({ custom: true }), resume()),
      {
        ...resume(),
        positions: Object.assign(Object.create({ custom: true }), { agent: position() }),
      },
      {
        ...resume(),
        positions: { agent: Object.assign(Object.create({ custom: true }), position()) },
      },
    ])
      expectInvalid(value);
  });

  it('rejects hidden keys and required fields that would be dropped during JSON export', () => {
    for (const value of [
      Object.defineProperty(resume(), 'hidden', { value: true }),
      Object.defineProperty(resume(), 'last_track', { enumerable: false }),
      {
        ...resume(),
        positions: Object.defineProperty({ agent: position() }, 'hidden', { value: true }),
      },
      {
        ...resume(),
        positions: Object.defineProperty({ agent: position() }, 'agent', { enumerable: false }),
      },
      {
        ...resume(),
        positions: { agent: Object.defineProperty(position(), 'hidden', { value: true }) },
      },
      {
        ...resume(),
        positions: {
          agent: Object.defineProperty(position(), 'visited_at', { enumerable: false }),
        },
      },
    ])
      expectInvalid(value);
  });
});

describe('recording lesson visits', () => {
  it('adds a visit while preserving all unrelated progress values and references', () => {
    const original = existingProgress();
    const saved = recordLessonVisit(original, agentLessons[1], visitedAt);
    expect(saved).not.toBe(original);
    expectOtherProgressUnchanged(saved, original);
    expect(saved.resume).toEqual(resume({ positions: { agent: position('agent-memory') } }));
    expect(original.resume).toBeUndefined();
    expect(validateProgress(saved)).toBe(true);
  });

  it('returns the original progress only when lesson, time and selected track are unchanged', () => {
    const first = recordLessonVisit(existingProgress(), agentLessons[0], visitedAt);
    expect(recordLessonVisit(first, agentLessons[0], visitedAt)).toBe(first);
    const laterVisit = recordLessonVisit(first, agentLessons[0], later);
    expect(laterVisit).not.toBe(first);
    expect(laterVisit.resume?.positions.agent).toEqual(position('agent-tools', later));
    expect(first.resume?.positions.agent).toEqual(position());
    expectOtherProgressUnchanged(laterVisit, first);
  });

  it('switches the selected track even when both saved positions have the same timestamp', () => {
    const first = recordLessonVisit(existingProgress(), agentLessons[0], visitedAt);
    const both = recordLessonVisit(first, fullstackLessons[0], visitedAt);
    const switched = recordLessonVisit(both, agentLessons[0], visitedAt);
    expect(both.resume?.last_track).toBe('fullstack');
    expect(switched).not.toBe(both);
    expect(switched.resume?.last_track).toBe('agent');
    expect(switched.resume?.positions).toEqual(both.resume?.positions);
    expect(resumeLesson(switched, tracks)).toBe(agentLessons[0]);
    expect(recordLessonVisit(switched, agentLessons[0], visitedAt)).toBe(switched);
    expectOtherProgressUnchanged(switched, both);
  });

  it('keeps only the latest visited lesson per route without mutating previous snapshots', () => {
    const agent = recordLessonVisit(existingProgress(), agentLessons[0], visitedAt);
    const both = recordLessonVisit(agent, fullstackLessons[0], visitedAt);
    const nextAgent = recordLessonVisit(both, agentLessons[1], later);
    const nextFullstack = recordLessonVisit(nextAgent, fullstackLessons[1], later);
    expect(nextFullstack.resume).toEqual({
      last_track: 'fullstack',
      positions: {
        agent: position('agent-memory', later),
        fullstack: position('fullstack-storage', later),
      },
    });
    expect(both.resume?.positions).toEqual({
      agent: position(),
      fullstack: position('fullstack-routing'),
    });
    expect(agent.resume?.positions).toEqual({ agent: position() });
    expectOtherProgressUnchanged(nextFullstack, both);
  });

  it('leaves existing progress unchanged for an invalid lesson id, route or timestamp', () => {
    const original = recordLessonVisit(existingProgress(), agentLessons[0], visitedAt);
    for (const id of ['', '../agent-tools', 'agent-tools\n', 'a'.repeat(101)]) {
      expect(recordLessonVisit(original, { id, track: 'agent' }, visitedAt)).toBe(original);
    }
    expect(
      recordLessonVisit(original, { id: 'agent-tools', track: 'other' as TrackId }, visitedAt),
    ).toBe(original);
    for (const time of ['invalid', '2026-02-30T12:34:56.000Z', '+010000-10-03T12:34:56.000Z']) {
      expect(recordLessonVisit(original, agentLessons[0], time)).toBe(original);
    }
  });
});

describe('looking up a resume lesson', () => {
  it('uses the explicitly requested route even when another route was visited last', () => {
    const progress: Progress = {
      ...existingProgress(),
      resume: resume({
        positions: { agent: position(), fullstack: position('fullstack-routing') },
      }),
    };
    expect(resumeLesson(progress, tracks, 'fullstack')).toBe(fullstackLessons[0]);
    expect(resumeLesson(progress, tracks, 'agent')).toBe(agentLessons[0]);
    expect(resumeLesson(progress, tracks)).toBe(agentLessons[0]);
    expect(progress.completed).toContain('agent-tools');
  });

  it('prefers the selected route over timestamp order or catalog order', () => {
    const progress: Progress = {
      ...emptyProgress,
      resume: resume({
        last_track: 'fullstack',
        positions: {
          agent: position('agent-tools', later),
          fullstack: position('fullstack-routing'),
        },
      }),
    };
    expect(resumeLesson(progress, [...tracks].reverse())).toBe(fullstackLessons[0]);
  });

  it('does not fall back to another route when an explicit route has no saved position', () => {
    const progress: Progress = { ...emptyProgress, resume: resume() };
    expect(resumeLesson(progress, tracks, 'fullstack')).toBeUndefined();
    expect(resumeLesson(progress, tracks)).toBe(agentLessons[0]);
  });

  it.each(['unknown-course', 'fullstack-routing'])(
    'ignores unavailable or wrong-route lesson %s and falls back only for the global lookup',
    (id) => {
      const progress: Progress = {
        ...existingProgress(),
        resume: resume({
          positions: { agent: position(id), fullstack: position('fullstack-routing') },
        }),
      };
      expect(validateProgress(progress)).toBe(true);
      expect(resumeLesson(progress, tracks, 'agent')).toBeUndefined();
      expect(resumeLesson(progress, tracks)).toBe(fullstackLessons[0]);
    },
  );

  it('falls back to the valid Agent position when the last Fullstack lesson was removed', () => {
    const progress: Progress = {
      ...emptyProgress,
      resume: resume({
        last_track: 'fullstack',
        positions: { agent: position(), fullstack: position('removed-course') },
      }),
    };
    expect(resumeLesson(progress, tracks)).toBe(agentLessons[0]);
    expect(resumeLesson(progress, tracks, 'fullstack')).toBeUndefined();
  });

  it('checks the lesson track inside its catalog rather than trusting only a matching id', () => {
    const inconsistentTracks = [
      track('agent', [lesson('agent-tools', 'fullstack')]),
      track('fullstack', fullstackLessons),
    ];
    const progress: Progress = {
      ...emptyProgress,
      resume: resume({
        positions: { agent: position(), fullstack: position('fullstack-routing') },
      }),
    };
    expect(resumeLesson(progress, inconsistentTracks, 'agent')).toBeUndefined();
    expect(resumeLesson(progress, inconsistentTracks)).toBe(fullstackLessons[0]);
  });

  it('returns no lesson when the catalogs or all recorded lessons are unavailable', () => {
    const progress: Progress = {
      ...emptyProgress,
      resume: resume({
        positions: { agent: position('removed-agent'), fullstack: position('removed-fullstack') },
      }),
    };
    expect(resumeLesson(progress, tracks)).toBeUndefined();
    expect(resumeLesson({ ...emptyProgress, resume: resume() }, [])).toBeUndefined();
    expect(
      resumeLesson({ ...emptyProgress, resume: resume() }, [tracks[0]], 'agent'),
    ).toBeUndefined();
  });
});

describe('next incomplete lesson', () => {
  it('uses course order and common completion without deriving completion from a last visit or practice', () => {
    const progress: Progress = {
      ...existingProgress(),
      completed: ['agent-memory', 'unrelated-course'],
      resume: resume(),
      practice: [{ lesson_id: 'agent-tools', language: 'python', completed_at: visitedAt }],
    };
    expect(nextIncompleteLesson(progress, agentLessons)).toBe(agentLessons[0]);
    expect(nextIncompleteLesson({ ...progress, completed: ['agent-tools'] }, agentLessons)).toBe(
      agentLessons[1],
    );
  });

  it('returns no lesson when every lesson is complete or the course is empty', () => {
    const completed: Progress = {
      ...existingProgress(),
      completed: agentLessons.map((item) => item.id),
    };
    expect(nextIncompleteLesson(completed, agentLessons)).toBeUndefined();
    expect(nextIncompleteLesson(emptyProgress, [])).toBeUndefined();
  });
});
