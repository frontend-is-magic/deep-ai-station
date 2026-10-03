import { describe, expect, it } from 'vitest';
import { createSSEParser } from '../src/lib/api';
import { emptyProgress, toggleBookmark, validateProgress } from '../src/lib/state';
import type { FeedItem } from '../src/lib/types';

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
});
