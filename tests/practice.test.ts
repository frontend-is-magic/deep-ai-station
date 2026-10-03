import { describe, expect, it } from 'vitest';
import {
  MAX_PRACTICE_RECORDS,
  practiceFor,
  removePractice,
  savePractice,
  validPracticeRecord,
  validPracticeRecords,
} from '../src/lib/practice';
import { emptyProgress, validateProgress } from '../src/lib/state';
import type { EvidenceRecord, Language, PracticeRecord, Progress } from '../src/lib/types';

function record(overrides: Partial<PracticeRecord> = {}): PracticeRecord {
  return {
    lesson_id: 'fullstack-routing',
    language: 'python',
    completed_at: '2026-10-03T12:34:56.000Z',
    ...overrides,
  };
}

function existingProgress(): Progress {
  const evidence: EvidenceRecord = {
    lesson_id: 'fullstack-routing',
    language: 'python',
    revision: 'lesson-example',
    command: 'uv run pytest',
    success: '本地通过',
    failure: '非法输入已拒绝',
    pending: '尚未部署',
    updated_at: '2026-10-03T12:30:00.000Z',
  };
  return {
    ...emptyProgress,
    completed: ['fullstack-routing', 'agent-research-agent'],
    bookmarks: ['saved-example'],
    language: 'go',
    notes: { 'fullstack-routing': '已有课程笔记' },
    evidence: [evidence],
  };
}

function fullProgress(): Progress {
  return {
    ...existingProgress(),
    practice: Array.from({ length: MAX_PRACTICE_RECORDS }, (_, index) =>
      record({ lesson_id: `fullstack-example-${index}` }),
    ),
  };
}

function expectOtherProgressUnchanged(result: Progress, original: Progress) {
  expect(result.version).toBe(1);
  expect(result.completed).toBe(original.completed);
  expect(result.notes).toBe(original.notes);
  expect(result.evidence).toBe(original.evidence);
  expect(result.bookmarks).toBe(original.bookmarks);
  expect(result.savedItems).toBe(original.savedItems);
  expect(result.language).toBe(original.language);
  expect(result.runs).toBe(original.runs);
}

describe('language practice import', () => {
  it('accepts version-one backups without deriving practice from completion, language or evidence', () => {
    const old = JSON.parse(JSON.stringify(existingProgress()));
    const before = JSON.stringify(old);
    expect(validateProgress(old)).toBe(true);
    for (const language of ['python', 'typescript', 'go'] as const)
      expect(practiceFor(old, 'fullstack-routing', language)).toBeUndefined();
    expect(practiceFor(old, 'agent-research-agent', 'python')).toBeUndefined();
    expect(JSON.stringify(old)).toBe(before);
    expect(old.practice).toBeUndefined();
    expect(emptyProgress.practice).toBeUndefined();
    expect(validateProgress({ ...old, practice: [] })).toBe(true);
  });

  it('roundtrips practices independently from common course completion', () => {
    const saved = savePractice(emptyProgress, record());
    const restored = JSON.parse(JSON.stringify(saved));
    expect(validateProgress(restored)).toBe(true);
    expect(restored.version).toBe(1);
    expect(restored.completed).toEqual([]);
    expect(practiceFor(restored, 'fullstack-routing', 'python')).toEqual(record());
    expect(emptyProgress.practice).toBeUndefined();
  });

  it('accepts three fullstack languages, Python Agent work and the maximum slug length', () => {
    for (const language of ['python', 'typescript', 'go'] as const)
      expect(validPracticeRecord(record({ language }))).toBe(true);
    expect(validPracticeRecord(record({ lesson_id: 'agent-research-agent' }))).toBe(true);
    expect(validPracticeRecord(record({ lesson_id: 'fullstack-' + 'a'.repeat(90) }))).toBe(true);
    expect(validPracticeRecord(record({ completed_at: '2024-02-29T23:59:59.999Z' }))).toBe(true);
  });

  it.each([
    ['null', null],
    ['array', []],
    ['missing field', { lesson_id: 'fullstack-routing', language: 'python' }],
    ['extra field', { ...record(), verified: true }],
    ['extra undefined field', { ...record(), ignored: undefined }],
    ['extra symbol', { ...record(), [Symbol('extra')]: true }],
    ['empty lesson', record({ lesson_id: '' })],
    ['unknown track', record({ lesson_id: 'other-routing' })],
    ['empty slug', record({ lesson_id: 'fullstack-' })],
    ['leading slug hyphen', record({ lesson_id: 'fullstack--routing' })],
    ['trailing slug hyphen', record({ lesson_id: 'fullstack-routing-' })],
    ['repeated slug hyphen', record({ lesson_id: 'fullstack-route--test' })],
    ['uppercase slug', record({ lesson_id: 'fullstack-Routing' })],
    ['path traversal', record({ lesson_id: '../fullstack-routing' })],
    ['oversized lesson', record({ lesson_id: 'fullstack-' + 'a'.repeat(91) })],
    ['unknown language', { ...record(), language: 'rust' }],
    ['Agent Go', record({ lesson_id: 'agent-research-agent', language: 'go' })],
    ['Agent TypeScript', record({ lesson_id: 'agent-research-agent', language: 'typescript' })],
    ['undefined time', { ...record(), completed_at: undefined }],
    ['number time', { ...record(), completed_at: 1791030896000 }],
    ['empty time', record({ completed_at: '' })],
    ['invalid time', record({ completed_at: 'not-a-date' })],
    ['date only', record({ completed_at: '2026-10-03' })],
    ['missing milliseconds', record({ completed_at: '2026-10-03T12:34:56Z' })],
    ['one millisecond digit', record({ completed_at: '2026-10-03T12:34:56.0Z' })],
    ['timezone offset', record({ completed_at: '2026-10-03T20:34:56.000+08:00' })],
    ['lowercase zone', record({ completed_at: '2026-10-03T12:34:56.000z' })],
    ['normalized impossible date', record({ completed_at: '2026-02-30T12:34:56.000Z' })],
    ['time over length', record({ completed_at: ' '.repeat(41) + record().completed_at })],
  ])('rejects %s without accepting it in an imported progress', (_label, invalid) => {
    expect(validPracticeRecord(invalid)).toBe(false);
    expect(validateProgress({ ...emptyProgress, practice: [invalid] })).toBe(false);
  });

  it('rejects inherited fields and non-enumerable fields that cannot roundtrip through JSON', () => {
    const inherited = Object.assign(Object.create({ completed_at: record().completed_at }), {
      lesson_id: 'fullstack-routing',
      language: 'python',
    });
    const extra = Object.defineProperty(record(), 'hidden', { value: true, enumerable: false });
    expect(validPracticeRecord(inherited)).toBe(false);
    expect(validPracticeRecord(extra)).toBe(false);
    const hiddenTime = Object.defineProperty(record(), 'completed_at', { enumerable: false });
    expect(validPracticeRecord(hiddenTime)).toBe(false);
    expect(savePractice(emptyProgress, hiddenTime)).toBe(emptyProgress);
  });

  it('rejects duplicate pairs, sparse collections and oversized lists but allows separate languages', () => {
    expect(MAX_PRACTICE_RECORDS).toBe(256);
    for (const invalid of [
      null,
      {},
      Array(1),
      [record(), record()],
      [...fullProgress().practice!, record()],
    ]) {
      expect(validPracticeRecords(invalid)).toBe(false);
      expect(validateProgress({ ...emptyProgress, practice: invalid })).toBe(false);
    }
    const twoLanguages = [record(), record({ language: 'go' })];
    expect(validPracticeRecords(twoLanguages)).toBe(true);
    expect(validateProgress({ ...emptyProgress, practice: twoLanguages })).toBe(true);
    expect(validPracticeRecords(fullProgress().practice)).toBe(true);
    expect(validateProgress(fullProgress())).toBe(true);
  });
});

describe('language practice changes', () => {
  it('saves a copied record and preserves all common progress fields and other language records', () => {
    const original: Progress = { ...existingProgress(), practice: [record({ language: 'go' })] };
    const incoming = record();
    const saved = savePractice(original, incoming);
    expect(saved).not.toBe(original);
    expectOtherProgressUnchanged(saved, original);
    expect(saved.practice).toHaveLength(2);
    expect(saved.practice?.[0]).toBe(original.practice?.[0]);
    expect(original.practice).toHaveLength(1);
    incoming.completed_at = '2026-10-03T13:00:00.000Z';
    expect(practiceFor(saved, 'fullstack-routing', 'python')?.completed_at).toBe(
      record().completed_at,
    );
  });

  it('keeps the first timestamp on repeated confirmation and permits a new timestamp after undo', () => {
    const first = savePractice(existingProgress(), record());
    const later = record({ completed_at: '2026-10-03T13:00:00.000Z' });
    expect(savePractice(first, later)).toBe(first);
    expect(practiceFor(first, later.lesson_id, later.language)?.completed_at).toBe(
      record().completed_at,
    );
    const removed = removePractice(first, later.lesson_id, later.language);
    expect(removed).not.toBe(first);
    expect(removed.practice).toEqual([]);
    expectOtherProgressUnchanged(removed, first);
    const confirmedAgain = savePractice(removed, later);
    expect(practiceFor(confirmedAgain, later.lesson_id, later.language)).toEqual(later);
    expect(practiceFor(first, later.lesson_id, later.language)).toEqual(record());
  });

  it('undoes only the selected lesson and language without completing or undoing common lessons', () => {
    const original: Progress = {
      ...existingProgress(),
      practice: [
        record(),
        record({ language: 'go' }),
        record({ lesson_id: 'agent-research-agent' }),
      ],
    };
    const removed = removePractice(original, 'fullstack-routing', 'python');
    expectOtherProgressUnchanged(removed, original);
    expect(practiceFor(removed, 'fullstack-routing', 'python')).toBeUndefined();
    expect(practiceFor(removed, 'fullstack-routing', 'go')).toBe(original.practice?.[1]);
    expect(practiceFor(removed, 'agent-research-agent', 'python')).toBe(original.practice?.[2]);
    expect(original.practice).toHaveLength(3);
    expect(removePractice(removed, 'fullstack-routing', 'python')).toBe(removed);
    expect(removePractice(emptyProgress, 'fullstack-routing', 'python')).toBe(emptyProgress);
  });

  it('refuses new records at capacity while preserving existing times and allowing removal', () => {
    const full = fullProgress();
    expect(savePractice(full, record())).toBe(full);
    const changed = record({
      lesson_id: 'fullstack-example-12',
      completed_at: '2026-10-04T00:00:00.000Z',
    });
    expect(savePractice(full, changed)).toBe(full);
    expect(practiceFor(full, changed.lesson_id, changed.language)?.completed_at).toBe(
      record().completed_at,
    );
    const removed = removePractice(full, changed.lesson_id, changed.language);
    expect(removed.practice).toHaveLength(255);
    expect(full.practice).toHaveLength(256);
    expect(savePractice(removed, record()).practice).toHaveLength(256);
  });

  it('leaves malformed collections and invalid inputs unchanged', () => {
    for (const invalid of [
      null,
      Array(1),
      [record(), record()],
      [record({ completed_at: 'invalid' })],
      [...fullProgress().practice!, record()],
    ]) {
      const malformed = { ...existingProgress(), practice: invalid } as Progress;
      expect(practiceFor(malformed, 'fullstack-routing', 'python')).toBeUndefined();
      expect(savePractice(malformed, record({ language: 'go' }))).toBe(malformed);
      expect(removePractice(malformed, 'fullstack-routing', 'python')).toBe(malformed);
    }
    const original = savePractice(existingProgress(), record());
    expect(savePractice(original, record({ completed_at: 'invalid' }))).toBe(original);
    expect(savePractice(original, { ...record(), extra: true } as PracticeRecord)).toBe(original);
    for (const [lessonId, language] of [
      ['../fullstack-routing', 'python'],
      ['agent-research-agent', 'go'],
      ['fullstack-routing', 'rust'],
    ] as const) {
      expect(practiceFor(original, lessonId, language as Language)).toBeUndefined();
      expect(removePractice(original, lessonId, language as Language)).toBe(original);
    }
  });
});
