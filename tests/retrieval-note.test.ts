import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import { describe, expect, it } from 'vitest';
import {
  EVALUATION_REFLECTION_LIMIT,
  prepareEvaluationNote,
  saveEvaluationNote,
  type EvaluationLesson,
} from '../src/lib/retrieval-note';
import type { RetrievalEvaluationResponse, RetrievalMetrics } from '../src/lib/retrievalEvaluation';
import { emptyProgress, validateProgress } from '../src/lib/state';
import type { Progress, TrackId } from '../src/lib/types';

const RUN_ID = 'f9a3e20e-456b-4dd7-a4c2-10191eb982ac';
const SECOND_RUN_ID = '7107c8b4-0985-4c76-9850-6f8a8cdb3280';
const lesson: EvaluationLesson = { id: 'agent-reranking', track: 'agent', title: '检索与重排' };
const reflection = '观察：B 的召回有所改善。\n下一步：用独立验收集复查无证据问题。';

function report(track: TrackId = 'agent'): RetrievalEvaluationResponse {
  const metrics: RetrievalMetrics = {
    positive_cases: 1,
    negative_cases: 1,
    precision_at_k: 0,
    recall_at_k: 0,
    mrr: 0,
    no_result_accuracy: 1,
  };
  return {
    track,
    dataset_version: 'course-retrieval-v1',
    corpus_revision: 'sha256:' + 'a'.repeat(64),
    run_id: RUN_ID,
    model_calls: 0,
    notice: 'teaching report',
    configurations: {
      baseline: { strategy: 'title', top_k: 1 },
      candidate: { strategy: 'weighted', top_k: 5 },
    },
    metrics: {
      baseline: { ...metrics },
      candidate: {
        ...metrics,
        precision_at_k: 0.123456789,
        recall_at_k: 0.987654321,
        mrr: 1 / 3,
        no_result_accuracy: 0,
      },
    },
    cases: [false, true].map((is_negative, index) => ({
      id: `${track}-case-${index}`,
      query: '固定教学问题',
      is_negative,
      relevant: is_negative ? [] : [{ id: `${track}-retrieval`, title: '检索' }],
      baseline: {
        results: [],
        metrics: {
          precision_at_k: is_negative ? null : 0,
          recall_at_k: is_negative ? null : 0,
          reciprocal_rank: is_negative ? null : 0,
          no_result_accuracy: is_negative ? 1 : null,
        },
      },
      candidate: {
        results: [],
        metrics: {
          precision_at_k: is_negative ? null : 0,
          recall_at_k: is_negative ? null : 0,
          reciprocal_rank: is_negative ? null : 0,
          no_result_accuracy: is_negative ? 1 : null,
        },
      },
    })),
  };
}
function originalProgress(): Progress {
  return {
    ...emptyProgress,
    notes: { [lesson.id]: '已有手工笔记  \n', 'fullstack-http': '另一个课时' },
    completed: ['agent-reranking'],
    bookmarks: ['saved-id'],
    practice: [
      { lesson_id: lesson.id, language: 'python', completed_at: '2026-10-04T12:00:00.000Z' },
    ],
    evidence: [
      {
        lesson_id: lesson.id,
        language: 'python',
        revision: '',
        command: '',
        success: '',
        failure: '',
        pending: '',
        updated_at: '2026-10-04T12:00:00.000Z',
      },
    ],
    resume: {
      last_track: 'agent',
      positions: { agent: { lesson_id: lesson.id, visited_at: '2026-10-04T12:00:00.000Z' } },
    },
  };
}
function invalidResult(value: unknown) {
  return value as RetrievalEvaluationResponse;
}
function expectUnchanged(
  progress: Progress,
  result: RetrievalEvaluationResponse,
  target: EvaluationLesson = lesson,
  text = reflection,
) {
  expect(prepareEvaluationNote(progress, result, target, text).status).toBe('invalid');
  expect(saveEvaluationNote(progress, result, target, text)).toBe(progress);
}

describe('retrieval report notes', () => {
  it('includes actual report configuration, versions, exact metrics, sample counts and personal text', () => {
    const result = report();
    const prepared = prepareEvaluationNote(emptyProgress, result, lesson, reflection);
    expect(prepared.status).toBe('ready');
    expect(prepared.nextNote).toBe(prepared.entry);
    for (const text of [
      lesson.title,
      lesson.id,
      'agent',
      RUN_ID,
      result.dataset_version,
      result.corpus_revision,
      '仅标题匹配（title）',
      '加权词法匹配（weighted）',
      'top_k=1',
      'top_k=5',
      'precision@k：0',
      'recall@k：0',
      'MRR：0',
      '无结果准确率：0',
      '正例数：1',
      '负例数：1',
      String(result.metrics.candidate.precision_at_k),
      String(result.metrics.candidate.recall_at_k),
      String(result.metrics.candidate.mrr),
      reflection,
      '小型词法教学集不能代表真实生成质量',
    ])
      expect(prepared.entry).toContain(text);
    expect(prepared.entry.split(RUN_ID)).toHaveLength(2);
    expect(prepared.entry).toContain('模型调用次数：0');
  });

  it('appends immutably without changing other notes, completion, practice, evidence, runs or resume', () => {
    const original = originalProgress();
    const saved = saveEvaluationNote(original, report(), lesson, reflection);
    expect(saved).not.toBe(original);
    expect(saved.notes).not.toBe(original.notes);
    expect(saved.notes[lesson.id]).toBe(
      original.notes[lesson.id] +
        '\n\n' +
        prepareEvaluationNote(original, report(), lesson, reflection).entry,
    );
    expect(saved.notes['fullstack-http']).toBe(original.notes['fullstack-http']);
    for (const key of [
      'completed',
      'bookmarks',
      'savedItems',
      'runs',
      'evidence',
      'practice',
      'resume',
      'language',
      'version',
    ] as const)
      expect(saved[key]).toBe(original[key]);
    expect(original.notes[lesson.id]).toBe('已有手工笔记  \n');
    expect(validateProgress(saved)).toBe(true);
  });

  it('roundtrips an old v1 backup without introducing progress fields or a new schema', () => {
    const old = JSON.parse(JSON.stringify(emptyProgress)) as Progress;
    const saved = saveEvaluationNote(old, report(), lesson, reflection);
    const restored = JSON.parse(JSON.stringify(saved)) as Progress;
    expect(validateProgress(restored)).toBe(true);
    expect(restored.version).toBe(1);
    expect(Object.keys(restored)).toEqual(Object.keys(old));
    expect(restored.completed).toEqual([]);
    expect(restored.runs).toEqual([]);
    expect(restored.practice).toBeUndefined();
    expect(restored.notes[lesson.id]).toBe(saved.notes[lesson.id]);
    expect(saveEvaluationNote(restored, report(), lesson, reflection)).toBe(restored);
  });

  it('deduplicates an exact marker before requiring reflection while leaving UUID mentions alone', () => {
    const saved = saveEvaluationNote(emptyProgress, report(), lesson, reflection);
    for (const text of ['', '  ', 'x'.repeat(2001), '改变观察']) {
      const prepared = prepareEvaluationNote(saved, report(), lesson, text);
      expect(prepared.status).toBe('saved');
      expect(prepared.entry).toBe('');
      expect(prepared.nextNote).toBe(saved.notes[lesson.id]);
      expect(saveEvaluationNote(saved, report(), lesson, text)).toBe(saved);
    }
    const marker = prepareEvaluationNote(emptyProgress, report(), lesson, reflection).entry.split(
      '\n',
    )[0];
    for (const existing of [
      `稍后查看 ${RUN_ID}`,
      `prefix${marker}`,
      `${marker}suffix`,
      `>${marker}`,
      `另一次运行 ${RUN_ID}-suffix`,
    ])
      expect(
        prepareEvaluationNote(
          { ...emptyProgress, notes: { [lesson.id]: existing } },
          report(),
          lesson,
          reflection,
        ).status,
      ).toBe('ready');
    const restoredCRLF = {
      ...saved,
      notes: { [lesson.id]: saved.notes[lesson.id].replaceAll('\n', '\r\n') },
    };
    expect(prepareEvaluationNote(restoredCRLF, report(), lesson, '').status).toBe('saved');
  });

  it('normalizes UUID identity and permits the same report on another valid course', () => {
    const upper = { ...report(), run_id: RUN_ID.toUpperCase() };
    const saved = saveEvaluationNote(emptyProgress, upper, lesson, reflection);
    expect(saveEvaluationNote(saved, report(), lesson, reflection)).toBe(saved);
    const nextLesson = { ...lesson, id: 'agent-datasets' };
    const twice = saveEvaluationNote(saved, report(), nextLesson, reflection);
    expect(twice.notes[lesson.id]).toBe(saved.notes[lesson.id]);
    expect(twice.notes[nextLesson.id]).toContain(RUN_ID);
    const fullstack = { id: 'fullstack-ai-rag', track: 'fullstack', title: '文档问答' } as const;
    expect(
      prepareEvaluationNote(emptyProgress, report('fullstack'), fullstack, reflection).status,
    ).toBe('ready');
  });

  it('rechecks the latest progress when other updates append, fill or save a note', () => {
    const original = originalProgress();
    expect(prepareEvaluationNote(original, report(), lesson, reflection).status).toBe('ready');
    const edited = {
      ...original,
      notes: { ...original.notes, [lesson.id]: original.notes[lesson.id] + '并发追加' },
    };
    const saved = saveEvaluationNote(edited, report(), lesson, reflection);
    expect(saved.notes[lesson.id]).toContain('并发追加\n\n');
    expect(saveEvaluationNote(saved, report(), lesson, reflection)).toBe(saved);
    const second = saveEvaluationNote(
      saved,
      { ...report(), run_id: SECOND_RUN_ID },
      lesson,
      reflection,
    );
    expect(second.notes[lesson.id]).toContain(saved.notes[lesson.id]);
    expect(second.notes[lesson.id].split(RUN_ID)).toHaveLength(2);
    expect(second.notes[lesson.id].split(SECOND_RUN_ID)).toHaveLength(2);
    const full = { ...original, notes: { ...original.notes, [lesson.id]: 'x'.repeat(10000) } };
    expect(saveEvaluationNote(full, report(), lesson, reflection)).toBe(full);
  });

  it('accepts exactly 10000 note characters and refuses one extra without truncation', () => {
    const entry = prepareEvaluationNote(emptyProgress, report(), lesson, reflection).entry;
    const existing = 'x'.repeat(10000 - entry.length - 2);
    const exact = { ...emptyProgress, notes: { [lesson.id]: existing } };
    const prepared = prepareEvaluationNote(exact, report(), lesson, reflection);
    expect(prepared.status).toBe('ready');
    expect(prepared.nextNote).toHaveLength(10000);
    const extra = { ...emptyProgress, notes: { [lesson.id]: existing + 'x' } };
    expect(prepareEvaluationNote(extra, report(), lesson, reflection).status).toBe('too-long');
    expect(saveEvaluationNote(extra, report(), lesson, reflection)).toBe(extra);
  });

  it('enforces 1000 note keys while allowing an append to an existing key at capacity', () => {
    const notes = Object.fromEntries(
      Array.from({ length: 1000 }, (_, index) => [`agent-note-${index}`, 'existing']),
    );
    const full = { ...emptyProgress, notes };
    expect(prepareEvaluationNote(full, report(), lesson, reflection).status).toBe('too-long');
    expect(saveEvaluationNote(full, report(), lesson, reflection)).toBe(full);
    const existing = { ...lesson, id: 'agent-note-0' };
    const updated = saveEvaluationNote(full, report(), existing, reflection);
    expect(updated).not.toBe(full);
    expect(Object.keys(updated.notes)).toHaveLength(1000);
    expect(notes['agent-note-0']).toBe('existing');
  });

  it('requires reflection, keeps exact whitespace, and accepts the reflection limit', () => {
    expect(EVALUATION_REFLECTION_LIMIT).toBe(2000);
    for (const text of ['', ' \n\t']) {
      expect(prepareEvaluationNote(emptyProgress, report(), lesson, text).status).toBe(
        'missing-reflection',
      );
      expect(saveEvaluationNote(emptyProgress, report(), lesson, text)).toBe(emptyProgress);
    }
    const exact = '  ' + '观'.repeat(1996) + '\n\t';
    expect(exact).toHaveLength(2000);
    const prepared = prepareEvaluationNote(emptyProgress, report(), lesson, exact);
    expect(prepared.status).toBe('ready');
    expect(prepared.entry).toContain(exact);
    expect(prepareEvaluationNote(emptyProgress, report(), lesson, exact + 'x').status).toBe(
      'too-long',
    );
  });

  it('fences titles, versions and personal Markdown or HTML without escape or rewriting', () => {
    const attack =
      '``````\n<img src="https://example.com/pixel">\n<script>bad()</script>\n# injected\n~~~';
    const malicious = { ...report(), dataset_version: attack, corpus_revision: attack };
    const prepared = prepareEvaluationNote(
      emptyProgress,
      malicious,
      { ...lesson, title: attack },
      attack,
    );
    expect(prepared.status).toBe('ready');
    expect(prepared.entry).toContain('```````text\n' + attack + '\n```````');
    const html = renderToStaticMarkup(createElement(ReactMarkdown, { children: prepared.entry }));
    expect(html).not.toContain('<img');
    expect(html).not.toContain('<script>');
    expect(html).not.toContain('<h1>injected</h1>');
    expect(html).toContain('&lt;script&gt;bad()&lt;/script&gt;');
  });

  it.each([
    ['track mismatch', { ...lesson, track: 'fullstack' }],
    ['wrong prefix', { ...lesson, id: 'fullstack-reranking' }],
    ['trailing newline', { ...lesson, id: lesson.id + '\n' }],
    ['empty slug', { ...lesson, id: 'agent-' }],
    ['trailing hyphen', { ...lesson, id: 'agent-note-' }],
    ['double hyphen', { ...lesson, id: 'agent-note--test' }],
    ['path traversal', { ...lesson, id: '../agent-note' }],
    ['overlong ID', { ...lesson, id: 'agent-' + 'x'.repeat(95) }],
    ['blank title', { ...lesson, title: ' ' }],
    ['overlong title', { ...lesson, title: 'x'.repeat(301) }],
  ])('rejects invalid lesson: %s', (_label, value) => {
    expectUnchanged(emptyProgress, report(), value as EvaluationLesson);
  });

  it.each([
    ['null', null],
    ['array', []],
    ['track', { ...report(), track: 'other' }],
    ['model calls', { ...report(), model_calls: 1 }],
    ['boolean model calls', { ...report(), model_calls: false }],
    ['UUID', { ...report(), run_id: 'run-1' }],
    ['UUID newline', { ...report(), run_id: RUN_ID + '\n' }],
    ['nil UUID', { ...report(), run_id: '00000000-0000-0000-0000-000000000000' }],
    ['empty version', { ...report(), dataset_version: ' ' }],
    ['long version', { ...report(), dataset_version: 'x'.repeat(201) }],
    ['missing revision', { ...report(), corpus_revision: undefined }],
    ['long revision', { ...report(), corpus_revision: 'x'.repeat(201) }],
    ['configuration', { ...report(), configurations: null }],
    [
      'unknown strategy',
      {
        ...report(),
        configurations: {
          ...report().configurations,
          baseline: { strategy: 'semantic', top_k: 2 },
        },
      },
    ],
    ...[0, 6, 1.5, true, '3'].map((top_k) => [
      'top_k ' + String(top_k),
      {
        ...report(),
        configurations: { ...report().configurations, candidate: { strategy: 'weighted', top_k } },
      },
    ]),
    ['empty cases', { ...report(), cases: [] }],
    ['sparse cases', { ...report(), cases: Array(2) }],
    [
      'count mismatch',
      {
        ...report(),
        metrics: {
          ...report().metrics,
          baseline: { ...report().metrics.baseline, positive_cases: 2 },
        },
      },
    ],
    [
      'fraction count',
      {
        ...report(),
        metrics: {
          ...report().metrics,
          candidate: { ...report().metrics.candidate, negative_cases: 1.5 },
        },
      },
    ],
    [
      'negative count',
      {
        ...report(),
        metrics: {
          ...report().metrics,
          candidate: { ...report().metrics.candidate, negative_cases: -1 },
        },
      },
    ],
  ])('rejects invalid report: %s', (_label, value) => {
    expectUnchanged(emptyProgress, invalidResult(value));
  });

  it.each([NaN, Infinity, -0.01, 1.01, true, '0', null])(
    'rejects an invalid metric: %s',
    (value) => {
      for (const side of ['baseline', 'candidate'] as const)
        for (const metric of [
          'precision_at_k',
          'recall_at_k',
          'mrr',
          'no_result_accuracy',
        ] as const) {
          const result = report();
          (result.metrics[side] as unknown as Record<string, unknown>)[metric] = value;
          expectUnchanged(emptyProgress, result);
        }
    },
  );

  it('rejects malformed existing notes rather than mutating or truncating them', () => {
    for (const notes of [
      null,
      [],
      { [lesson.id]: 12 },
      { other: 'x'.repeat(10001) },
      Object.create({ inherited: 'text' }),
      Object.defineProperty({}, 'hidden', { value: 'text' }),
      { [Symbol('hidden')]: 'text' },
      Object.fromEntries(Array.from({ length: 1001 }, (_, index) => [String(index), 'text'])),
    ]) {
      const malformed = { ...emptyProgress, notes } as unknown as Progress;
      expectUnchanged(malformed, report());
    }
  });
});
