import { describe, expect, it, vi } from 'vitest';
import { courseLabFor } from '../src/lib/course-labs';
import { labPracticeGroups } from '../src/lib/lab-practice';
import { removePractice, savePractice } from '../src/lib/practice';
import { emptyProgress, validateProgress } from '../src/lib/state';
import type { Language, Lesson, Progress, Track, TrackId } from '../src/lib/types';

const DATE = '2026-10-04T12:00:00.000Z';
const fullstackIds = [
  'fullstack-async',
  'fullstack-database',
  'fullstack-routing',
  'fullstack-product-planning',
  'fullstack-ai-stream',
  'fullstack-validation',
  'fullstack-migrations',
  'fullstack-auth',
  'fullstack-app-security',
  'fullstack-ai-rag',
  'fullstack-product',
];
const agentIds = [
  'agent-state-machine',
  'agent-mcp',
  'agent-tool-safety',
  'agent-chunking',
  'agent-regression',
  'agent-memory',
  'agent-research-agent',
];
function lesson(id: string, track: TrackId = 'fullstack'): Lesson {
  return {
    id,
    track,
    title: `课时 ${id}`,
    stage: track === 'agent' ? 'capstone' : 'ship',
    objective: '练习成功与失败输入',
    minutes: 20,
    level: '基础',
    body: [],
    steps: [],
    criteria: [],
    resources: [],
    quiz: { question: '问题', options: ['选项'], answer: 0, explanation: '解释' },
    snippets:
      track === 'agent'
        ? { python: 'print(1)' }
        : { typescript: 'console.log(1)', go: 'package main', python: 'print(1)' },
  };
}
function track(id: TrackId, ids: string[]): Track {
  return {
    id,
    title: id,
    description: '路线',
    languages: id === 'agent' ? ['python'] : ['typescript', 'go', 'python'],
    stages: [],
    lessons: ids.map((key) => lesson(key, id)),
  };
}
function tracks(): Track[] {
  return [track('agent', agentIds), track('fullstack', fullstackIds)];
}
function progress(extra: Partial<Progress> = {}): Progress {
  return { ...emptyProgress, completed: [], bookmarks: [], notes: {}, runs: [], ...extra };
}
function freeze<T>(value: T): T {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}
function lessonIds(catalogue: Track[], current = progress()) {
  return labPracticeGroups(catalogue, 'fullstack', current).flatMap((group) =>
    group.lessons.map((entry) => entry.lesson.id),
  );
}

describe('runnable practice groups', () => {
  it.each(['typescript', 'go', 'python'] as const)(
    'uses existing fullstack labs and exact %s links in catalogue order',
    (language) => {
      const catalogue = tracks();
      const current = progress({ language });
      const groups = labPracticeGroups(catalogue, 'fullstack', current);
      expect(groups.map((group) => group.lab.id)).toEqual([
        'sse-stream',
        'sqlite-storage',
        'api-contract',
        'session-authorization',
        'text-upload',
      ]);
      expect(groups.map((group) => group.lessons.map((entry) => entry.lesson.id))).toEqual([
        ['fullstack-async', 'fullstack-ai-stream'],
        ['fullstack-database', 'fullstack-migrations'],
        ['fullstack-routing', 'fullstack-validation'],
        ['fullstack-auth', 'fullstack-app-security'],
        ['fullstack-ai-rag'],
      ]);
      for (const group of groups) {
        expect(group.language).toBe(language);
        expect(group.lab).toBe(courseLabFor(group.lessons[0].lesson.id, language));
        for (const entry of group.lessons) {
          expect(entry.lesson).toBe(catalogue[1].lessons.find((row) => row.id === entry.lesson.id));
          expect(entry.href).toBe(`/lesson/${entry.lesson.id}?language=${language}#course-lab`);
          const url = new URL(entry.href, 'https://example.com');
          expect(url.searchParams.getAll('language')).toEqual([language]);
          expect(url.hash).toBe('#course-lab');
          expect(entry.practiceRecorded).toBe(false);
        }
      }
      expect(current.language).toBe(language);
    },
  );

  it.each(['typescript', 'go', 'python'] as const)(
    'keeps Agent Python while the service-language preference is %s',
    (language) => {
      const current = progress({ language });
      const groups = labPracticeGroups(tracks(), 'agent', current);
      expect(groups.map((group) => group.lab.id)).toEqual([
        'workflow-checkpoint',
        'mcp-readonly',
        'agent-write-safety',
        'document-chunking',
        'output-regression',
        'memory-policy',
      ]);
      for (const group of groups) {
        expect(group.language).toBe('python');
        for (const entry of group.lessons) {
          expect(entry.href).toBe(`/lesson/${entry.lesson.id}#course-lab`);
          expect(entry.href).not.toContain('?');
        }
      }
      expect(current.language).toBe(language);
    },
  );

  it('orders by the first eligible lesson and retains within-group order, not metadata order', () => {
    const catalogue = [
      track('fullstack', [
        'fullstack-validation',
        'fullstack-ai-stream',
        'fullstack-routing',
        'fullstack-async',
      ]),
    ];
    const groups = labPracticeGroups(catalogue, 'fullstack', progress());
    expect(groups.map((group) => group.lab.id)).toEqual(['api-contract', 'sse-stream']);
    expect(groups[0].lessons.map((entry) => entry.lesson.id)).toEqual([
      'fullstack-validation',
      'fullstack-routing',
    ]);
    expect(groups[1].lessons.map((entry) => entry.lesson.id)).toEqual([
      'fullstack-ai-stream',
      'fullstack-async',
    ]);
    catalogue[0].lessons[0].snippets.typescript = '';
    expect(
      labPracticeGroups(catalogue, 'fullstack', progress()).map((group) => group.lab.id),
    ).toEqual(['sse-stream', 'api-contract']);
  });

  it('retains both course states in one package and leaves fully recorded groups to the UI filter', () => {
    const catalogue = [track('fullstack', ['fullstack-routing', 'fullstack-validation'])];
    const partial = progress({
      practice: [
        { lesson_id: 'fullstack-routing', language: 'typescript', completed_at: DATE },
        { lesson_id: 'fullstack-validation', language: 'go', completed_at: DATE },
      ],
    });
    const first = labPracticeGroups(catalogue, 'fullstack', partial);
    expect(first).toHaveLength(1);
    expect(first[0].lessons.map((entry) => entry.practiceRecorded)).toEqual([true, false]);
    expect(
      first.filter((group) => group.lessons.some((entry) => !entry.practiceRecorded)),
    ).toHaveLength(1);
    const confirmed = savePractice(partial, {
      lesson_id: 'fullstack-validation',
      language: 'typescript',
      completed_at: DATE,
    });
    const all = labPracticeGroups(catalogue, 'fullstack', confirmed);
    expect(all).toHaveLength(1);
    expect(all[0].lessons.map((entry) => entry.practiceRecorded)).toEqual([true, true]);
    expect(all.filter((group) => group.lessons.some((entry) => !entry.practiceRecorded))).toEqual(
      [],
    );
    const revoked = removePractice(confirmed, 'fullstack-routing', 'typescript');
    expect(
      labPracticeGroups(catalogue, 'fullstack', revoked)[0].lessons.map(
        (entry) => entry.practiceRecorded,
      ),
    ).toEqual([false, true]);
    expect(
      labPracticeGroups(catalogue, 'fullstack', {
        ...confirmed,
        language: 'python',
      })[0].lessons.map((entry) => entry.practiceRecorded),
    ).toEqual([false, false]);
    expect(
      labPracticeGroups(catalogue, 'fullstack', { ...confirmed, language: 'go' })[0].lessons.map(
        (entry) => entry.practiceRecorded,
      ),
    ).toEqual([false, true]);
  });

  it('keeps old v1 completion, notes and evidence distinct from practice without migration', () => {
    const old = progress({
      completed: fullstackIds,
      notes: { 'fullstack-routing': '已经读完并填写' },
      evidence: [
        {
          lesson_id: 'fullstack-routing',
          language: 'typescript',
          revision: 'v1',
          command: 'pnpm test',
          success: '通过',
          failure: '拒绝',
          pending: '',
          updated_at: DATE,
        },
      ],
    });
    const restored = JSON.parse(JSON.stringify(old)) as Progress;
    const before = JSON.stringify(restored);
    const groups = labPracticeGroups(tracks(), 'fullstack', restored);
    expect(groups).toHaveLength(5);
    expect(groups.flatMap((group) => group.lessons).every((entry) => !entry.practiceRecorded)).toBe(
      true,
    );
    expect(JSON.stringify(restored)).toBe(before);
    expect(restored).not.toHaveProperty('practice');
    expect(validateProgress(restored)).toBe(true);
  });

  it('does not trust malformed practice or infer Agent practice from unrelated records', () => {
    const current = progress({
      practice: [
        { lesson_id: 'fullstack-routing', language: 'typescript', completed_at: DATE },
        { lesson_id: 'agent-mcp', language: 'python', completed_at: 'invalid-date' },
      ],
    });
    expect(
      labPracticeGroups(tracks(), 'fullstack', current)
        .flatMap((group) => group.lessons)
        .every((entry) => !entry.practiceRecorded),
    ).toBe(true);
    const valid = progress({
      practice: [{ lesson_id: 'agent-mcp', language: 'python', completed_at: DATE }],
    });
    const entries = labPracticeGroups(tracks(), 'agent', valid).flatMap((group) => group.lessons);
    expect(
      entries.filter((entry) => entry.practiceRecorded).map((entry) => entry.lesson.id),
    ).toEqual(['agent-mcp']);
  });

  it('does not add regular lessons or graduation skeletons to the existing lab inventory', () => {
    expect(
      labPracticeGroups(
        [track('fullstack', ['fullstack-product-planning', 'fullstack-product'])],
        'fullstack',
        progress(),
      ),
    ).toEqual([]);
    expect(
      labPracticeGroups(
        [track('agent', ['agent-agent-loop', 'agent-research-agent'])],
        'agent',
        progress(),
      ),
    ).toEqual([]);
  });

  it('is read-only with frozen data and does not consult browser persistence', () => {
    const current = freeze(
      progress({ practice: [{ lesson_id: 'agent-mcp', language: 'python', completed_at: DATE }] }),
    );
    const catalogue = freeze(tracks());
    const before = JSON.stringify([catalogue, current]);
    const getItem = vi.fn(() => {
      throw new Error('must not read');
    });
    vi.stubGlobal('localStorage', { getItem });
    try {
      const first = labPracticeGroups(catalogue, 'agent', current);
      const second = labPracticeGroups(catalogue, 'agent', current);
      expect(first).toEqual(second);
      expect(first).not.toBe(second);
      expect(first[0].lessons).not.toBe(second[0].lessons);
      expect(JSON.stringify([catalogue, current])).toBe(before);
      expect(getItem).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe('catalogue link eligibility', () => {
  it('requires one parent and rejects every global duplicate before route lookup', () => {
    const selected = track('fullstack', ['fullstack-routing', 'fullstack-validation']);
    expect(labPracticeGroups([selected, track('fullstack', [])], 'fullstack', progress())).toEqual(
      [],
    );
    const sameTrack = track('fullstack', [
      'fullstack-routing',
      'fullstack-routing',
      'fullstack-validation',
    ]);
    expect(lessonIds([sameTrack])).toEqual(['fullstack-validation']);
    const wrongParent = track('agent', []);
    wrongParent.lessons = [lesson('fullstack-routing', 'agent')];
    expect(lessonIds([selected, wrongParent])).toEqual(['fullstack-validation']);
    wrongParent.lessons[0].snippets = {};
    expect(lessonIds([selected, wrongParent])).toEqual(['fullstack-validation']);
    expect(labPracticeGroups([wrongParent], 'agent', progress())).toEqual([]);
  });

  it.each([null, [], [null], [{ id: 'fullstack', lessons: null }], Array(1)])(
    'returns no links when route uniqueness cannot be established: %j',
    (catalogue) => {
      expect(labPracticeGroups(catalogue as unknown as Track[], 'fullstack', progress())).toEqual(
        [],
      );
    },
  );

  it('does not accept an absent or unknown selected route', () => {
    expect(labPracticeGroups([track('agent', ['agent-mcp'])], 'fullstack', progress())).toEqual([]);
    expect(labPracticeGroups(tracks(), 'other' as TrackId, progress())).toEqual([]);
  });

  it.each([[], ['go'], ['typescript', 'ruby'], ['typescript', undefined], null])(
    'requires a supported, well-formed language declaration %j',
    (languages) => {
      const parent = { ...track('fullstack', ['fullstack-routing']), languages } as Track;
      expect(labPracticeGroups([parent], 'fullstack', progress())).toEqual([]);
    },
  );

  it('requires Agent Python support and rejects an invalid fullstack preference', () => {
    const parent = { ...track('agent', ['agent-mcp']), languages: ['go'] as Language[] };
    expect(labPracticeGroups([parent], 'agent', progress())).toEqual([]);
    expect(
      labPracticeGroups(tracks(), 'fullstack', progress({ language: 'ruby' as Language })),
    ).toEqual([]);
  });

  it.each([
    'fullstack-routing\n',
    'fullstack-routing/extra',
    '../fullstack-routing',
    'fullstack--routing',
    'fullstack-',
    'fullstack-' + 'a'.repeat(91),
  ])('refuses malformed or noncanonical lesson IDs %s', (id) => {
    expect(lessonIds([track('fullstack', [id])])).toEqual([]);
  });

  it('refuses mismatched lesson ownership while retaining another eligible lesson', () => {
    const parent = track('fullstack', ['fullstack-routing', 'fullstack-validation']);
    parent.lessons[0].track = 'agent';
    expect(lessonIds([parent])).toEqual(['fullstack-validation']);
  });

  it.each([
    {},
    { typescript: '' },
    { typescript: ' \n\t' },
    { typescript: 12 },
    Object.create({ typescript: 'inherited' }),
    null,
    [],
  ])('requires an own nonempty selected-language snippet %j', (snippets) => {
    const parent = track('fullstack', ['fullstack-routing', 'fullstack-validation']);
    parent.lessons[0].snippets = snippets as Lesson['snippets'];
    expect(lessonIds([parent])).toEqual(['fullstack-validation']);
  });
});

describe('shared frontend practice discovery', () => {
  it.each(['typescript', 'go', 'python'] as const)(
    'reads only TypeScript facts without changing %s preference',
    (language) => {
      const catalogue = [track('fullstack', ['fullstack-components', 'fullstack-jotai'])];
      const current = freeze(
        progress({
          language,
          practice: [
            { lesson_id: 'fullstack-components', language: 'go', completed_at: DATE },
            { lesson_id: 'fullstack-jotai', language: 'typescript', completed_at: DATE },
          ],
        }),
      );
      const [group] = labPracticeGroups(catalogue, 'fullstack', current);
      expect(group.lab.id).toBe('frontend-state');
      expect(group.language).toBe('typescript');
      expect(group.lessons.map((item) => item.practiceRecorded)).toEqual([false, true]);
      expect(group.lessons.map((item) => item.href)).toEqual([
        '/lesson/fullstack-components#course-lab',
        '/lesson/fullstack-jotai#course-lab',
      ]);
      expect(current.language).toBe(language);
      expect(current.practice).toHaveLength(2);
    },
  );
  it('requires a real TypeScript reference even when the selected server reference exists', () => {
    const catalogue = [track('fullstack', ['fullstack-jotai'])];
    catalogue[0].lessons[0].snippets.typescript = '';
    expect(labPracticeGroups(catalogue, 'fullstack', progress({ language: 'go' }))).toEqual([]);
  });
});
