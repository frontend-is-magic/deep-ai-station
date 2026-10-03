import { createStore } from 'jotai';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  editLatestProgress,
  RESET_NOTICE,
  setBookmarkSaved,
  setLessonNote,
  setLessonCompleted,
} from '../src/lib/progress-write';
import { emptyProgress, getStorageIssue, progressAtom, validateProgress } from '../src/lib/state';
import { evidenceFor, saveEvidence } from '../src/lib/evidence';
import { removePractice, savePractice } from '../src/lib/practice';
import type { EvidenceRecord, FeedItem, Progress } from '../src/lib/types';

const firstReset = '00000000-0000-4000-8000-000000000001';
const secondReset = '00000000-0000-4000-8000-000000000002';
const date = '2026-10-04T00:00:00.000Z';
const item: FeedItem = {
  id: 'public-guide',
  title: '公开课程',
  summary: '摘要',
  source: '课程文档',
  url: 'https://example.com/course',
  track: 'agent',
  tags: ['MCP'],
  kind: 'guide',
  published: null,
};
const evidence: EvidenceRecord = {
  lesson_id: 'fullstack-database',
  language: 'go',
  revision: 'current-version',
  command: 'go test ./...',
  success: 'latest success',
  failure: 'latest failure',
  pending: '',
  updated_at: date,
};
function progress(overrides: Partial<Progress> = {}): Progress {
  return { ...emptyProgress, history_reset_id: firstReset, ...overrides };
}
function disk(value: Progress | null) {
  const storage = {
    getItem: vi.fn(() => (value === null ? null : JSON.stringify(value))),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  };
  vi.stubGlobal('localStorage', storage);
  return storage;
}

afterEach(() => vi.unstubAllGlobals());

describe('editLatestProgress', () => {
  it('edits only the target on the latest valid snapshot before a storage event arrives', () => {
    const old = progress({ notes: { 'agent-mcp': 'old note' } });
    const latest = progress({
      notes: { 'agent-mcp': 'latest note', 'agent-memory': 'another tab' },
      completed: ['agent-memory'],
      evidence: [evidence],
      practice: [{ lesson_id: 'fullstack-database', language: 'go', completed_at: date }],
      quizReview: [{ lesson_id: 'agent-tool-contract', added_at: date }],
      runs: [
        {
          id: 'old-compatible-id',
          prompt: 'original prompt',
          answer: 'original answer',
          provider: 'old-provider',
          track: 'agent',
          date,
          duration_ms: 0,
          usage: { total_tokens: 0 },
        },
      ],
    });
    expect(validateProgress(latest)).toBe(true);
    const storage = disk(latest);
    const change = vi.fn((current: Progress) => ({ ...current, language: 'go' as const }));
    expect(editLatestProgress(old, change)).toEqual({
      progress: { ...latest, language: 'go' },
      reset: false,
    });
    expect(change).toHaveBeenCalledOnce();
    expect(old.notes).toEqual({ 'agent-mcp': 'old note' });
    expect(storage.setItem).not.toHaveBeenCalled();
    expect(storage.removeItem).not.toHaveBeenCalled();
  });

  it.each([
    [firstReset, secondReset],
    [undefined, secondReset],
    [firstReset, undefined],
  ])('rejects old content when the displayed reset ID %s differs from %s', (oldId, newId) => {
    const old = progress({ history_reset_id: oldId, notes: { 'agent-mcp': 'old content' } });
    const latest = progress({ history_reset_id: newId, notes: { 'agent-mcp': 'imported' } });
    disk(latest);
    const change = vi.fn(() => old);
    expect(editLatestProgress(old, change, { resetId: oldId })).toEqual({
      progress: latest,
      reset: true,
    });
    expect(change).not.toHaveBeenCalled();
  });

  it('allows legacy v1 content when both the displayed and stored IDs are absent', () => {
    const legacy = { ...emptyProgress, notes: { unknown: 'compatible old key' } };
    disk(legacy);
    const result = editLatestProgress(
      legacy,
      (current) => ({ ...current, notes: { ...current.notes, 'agent-mcp': 'new note' } }),
      { resetId: undefined },
    );
    expect(result.reset).toBe(false);
    expect(result.progress.notes).toEqual({
      unknown: 'compatible old key',
      'agent-mcp': 'new note',
    });
    expect(result.progress).not.toHaveProperty('history_reset_id');
    expect(validateProgress(result.progress)).toBe(true);
  });

  it('permits an explicit new action after reset without reviving old fields', () => {
    const old = progress({ notes: { 'agent-mcp': 'old' }, evidence: [evidence] });
    const cleared = progress({ history_reset_id: secondReset });
    disk(cleared);
    const result = editLatestProgress(old, (current) => setBookmarkSaved(current, item, true));
    expect(result).toEqual({
      progress: { ...cleared, bookmarks: [item.id], savedItems: [item] },
      reset: false,
    });
    expect(result.progress.notes).toEqual({});
    expect(result.progress.evidence).toBeUndefined();
  });

  it('preserves newer notes in the same epoch while editing one course', () => {
    const old = progress({ notes: { 'agent-mcp': 'old text' } });
    const latest = progress({ notes: { 'agent-mcp': 'changed', 'agent-memory': 'keep' } });
    disk(latest);
    const result = editLatestProgress(
      old,
      (current) => ({ ...current, notes: { ...current.notes, 'agent-mcp': 'intentional edit' } }),
      { resetId: firstReset },
    );
    expect(result.progress.notes).toEqual({
      'agent-mcp': 'intentional edit',
      'agent-memory': 'keep',
    });
    expect(result.reset).toBe(false);
  });

  it('edits one evidence field from latest fields and does not overwrite other languages', () => {
    const old = progress({ evidence: [{ ...evidence, command: 'old', failure: 'old' }] });
    const latest = progress({
      evidence: [evidence, { ...evidence, language: 'python', command: 'pytest' }],
    });
    disk(latest);
    const result = editLatestProgress(
      old,
      (current) =>
        saveEvidence(current, {
          ...evidenceFor(current, evidence.lesson_id, 'go')!,
          command: 'go test -race ./...',
        }),
      { resetId: firstReset },
    );
    expect(result.progress.evidence).toEqual([
      { ...evidence, command: 'go test -race ./...' },
      latest.evidence![1],
    ]);
  });

  it('does not reconstruct a deleted evidence record from old content after reset', () => {
    const old = progress({ evidence: [evidence] });
    const latest = progress({ history_reset_id: secondReset });
    disk(latest);
    const save = vi.fn((current: Progress) => saveEvidence(current, evidence));
    expect(editLatestProgress(old, save, { resetId: firstReset })).toEqual({
      progress: latest,
      reset: true,
    });
    expect(save).not.toHaveBeenCalled();
  });

  it('treats actual storage deletion as empty data without resurrecting old fields', () => {
    disk(null);
    const result = editLatestProgress(progress({ evidence: [evidence] }), (current) => ({
      ...current,
      language: 'python',
    }));
    expect(result).toEqual({ progress: { ...emptyProgress, language: 'python' }, reset: false });
  });

  it.each(['{', 'null', '[]', JSON.stringify({ ...emptyProgress, version: 2 })])(
    'retains memory rescue if persisted data is invalid: %s',
    (raw) => {
      vi.stubGlobal('localStorage', { getItem: () => raw });
      const previous = progress({ notes: { rescue: 'keep' } });
      expect(editLatestProgress(previous, (current) => current).progress).toBe(previous);
    },
  );

  it('retains memory if localStorage cannot be read', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('unavailable');
      },
    });
    const previous = progress({ notes: { rescue: 'keep' } });
    expect(editLatestProgress(previous, (current) => current).progress).toBe(previous);
  });

  it('keeps failed writes in memory even if readable disk has an older epoch', () => {
    const stored = progress();
    const memory = progress({
      history_reset_id: secondReset,
      notes: { rescue: 'not yet on disk' },
    });
    const storage = disk(stored);
    storage.setItem.mockImplementation(() => {
      throw new Error('quota');
    });
    const store = createStore();
    try {
      store.set(progressAtom, memory);
      expect(getStorageIssue()).not.toBeNull();
      const result = editLatestProgress(
        store.get(progressAtom),
        (current) => ({ ...current, language: 'go' }),
        { resetId: secondReset },
      );
      expect(result).toEqual({ progress: { ...memory, language: 'go' }, reset: false });
    } finally {
      storage.setItem.mockImplementation(() => undefined);
      store.set(progressAtom, memory);
      expect(getStorageIssue()).toBeNull();
    }
  });

  it('leaves raw import and clear replacement available without merging old state', () => {
    disk(progress({ notes: { old: 'must disappear' }, evidence: [evidence] }));
    const store = createStore();
    const imported = progress({ history_reset_id: secondReset, notes: { imported: 'only this' } });
    store.set(progressAtom, imported);
    expect(store.get(progressAtom)).toBe(imported);
    const cleared = { ...emptyProgress, history_reset_id: firstReset };
    store.set(progressAtom, cleared);
    expect(store.get(progressAtom)).toBe(cleared);
    expect(store.get(progressAtom).evidence).toBeUndefined();
  });

  it('offers a plain user-facing rejection notice', () => {
    expect(RESET_NOTICE).toContain('未保存');
    expect(RESET_NOTICE).not.toMatch(/epoch|UUID|history_reset_id/);
  });
});

describe('explicit bookmark intent', () => {
  it('ensures saved while preserving a newer saved item snapshot', () => {
    const newerItem = { ...item, title: 'updated title', summary: 'updated snapshot' };
    const current = progress({ bookmarks: [item.id], savedItems: [newerItem] });
    expect(setBookmarkSaved(current, item, true)).toBe(current);
    expect(current.savedItems).toEqual([newerItem]);
  });

  it('does not turn a repeated remove into an add', () => {
    const current = progress();
    expect(setBookmarkSaved(current, item, false)).toBe(current);
    expect(current).not.toHaveProperty('savedItems');
  });

  it('adds and removes only the selected item, preserving latest unrelated data', () => {
    const other = { ...item, id: 'other' };
    const current = progress({
      bookmarks: [other.id],
      savedItems: [other],
      notes: { 'agent-mcp': 'keep' },
      evidence: [evidence],
    });
    const saved = setBookmarkSaved(current, item, true);
    expect(saved).toEqual({
      ...current,
      bookmarks: [other.id, item.id],
      savedItems: [other, item],
    });
    expect(saved.notes).toBe(current.notes);
    expect(saved.evidence).toBe(current.evidence);
    expect(setBookmarkSaved(saved, item, false)).toEqual(current);
  });

  it('uses the latest membership rather than toggling what the stale page displayed', () => {
    const old = progress();
    const stored = setBookmarkSaved(old, { ...item, title: 'newer' }, true);
    disk(stored);
    const result = editLatestProgress(old, (current) => setBookmarkSaved(current, item, true));
    expect(result.progress).toEqual(stored);
  });

  it('checks latest capacity while keeping existing actions idempotent', () => {
    const current = progress({
      bookmarks: Array.from({ length: 200 }, (_, index) => `saved-${index}`),
    });
    disk(current);
    expect(
      editLatestProgress(progress(), (latest) => setBookmarkSaved(latest, item, true)).progress,
    ).toEqual(current);
    expect(setBookmarkSaved(current, { ...item, id: 'saved-0' }, true)).toBe(current);
    expect(setBookmarkSaved(current, { ...item, id: 'saved-0' }, false).bookmarks).toHaveLength(
      199,
    );
  });

  it('retains the existing invalid feed item rejection', () => {
    const current = progress();
    expect(setBookmarkSaved(current, { ...item, url: 'javascript:alert(1)' }, true)).toBe(current);
  });
});

describe('practice intent composed with latest progress', () => {
  it('does not replace a newer completion timestamp on repeat add', () => {
    const record = { lesson_id: 'fullstack-database', language: 'go' as const, completed_at: date };
    const current = progress({ practice: [record], notes: { another: 'keep' } });
    disk(current);
    expect(
      editLatestProgress(progress(), (latest) =>
        savePractice(latest, { ...record, completed_at: '2026-10-05T00:00:00.000Z' }),
      ).progress,
    ).toEqual(current);
  });

  it('refuses new practice at latest capacity and still allows an explicit remove', () => {
    const current = progress({
      practice: Array.from({ length: 256 }, (_, index) => ({
        lesson_id: `fullstack-lesson-${index}`,
        language: 'go',
        completed_at: date,
      })),
      notes: { another: 'keep' },
    });
    disk(current);
    const result = editLatestProgress(progress(), (latest) =>
      savePractice(latest, { lesson_id: 'agent-mcp', language: 'python', completed_at: date }),
    );
    expect(result.progress).toEqual(current);
    const removed = editLatestProgress(progress(), (latest) =>
      removePractice(latest, 'fullstack-lesson-0', 'go'),
    ).progress;
    expect(removed.practice).toHaveLength(255);
    expect(removed.notes).toEqual(current.notes);
  });
});

describe('latest note and completion capacity', () => {
  function fullNotes(): Progress {
    return progress({
      notes: Object.fromEntries(
        Array.from({ length: 1000 }, (_, i) => [`legacy key ${i}`, 'keep']),
      ),
      evidence: [evidence],
    });
  }
  function fullCompleted(): Progress {
    return progress({ completed: Array.from({ length: 2000 }, (_, i) => `legacy key ${i}`) });
  }

  it('rejects a 1001st note from stale memory and retains a valid latest backup', () => {
    const latest = fullNotes();
    expect(validateProgress(latest)).toBe(true);
    disk(latest);
    let capacityReached = false;
    const result = editLatestProgress(progress(), (current) => {
      const edit = setLessonNote(current, 'agent-mcp', 'new content');
      capacityReached = edit.capacityReached;
      return edit.progress;
    });
    expect(capacityReached).toBe(true);
    expect(result.progress).toEqual(latest);
    expect(validateProgress(result.progress)).toBe(true);
  });

  it('allows a full list to edit and delete existing notes, then reuse the released slot', () => {
    const latest = fullNotes();
    const edited = setLessonNote(latest, 'legacy key 0', 'edited');
    expect(edited.capacityReached).toBe(false);
    expect(edited.progress.notes['legacy key 0']).toBe('edited');
    expect(edited.progress.evidence).toBe(latest.evidence);
    expect(setLessonNote(edited.progress, 'legacy key 0', 'edited').progress).toBe(edited.progress);
    const cleared = setLessonNote(edited.progress, 'legacy key 0', '');
    expect(cleared.capacityReached).toBe(false);
    expect(cleared.progress.notes).not.toHaveProperty('legacy key 0');
    const added = setLessonNote(cleared.progress, 'agent-mcp', 'new');
    expect(added.capacityReached).toBe(false);
    expect(Object.keys(added.progress.notes)).toHaveLength(1000);
    expect(validateProgress(added.progress)).toBe(true);
    expect(latest.notes['legacy key 0']).toBe('keep');
  });

  it('does not allocate a slot for an empty new note or trim intentional whitespace', () => {
    const latest = fullNotes();
    expect(setLessonNote(latest, 'agent-mcp', '')).toEqual({
      progress: latest,
      capacityReached: false,
    });
    expect(setLessonNote(latest, 'agent-mcp', '').progress).toBe(latest);
    const result = setLessonNote(progress(), 'agent-mcp', '  ');
    expect(result.progress.notes['agent-mcp']).toBe('  ');
  });

  it('resolves reset before capacity without running the rejected note edit', () => {
    disk({ ...fullNotes(), history_reset_id: secondReset });
    const edit = vi.fn((current: Progress) => setLessonNote(current, 'agent-mcp', 'old').progress);
    expect(editLatestProgress(progress(), edit, { resetId: firstReset }).reset).toBe(true);
    expect(edit).not.toHaveBeenCalled();
  });

  it('rejects a 2001st completion from stale memory without making the backup invalid', () => {
    const latest = fullCompleted();
    expect(validateProgress(latest)).toBe(true);
    disk(latest);
    let capacityReached = false;
    const result = editLatestProgress(progress(), (current) => {
      const edit = setLessonCompleted(current, 'agent-mcp', true);
      capacityReached = edit.capacityReached;
      return edit.progress;
    });
    expect(capacityReached).toBe(true);
    expect(result.progress).toEqual(latest);
    expect(validateProgress(result.progress)).toBe(true);
  });

  it('keeps completion idempotent at capacity and allows remove then add', () => {
    const latest = fullCompleted();
    expect(setLessonCompleted(latest, 'legacy key 0', true)).toEqual({
      progress: latest,
      capacityReached: false,
    });
    expect(setLessonCompleted(latest, 'legacy key 0', true).progress).toBe(latest);
    expect(setLessonCompleted(latest, 'absent', false).progress).toBe(latest);
    const removed = setLessonCompleted(latest, 'legacy key 0', false);
    expect(removed.capacityReached).toBe(false);
    expect(removed.progress.completed).toHaveLength(1999);
    const added = setLessonCompleted(removed.progress, 'agent-mcp', true);
    expect(added.capacityReached).toBe(false);
    expect(added.progress.completed).toHaveLength(2000);
    expect(added.progress.notes).toBe(latest.notes);
    expect(validateProgress(added.progress)).toBe(true);
  });
});
