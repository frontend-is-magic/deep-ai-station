import { describe, expect, it } from 'vitest';
import {
  MAX_QUIZ_REVIEWS,
  addQuizReview,
  removeQuizReview,
  resolveQuizReviews,
  validQuizReviewRecords,
} from '../src/lib/quiz-review';
import { emptyProgress, validateProgress } from '../src/lib/state';
import type { Lesson, Progress, QuizReviewRecord, Track, TrackId } from '../src/lib/types';

const FIRST = '2026-10-04T01:02:03.000Z';
const LATER = '2026-10-05T01:02:03.001Z';
function record(lesson_id = 'agent-tool-contract', added_at = FIRST): QuizReviewRecord {
  return { lesson_id, added_at };
}
function lesson(id: string, track: TrackId = 'agent'): Lesson {
  return {
    id,
    track,
    stage: 'basics',
    title: `当前课程 ${id}`,
    objective: '理解课程',
    minutes: 20,
    level: '基础',
    body: [],
    steps: [],
    criteria: [],
    resources: [],
    snippets: {},
    quiz: { question: '当前问题', options: ['A', 'B'], answer: 1, explanation: '当前解释' },
  };
}
function track(id: TrackId, lessons: Lesson[]): Track {
  return {
    id,
    lessons,
    title: id,
    description: '',
    languages: id === 'agent' ? ['python'] : ['typescript', 'go', 'python'],
    stages: [],
  };
}
function richProgress(): Progress {
  return {
    ...emptyProgress,
    completed: ['agent-tool-contract'],
    bookmarks: ['official-guide'],
    savedItems: [
      {
        id: 'official-guide',
        title: '官方资料',
        summary: '资料摘要',
        source: '官方',
        url: 'https://example.com/guide',
        track: 'agent',
        tags: ['指南'],
        kind: 'guide',
        published: null,
      },
    ],
    notes: { 'agent-tool-contract': '已有笔记' },
    language: 'go',
    runs: [
      {
        id: 'old-run',
        prompt: '已有问题',
        answer: '已有回答',
        provider: 'demo',
        track: 'agent',
        date: FIRST,
        duration_ms: 50,
      },
    ],
    evidence: [
      {
        lesson_id: 'agent-research-agent',
        language: 'python',
        revision: '已有版本',
        command: '已有命令',
        success: '已有成功',
        failure: '已有失败',
        pending: '已有待验',
        updated_at: FIRST,
      },
    ],
    practice: [{ lesson_id: 'fullstack-database', language: 'go', completed_at: FIRST }],
    resume: {
      last_track: 'agent',
      positions: { agent: { lesson_id: 'agent-tool-contract', visited_at: FIRST } },
    },
  };
}

describe('quiz review version-one validation', () => {
  it('keeps old backups untouched and does not infer review from completion or other evidence', () => {
    const old = JSON.parse(JSON.stringify(richProgress()));
    expect(validateProgress(old)).toBe(true);
    expect(Object.hasOwn(old, 'quizReview')).toBe(false);
    expect(resolveQuizReviews(old, [track('agent', [lesson('agent-tool-contract')])])).toEqual({
      available: [],
      unavailable: [],
    });
    expect(removeQuizReview(old, 'agent-tool-contract')).toBe(old);
    expect(validateProgress({ ...old, quizReview: [] })).toBe(true);
  });

  it('roundtrips unknown but valid IDs without inventing answers or pass status', () => {
    const saved = addQuizReview(emptyProgress, 'fullstack-retired-course', FIRST);
    const restored = JSON.parse(JSON.stringify(saved));
    expect(validateProgress(restored)).toBe(true);
    expect(restored.version).toBe(1);
    expect(restored.quizReview).toEqual([record('fullstack-retired-course')]);
    expect(Object.keys(restored.quizReview[0]).sort()).toEqual(['added_at', 'lesson_id']);
    expect(resolveQuizReviews(restored, [])).toEqual({
      available: [],
      unavailable: restored.quizReview,
    });
    expect(removeQuizReview(restored, 'fullstack-retired-course').quizReview).toEqual([]);
  });

  it('accepts the exact ID length limit and canonical leap-day timestamps', () => {
    expect(
      validQuizReviewRecords([record('fullstack-' + 'a'.repeat(90), '2024-02-29T23:59:59.999Z')]),
    ).toBe(true);
    expect(validQuizReviewRecords([Object.assign(Object.create(null), record())])).toBe(true);
  });

  it.each([
    '',
    'agent-',
    'fullstack-',
    'agent--tool',
    'agent-tool-',
    'agent-Tool',
    'agent-tool_contract',
    'other-course',
    '../agent-tool',
    'agent-tool\n',
    ' agent-tool',
    'fullstack-' + 'a'.repeat(91),
  ])('rejects invalid IDs including trailing-newline matching: %j', (lessonId) => {
    const incoming = [record(lessonId)];
    expect(validQuizReviewRecords(incoming)).toBe(false);
    expect(validateProgress({ ...emptyProgress, quizReview: incoming })).toBe(false);
  });

  it.each([
    '',
    'invalid',
    '2026-10-04',
    '2026-10-04T01:02:03Z',
    '2026-10-04T01:02:03.000+00:00',
    '2026-10-04T01:02:03.000Z\n',
    '2026-02-29T01:02:03.000Z',
    '2026-10-04T24:00:00.000Z',
    '+010000-10-04T01:02:03.000Z',
    '2026-10-04T01:02:03.000z',
    '2026-10-04T01:02:03.0000Z',
  ])('rejects noncanonical or invalid dates: %j', (addedAt) => {
    expect(validQuizReviewRecords([record('agent-tool-contract', addedAt)])).toBe(false);
  });

  it.each([
    null,
    [],
    {},
    { lesson_id: 'agent-tool-contract' },
    { added_at: FIRST },
    { ...record(), answer: 1 },
    { ...record(), passed: true },
    { ...record(), lesson_id: null },
    { ...record(), added_at: 0 },
    { ...record(), [Symbol('extra')]: 'not allowed' },
    Object.create(record()),
    Object.defineProperty({ lesson_id: 'agent-tool-contract' }, 'added_at', { value: FIRST }),
  ])('rejects records with missing, non-own or additional fields (%#)', (invalid) => {
    expect(validQuizReviewRecords([invalid])).toBe(false);
    expect(validateProgress({ ...emptyProgress, quizReview: [invalid] })).toBe(false);
  });

  it.each([undefined, null, {}, '[]', Array(1), [record(), record('agent-tool-contract', LATER)]])(
    'rejects invalid collections (%#)',
    (invalid) => {
      expect(validQuizReviewRecords(invalid)).toBe(false);
    },
  );

  it('accepts 64 unique records and refuses 65 without changing the existing progress', () => {
    expect(MAX_QUIZ_REVIEWS).toBe(64);
    const records = Array.from({ length: 64 }, (_, index) => record(`fullstack-course-${index}`));
    const full: Progress = { ...emptyProgress, quizReview: records };
    expect(validQuizReviewRecords(records)).toBe(true);
    expect(validateProgress(full)).toBe(true);
    expect(addQuizReview(full, 'agent-tool-contract', FIRST)).toBe(full);
    expect(addQuizReview(full, 'fullstack-course-10', LATER)).toBe(full);
    expect(full.quizReview?.[10].added_at).toBe(FIRST);
    const oversized = [...records, record()];
    expect(validQuizReviewRecords(oversized)).toBe(false);
    expect(validateProgress({ ...full, quizReview: oversized })).toBe(false);
    const reduced = removeQuizReview(full, 'fullstack-course-10');
    expect(reduced.quizReview).toHaveLength(63);
    expect(addQuizReview(reduced, 'agent-tool-contract', LATER).quizReview).toHaveLength(64);
    expect(full.quizReview).toBe(records);
    expect(records).toHaveLength(64);
  });
});

describe('quiz review mutations', () => {
  it('preserves the first time on repeat and starts a new time only after removal', () => {
    const initial = addQuizReview(emptyProgress, 'agent-tool-contract', FIRST);
    expect(addQuizReview(initial, 'agent-tool-contract', LATER)).toBe(initial);
    const removed = removeQuizReview(initial, 'agent-tool-contract');
    const readded = addQuizReview(removed, 'agent-tool-contract', LATER);
    expect(readded.quizReview).toEqual([record('agent-tool-contract', LATER)]);
    expect(initial.quizReview).toEqual([record()]);
    expect(removed.quizReview).toEqual([]);
  });

  it('only replaces quizReview and preserves every unrelated latest learning field', () => {
    const original = richProgress();
    const added = addQuizReview(original, 'agent-tool-contract', FIRST);
    const another = addQuizReview(added, 'fullstack-database', LATER);
    const removed = removeQuizReview(another, 'agent-tool-contract');
    for (const changed of [added, another, removed]) {
      for (const key of Object.keys(original) as Array<keyof Progress>)
        expect(changed[key]).toBe(original[key]);
      expect(validateProgress(changed)).toBe(true);
    }
    expect(original.quizReview).toBeUndefined();
    expect(added.quizReview).toEqual([record()]);
    expect(removed.quizReview).toEqual([record('fullstack-database', LATER)]);
    expect(removed.quizReview?.[0]).toBe(another.quizReview?.[1]);
  });

  it('returns the same object for invalid parameters and never repairs malformed current data implicitly', () => {
    const valid = addQuizReview(emptyProgress, 'agent-tool-contract', FIRST);
    expect(addQuizReview(valid, 'agent-tool-contract', 'bad date')).toBe(valid);
    expect(addQuizReview(valid, 'agent-tool\n', FIRST)).toBe(valid);
    expect(removeQuizReview(valid, 'agent-tool\n')).toBe(valid);
    expect(removeQuizReview(valid, 'fullstack-not-present')).toBe(valid);
    for (const invalid of [null, Array(1), [record(), record()], [{ ...record(), answer: 0 }]]) {
      const malformed = { ...emptyProgress, quizReview: invalid } as unknown as Progress;
      expect(addQuizReview(malformed, 'fullstack-database', FIRST)).toBe(malformed);
      expect(removeQuizReview(malformed, 'agent-tool-contract')).toBe(malformed);
      expect(resolveQuizReviews(malformed, [])).toEqual({ available: [], unavailable: [] });
    }
  });
});

describe('resolving current review routes', () => {
  it('returns current unique lessons and separates unavailable records with stable chronological and ID order', () => {
    const source = [
      record('fullstack-missing', LATER),
      record('fullstack-database'),
      record('agent-tool-contract'),
      record('agent-retired'),
    ];
    const progress: Progress = { ...emptyProgress, quizReview: source };
    const agent = lesson('agent-tool-contract');
    const fullstack = lesson('fullstack-database', 'fullstack');
    const tracks = [track('fullstack', [fullstack]), track('agent', [agent])];
    const result = resolveQuizReviews(progress, tracks);
    expect(result.available.map(({ record: item }) => item.lesson_id)).toEqual([
      'agent-tool-contract',
      'fullstack-database',
    ]);
    expect(result.available[0].lesson).toBe(agent);
    expect(result.available[1].lesson).toBe(fullstack);
    expect(result.unavailable).toEqual([
      record('agent-retired'),
      record('fullstack-missing', LATER),
    ]);
    expect(progress.quizReview).toBe(source);
    expect(source[0].lesson_id).toBe('fullstack-missing');
    expect(tracks[0].id).toBe('fullstack');
  });

  it.each([
    [track('agent', [lesson('agent-tool-contract'), lesson('agent-tool-contract')])],
    [
      track('agent', [lesson('agent-tool-contract')]),
      track('fullstack', [lesson('agent-tool-contract', 'fullstack')]),
    ],
    [track('agent', []), track('agent', [lesson('agent-tool-contract')])],
    [track('agent', [lesson('agent-tool-contract', 'fullstack')])],
    [track('fullstack', [lesson('agent-tool-contract', 'fullstack')])],
    [track('fullstack', [lesson('agent-tool-contract')])],
  ])('refuses ambiguous or mismatched catalogue routes (%#)', (...tracks) => {
    const progress: Progress = { ...emptyProgress, quizReview: [record()] };
    expect(resolveQuizReviews(progress, tracks)).toEqual({
      available: [],
      unavailable: [record()],
    });
    expect(removeQuizReview(progress, 'agent-tool-contract').quizReview).toEqual([]);
  });

  it('keeps valid routes available even when unrelated records cannot resolve', () => {
    const progress: Progress = {
      ...emptyProgress,
      quizReview: [record(), record('fullstack-retired')],
    };
    const current = lesson('agent-tool-contract');
    const result = resolveQuizReviews(progress, [track('agent', [current])]);
    expect(result.available).toEqual([{ record: record(), lesson: current }]);
    expect(result.unavailable).toEqual([record('fullstack-retired')]);
  });
});
