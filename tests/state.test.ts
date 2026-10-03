import { describe, expect, it } from 'vitest';
import { createSSEParser } from '../src/lib/api';
import { emptyProgress, toggleBookmark, validateProgress } from '../src/lib/state';
import type { FeedItem, RunRecord } from '../src/lib/types';

const article: FeedItem = {
  id: 'news-1',
  title: '工具授权',
  summary: '保存资料摘要',
  source: '官方',
  url: 'https://example.com/article',
  track: 'agent',
  tags: ['Tools'],
  kind: 'news',
  published: null,
};

describe('stream framing', () => {
  it('buffers frames and UTF-8 JSON payloads across arbitrary delivery boundaries', () => {
    const events: unknown[] = [];
    const parse = createSSEParser((event) => events.push(event));
    parse('event: delta\nda');
    parse('ta: {"text":"你好"}\n');
    expect(events).toHaveLength(0);
    parse('\nevent: done\ndata: {"run_id":"1"}\n\n');
    expect(events).toEqual([
      { event: 'delta', data: { text: '你好' } },
      { event: 'done', data: { run_id: '1' } },
    ]);
  });
});
describe('import boundary', () => {
  it('accepts a versioned progress snapshot', () =>
    expect(validateProgress(emptyProgress)).toBe(true));
  it.each([
    null,
    {},
    { ...emptyProgress, language: 'rust' },
    { ...emptyProgress, notes: null },
    { ...emptyProgress, completed: [1] },
    { ...emptyProgress, runs: [{ id: 'x' }] },
    { ...emptyProgress, token: 'must-not-import' },
    {
      ...emptyProgress,
      bookmarks: ['news-1'],
      savedItems: [{ ...article, url: 'javascript:alert(1)' }],
    },
    {
      ...emptyProgress,
      bookmarks: ['news-1'],
      savedItems: [{ ...article, url: 'https://secret@example.com/x' }],
    },
    { ...emptyProgress, savedItems: [article] },
  ])('rejects malformed persisted data: %s', (data) => expect(validateProgress(data)).toBe(false));
  it('keeps a complete article in exportable progress and removes it when unsaved', () => {
    const saved = toggleBookmark(emptyProgress, article);
    expect(validateProgress(JSON.parse(JSON.stringify(saved)))).toBe(true);
    expect(saved.savedItems?.[0].title).toBe(article.title);
    expect(toggleBookmark(saved, article).savedItems).toEqual([]);
  });
  it('accepts legacy runs and course runs but rejects mismatched or unsafe course identifiers', () => {
    const run: RunRecord = {
      id: 'run-1',
      prompt: '任务',
      answer: '结果',
      provider: 'demo',
      track: 'fullstack',
      date: '2026-10-03T00:00:00Z',
      duration_ms: 10,
    };
    expect(validateProgress({ ...emptyProgress, runs: [run] })).toBe(true);
    expect(
      validateProgress({ ...emptyProgress, runs: [{ ...run, lesson_id: 'fullstack-http' }] }),
    ).toBe(true);
    for (const lesson_id of ['agent-mcp', '', '../fullstack-http', 12]) {
      expect(validateProgress({ ...emptyProgress, runs: [{ ...run, lesson_id }] })).toBe(false);
    }
  });
  it('keeps bounded agent traces while rejecting unknown workflow and trace fields', () => {
    const run: RunRecord = {
      id: 'run-1',
      prompt: 'MCP',
      answer: '结果',
      provider: 'demo',
      track: 'agent',
      date: '2026-10-03T00:00:00Z',
      duration_ms: 10,
      workflow: 'agent',
      usage: { total_tokens: 12 },
      usage_complete: false,
      steps: 3,
      tool_count: 2,
      trace: [
        { id: 'run-1:model:1', title: '模型请求 1', detail: '供应商响应已完成', status: 'success' },
      ],
    };
    expect(validateProgress({ ...emptyProgress, runs: [run] })).toBe(true);
    for (const invalid of [
      { ...run, workflow: 'arbitrary' },
      { ...run, trace: [{ ...run.trace![0], token: 'must-not-import' }] },
      { ...run, trace: Array(13).fill(run.trace![0]) },
      { ...run, trace: [{ ...run.trace![0], status: 'arbitrary' }] },
      { ...run, usage: { token: 'must-not-import' } },
      { ...run, usage: { total_tokens: -1 } },
      { ...run, usage: { total_tokens: 300_000_001 } },
      { ...run, usage: { total_tokens: true } },
      { ...run, usage_complete: 'true' },
      { ...run, steps: 4 },
      { ...run, tool_count: 3 },
    ])
      expect(validateProgress({ ...emptyProgress, runs: [invalid] })).toBe(false);
  });
});
