import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import { describe, expect, it } from 'vitest';
import {
  evidenceFor,
  evidenceMarkdown,
  MAX_EVIDENCE_RECORDS,
  saveEvidence,
  validEvidenceRecord,
  validEvidenceRecords,
} from '../src/lib/evidence';
import { courseLabFor } from '../src/lib/course-labs';
import { emptyProgress, validateProgress } from '../src/lib/state';
import type { EvidenceRecord, Language, Progress } from '../src/lib/types';

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
    const evidence = Array.from({ length: MAX_EVIDENCE_RECORDS + 1 }, (_, index) =>
      record({ lesson_id: `fullstack-course-${index}` }),
    );
    expect(validateProgress({ ...emptyProgress, evidence })).toBe(false);
    expect(
      validateProgress({ ...emptyProgress, evidence: evidence.slice(0, MAX_EVIDENCE_RECORDS) }),
    ).toBe(true);
  });
});

describe('practice evidence saving', () => {
  it('updates only the selected pair and preserves notes, completion and other records', () => {
    const original: Progress = {
      ...emptyProgress,
      completed: ['fullstack-http'],
      bookmarks: ['article-example'],
      notes: { 'fullstack-integration': '已有实践笔记' },
      practice: [
        { lesson_id: 'fullstack-integration', language: 'go', completed_at: record().updated_at },
      ],
      resume: {
        last_track: 'agent',
        positions: {
          agent: { lesson_id: 'agent-research-agent', visited_at: record().updated_at },
        },
      },
      savedItems: [
        {
          id: 'article-example',
          title: '官方资料',
          summary: '已保存摘要',
          source: '官方',
          url: 'https://example.com/guide',
          track: 'agent',
          tags: ['指南'],
          kind: 'guide',
          published: null,
        },
      ],
      runs: [
        {
          id: 'saved-run',
          prompt: '已有问题',
          answer: '已有回答',
          provider: 'demo',
          track: 'fullstack',
          date: record().updated_at,
          duration_ms: 100,
        },
      ],
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
    expect(saved.practice).toBe(original.practice);
    expect(saved.resume).toBe(original.resume);
    expect(saved.savedItems).toBe(original.savedItems);
    expect(saved.language).toBe(original.language);
    expect(saved.evidence?.[1]).toBe(original.evidence?.[1]);
    expect(saved.evidence?.[2]).toBe(original.evidence?.[2]);
    expect(original.evidence?.[0].command).toBe('uv run pytest');
    incoming.command = 'caller changed its input';
    expect(saved.evidence?.[0].command).toBe('uv run pytest -q');
  });

  it('refuses invalid input and full-capacity additions but permits updating a full list', () => {
    const full: Progress = {
      ...emptyProgress,
      evidence: Array.from({ length: MAX_EVIDENCE_RECORDS }, (_, index) =>
        record({ lesson_id: `fullstack-course-${index}` }),
      ),
    };
    expect(saveEvidence(full, record())).toBe(full);
    expect(saveEvidence(full, record({ command: 'x'.repeat(2001) }))).toBe(full);
    const update = record({ lesson_id: 'fullstack-course-12', success: '更新后的实际结果' });
    const saved = saveEvidence(full, update);
    expect(saved).not.toBe(full);
    expect(saved.evidence).toHaveLength(MAX_EVIDENCE_RECORDS);
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

describe('experiment evidence compatibility and capacity', () => {
  it('uses the shared 48-record boundary for validation and saving without mutating rejected input', () => {
    expect(MAX_EVIDENCE_RECORDS).toBe(48);
    const records = Array.from({ length: 47 }, (_, index) =>
      record({ lesson_id: `fullstack-existing-${index}` }),
    );
    const original: Progress = { ...emptyProgress, evidence: records };
    const last = record({ lesson_id: 'fullstack-routing', language: 'go' });
    const full = saveEvidence(original, last);
    expect(validEvidenceRecords(full.evidence)).toBe(true);
    expect(validateProgress(JSON.parse(JSON.stringify(full)))).toBe(true);
    expect(full.evidence).toHaveLength(48);
    expect(original.evidence).toHaveLength(47);
    expect(saveEvidence(full, record({ ...last, language: 'python' }))).toBe(full);
    expect(validEvidenceRecords([...full.evidence!, record({ ...last, language: 'python' })])).toBe(
      false,
    );
    expect(saveEvidence(full, { ...last, failure: '更新后的失败案例' }).evidence).toHaveLength(48);
  });

  it('keeps old 24-record backups, legacy identifiers and non-ISO valid dates readable', () => {
    const old: Progress = {
      ...emptyProgress,
      evidence: Array.from({ length: 24 }, (_, index) =>
        record({
          lesson_id: `fullstack-legacy--${index}-`,
          updated_at: 'Oct 03 2026 12:34:56 GMT+0000',
        }),
      ),
    };
    const restored = JSON.parse(JSON.stringify(old));
    expect(validateProgress(restored)).toBe(true);
    const saved = saveEvidence(restored, record({ lesson_id: 'fullstack-database' }));
    expect(saved.version).toBe(1);
    expect(saved.evidence).toHaveLength(25);
    expect(saved.evidence?.slice(0, 24)).toEqual(old.evidence);
    expect(evidenceFor(saved, 'fullstack-legacy--0-', 'python')).toEqual(old.evidence?.[0]);
  });

  it('roundtrips the actual 29 lab and 12 capstone course-language combinations', () => {
    const labLessons = [
      'fullstack-routing',
      'fullstack-validation',
      'fullstack-database',
      'fullstack-migrations',
      'fullstack-auth',
      'fullstack-app-security',
      'fullstack-ai-rag',
      'fullstack-ai-stream',
      'fullstack-async',
    ];
    const fullstackCapstones = ['fullstack-product', 'fullstack-integration', 'fullstack-launch'];
    const agentCapstones = [
      'agent-research-agent',
      'agent-research-workflow',
      'agent-research-release',
    ];
    const languages: Language[] = ['typescript', 'go', 'python'];
    for (const lessonId of labLessons) expect(courseLabFor(lessonId)).toBeDefined();
    const records = [
      record({ lesson_id: 'agent-tool-safety', language: 'python' }),
      record({ lesson_id: 'agent-mcp', language: 'python' }),
      ...[...labLessons, ...fullstackCapstones].flatMap((lesson_id) =>
        languages.map((language) =>
          record({ lesson_id, language, success: `${lesson_id}/${language}` }),
        ),
      ),
      ...agentCapstones.map((lesson_id) => record({ lesson_id, language: 'python' })),
    ];
    expect(records).toHaveLength(41);
    const saved = records.reduce(saveEvidence, emptyProgress);
    expect(saved.evidence).toHaveLength(41);
    const restored = JSON.parse(JSON.stringify(saved));
    expect(validateProgress(restored)).toBe(true);
    for (const expected of records)
      expect(evidenceFor(restored, expected.lesson_id, expected.language)).toEqual(expected);
    const changed = saveEvidence(
      restored,
      record({ lesson_id: 'fullstack-database', language: 'go', success: '仅更新 Go' }),
    );
    expect(evidenceFor(changed, 'fullstack-database', 'go')?.success).toBe('仅更新 Go');
    expect(evidenceFor(changed, 'fullstack-database', 'python')?.success).toBe(
      'fullstack-database/python',
    );
    expect(evidenceFor(changed, 'fullstack-database', 'typescript')?.success).toBe(
      'fullstack-database/typescript',
    );
    expect(changed.evidence).toHaveLength(41);
    expect(restored.completed).toEqual([]);
    expect(restored.practice).toBeUndefined();
  });
});

describe('experiment evidence Markdown export', () => {
  it('adds the lab title without changing the two-argument capstone document', () => {
    const evidence = record({ lesson_id: 'fullstack-database' });
    const lesson = { id: evidence.lesson_id, title: '数据持久化' };
    const original = evidenceMarkdown(lesson, evidence);
    expect(original).toMatch(/^# 毕业实践证据\n/);
    expect(original).not.toContain('## 对应实验');
    expect(evidenceMarkdown(lesson, evidence, {})).toBe(original);
    expect(evidenceMarkdown(lesson, evidence, { labTitle: undefined })).toBe(original);
    const exported = evidenceMarkdown(lesson, evidence, { labTitle: '可运行 SQLite 数据实验' });
    expect(exported).toMatch(/^# 实验实践证据\n/);
    expect(exported).toContain('## 对应实验\n\n```text\n可运行 SQLite 数据实验\n```');
    expect(exported).toContain('未经平台核验');
    expect(exported).toContain('不会自动完成课程');
    expect(exported).toContain(evidence.failure);
    expect(exported).toContain(evidence.pending);
  });

  it('keeps malicious lab titles and every editable value inside dynamic plain-text fences', () => {
    const attack =
      '````````\n<h1>伪造实验</h1>\n<img src="https://example.com/pixel">\n# injected heading\n[escape](javascript:alert(1))';
    const evidence = record({
      revision: attack,
      command: attack,
      success: attack,
      failure: attack,
      pending: attack,
    });
    const lesson = { id: 'course\n' + attack, title: attack };
    const markdown = evidenceMarkdown(lesson, evidence, { labTitle: attack });
    expect(markdown).toContain('## 对应实验\n\n`````````text\n' + attack + '\n`````````');
    const html = renderToStaticMarkup(createElement(ReactMarkdown, { children: markdown }));
    expect(html).toContain('&lt;h1&gt;伪造实验&lt;/h1&gt;');
    expect(html).not.toContain('<img');
    expect(html).not.toContain('<h1>伪造实验</h1>');
    expect(html).not.toContain('<h1>injected heading</h1>');
    expect(html).not.toContain('<a ');
    expect((html.match(/<code class="language-text">/g) || []).length).toBe(9);
  });
});

describe('clearing evidence and recovering capacity', () => {
  const blank = { revision: '  ', command: '\t', success: '\n', failure: '', pending: ' \n\t ' };

  it('clears an existing full-capacity record and releases its slot for a new combination', () => {
    const full: Progress = {
      ...emptyProgress,
      notes: { 'fullstack-database': '保留的课程笔记' },
      completed: ['fullstack-database'],
      evidence: Array.from({ length: MAX_EVIDENCE_RECORDS }, (_, index) =>
        record({ lesson_id: `fullstack-existing-${index}` }),
      ),
    };
    const cleared = saveEvidence(full, record({ lesson_id: 'fullstack-existing-12', ...blank }));
    expect(cleared.evidence).toHaveLength(47);
    expect(evidenceFor(cleared, 'fullstack-existing-12', 'python')).toBeUndefined();
    expect(cleared.evidence).toEqual(full.evidence!.filter((_, index) => index !== 12));
    expect(cleared.notes).toBe(full.notes);
    expect(cleared.completed).toBe(full.completed);
    expect(full.evidence).toHaveLength(48);
    const saved = saveEvidence(
      cleared,
      record({ lesson_id: 'fullstack-database', language: 'go' }),
    );
    expect(saved.evidence).toHaveLength(48);
    expect(evidenceFor(saved, 'fullstack-database', 'go')).toEqual(
      record({ lesson_id: 'fullstack-database', language: 'go' }),
    );
  });

  it('does not occupy a slot for a new blank combination or resurrect cleared values', () => {
    expect(saveEvidence(emptyProgress, record(blank))).toBe(emptyProgress);
    const progress = saveEvidence(emptyProgress, record());
    const cleared = saveEvidence(progress, record(blank));
    expect(cleared.evidence).toEqual([]);
    const incoming = record({
      revision: '',
      command: '新命令',
      success: '',
      failure: '',
      pending: '',
    });
    const saved = saveEvidence(cleared, incoming);
    expect(saved.evidence).toEqual([incoming]);
    expect(saved.evidence?.[0].revision).toBe('');
    expect(saved.evidence?.[0].success).toBe('');
    expect(saved.evidence?.[0].failure).toBe('');
    expect(saved.evidence?.[0].pending).toBe('');
  });

  it('reads legacy empty records until explicitly cleared and preserves another language', () => {
    const legacy = record({ ...blank, language: 'python' });
    const other = record({ language: 'go' });
    const progress: Progress = { ...emptyProgress, evidence: [legacy, other] };
    const restored = JSON.parse(JSON.stringify(progress));
    expect(validateProgress(restored)).toBe(true);
    expect(evidenceFor(restored, legacy.lesson_id, 'python')).toEqual(legacy);
    const cleared = saveEvidence(restored, legacy);
    expect(cleared.evidence).toEqual([other]);
    expect(evidenceFor(cleared, legacy.lesson_id, 'python')).toBeUndefined();
    expect(evidenceFor(cleared, legacy.lesson_id, 'go')).toEqual(other);
    expect(restored.evidence).toEqual([legacy, other]);
  });
});
