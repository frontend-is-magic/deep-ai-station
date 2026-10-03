import { describe, expect, it } from 'vitest';
import {
  evidenceRecordForExport,
  filterEvidenceEntries,
  resolveEvidenceEntries,
} from '../src/lib/evidence-library';
import { validEvidenceRecords } from '../src/lib/evidence';
import type { EvidenceRecord, Language, Lesson, Progress, Track, TrackId } from '../src/lib/types';

function lesson(id: string, track: TrackId = 'fullstack', stage = 'data'): Lesson {
  return {
    id,
    track,
    stage,
    title: `${id} 标题`,
    objective: '公开课程目标',
    minutes: 30,
    level: '基础',
    body: [],
    steps: [],
    criteria: [],
    resources: [],
    quiz: { question: '问题', options: ['答案'], answer: 0, explanation: '解释' },
    snippets:
      track === 'agent' ? { python: 'print(1)' } : { typescript: '1', go: '1', python: '1' },
  };
}
function track(id: TrackId, lessons: Lesson[]): Track {
  return {
    id,
    title: id,
    description: '公开路线',
    languages: id === 'agent' ? ['python'] : ['typescript', 'go', 'python'],
    stages: [],
    lessons,
  };
}
function record(overrides: Partial<EvidenceRecord> = {}): EvidenceRecord {
  return {
    lesson_id: 'fullstack-database',
    language: 'go',
    revision: 'revision-1',
    command: 'go test ./...',
    success: 'learner-reported result',
    failure: '',
    pending: '',
    updated_at: '2026-10-04T01:00:00.000Z',
    ...overrides,
  };
}
function progress(evidence?: EvidenceRecord[]): Progress {
  return {
    version: 1,
    language: 'typescript',
    completed: [],
    bookmarks: [],
    notes: {},
    runs: [],
    ...(evidence === undefined ? {} : { evidence }),
  };
}
const fullstackLesson = lesson('fullstack-database');
const agentLesson = lesson('agent-mcp', 'agent', 'tools');
const catalog = [track('agent', [agentLesson]), track('fullstack', [fullstackLesson])];

function expectUnlinked(records: EvidenceRecord[], tracks: Track[]) {
  const entries = resolveEvidenceEntries(progress(records), tracks);
  expect(entries).toHaveLength(records.length);
  for (const entry of entries) {
    expect(entry).not.toHaveProperty('href');
    expect(entry).not.toHaveProperty('lesson');
    expect(entry.editable).toBe(false);
  }
  return entries;
}

describe('evidence catalog navigation', () => {
  it.each(['typescript', 'go', 'python'] as const)(
    'links a fullstack record to exactly its own %s language without changing preference',
    (language) => {
      const saved = progress([record({ language })]);
      const [entry] = resolveEvidenceEntries(saved, catalog);
      expect(entry.lesson).toBe(fullstackLesson);
      expect(entry.href).toBe(`/lesson/fullstack-database?language=${language}`);
      expect(entry.editable).toBe(true);
      expect(new URL(entry.href!, 'https://example.com').searchParams.getAll('language')).toEqual([
        language,
      ]);
      expect(saved.language).toBe('typescript');
      expect(saved.completed).toEqual([]);
      expect(saved).not.toHaveProperty('practice');
    },
  );

  it('links Agent Python without a language query or modifying fullstack preference', () => {
    const saved = progress([record({ lesson_id: 'agent-mcp', language: 'python' })]);
    saved.language = 'go';
    const [entry] = resolveEvidenceEntries(saved, catalog);
    expect(entry.href).toBe('/lesson/agent-mcp');
    expect(entry.lesson).toBe(agentLesson);
    expect(entry.editable).toBe(true);
    expect(saved.language).toBe('go');
  });

  it.each([
    ['agent', 'capstone', 'agent-research-agent', 'python'],
    ['fullstack', 'ship', 'fullstack-product', 'go'],
  ] as const)(
    'recognizes the existing %s graduation editing boundary',
    (id, stage, lessonId, language) => {
      const saved = progress([record({ lesson_id: lessonId, language })]);
      const [entry] = resolveEvidenceEntries(saved, [track(id, [lesson(lessonId, id, stage)])]);
      expect(entry.href).toBeDefined();
      expect(entry.editable).toBe(true);
    },
  );

  it('keeps ordinary known lessons linkable without inventing an evidence editor', () => {
    const ordinary = lesson('fullstack-jotai', 'fullstack', 'frontend');
    const [entry] = resolveEvidenceEntries(progress([record({ lesson_id: ordinary.id })]), [
      track('fullstack', [ordinary]),
    ]);
    expect(entry.href).toBe('/lesson/fullstack-jotai?language=go');
    expect(entry.editable).toBe(false);
  });

  it.each([
    [
      'same track duplicate lesson',
      [track('fullstack', [fullstackLesson, { ...fullstackLesson }])],
    ],
    [
      'cross track duplicate lesson',
      [track('fullstack', [fullstackLesson]), track('agent', [fullstackLesson])],
    ],
    [
      'duplicate parent even with empty second parent',
      [track('fullstack', [fullstackLesson]), track('fullstack', [])],
    ],
    ['wrong parent', [track('agent', [fullstackLesson])]],
    ['wrong lesson track', [track('fullstack', [{ ...fullstackLesson, track: 'agent' }])]],
    [
      'unsupported language',
      [{ ...track('fullstack', [fullstackLesson]), languages: ['typescript'] }],
    ],
    [
      'missing language snippet',
      [track('fullstack', [{ ...fullstackLesson, snippets: { python: '1' } }])],
    ],
    [
      'whitespace snippet',
      [track('fullstack', [{ ...fullstackLesson, snippets: { go: ' \n\t ' } }])],
    ],
    ['missing course', catalog.map((parent) => ({ ...parent, lessons: [] }))],
  ] as Array<[string, Track[]]>)('retains the record but refuses navigation: %s', (_, tracks) => {
    const saved = record();
    const [entry] = expectUnlinked([saved], tracks);
    expect(entry.record).toBe(saved);
  });

  it('refuses an inherited snippet without dropping the record', () => {
    const snippets = Object.create({ go: 'not an own snippet' });
    expectUnlinked([record()], [track('fullstack', [{ ...fullstackLesson, snippets }])]);
  });

  it('retains valid evidence if the whole catalog is unavailable or malformed', () => {
    expectUnlinked([record()], []);
    expectUnlinked([record()], null as unknown as Track[]);
    expectUnlinked([record()], [{ ...catalog[0], lessons: null } as unknown as Track]);
  });
});

describe('legacy evidence retention and ordering', () => {
  it('keeps unknown, legacy ID/date and empty records unchanged through a JSON roundtrip', () => {
    const records = [
      record({ lesson_id: 'fullstack-unknown', updated_at: 'Oct 03 2026 12:34:56 GMT+0000' }),
      record({ lesson_id: 'fullstack-legacy--0-', command: '<script>text</script>' }),
      record({
        lesson_id: 'agent-retired',
        language: 'python',
        revision: '',
        command: '',
        success: '',
        failure: '',
        pending: '',
      }),
    ];
    const saved = progress(records);
    const before = JSON.stringify(saved);
    const restored: Progress = JSON.parse(before);
    expect(validEvidenceRecords(restored.evidence)).toBe(true);
    const entries = expectUnlinked(restored.evidence!, catalog);
    expect(entries.find((entry) => entry.record.lesson_id === 'agent-retired')?.hasContent).toBe(
      false,
    );
    for (const original of records) {
      expect(evidenceRecordForExport(restored, original.lesson_id, original.language)).toEqual(
        original,
      );
    }
    expect(JSON.stringify(restored)).toBe(before);
  });

  it.each(['fullstack-legacy--0-', 'fullstack----'])(
    'does not promote a compatible legacy ID to a route: %j',
    (id) => {
      const saved = record({ lesson_id: id });
      expect(validEvidenceRecords([saved])).toBe(true);
      expectUnlinked([saved], [track('fullstack', [lesson(id)])]);
      expect(evidenceRecordForExport(progress([saved]), id, 'go')?.lesson_id).toBe(id);
    },
  );

  it('sorts by parsed time descending then raw ID and language without mutating storage order', () => {
    const records = [
      record({
        lesson_id: 'fullstack-b',
        language: 'python',
        updated_at: '2026-10-03T00:00:00.000Z',
      }),
      record({ lesson_id: 'fullstack-a', language: 'typescript' }),
      record({ lesson_id: 'fullstack-newest', updated_at: 'Oct 05 2026 00:00:00 GMT+0000' }),
      record({ lesson_id: 'fullstack-a', language: 'go' }),
      record({ lesson_id: 'fullstack-a', language: 'python' }),
    ];
    const frozen = records.map((item) => Object.freeze(item));
    Object.freeze(frozen);
    const saved = Object.freeze(progress(frozen));
    const entries = resolveEvidenceEntries(saved, catalog);
    expect(entries.map(({ record: item }) => `${item.lesson_id}:${item.language}`)).toEqual([
      'fullstack-newest:go',
      'fullstack-a:go',
      'fullstack-a:python',
      'fullstack-a:typescript',
      'fullstack-b:python',
    ]);
    expect(saved.evidence).toBe(frozen);
    expect(saved.evidence![0].lesson_id).toBe('fullstack-b');
  });

  it('accepts all 48 saved records while legacy v1 absence is simply empty', () => {
    const records = Array.from({ length: 48 }, (_, index) =>
      record({ lesson_id: `fullstack-old-${index}` }),
    );
    expect(resolveEvidenceEntries(progress(records), catalog)).toHaveLength(48);
    expect(resolveEvidenceEntries(progress(), catalog)).toEqual([]);
    expect(progress()).not.toHaveProperty('evidence');
  });

  it.each(['duplicate', 'over-capacity', 'invalid-language', 'trailing-newline'] as const)(
    'fails closed for invalid evidence without repairing it: %s',
    (kind) => {
      const records =
        kind === 'duplicate'
          ? [record(), record()]
          : kind === 'over-capacity'
            ? Array.from({ length: 49 }, (_, index) =>
                record({ lesson_id: `fullstack-old-${index}` }),
              )
            : kind === 'invalid-language'
              ? [record({ language: 'ruby' as Language })]
              : [record({ lesson_id: 'fullstack-database\n' })];
      const before = JSON.stringify(records);
      expect(resolveEvidenceEntries(progress(records), catalog)).toEqual([]);
      expect(
        evidenceRecordForExport(progress(records), records[0].lesson_id, records[0].language),
      ).toBeUndefined();
      expect(JSON.stringify(records)).toBe(before);
    },
  );
});

describe('literal content filters', () => {
  it.each(['revision', 'command', 'success', 'failure', 'pending'] as const)(
    'counts only %s content, without claiming verification',
    (field) => {
      const saved = record({
        revision: '',
        command: '',
        success: '',
        failure: '',
        pending: '',
        [field]: ' learner text ',
      });
      const [entry] = resolveEvidenceEntries(progress([saved]), catalog);
      expect(entry.hasContent).toBe(true);
      expect(entry.hasPending).toBe(field === 'pending');
      expect(entry).not.toHaveProperty('passed');
      expect(entry).not.toHaveProperty('grade');
      expect(entry).not.toHaveProperty('verified');
    },
  );

  it.each(['', ' \n\t', '无', '<script>not executed</script>', '```\ntext\n```', '😀未核验'])(
    'uses pending text literally: %j',
    (pending) => {
      const saved = record({ revision: '', command: '', success: '', failure: '', pending });
      const [entry] = resolveEvidenceEntries(progress([saved]), catalog);
      expect(entry.hasPending).toBe(Boolean(pending.trim()));
      expect(entry.hasContent).toBe(Boolean(pending.trim()));
      expect(entry.record.pending).toBe(pending);
    },
  );

  it('combines route, language and pending filters, including unknown entries, without reordering', () => {
    const records = [
      record({ pending: '无' }),
      record({ language: 'python', pending: '' }),
      record({ lesson_id: 'agent-mcp', language: 'python', pending: '待核验' }),
      record({ lesson_id: 'fullstack-unknown', pending: '待核验' }),
    ];
    const entries = resolveEvidenceEntries(progress(records), catalog);
    expect(filterEvidenceEntries(entries)).toEqual(entries);
    expect(filterEvidenceEntries(entries, { track: 'all', language: 'all' })).toEqual(entries);
    const selected = filterEvidenceEntries(entries, {
      track: 'fullstack',
      language: 'go',
      pendingOnly: true,
    });
    expect(selected.map((entry) => entry.record.lesson_id)).toEqual([
      'fullstack-database',
      'fullstack-unknown',
    ]);
    expect(filterEvidenceEntries(entries, { track: 'agent', language: 'go' })).toEqual([]);
    expect(entries).toHaveLength(4);
    expect(selected[0]).toBe(entries.find((entry) => entry.record === records[0]));
  });
});

describe('current record lookup before export', () => {
  it('returns the actual latest same-key record even across a reset and never an older closure', () => {
    const old = record({ command: 'old command' });
    const newer = record({ command: 'new command', pending: 'new pending' });
    const current = {
      ...progress([newer, record({ language: 'python' })]),
      history_reset_id: '00000000-0000-4000-8000-000000000002',
    };
    const exported = evidenceRecordForExport(current, old.lesson_id, old.language)!;
    expect(exported).toEqual(newer);
    expect(exported).not.toBe(newer);
    expect(Object.keys(exported).sort()).toEqual(
      [
        'lesson_id',
        'language',
        'revision',
        'command',
        'success',
        'failure',
        'pending',
        'updated_at',
      ].sort(),
    );
    exported.command = 'changed download copy';
    expect(current.evidence[0].command).toBe('new command');
  });

  it('returns undefined after deletion and never substitutes another language or lesson', () => {
    const current = progress([
      record({ language: 'python' }),
      record({ lesson_id: 'fullstack-other' }),
    ]);
    const before = JSON.stringify(current);
    expect(evidenceRecordForExport(current, 'fullstack-database', 'go')).toBeUndefined();
    expect(evidenceRecordForExport(progress(), 'fullstack-database', 'go')).toBeUndefined();
    expect(JSON.stringify(current)).toBe(before);
  });

  it('exports an existing empty legacy record rather than treating it as deleted', () => {
    const empty = record({
      lesson_id: 'fullstack-retired--',
      revision: '',
      command: '',
      success: '',
      failure: '',
      pending: '',
    });
    expect(evidenceRecordForExport(progress([empty]), empty.lesson_id, empty.language)).toEqual(
      empty,
    );
  });
});
