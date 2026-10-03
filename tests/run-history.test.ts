import { describe, expect, it } from 'vitest';
import {
  appendTerminalRun,
  buildTerminalRun,
  MAX_RUN_HISTORY,
  readRunUsage,
  runRecordStatus,
  validHistoryResetId,
  validRunRecord,
  type TerminalRunInput,
} from '../src/lib/run-history';
import {
  emptyProgress,
  validateProgress,
  validRunRecord as importedRecordGuard,
} from '../src/lib/state';
import type { Progress, RunRecord } from '../src/lib/types';

const id = (index: number) => `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`;
const baseInput: TerminalRunInput = {
  id: id(1),
  status: 'completed',
  prompt: '检查工具权限',
  answer: '观察到的片段',
  provider: 'deepseek',
  track: 'agent',
  lesson_id: 'agent-tool-safety',
  workflow: 'agent',
  date: '2026-10-04T01:02:03.000Z',
  duration_ms: 120,
};
const legacy: RunRecord = {
  id: 'old-service-id',
  prompt: '旧输入',
  answer: '旧结果',
  provider: 'anthropic',
  track: 'agent',
  date: '2024-01-02',
  duration_ms: -0.5,
  usage: { input_tokens: 0, output_tokens: 7 },
};
function built(patch: Partial<TerminalRunInput> = {}): RunRecord {
  const result = buildTerminalRun({ ...baseInput, ...patch });
  expect(result).not.toBeNull();
  return result!;
}

describe('compatible v1 run import', () => {
  it('shares one record guard and keeps old id, date, provider and usage semantics', () => {
    expect(importedRecordGuard).toBe(validRunRecord);
    expect(validRunRecord(legacy)).toBe(true);
    expect(validRunRecord({ ...legacy, id: '', provider: '' })).toBe(true);
    expect(runRecordStatus(legacy)).toBe('completed');
    const exported = JSON.parse(JSON.stringify({ ...emptyProgress, runs: [legacy] }));
    expect(validateProgress(exported)).toBe(true);
    expect(exported.runs[0]).toEqual(legacy);
    expect(exported.runs[0]).not.toHaveProperty('status');
    expect(exported.runs[0]).not.toHaveProperty('usage_complete');
    expect(exported.runs[0]).not.toHaveProperty('server_run_id');
  });

  it.each([
    ['completed', undefined],
    ['failed', 'server_error'],
    ['failed', 'transport_error'],
    ['failed', 'stream_ended'],
    ['failed', 'output_limit'],
    ['cancelled', 'user_stop'],
    ['cancelled', 'context_changed'],
  ])('accepts the fixed terminal pair %s/%s', (status, reason) => {
    const value = { ...legacy, status, ...(reason === undefined ? {} : { reason }) };
    expect(validRunRecord(value)).toBe(true);
    expect(validateProgress({ ...emptyProgress, runs: [legacy, value] })).toBe(true);
    expect(runRecordStatus(value as RunRecord)).toBe(status);
  });

  it.each([
    { reason: 'server_error' },
    { status: 'completed', reason: 'server_error' },
    { status: 'failed' },
    { status: 'failed', reason: 'user_stop' },
    { status: 'cancelled', reason: 'stream_ended' },
    { status: 'cancelled' },
    { status: 'failed', reason: 'private-error-text' },
    { status: 'running' },
    { status: null },
    { status: 'cancelled', reason: 'user_stop', usage_complete: true },
  ])('rejects invalid new terminal metadata without changing legacy rules: %j', (patch) => {
    expect(validRunRecord({ ...legacy, ...patch })).toBe(false);
    expect(validateProgress({ ...emptyProgress, runs: [{ ...legacy, ...patch }] })).toBe(false);
  });

  it.each(['usage-run-0', 'AbC_01:model.1', 'a'.repeat(100)])(
    'accepts a received bounded ASCII service id %s',
    (server_run_id) => {
      expect(validRunRecord({ ...legacy, server_run_id })).toBe(true);
    },
  );
  it.each([
    '',
    'a'.repeat(101),
    'id\n',
    ' id',
    'id\t',
    'id\0',
    '服务端',
    'https://host/id',
    null,
    1,
  ])('rejects unsafe or malformed service id %j', (server_run_id) => {
    expect(validRunRecord({ ...legacy, server_run_id })).toBe(false);
    expect(built({ server_run_id })).not.toHaveProperty('server_run_id');
  });

  it('rejects unknown record/trace fields, preserving the existing secret boundary', () => {
    for (const field of ['token', 'system', 'headers', 'error', 'message', 'raw_event']) {
      expect(validRunRecord({ ...legacy, [field]: 'private-secret-sentinel' })).toBe(false);
      expect(validateProgress({ ...emptyProgress, runs: [{ ...legacy, [field]: 'secret' }] })).toBe(
        false,
      );
    }
    expect(
      validRunRecord({
        ...legacy,
        trace: [{ title: '工具', detail: '完成', status: 'success', raw: 'secret' }],
      }),
    ).toBe(false);
  });

  it('round-trips mixed completed, failed, cancelled and old records without changing status', () => {
    const values = [
      legacy,
      built(),
      built({ id: id(2), status: 'failed', reason: 'server_error' }),
      built({ id: id(3), status: 'cancelled', reason: 'user_stop' }),
    ];
    const original = { ...emptyProgress, runs: values, history_reset_id: id(9) };
    const restored = JSON.parse(JSON.stringify(original));
    expect(validateProgress(restored)).toBe(true);
    expect(restored).toEqual(original);
    expect(restored.runs.map(runRecordStatus)).toEqual([
      'completed',
      'completed',
      'failed',
      'cancelled',
    ]);
  });
});

describe('history reset epoch', () => {
  it('keeps an absent old epoch and accepts a canonical v4 epoch without changing backup version', () => {
    expect(validateProgress(emptyProgress)).toBe(true);
    expect(emptyProgress).not.toHaveProperty('history_reset_id');
    const progress = { ...emptyProgress, history_reset_id: id(8), runs: [legacy] };
    expect(validHistoryResetId(id(8))).toBe(true);
    expect(validateProgress(progress)).toBe(true);
    expect(JSON.parse(JSON.stringify(progress))).toEqual(progress);
    expect(progress.version).toBe(1);
  });

  it.each([
    null,
    12,
    '',
    'epoch-1',
    id(1) + '\n',
    'ABCDEF00-0000-4000-8000-000000000001',
    '00000000-0000-1000-8000-000000000001',
    '00000000-0000-4000-7000-000000000001',
  ])('rejects a malformed reset epoch %j', (history_reset_id) => {
    expect(validHistoryResetId(history_reset_id)).toBe(false);
    expect(validateProgress({ ...emptyProgress, history_reset_id })).toBe(false);
  });
});

describe('safe terminal snapshots', () => {
  it('copies only the durable whitelist and detaches observed trace/usage objects', () => {
    const trace = [
      {
        id: 'server:model:1',
        title: '请求模型',
        detail: '供应商响应未完成',
        status: 'running',
        token: 'private-secret-sentinel',
      },
    ];
    const usage = { total_tokens: 0 };
    const source = {
      ...baseInput,
      status: 'failed' as const,
      reason: 'server_error' as const,
      trace,
      usage,
      usage_complete: true,
      token: 'private-secret-sentinel',
      system: 'private-secret-sentinel',
      error: new Error('private-secret-sentinel'),
      message: 'private-secret-sentinel',
      raw_event: { authorization: 'private-secret-sentinel' },
    };
    const record = buildTerminalRun(source)!;
    expect(validRunRecord(record)).toBe(true);
    expect(JSON.stringify(record)).not.toContain('private-secret-sentinel');
    expect(record.trace).toEqual([
      { id: 'server:model:1', title: '请求模型', detail: '供应商响应未完成', status: 'running' },
    ]);
    trace[0].detail = 'later value';
    usage.total_tokens = 30;
    expect(record.trace![0].detail).toBe('供应商响应未完成');
    expect(record.usage).toEqual({ total_tokens: 0 });
    expect(record.usage_complete).toBe(true);
  });

  it('retains explicit user input but never saves an Error used as the answer', () => {
    const record = built({
      prompt: '用户自己填写的文本',
      answer: new Error('private-secret-sentinel'),
    });
    expect(record.prompt).toBe('用户自己填写的文本');
    expect(record.answer).toBe('');
    expect(JSON.stringify(record)).not.toContain('private-secret-sentinel');
    expect(record).not.toHaveProperty('server_run_id');
    expect(record).not.toHaveProperty('steps');
    expect(record).not.toHaveProperty('tool_count');
  });

  it('bounds text without splitting a valid surrogate pair at the storage boundary', () => {
    const record = built({
      prompt: 'a'.repeat(3999) + '😀',
      answer: 'b'.repeat(49999) + '😀',
      trace: [
        {
          title: 'c'.repeat(99) + '😀',
          detail: 'd'.repeat(499) + '😀',
          status: 'running',
          id: 'e'.repeat(101),
        },
      ],
    });
    expect(record.prompt).toHaveLength(3999);
    expect(record.answer).toHaveLength(49999);
    expect(record.trace![0].title).toHaveLength(99);
    expect(record.trace![0].detail).toHaveLength(499);
    expect(record.trace![0]).not.toHaveProperty('id');
    expect(validRunRecord(record)).toBe(true);
    const full = built({ prompt: 'a'.repeat(4001), answer: 'b'.repeat(50001) });
    expect(full.prompt).toHaveLength(4000);
    expect(full.answer).toHaveLength(50000);
  });

  it('stores at most twelve valid trace observations without inventing a success status', () => {
    const trace = [
      null,
      4,
      new Error('private-secret-sentinel'),
      { title: '', detail: 'invalid', status: 'error' },
      ...Array.from({ length: 14 }, (_, index) => ({
        title: `步骤 ${index}`,
        detail: '已观察',
        status: index === 0 ? 'running' : 'success',
        id: index === 0 ? '' : String(index),
        error: 'private-secret-sentinel',
      })),
    ];
    const record = built({ status: 'cancelled', reason: 'context_changed', trace });
    expect(record.trace).toHaveLength(12);
    expect(record.trace![0].status).toBe('running');
    expect(record.trace![0]).not.toHaveProperty('id');
    expect(record.trace![11].title).toBe('步骤 11');
    expect(JSON.stringify(record)).not.toContain('private-secret-sentinel');
  });

  it.each([
    [undefined, null],
    [null, null],
    [{}, null],
    [{ total_tokens: true }, null],
    [{ total_tokens: -1 }, null],
    [{ total_tokens: 300_000_001 }, null],
    [{ secret: 3 }, null],
    [{ total_tokens: 0 }, { total_tokens: 0 }],
    [{ prompt_tokens: 4 }, { prompt_tokens: 4 }],
  ])(
    'keeps zero distinct from unknown and does not fill missing usage fields: %j',
    (usage, expected) => {
      const record = built({ usage });
      expect(record.usage).toEqual(expected);
      expect(record).not.toHaveProperty('usage_complete');
      expect(readRunUsage(usage)).toEqual(expected);
    },
  );

  it.each([
    ['completed', undefined],
    ['failed', 'server_error'],
    ['failed', 'output_limit'],
  ] as const)('preserves unknown versus explicit incomplete usage for %s/%s', (status, reason) => {
    for (const usage of [null, { total_tokens: 0 }]) {
      const unknown = built({ status, reason, usage });
      expect(unknown).not.toHaveProperty('usage_complete');
      expect(JSON.parse(JSON.stringify(unknown))).not.toHaveProperty('usage_complete');
      expect(built({ status, reason, usage, usage_complete: false }).usage_complete).toBe(false);
    }
  });

  it.each(['server_error', 'output_limit'] as const)(
    'allows known complete usage even for %s',
    (reason) => {
      const record = built({
        status: 'failed',
        reason,
        usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
        usage_complete: true,
      });
      expect(record.usage_complete).toBe(true);
      expect(record.status).toBe('failed');
    },
  );

  it.each([
    ['failed', 'transport_error'],
    ['failed', 'stream_ended'],
    ['cancelled', 'user_stop'],
    ['cancelled', 'context_changed'],
  ] as const)('marks %s/%s incomplete while retaining known counts', (status, reason) => {
    for (const usage_complete of [undefined, false, true]) {
      const record = built({ status, reason, usage: { total_tokens: 8 }, usage_complete });
      expect(record.usage).toEqual({ total_tokens: 8 });
      expect(record.usage_complete).toBe(false);
    }
  });

  it('does not claim complete usage when the supplied count object is unknown or invalid', () => {
    for (const usage of [null, undefined, {}, { total_tokens: true }]) {
      expect(built({ usage, usage_complete: true }).usage_complete).toBe(false);
    }
  });

  it('omits invalid optional attribution/counters instead of copying raw payload fields', () => {
    const record = built({
      lesson_id: 'fullstack-http',
      workflow: 'private-secret-sentinel',
      steps: true,
      tool_count: -1,
    });
    for (const field of ['lesson_id', 'workflow', 'steps', 'tool_count'])
      expect(record).not.toHaveProperty(field);
    expect(built({ lesson_id: 'agent-mcp\n' })).not.toHaveProperty('lesson_id');
    expect(built({ steps: 1, tool_count: 0 })).toMatchObject({ steps: 1, tool_count: 0 });
  });

  it.each([
    { id: 'old-run-id' },
    { status: undefined },
    { status: null },
    { status: 'running' },
    { status: 'completed', reason: 'user_stop' },
    { status: 'failed' },
    { prompt: null },
    { provider: null },
    { track: 'other' },
    { date: null },
    { date: 'bad-date' },
    { duration_ms: null },
    { duration_ms: Infinity },
  ])('rejects invalid new attempt metadata: %j', (patch) => {
    expect(buildTerminalRun({ ...baseInput, ...patch } as TerminalRunInput)).toBeNull();
    expect(validRunRecord(legacy)).toBe(true);
  });
});

describe('terminal append uses the current progress without replaying an old snapshot', () => {
  it('changes only runs and preserves the caller-supplied newest data/reset epoch', () => {
    const current: Progress = {
      ...emptyProgress,
      completed: ['agent-mcp'],
      notes: { 'agent-mcp': 'other tab note' },
      quizReview: [{ lesson_id: 'agent-tool-safety', added_at: '2026-10-04T00:00:00.000Z' }],
      practice: [
        { lesson_id: 'agent-mcp', language: 'python', completed_at: '2026-10-04T00:00:00.000Z' },
      ],
      history_reset_id: id(10),
      runs: [legacy],
    };
    const result = appendTerminalRun(current, built({ status: 'cancelled', reason: 'user_stop' }));
    expect(result).not.toBe(current);
    expect(result.runs).toHaveLength(2);
    expect(result.notes).toBe(current.notes);
    expect(result.completed).toBe(current.completed);
    expect(result.practice).toBe(current.practice);
    expect(result.quizReview).toBe(current.quizReview);
    expect(result.history_reset_id).toBe(id(10));
    expect(current.runs).toEqual([legacy]);
    expect(validateProgress(result)).toBe(true);
  });

  it('does not duplicate, replace, reorder or upgrade the same terminal attempt', () => {
    const first = appendTerminalRun(
      emptyProgress,
      built({ status: 'failed', reason: 'server_error' }),
    );
    expect(appendTerminalRun(first, built())).toBe(first);
    expect(appendTerminalRun(first, built({ status: 'cancelled', reason: 'user_stop' }))).toBe(
      first,
    );
    expect(first.runs[0].status).toBe('failed');
  });

  it('shares one twenty-entry budget across all terminal states and drops only the oldest', () => {
    const old = Array.from({ length: MAX_RUN_HISTORY }, (_, index) =>
      built({ id: id(index + 1), status: 'failed', reason: 'server_error' }),
    );
    const progress = { ...emptyProgress, runs: old };
    const appended = appendTerminalRun(
      progress,
      built({ id: id(21), status: 'cancelled', reason: 'user_stop' }),
    );
    expect(appended.runs).toHaveLength(20);
    expect(appended.runs.map((item) => item.id)).toEqual([
      id(21),
      ...old.slice(0, 19).map((item) => item.id),
    ]);
    expect(progress.runs).toBe(old);
    expect(appendTerminalRun(appended, built({ id: id(21) }))).toBe(appended);
  });

  it('returns the original progress for invalid records and never serializes raw failures', () => {
    const progress = { ...emptyProgress, runs: [legacy] };
    for (const record of [
      null,
      {},
      new Error('private-secret-sentinel'),
      { ...legacy, token: 'private-secret-sentinel' },
      { ...legacy, status: 'failed', reason: 'unknown' },
    ]) {
      expect(appendTerminalRun(progress, record)).toBe(progress);
    }
    expect(JSON.stringify(progress)).not.toContain('private-secret-sentinel');
  });

  it('detaches the appended record so later local mutations cannot alter saved evidence', () => {
    const record = built({
      trace: [{ title: '请求', detail: '完成前', status: 'running' }],
      usage: { total_tokens: 0 },
    });
    const progress = appendTerminalRun(emptyProgress, record);
    record.trace![0].detail = 'later';
    record.usage!.total_tokens = 7;
    expect(progress.runs[0].trace![0].detail).toBe('完成前');
    expect(progress.runs[0].usage).toEqual({ total_tokens: 0 });
  });
});
