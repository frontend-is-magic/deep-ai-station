import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import { describe, expect, it } from 'vitest';
import {
  evidenceFor,
  evidenceMarkdown,
  saveEvidence,
  validEvidenceRecord,
} from '../src/lib/evidence';
import { emptyProgress, validateProgress } from '../src/lib/state';
import type { EvidenceRecord, Progress } from '../src/lib/types';

function record(overrides: Partial<EvidenceRecord> = {}): EvidenceRecord {
  return {
    lesson_id: 'fullstack-integration',
    language: 'python',
    revision: 'feat: 课程资料 API · commit example',
    command: 'uv run pytest',
    success: '演示请求返回来源 api。',
    failure: '缺少访问码返回 401。',
    pending: '真实模型与生产发布尚未验收。',
    updated_at: '2026-10-03T12:34:56.000Z',
    ...overrides,
  };
}

describe('practice evidence import', () => {
  it('accepts old version-one backups without adding or requiring evidence', () => {
    const old = JSON.parse(JSON.stringify(emptyProgress));
    expect(validateProgress(old)).toBe(true);
    expect('evidence' in old).toBe(false);
    expect(evidenceFor(old, 'fullstack-integration', 'python')).toBeUndefined();
    expect(validateProgress({ ...old, evidence: [] })).toBe(true);
  });

  it('roundtrips a complete record without changing version or the existing progress', () => {
    const progress = saveEvidence(emptyProgress, record());
    const restored = JSON.parse(JSON.stringify(progress));
    expect(validateProgress(restored)).toBe(true);
    expect(restored.version).toBe(1);
    expect(evidenceFor(restored, 'fullstack-integration', 'python')).toEqual(record());
    expect(emptyProgress.evidence).toBeUndefined();
  });

  it('accepts empty text fields and the documented maximum text lengths', () => {
    expect(
      validEvidenceRecord(
        record({ revision: '', command: '', success: '', failure: '', pending: '' }),
      ),
    ).toBe(true);
    expect(
      validEvidenceRecord(
        record({
          lesson_id: 'fullstack-' + 'a'.repeat(90),
          revision: 'r'.repeat(200),
          command: 'c'.repeat(2000),
          success: 's'.repeat(2000),
          failure: 'f'.repeat(2000),
          pending: 'p'.repeat(2000),
        }),
      ),
    ).toBe(true);
    expect(validEvidenceRecord(record({ lesson_id: 'agent-research-agent' }))).toBe(true);
  });

  it('rejects malformed, oversized, extra and mismatched course-language fields', () => {
    for (const invalid of [
      null,
      [],
      { ...record(), lesson_id: '' },
      { ...record(), lesson_id: '../fullstack-integration' },
      { ...record(), lesson_id: 'unrelated-course' },
      { ...record(), lesson_id: 'fullstack-' + 'a'.repeat(91) },
      { ...record(), lesson_id: 'agent-research-agent', language: 'go' },
      { ...record(), language: 'rust' },
      { ...record(), revision: 'x'.repeat(201) },
      ...(['command', 'success', 'failure', 'pending'] as const).map((field) => ({
        ...record(),
        [field]: 'x'.repeat(2001),
      })),
      { ...record(), command: 12 },
      { ...record(), pending: undefined },
      { ...record(), token: 'must-not-import' },
      { ...record(), updated_at: 'invalid-date' },
      { ...record(), updated_at: ' '.repeat(100) + record().updated_at },
    ]) {
      expect(validEvidenceRecord(invalid)).toBe(false);
      expect(validateProgress({ ...emptyProgress, evidence: [invalid] })).toBe(false);
    }
  });

  it('rejects duplicate pairs and lists over capacity while allowing other languages', () => {
    expect(validateProgress({ ...emptyProgress, evidence: [record(), record()] })).toBe(false);
    expect(
      validateProgress({ ...emptyProgress, evidence: [record(), record({ language: 'go' })] }),
    ).toBe(true);
    expect(validateProgress({ ...emptyProgress, evidence: null })).toBe(false);
    expect(validateProgress({ ...emptyProgress, evidence: Array(1) })).toBe(false);
    const evidence = Array.from({ length: 25 }, (_, index) =>
      record({ lesson_id: `fullstack-course-${index}` }),
    );
    expect(validateProgress({ ...emptyProgress, evidence })).toBe(false);
    expect(validateProgress({ ...emptyProgress, evidence: evidence.slice(0, 24) })).toBe(true);
  });
});

describe('practice evidence saving', () => {
  it('updates only the selected pair and preserves notes, completion and other records', () => {
    const original: Progress = {
      ...emptyProgress,
      completed: ['fullstack-http'],
      bookmarks: ['article-example'],
      notes: { 'fullstack-integration': '已有实践笔记' },
      evidence: [
        record(),
        record({ language: 'go' }),
        record({ lesson_id: 'agent-research-agent' }),
      ],
    };
    const incoming = record({ command: 'uv run pytest -q', updated_at: '2026-10-03T12:35:00Z' });
    const saved = saveEvidence(original, incoming);
    expect(evidenceFor(saved, incoming.lesson_id, incoming.language)).toEqual(incoming);
    expect(saved.notes).toBe(original.notes);
    expect(saved.completed).toBe(original.completed);
    expect(saved.bookmarks).toBe(original.bookmarks);
    expect(saved.runs).toBe(original.runs);
    expect(saved.evidence?.[1]).toBe(original.evidence?.[1]);
    expect(saved.evidence?.[2]).toBe(original.evidence?.[2]);
    expect(original.evidence?.[0].command).toBe('uv run pytest');
    incoming.command = 'caller changed its input';
    expect(saved.evidence?.[0].command).toBe('uv run pytest -q');
  });

  it('refuses invalid input and full-capacity additions but permits updating a full list', () => {
    const full: Progress = {
      ...emptyProgress,
      evidence: Array.from({ length: 24 }, (_, index) =>
        record({ lesson_id: `fullstack-course-${index}` }),
      ),
    };
    expect(saveEvidence(full, record())).toBe(full);
    expect(saveEvidence(full, record({ command: 'x'.repeat(2001) }))).toBe(full);
    const update = record({ lesson_id: 'fullstack-course-12', success: '更新后的实际结果' });
    const saved = saveEvidence(full, update);
    expect(saved).not.toBe(full);
    expect(saved.evidence).toHaveLength(24);
    expect(evidenceFor(saved, update.lesson_id, 'python')).toEqual(update);
    const malformed = { ...emptyProgress, evidence: [record(), record()] };
    expect(saveEvidence(malformed, record({ language: 'go' }))).toBe(malformed);
  });
});

describe('practice evidence Markdown export', () => {
  it('contains the course, language, timestamp, all five values and honest verification status', () => {
    const evidence = record();
    const markdown = evidenceMarkdown({ id: evidence.lesson_id, title: '毕业项目集成' }, evidence);
    for (const value of [
      '毕业项目集成',
      evidence.lesson_id,
      'Python',
      evidence.updated_at,
      evidence.revision,
      evidence.command,
      evidence.success,
      evidence.failure,
      evidence.pending,
    ])
      expect(markdown).toContain(value);
    expect(markdown).toContain('未经平台核验');
    expect(markdown).toContain('不会自动完成课程');
  });

  it('explains empty fields and retains intentional whitespace', () => {
    const evidence = record({ revision: '', command: '', success: '', failure: '', pending: '' });
    const lesson = { id: evidence.lesson_id, title: '毕业实践' };
    expect(evidenceMarkdown(lesson, evidence).match(/未填写/g)).toHaveLength(5);
    expect(evidenceMarkdown(lesson, { ...evidence, command: '  retained  \n\tline' })).toContain(
      '  retained  \n\tline',
    );
  });

  it('uses longer plain-text fences so user HTML and markdown cannot escape into the document', () => {
    const attack =
      '``````\n<script>alert(1)</script>\n<img src="https://example.com/pixel">\n# injected heading\n~~~';
    const evidence = record({
      revision: attack,
      command: attack,
      success: attack,
      failure: attack,
      pending: attack,
    });
    const markdown = evidenceMarkdown({ id: 'course\n' + attack, title: attack }, evidence);
    expect(markdown).toContain('```````text\n' + attack + '\n```````');
    const html = renderToStaticMarkup(createElement(ReactMarkdown, { children: markdown }));
    expect(html).toContain('&lt;script&gt;alert(1)&lt;/script&gt;');
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('<img');
    expect(html).not.toContain('<h1>injected heading</h1>');
    expect((html.match(/<code class="language-text">/g) || []).length).toBe(8);
  });
});
