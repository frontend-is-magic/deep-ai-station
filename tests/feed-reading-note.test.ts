import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  createFeedReadingDraft,
  FEED_READING_QUESTION_LIMIT,
  FEED_READING_UNDERSTANDING_LIMIT,
  prepareFeedReadingNote,
  saveFeedReadingNote,
  type FeedReadingDraft,
} from '../src/lib/feed-reading-note';
import { editLatestProgress } from '../src/lib/progress-write';
import { emptyProgress, validateProgress } from '../src/lib/state';
import type { FeedItem, Lesson, Progress, Track, TrackId } from '../src/lib/types';

const ID = '9f6f0232-0fbe-4f92-a7f6-c32731bb244b';
const NEXT_ID = 'ce639dc6-070d-4c24-9e2f-be878ed55918';
const RESET_ID = '1fb35d2b-e072-4030-a53b-f7d7d8dba949';
const AT = '2026-10-04T12:34:56.789Z';
const UNDERSTANDING = '  服务端应明确校验每个参数。\n这与框架自动转换不同。  ';
const QUESTION = '\t验证缺失参数与错误类型返回的区别。\n';
const mappings = [
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

function source(id: string = 'fastapi-guide'): FeedItem {
  const row = mappings.find((entry) => entry[0] === id)!;
  return {
    id: row[0],
    url: row[1],
    track: row[2],
    kind: 'guide',
    title: `资料 ${row[0]}`,
    summary: '仅平台摘要，不能当作原文引句',
    source: '公开官方资料',
    tags: ['参考'],
    published: null,
  };
}
function lesson(id: string, track: TrackId): Lesson {
  return {
    id,
    track,
    title: `课程 ${id}`,
    stage: 'basics',
    objective: '学习目标',
    minutes: 15,
    level: '入门',
    body: ['原理'],
    steps: ['实践'],
    criteria: ['验收'],
    resources: [],
    quiz: { question: '问题', options: ['选项'], answer: 0, explanation: '解释' },
    snippets:
      track === 'agent'
        ? { python: 'print("agent")' }
        : {
            python: 'print("api")',
            typescript: 'console.log("api")',
            go: 'package main',
          },
  };
}
function tracks(): Track[] {
  return (['agent', 'fullstack'] as const).map((id) => ({
    id,
    title: id,
    description: '路线',
    stages: [],
    languages: id === 'agent' ? ['python'] : ['typescript', 'go', 'python'],
    lessons: [...new Set(mappings.filter((row) => row[2] === id).map((row) => row[3]))].map((key) =>
      lesson(key, id),
    ),
  }));
}
function progress(extra: Partial<Progress> = {}): Progress {
  return {
    ...emptyProgress,
    language: 'go',
    completed: [],
    bookmarks: [],
    notes: {},
    runs: [],
    ...extra,
  };
}
function draft(current = progress(), item = source()): FeedReadingDraft {
  const value = createFeedReadingDraft(item, tracks(), current, ID, AT);
  expect(value).toBeDefined();
  return value!;
}
function freeze<T>(value: T): T {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}
function expectRefused(current: Progress, catalogue: Track[], entry: FeedReadingDraft) {
  expect(prepareFeedReadingNote(current, catalogue, entry, UNDERSTANDING, QUESTION).status).toBe(
    'invalid',
  );
  expect(saveFeedReadingNote(current, catalogue, entry, UNDERSTANDING, QUESTION)).toBe(current);
}
afterEach(() => vi.unstubAllGlobals());

describe('controlled reading draft', () => {
  it.each(mappings)(
    'records the exact source, lesson and language for %s',
    (id, url, track, lessonId, language) => {
      const current = progress({ history_reset_id: RESET_ID });
      const item = source(id);
      const value = draft(current, item);
      expect(value).toMatchObject({
        id: ID,
        createdAt: AT,
        resetId: RESET_ID,
        item,
        lessonId,
        language,
      });
      expect(value.item).not.toBe(item);
      expect(value.item.tags).not.toBe(item.tags);
      const prepared = prepareFeedReadingNote(current, tracks(), value, UNDERSTANDING, QUESTION);
      expect(prepared.status).toBe('ready');
      for (const text of [
        url,
        item.title,
        item.source,
        lessonId,
        track,
        language,
        AT,
        UNDERSTANDING,
        QUESTION,
      ]) {
        expect(prepared.entry).toContain(text);
      }
      expect(prepared.entry).not.toContain(item.summary);
      expect(prepared.entry.split(ID)).toHaveLength(2);
      expect(prepared.entry).toContain('个人阅读摘记；不代表平台核验或实践完成');
    },
  );

  it('captures the source copy and the Jotai language without changing later preference', () => {
    const item = source('jotai-guide');
    const value = draft(progress(), item);
    item.title = 'later live title';
    item.tags.push('later tag');
    const changed = progress({ language: 'python' });
    const prepared = prepareFeedReadingNote(changed, tracks(), value, UNDERSTANDING, QUESTION);
    expect(prepared.status).toBe('ready');
    expect(prepared.entry).toContain('资料 jotai-guide');
    expect(prepared.entry).not.toContain('later live title');
    expect(value.item.tags).toEqual(['参考']);
    expect(prepared.entry).toContain('参考语言：go（公共前端）');
    expect(saveFeedReadingNote(changed, tracks(), value, UNDERSTANDING, QUESTION).language).toBe(
      'python',
    );
  });

  it('uses restored savedItems even when the live source is unavailable', () => {
    const old = progress({ bookmarks: ['fastapi-guide'], savedItems: [source()] });
    const restored = JSON.parse(JSON.stringify(old)) as Progress;
    const value = draft(restored, restored.savedItems![0]);
    const result = saveFeedReadingNote(restored, tracks(), value, UNDERSTANDING, QUESTION);
    expect(result.notes[value.lessonId]).toContain(value.item.url);
    expect(result.savedItems).toBe(restored.savedItems);
    expect(result.bookmarks).toBe(restored.bookmarks);
  });

  it.each([
    { id: 'unknown-guide' },
    { kind: 'news' },
    { url: 'https://fastapi.tiangolo.com/tutorial' },
    { track: 'agent' },
    { title: ' ' },
    { title: 'x'.repeat(251) },
    { source: 'x'.repeat(101) },
  ])('does not create or save a draft for an invalid source %j', (change) => {
    const current = progress();
    const item = { ...source(), ...change } as FeedItem;
    expect(createFeedReadingDraft(item, tracks(), current, ID, AT)).toBeUndefined();
    expectRefused(current, tracks(), { ...draft(current), item });
  });

  it.each([
    ['not-a-uuid', AT],
    [ID.toUpperCase(), AT],
    [ID.replace('-4f92-', '-1f92-'), AT],
    [ID, '2026-10-04T12:34:56Z'],
    [ID, '2026-02-30T12:34:56.789Z'],
    [ID, AT + '\n'],
  ])('rejects a noncanonical draft identity/time %s %s', (id, createdAt) => {
    const current = progress();
    expect(createFeedReadingDraft(source(), tracks(), current, id, createdAt)).toBeUndefined();
    expectRefused(current, tracks(), { ...draft(current), id, createdAt });
  });

  it('revalidates current catalogue uniqueness, language availability and draft target', () => {
    const current = progress();
    const value = draft(current);
    const missing = tracks();
    missing[1].lessons = [];
    const duplicate = tracks();
    duplicate[1].lessons.push(duplicate[1].lessons[0]);
    const emptyCode = tracks();
    emptyCode[1].lessons[0].snippets.python = ' \n';
    const wrongLanguage = tracks();
    wrongLanguage[1].languages = ['typescript'];
    const noTitle = tracks();
    noTitle[1].lessons[0].title = '';
    for (const catalogue of [missing, duplicate, emptyCode, wrongLanguage, noTitle]) {
      expect(createFeedReadingDraft(source(), catalogue, current, ID, AT)).toBeUndefined();
      expectRefused(current, catalogue, value);
    }
    expectRefused(current, tracks(), { ...value, lessonId: 'fullstack-async' });
    expectRefused(current, tracks(), { ...value, language: 'typescript' });
    expectRefused(current, tracks(), {
      ...value,
      language: 'ruby' as FeedReadingDraft['language'],
    });
  });

  it.each([
    [undefined, RESET_ID],
    [RESET_ID, undefined],
    [RESET_ID, NEXT_ID],
  ])(
    'rejects a draft from epoch %s against %s before treating its marker as saved',
    (oldEpoch, newEpoch) => {
      const original = progress({ history_reset_id: oldEpoch });
      const value = draft(original);
      const written = saveFeedReadingNote(original, tracks(), value, UNDERSTANDING, QUESTION);
      expectRefused({ ...written, history_reset_id: newEpoch }, tracks(), value);
    },
  );
});

describe('reading note append', () => {
  it('preserves all existing fields and raw text immutably through an old v1 backup', () => {
    const original = freeze(
      progress({
        notes: { 'fullstack-routing': '既有笔记  \r\n', 'agent-mcp': '另课笔记' },
        completed: ['fullstack-routing'],
        practice: [{ lesson_id: 'fullstack-routing', language: 'go', completed_at: AT }],
        evidence: [
          {
            lesson_id: 'fullstack-routing',
            language: 'go',
            revision: '',
            command: '',
            success: '',
            failure: '',
            pending: '',
            updated_at: AT,
          },
        ],
        quizReview: [{ lesson_id: 'agent-mcp', added_at: AT }],
        runs: [
          {
            id: 'legacy-id',
            prompt: '旧提问',
            answer: '旧输出',
            provider: 'demo',
            track: 'agent',
            date: AT,
            duration_ms: 0,
          },
        ],
      }),
    );
    const catalogue = freeze(tracks());
    const value = freeze(draft(original));
    const before = JSON.stringify(original);
    const result = saveFeedReadingNote(original, catalogue, value, UNDERSTANDING, QUESTION);
    expect(result).not.toBe(original);
    expect(result.notes).not.toBe(original.notes);
    expect(result.notes[value.lessonId]).toBe(
      original.notes[value.lessonId] +
        '\n\n' +
        prepareFeedReadingNote(original, catalogue, value, UNDERSTANDING, QUESTION).entry,
    );
    expect(result.notes['agent-mcp']).toBe(original.notes['agent-mcp']);
    for (const key of Object.keys(original) as (keyof Progress)[]) {
      if (key !== 'notes') expect(result[key]).toBe(original[key]);
    }
    expect(JSON.stringify(original)).toBe(before);
    const restored = JSON.parse(JSON.stringify(result)) as Progress;
    expect(validateProgress(restored)).toBe(true);
    expect(Object.keys(restored)).toEqual(Object.keys(original));
    expect(restored).not.toHaveProperty('history_reset_id');
    expect(restored.runs[0]).not.toHaveProperty('status');
    expect(saveFeedReadingNote(restored, catalogue, value, UNDERSTANDING, QUESTION)).toBe(restored);
  });

  it('deduplicates the whole marker line, not source identity or a UUID mentioned in prose', () => {
    const current = progress();
    const value = draft(current);
    const result = saveFeedReadingNote(current, tracks(), value, UNDERSTANDING, QUESTION);
    expect(prepareFeedReadingNote(result, tracks(), value, '', '').status).toBe('saved');
    expect(
      saveFeedReadingNote(result, tracks(), value, 'changed thought', 'changed question'),
    ).toBe(result);
    const crlf = {
      ...result,
      notes: { [value.lessonId]: result.notes[value.lessonId].replaceAll('\n', '\r\n') },
    };
    expect(prepareFeedReadingNote(crlf, tracks(), value, '', '').status).toBe('saved');
    for (const text of [
      ID,
      `稍后核对 ${ID}`,
      `### 阅读摘记 ${ID}extra`,
      `prefix### 阅读摘记 ${ID}`,
    ]) {
      const latest = progress({ notes: { [value.lessonId]: text } });
      expect(prepareFeedReadingNote(latest, tracks(), value, UNDERSTANDING, QUESTION).status).toBe(
        'ready',
      );
    }
    const another = createFeedReadingDraft(source(), tracks(), result, NEXT_ID, AT)!;
    const twice = saveFeedReadingNote(result, tracks(), another, UNDERSTANDING, QUESTION);
    expect(twice.notes[value.lessonId].split(ID)).toHaveLength(2);
    expect(twice.notes[value.lessonId].split(NEXT_ID)).toHaveLength(2);
    expect(twice.notes[value.lessonId]).toContain(result.notes[value.lessonId]);
  });

  it('requires both fields, preserves whitespace and counts UTF-16 units like the textarea', () => {
    const current = progress();
    const value = draft(current);
    expect(FEED_READING_UNDERSTANDING_LIMIT).toBe(1500);
    expect(FEED_READING_QUESTION_LIMIT).toBe(500);
    for (const [understanding, question] of [
      ['', QUESTION],
      [UNDERSTANDING, '\n \t'],
      [' ', ' '],
    ]) {
      expect(prepareFeedReadingNote(current, tracks(), value, understanding, question).status).toBe(
        'missing-fields',
      );
      expect(saveFeedReadingNote(current, tracks(), value, understanding, question)).toBe(current);
    }
    const understanding = ' ' + '😀'.repeat(749) + '\n';
    const question = '😀'.repeat(250);
    expect(understanding).toHaveLength(1500);
    expect(question).toHaveLength(500);
    const exact = prepareFeedReadingNote(current, tracks(), value, understanding, question);
    expect(exact.status).toBe('ready');
    expect(exact.entry).toContain(understanding);
    expect(exact.entry).toContain(question);
    for (const [left, right] of [
      [understanding + 'x', question],
      [understanding, question + 'x'],
    ]) {
      expect(prepareFeedReadingNote(current, tracks(), value, left, right).status).toBe('too-long');
      expect(saveFeedReadingNote(current, tracks(), value, left, right)).toBe(current);
    }
    expect(
      prepareFeedReadingNote(current, tracks(), value, null as unknown as string, QUESTION).status,
    ).toBe('invalid');
  });

  it('accepts exactly 10000 units and refuses overflow without modifying or truncating the draft', () => {
    const current = progress();
    const value = freeze(draft(current));
    const entry = prepareFeedReadingNote(current, tracks(), value, UNDERSTANDING, QUESTION).entry;
    const prefix = 'n'.repeat(10000 - entry.length - 2);
    const exact = progress({ notes: { [value.lessonId]: prefix } });
    expect(
      saveFeedReadingNote(exact, tracks(), value, UNDERSTANDING, QUESTION).notes[value.lessonId],
    ).toHaveLength(10000);
    const full = progress({ notes: { [value.lessonId]: prefix + 'x' } });
    const rejected = prepareFeedReadingNote(full, tracks(), value, UNDERSTANDING, QUESTION);
    expect(rejected.status).toBe('too-long');
    expect(rejected.entry).toBe(entry);
    expect(rejected.nextNote).toHaveLength(10001);
    expect(saveFeedReadingNote(full, tracks(), value, UNDERSTANDING, QUESTION)).toBe(full);
    expect(prepareFeedReadingNote(exact, tracks(), value, UNDERSTANDING, QUESTION).status).toBe(
      'ready',
    );
    expect(value.id).toBe(ID);
  });

  it('allows an existing note at the 1000-key limit and declines a new one', () => {
    const current = progress({
      notes: Object.fromEntries(
        Array.from({ length: 1000 }, (_, i) => [`agent-note-${i}`, 'keep']),
      ),
    });
    const value = draft(current);
    expect(prepareFeedReadingNote(current, tracks(), value, UNDERSTANDING, QUESTION).status).toBe(
      'too-long',
    );
    expect(saveFeedReadingNote(current, tracks(), value, UNDERSTANDING, QUESTION)).toBe(current);
    const notes = { ...current.notes };
    delete notes['agent-note-0'];
    notes[value.lessonId] = 'original';
    const latest = { ...current, notes };
    const saved = saveFeedReadingNote(latest, tracks(), value, UNDERSTANDING, QUESTION);
    expect(Object.keys(saved.notes)).toHaveLength(1000);
    expect(saved.notes[value.lessonId]).toMatch(/^original\n\n### 阅读摘记/);
  });

  it('rechecks the latest note and duplicate without using an earlier preview nextNote', () => {
    const old = progress({ history_reset_id: RESET_ID, notes: { 'fullstack-routing': 'old' } });
    const value = draft(old);
    const preview = prepareFeedReadingNote(old, tracks(), value, UNDERSTANDING, QUESTION);
    const latest = {
      ...old,
      notes: { 'fullstack-routing': 'other tab edit', 'agent-mcp': 'keep newer' },
    };
    const storage = { getItem: vi.fn(() => JSON.stringify(latest)), setItem: vi.fn() };
    vi.stubGlobal('localStorage', storage);
    const result = editLatestProgress(
      old,
      (current) => saveFeedReadingNote(current, tracks(), value, UNDERSTANDING, QUESTION),
      { resetId: value.resetId },
    );
    expect(result.reset).toBe(false);
    expect(result.progress.notes[value.lessonId]).toBe('other tab edit\n\n' + preview.entry);
    expect(result.progress.notes['agent-mcp']).toBe('keep newer');
    expect(storage.setItem).not.toHaveBeenCalled();
    expect(saveFeedReadingNote(result.progress, tracks(), value, UNDERSTANDING, QUESTION)).toBe(
      result.progress,
    );
    storage.getItem.mockReturnValue(JSON.stringify({ ...latest, history_reset_id: NEXT_ID }));
    const change = vi.fn((current: Progress) =>
      saveFeedReadingNote(current, tracks(), value, UNDERSTANDING, QUESTION),
    );
    expect(editLatestProgress(old, change, { resetId: value.resetId }).reset).toBe(true);
    expect(change).not.toHaveBeenCalled();
  });

  it('renders personal text and metadata as fenced text, never HTML or injected Markdown', () => {
    const attack =
      '``````\n<img src="https://example.com/image">\n<script>bad()</script>\n# injected\n~~~';
    const item = { ...source(), title: attack, source: attack };
    const current = progress();
    const catalogue = tracks();
    catalogue[1].lessons[0].title = attack;
    const value = createFeedReadingDraft(item, catalogue, current, ID, AT)!;
    const prepared = prepareFeedReadingNote(current, catalogue, value, attack, attack);
    expect(prepared.status).toBe('ready');
    expect(prepared.entry).toContain('```````text\n' + attack + '\n```````');
    const html = renderToStaticMarkup(createElement(ReactMarkdown, { children: prepared.entry }));
    expect(html).not.toContain('<img');
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('<h1>injected</h1>');
    expect(html).toContain('&lt;script&gt;bad()&lt;/script&gt;');
  });

  it('does not append into an invalid progress snapshot or malformed draft', () => {
    const value = draft();
    for (const current of [
      progress({ notes: { 'fullstack-routing': 'x'.repeat(10001) } }),
      { ...progress(), version: 2 } as unknown as Progress,
    ]) {
      expectRefused(current, tracks(), value);
      expect(createFeedReadingDraft(source(), tracks(), current, ID, AT)).toBeUndefined();
    }
    expectRefused(progress(), tracks(), null as unknown as FeedReadingDraft);
  });
});
