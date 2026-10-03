import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import { describe, expect, it } from 'vitest';
import { emptyProgress, validateProgress } from '../src/lib/state';
import type { Progress } from '../src/lib/types';
import type { ToolContractLesson, ToolContractReport } from '../src/lib/tool-contract';
import {
  prepareToolContractNote,
  saveToolContractNote,
  TOOL_CONTRACT_REFLECTION_LIMIT,
} from '../src/lib/tool-contract-note';

const runId = 'c7cdf7f8-4b8f-4e24-9c33-131b0c519b74';
const lesson: ToolContractLesson = {
  id: 'agent-tool-contract',
  track: 'agent',
  title: '工具契约与 Function Calling',
};
const reflection = '额外字段被拒绝。\n下一步：验证类型与长度边界。';
function report(): ToolContractReport {
  return {
    contract_version: 'tool-contract-v1',
    run_id: runId,
    track: 'agent',
    lesson_id: lesson.id,
    tool_name: 'knowledge_search',
    arguments_json: ' {"query":" MCP "} ',
    model_calls: 0,
    outcome: 'success',
    observation: { read_only: true, operation_id: `${runId}:1`, items: [] },
  };
}
function progress(): Progress {
  return {
    ...emptyProgress,
    notes: { [lesson.id]: '原始笔记  \n', 'fullstack-http': '其他课的笔记' },
    completed: ['agent-structured-output'],
    bookmarks: ['saved-item'],
    practice: [
      { lesson_id: lesson.id, language: 'python', completed_at: '2026-10-04T12:00:00.000Z' },
    ],
    evidence: [],
    resume: {
      last_track: 'agent',
      positions: { agent: { lesson_id: lesson.id, visited_at: '2026-10-04T12:00:00.000Z' } },
    },
  };
}
const marker = `### 工具契约实验记录 ${runId}`;

describe('tool contract lesson notes', () => {
  it('records the exact request, version, result, operation ID and reflection', () => {
    const value = report();
    const prepared = prepareToolContractNote(emptyProgress, value, lesson, reflection);
    expect(prepared.status).toBe('ready');
    for (const expected of [
      marker,
      lesson.title,
      lesson.id,
      'tool-contract-v1',
      value.arguments_json,
      `${runId}:1`,
      'success（成功）',
      '模型调用次数：0',
      reflection,
    ])
      expect(prepared.entry).toContain(expected);
    expect(prepared.nextNote).toBe(prepared.entry);
  });
  it('records rejected experiments with their real error instead of treating them as successful execution', () => {
    const value = report();
    value.outcome = 'rejected';
    value.arguments_json = '{"query":false}';
    value.observation = { read_only: true, operation_id: `${runId}:1`, error: 'invalid_arguments' };
    const saved = saveToolContractNote(emptyProgress, value, lesson, reflection);
    expect(saved.notes[lesson.id]).toContain('rejected（校验拒绝）');
    expect(saved.notes[lesson.id]).toContain('invalid_arguments');
    expect(saved.notes[lesson.id]).not.toContain('success（成功）');
  });
  it.each([
    ['\ufeff', 'success'],
    ['\u0085', 'rejected'],
    ['知识检索 🔎', 'success'],
  ] as const)('saves the actual server outcome for Unicode query %j', (query, outcome) => {
    const value = report();
    value.arguments_json = JSON.stringify({ query });
    value.outcome = outcome;
    if (outcome === 'rejected')
      value.observation = {
        read_only: true,
        operation_id: `${runId}:1`,
        error: 'invalid_arguments',
      };
    const prepared = prepareToolContractNote(emptyProgress, value, lesson, reflection);
    expect(prepared.status).toBe('ready');
    const saved = saveToolContractNote(emptyProgress, value, lesson, reflection);
    expect(saved.notes[lesson.id]).toContain(value.arguments_json);
    expect(saved.notes[lesson.id]).toContain(outcome);
  });
  it('only appends to the owner note and preserves v1 records and object references', () => {
    const current = progress();
    const before = structuredClone(current);
    const saved = saveToolContractNote(current, report(), lesson, reflection);
    expect(current).toEqual(before);
    expect(saved.notes[lesson.id].startsWith(`${current.notes[lesson.id]}\n\n`)).toBe(true);
    expect(saved.notes['fullstack-http']).toBe(current.notes['fullstack-http']);
    for (const key of ['completed', 'bookmarks', 'practice', 'evidence', 'resume', 'runs'] as const)
      expect(saved[key]).toBe(current[key]);
    expect(saved.version).toBe(1);
    expect(validateProgress(JSON.parse(JSON.stringify(saved)))).toBe(true);
  });
  it('appends to the latest text supplied by a functional state updater', () => {
    const original = progress();
    expect(prepareToolContractNote(original, report(), lesson, reflection).status).toBe('ready');
    const latest = { ...original, notes: { ...original.notes, [lesson.id]: '另一标签页的新笔记' } };
    const saved = saveToolContractNote(latest, report(), lesson, reflection);
    expect(saved.notes[lesson.id].startsWith('另一标签页的新笔记\n\n')).toBe(true);
    expect(saved.notes[lesson.id]).not.toContain('原始笔记');
  });
  it('deduplicates after an imported saved note, even if the current reflection is empty', () => {
    const saved = saveToolContractNote(progress(), report(), lesson, reflection);
    const imported: Progress = JSON.parse(JSON.stringify(saved));
    expect(prepareToolContractNote(imported, report(), lesson, '').status).toBe('saved');
    expect(saveToolContractNote(imported, report(), lesson, reflection)).toBe(imported);
  });
  it.each(['\n', '\r\n', '\r'])('recognizes an exact marker line with %j newlines', (newline) => {
    const current = {
      ...emptyProgress,
      notes: { [lesson.id]: `之前${newline}${marker}${newline}之后` },
    };
    expect(prepareToolContractNote(current, report(), lesson, reflection).status).toBe('saved');
  });
  it.each([
    `这里只提到 ${runId}`,
    `${marker} extra`,
    `prefix ${marker}`,
    `\`\`\`text\n${runId}\n\`\`\``,
  ])('does not mistake ordinary text for a saved marker: %s', (note) => {
    const current = { ...emptyProgress, notes: { [lesson.id]: note } };
    expect(prepareToolContractNote(current, report(), lesson, reflection).status).toBe('ready');
  });
  it('canonicalizes UUID casing for deduplication without changing the operation ID', () => {
    const value = report();
    value.run_id = runId.toUpperCase();
    value.observation.operation_id = `${value.run_id}:1`;
    const saved = saveToolContractNote(emptyProgress, value, lesson, reflection);
    expect(saved.notes[lesson.id]).toContain(marker);
    expect(saveToolContractNote(saved, report(), lesson, reflection)).toBe(saved);
  });
  it('does not save a report to another eligible course after navigation', () => {
    const target: ToolContractLesson = { ...lesson, id: 'agent-structured-output' };
    const current = progress();
    expect(prepareToolContractNote(current, report(), target, reflection).status).toBe('invalid');
    expect(saveToolContractNote(current, report(), target, reflection)).toBe(current);
  });
  it('revalidates a mutated report before updating notes', () => {
    const value = report();
    const current = progress();
    expect(prepareToolContractNote(current, value, lesson, reflection).status).toBe('ready');
    value.observation.operation_id = 'other:1';
    expect(prepareToolContractNote(current, value, lesson, reflection).status).toBe('invalid');
    expect(saveToolContractNote(current, value, lesson, reflection)).toBe(current);
  });
  it.each(['', ' \t\n'])('requires a personal observation for %j', (text) => {
    expect(prepareToolContractNote(emptyProgress, report(), lesson, text).status).toBe(
      'missing-reflection',
    );
    expect(saveToolContractNote(emptyProgress, report(), lesson, text)).toBe(emptyProgress);
  });
  it('accepts the exact reflection limit and rejects overflow without truncation', () => {
    expect(
      prepareToolContractNote(
        emptyProgress,
        report(),
        lesson,
        'x'.repeat(TOOL_CONTRACT_REFLECTION_LIMIT),
      ).status,
    ).toBe('ready');
    expect(
      prepareToolContractNote(
        emptyProgress,
        report(),
        lesson,
        'x'.repeat(TOOL_CONTRACT_REFLECTION_LIMIT + 1),
      ).status,
    ).toBe('too-long');
  });
  it('accepts exactly 10000 note characters and rechecks capacity after concurrent changes', () => {
    const size = prepareToolContractNote(emptyProgress, report(), lesson, reflection).entry.length;
    const exact = { ...emptyProgress, notes: { [lesson.id]: 'x'.repeat(10000 - size - 2) } };
    expect(prepareToolContractNote(exact, report(), lesson, reflection).nextNote.length).toBe(
      10000,
    );
    expect(prepareToolContractNote(exact, report(), lesson, reflection).status).toBe('ready');
    const latest = { ...exact, notes: { [lesson.id]: `${exact.notes[lesson.id]}x` } };
    expect(prepareToolContractNote(latest, report(), lesson, reflection).status).toBe('too-long');
    expect(saveToolContractNote(latest, report(), lesson, reflection)).toBe(latest);
  });
  it('respects the 1000-note cap but allows updating an existing note at that cap', () => {
    const notes = Object.fromEntries(
      Array.from({ length: 1000 }, (_, index) => [`note-${index}`, 'x']),
    );
    const full = { ...emptyProgress, notes };
    expect(prepareToolContractNote(full, report(), lesson, reflection).status).toBe('too-long');
    expect(saveToolContractNote(full, report(), lesson, reflection)).toBe(full);
    delete notes['note-999'];
    notes[lesson.id] = 'existing';
    expect(prepareToolContractNote(full, report(), lesson, reflection).status).toBe('ready');
  });
  it.each([
    null,
    [],
    new Date(),
    { a: 123 },
    { a: 'x'.repeat(10001) },
    Object.assign({}, { [Symbol('hidden')]: 'x' }),
  ])('rejects malformed latest notes %j', (notes) => {
    const current = { ...emptyProgress, notes } as unknown as Progress;
    expect(prepareToolContractNote(current, report(), lesson, reflection).status).toBe('invalid');
    expect(saveToolContractNote(current, report(), lesson, reflection)).toBe(current);
  });
  it('fences user text dynamically so it cannot inject Markdown sections or HTML', () => {
    const payload =
      '````\n# injected\n<img src=x onerror=alert(1)>\n[click](javascript:alert(1))\n`````';
    const value = report();
    value.arguments_json = payload;
    value.outcome = 'rejected';
    value.observation = {
      read_only: true,
      operation_id: `${runId}:1`,
      error: 'invalid_arguments_json',
    };
    const target = { ...lesson, title: `课程\n${payload}` };
    const prepared = prepareToolContractNote(emptyProgress, value, target, payload);
    expect(prepared.status).toBe('ready');
    expect(prepared.entry).toContain(`\`\`\`\`\`\`text\n${payload}\n\`\`\`\`\`\``);
    const html = renderToStaticMarkup(createElement(ReactMarkdown, { children: prepared.entry }));
    expect(html).not.toContain('<h1>');
    expect(html).not.toContain('<img');
    expect(html).not.toContain('<a');
    expect(html.match(/<h3>/g)).toHaveLength(4);
  });
});
