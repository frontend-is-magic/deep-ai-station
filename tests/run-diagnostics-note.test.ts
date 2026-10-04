import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import { describe, expect, it } from 'vitest';
import { emptyProgress, validateProgress } from '../src/lib/state';
import type { DiagnosticJudgment, DiagnosticReport } from '../src/lib/run-diagnostics';
import { prepareDiagnosticNote, saveDiagnosticNote } from '../src/lib/run-diagnostics-note';

const id = '8d61e890-641c-4024-a2c5-ab008c975b40';
const lesson = { id: 'agent-tracing', track: 'agent' as const, title: '运行轨迹' };
const judgment: DiagnosticJudgment = {
  outcome: 'no_evidence',
  ended_at_stage: 'search',
  tool_dispatch_count: 1,
};
const reflection = '没有证据不等于检索失败。下一步核对查询与实际资料。';
const marker = `### 运行诊断记录 ${id}`;
function report(): DiagnosticReport {
  return {
    contract_version: 'run-diagnostics-v1',
    run_id: id,
    track: 'agent',
    lesson_id: lesson.id,
    scenario: 'no_evidence',
    read_only: true,
    model_calls: 0,
    outcome: 'no_evidence',
    ended_at_stage: 'search',
    tool_dispatch_count: 1,
    root: {
      id: `${id}:root`,
      parent_id: null,
      status: 'passed',
      start_ms: 0,
      end_ms: 1,
      duration_ms: 1,
    },
    stages: [
      {
        id: `${id}:search`,
        parent_id: `${id}:root`,
        name: 'search',
        status: 'passed',
        start_ms: 0.1,
        end_ms: 0.6,
        duration_ms: 0.5,
        tool_name: 'knowledge_search',
        operation_id: `${id}:1`,
        tool_dispatch_count: 1,
        error_code: null,
      },
      ...(['read', 'summary'] as const).map((name) => ({
        id: `${id}:${name}`,
        parent_id: `${id}:root`,
        name,
        status: 'skipped' as const,
        start_ms: null,
        end_ms: null,
        duration_ms: null,
        tool_name: null,
        operation_id: null,
        tool_dispatch_count: 0 as const,
        error_code: null,
      })),
    ],
    evidence: { found_ids: [], read_lesson: null, summary: null },
    cleanup_completed: true,
    timeout_wait_cancelled: false,
  };
}
const prepare = (
  progress = emptyProgress,
  observed = reflection,
  decision = judgment,
  resetId = progress.history_reset_id,
) => prepareDiagnosticNote(progress, report(), lesson, decision, observed, resetId);

describe('diagnostic note append', () => {
  it('records actual stage evidence, learner judgment and reflection separately', () => {
    const prepared = prepare();
    expect(prepared.status).toBe('ready');
    for (const value of [
      marker,
      lesson.title,
      'run-diagnostics-v1',
      'no_evidence',
      'knowledge_search',
      `${id}:1`,
      'null',
      '机械核对：一致',
      reflection,
      '模型调用次数：0',
    ])
      expect(prepared.entry).toContain(value);
    expect(prepared.nextNote).toBe(prepared.entry);
  });
  it('allows a wrong judgment but preserves the actual answer and disagreement', () => {
    const prepared = prepare(emptyProgress, reflection, {
      outcome: 'success',
      ended_at_stage: 'summary',
      tool_dispatch_count: 2,
    });
    expect(prepared.status).toBe('ready');
    expect(prepared.entry).toContain('机械核对：不一致');
    expect(prepared.entry).toContain('"outcome":"success"');
    expect(prepared.entry).toContain('"outcome":"no_evidence"');
  });
  it('changes only the current lesson note and merges a latest same-epoch snapshot', () => {
    const previous = {
      ...emptyProgress,
      notes: { [lesson.id]: '旧笔记\n', 'agent-memory': '其他笔记' },
      completed: ['agent-memory'],
      bookmarks: ['saved-resource'],
      practice: [
        {
          lesson_id: lesson.id,
          language: 'python' as const,
          completed_at: '2026-10-04T00:00:00.000Z',
        },
      ],
    };
    const saved = saveDiagnosticNote(previous, report(), lesson, judgment, reflection, undefined);
    expect(saved.notes[lesson.id]).toBe('旧笔记\n\n\n' + prepare(previous).entry);
    expect({ ...saved, notes: previous.notes }).toEqual(previous);
    expect(saved.notes['agent-memory']).toBe('其他笔记');
    expect(validateProgress(saved)).toBeTruthy();
    expect(previous.notes[lesson.id]).toBe('旧笔记\n');
  });
  it('deduplicates only a complete marker line, including CRLF', () => {
    const once = saveDiagnosticNote(
      emptyProgress,
      report(),
      lesson,
      judgment,
      reflection,
      undefined,
    );
    expect(saveDiagnosticNote(once, report(), lesson, judgment, '', undefined)).toBe(once);
    expect(
      prepare({ ...emptyProgress, notes: { [lesson.id]: marker + '\r\nexisting' } }, '').status,
    ).toBe('saved');
    expect(prepare({ ...emptyProgress, notes: { [lesson.id]: 'quoted ' + marker } }).status).toBe(
      'ready',
    );
    expect(prepare({ ...emptyProgress, notes: { [lesson.id]: marker + '0' } }).status).toBe(
      'ready',
    );
  });
  it.each(['', ' \n\t'])('requires personal observation %j', (value) => {
    expect(prepare(emptyProgress, value).status).toBe('missing-reflection');
  });
  it('enforces exact observation boundary without truncation', () => {
    expect(prepare(emptyProgress, '观'.repeat(2000)).status).toBe('ready');
    expect(prepare(emptyProgress, '观'.repeat(2001)).status).toBe('too-long');
  });
  it('checks current note capacity and permits the exact 10,000 boundary', () => {
    const entry = prepare().entry;
    const atLimit = {
      ...emptyProgress,
      notes: { [lesson.id]: 'n'.repeat(10000 - entry.length - 2) },
    };
    expect(prepare(atLimit).status).toBe('ready');
    expect(prepare(atLimit).nextNote.length).toBe(10000);
    const full = { ...atLimit, notes: { [lesson.id]: atLimit.notes[lesson.id] + 'n' } };
    const prepared = prepare(full);
    expect(prepared.status).toBe('too-long');
    expect(prepared.entry).toBe(entry);
    expect(saveDiagnosticNote(full, report(), lesson, judgment, reflection, undefined)).toBe(full);
  });
  it('full 1,000-note dictionary rejects new keys but allows existing and released slots', () => {
    const notes = Object.fromEntries(
      Array.from({ length: 1000 }, (_, i) => [`agent-note-${i}`, '旧笔记']),
    );
    expect(prepare({ ...emptyProgress, notes }).status).toBe('too-long');
    delete notes['agent-note-0'];
    expect(prepare({ ...emptyProgress, notes }).status).toBe('ready');
    notes[lesson.id] = '本课原文';
    expect(prepare({ ...emptyProgress, notes }).status).toBe('ready');
  });
  it.each([
    [undefined, '5d2c3386-6251-4945-8a50-5fc906e2c090'],
    ['5d2c3386-6251-4945-8a50-5fc906e2c090', undefined],
    ['5d2c3386-6251-4945-8a50-5fc906e2c090', '8d61e890-641c-4024-a2c5-ab008c975b40'],
  ])(
    'refuses old epoch %s after current %s even with a matching marker',
    (oldEpoch, currentEpoch) => {
      const progress = {
        ...emptyProgress,
        history_reset_id: currentEpoch,
        notes: { [lesson.id]: marker },
      };
      expect(
        prepareDiagnosticNote(progress, report(), lesson, judgment, reflection, oldEpoch).status,
      ).toBe('invalid');
      expect(saveDiagnosticNote(progress, report(), lesson, judgment, reflection, oldEpoch)).toBe(
        progress,
      );
    },
  );
  it('preserves malicious-looking text as inert fenced text', () => {
    const raw =
      '```\n<script>window.__unsafe = true</script>\n[link](javascript:alert(1))\n````\n### 假标题';
    const entry = prepare(emptyProgress, raw).entry;
    expect(entry).toContain('`````text\n' + raw + '\n`````');
    const html = renderToStaticMarkup(createElement(ReactMarkdown, { children: entry }));
    expect(html).toContain('&lt;script&gt;');
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('href="javascript:');
  });
  it('revalidates ownership, report and structured judgment before saving', () => {
    expect(
      prepareDiagnosticNote(
        emptyProgress,
        report(),
        { ...lesson, id: 'agent-memory' },
        judgment,
        reflection,
        undefined,
      ).status,
    ).toBe('invalid');
    const damaged = report();
    damaged.stages[0].operation_id = 'other:1';
    expect(
      prepareDiagnosticNote(emptyProgress, damaged, lesson, judgment, reflection, undefined).status,
    ).toBe('invalid');
    expect(
      prepare(emptyProgress, reflection, {
        ...judgment,
        tool_dispatch_count: true,
      } as unknown as DiagnosticJudgment).status,
    ).toBe('invalid');
    expect(prepare({ ...emptyProgress, notes: { [lesson.id]: 'n'.repeat(10001) } }).status).toBe(
      'invalid',
    );
  });
});
