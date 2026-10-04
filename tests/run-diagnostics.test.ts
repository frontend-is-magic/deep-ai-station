import { describe, expect, it, vi } from 'vitest';
import {
  diagnosticAnswer,
  diagnosticJudgmentMatches,
  parseDiagnosticReport,
  requestDiagnostics,
  type DiagnosticReport,
  type DiagnosticRequest,
  type DiagnosticStage,
} from '../src/lib/run-diagnostics';

const id = '8d61e890-641c-4024-a2c5-ab008c975b40';
const request: DiagnosticRequest = {
  track: 'agent',
  lesson_id: 'agent-tracing',
  scenario: 'success',
};
function fixture(
  outcome: DiagnosticReport['outcome'] = 'success',
  rejectedRead = false,
): DiagnosticReport {
  const statuses: DiagnosticStage['status'][] =
    outcome === 'success'
      ? ['passed', 'passed', 'passed']
      : outcome === 'no_evidence'
        ? ['passed', 'skipped', 'skipped']
        : outcome === 'timed_out'
          ? ['passed', 'timed_out', 'skipped']
          : rejectedRead
            ? ['passed', 'rejected', 'skipped']
            : ['rejected', 'skipped', 'skipped'];
  const stages = (['search', 'read', 'summary'] as const).map((name, index): DiagnosticStage => {
    const status = statuses[index];
    const skipped = status === 'skipped';
    const dispatch = !skipped && index < 2 && status !== 'timed_out';
    return {
      id: `${id}:${name}`,
      parent_id: `${id}:root`,
      name,
      status,
      start_ms: skipped ? null : index + 0.2,
      end_ms: skipped ? null : index + 0.8,
      duration_ms: skipped ? null : 0.6,
      tool_name: dispatch ? (index === 0 ? 'knowledge_search' : 'lesson_read') : null,
      operation_id: dispatch ? `${id}:${index + 1}` : null,
      tool_dispatch_count: dispatch ? 1 : 0,
      error_code:
        status === 'rejected'
          ? 'invalid_arguments'
          : status === 'timed_out'
            ? 'deadline_exceeded'
            : null,
    };
  });
  return {
    ...request,
    contract_version: 'run-diagnostics-v1',
    run_id: id,
    read_only: true,
    model_calls: 0,
    outcome,
    ended_at_stage:
      outcome === 'success'
        ? 'summary'
        : outcome === 'timed_out' || rejectedRead
          ? 'read'
          : 'search',
    tool_dispatch_count: outcome === 'success' || rejectedRead ? 2 : 1,
    root: {
      id: `${id}:root`,
      parent_id: null,
      start_ms: 0,
      end_ms: 3,
      duration_ms: 3,
      status:
        outcome === 'rejected' ? 'rejected' : outcome === 'timed_out' ? 'timed_out' : 'passed',
    },
    stages,
    evidence: {
      found_ids:
        outcome === 'no_evidence' || (outcome === 'rejected' && !rejectedRead)
          ? []
          : ['agent-tracing'],
      read_lesson:
        outcome === 'success'
          ? {
              id: 'agent-tracing',
              title: 'Trace',
              summary: '实际课程摘要',
              source: 'https://example.org/course',
            }
          : null,
      summary: outcome === 'success' ? 'Trace：实际课程摘要' : null,
    },
    cleanup_completed: true,
    timeout_wait_cancelled: outcome === 'timed_out',
  };
}

describe('diagnostic report contract', () => {
  it.each(['success', 'no_evidence', 'rejected', 'timed_out'] as const)(
    'accepts the real %s shape without deriving outcome from scenario',
    (outcome) => {
      const report = fixture(outcome);
      expect(parseDiagnosticReport(report, request)).toEqual(report);
      expect(diagnosticAnswer(report)).toEqual({
        outcome,
        ended_at_stage: report.ended_at_stage,
        tool_dispatch_count: report.tool_dispatch_count,
      });
      expect(diagnosticJudgmentMatches(report, diagnosticAnswer(report))).toBe(true);
      expect(
        diagnosticJudgmentMatches(report, { ...diagnosticAnswer(report), tool_dispatch_count: 0 }),
      ).toBe(false);
    },
  );
  it('accepts actual read rejection and fullstack ownership', () => {
    expect(parseDiagnosticReport(fixture('rejected', true), request).ended_at_stage).toBe('read');
    const report = fixture();
    report.track = 'fullstack';
    report.lesson_id = 'fullstack-observability';
    report.evidence.found_ids = ['fullstack-observability'];
    report.evidence.read_lesson!.id = 'fullstack-observability';
    expect(
      parseDiagnosticReport(report, {
        ...request,
        track: 'fullstack',
        lesson_id: report.lesson_id,
      }),
    ).toEqual(report);
  });
  it.each([
    ['extra field', (r: DiagnosticReport) => Object.assign(r, { secret: 'marker' })],
    [
      'foreign run',
      (r: DiagnosticReport) => {
        r.stages[0].parent_id = 'other:root';
      },
    ],
    [
      'wrong stage id',
      (r: DiagnosticReport) => {
        r.stages[1].id = r.stages[0].id;
      },
    ],
    [
      'upper UUID',
      (r: DiagnosticReport) => {
        r.run_id = id.toUpperCase();
      },
    ],
    [
      'version',
      (r: DiagnosticReport) => {
        Object.assign(r, { contract_version: 'v2' });
      },
    ],
    [
      'ownership',
      (r: DiagnosticReport) => {
        r.lesson_id = 'agent-memory';
      },
    ],
    [
      'scenario echo',
      (r: DiagnosticReport) => {
        r.scenario = 'timeout';
      },
    ],
    [
      'models',
      (r: DiagnosticReport) => {
        Object.assign(r, { model_calls: 1 });
      },
    ],
    [
      'cleanup',
      (r: DiagnosticReport) => {
        Object.assign(r, { cleanup_completed: false });
      },
    ],
    [
      'duration',
      (r: DiagnosticReport) => {
        r.stages[0].duration_ms = 999;
      },
    ],
    [
      'overlap',
      (r: DiagnosticReport) => {
        r.stages[1].start_ms = 0;
        r.stages[1].duration_ms = 1.8;
      },
    ],
    [
      'root bound',
      (r: DiagnosticReport) => {
        r.root.end_ms = 1;
        r.root.duration_ms = 1;
      },
    ],
    [
      'nonfinite',
      (r: DiagnosticReport) => {
        r.root.end_ms = Infinity;
        r.root.duration_ms = Infinity;
      },
    ],
    [
      'negative',
      (r: DiagnosticReport) => {
        r.stages[0].start_ms = -1;
      },
    ],
    [
      'count',
      (r: DiagnosticReport) => {
        r.tool_dispatch_count = 1;
      },
    ],
    [
      'bool count',
      (r: DiagnosticReport) => {
        Object.assign(r.stages[0], { tool_dispatch_count: true });
      },
    ],
    [
      'operation id',
      (r: DiagnosticReport) => {
        r.stages[1].operation_id = `${id}:1`;
      },
    ],
    [
      'tool name',
      (r: DiagnosticReport) => {
        r.stages[1].tool_name = 'knowledge_search';
      },
    ],
    [
      'error on passed',
      (r: DiagnosticReport) => {
        r.stages[0].error_code = 'invalid_arguments';
      },
    ],
    [
      'foreign evidence',
      (r: DiagnosticReport) => {
        r.evidence.found_ids = ['fullstack-http'];
      },
    ],
    [
      'duplicate evidence',
      (r: DiagnosticReport) => {
        r.evidence.found_ids.push('agent-tracing');
      },
    ],
    [
      'read mismatch',
      (r: DiagnosticReport) => {
        r.evidence.read_lesson!.id = 'agent-memory';
      },
    ],
    [
      'unsafe source',
      (r: DiagnosticReport) => {
        r.evidence.read_lesson!.source = 'https://user:secret@example.org/';
      },
    ],
    [
      'summary drift',
      (r: DiagnosticReport) => {
        r.evidence.summary = 'made up';
      },
    ],
    [
      'invalid unicode',
      (r: DiagnosticReport) => {
        r.evidence.read_lesson!.title = '\ud800';
      },
    ],
    [
      'extra root',
      (r: DiagnosticReport) => {
        Object.assign(r.root, { token: 'marker' });
      },
    ],
  ])('rejects %s', (_, mutate) => {
    const report = fixture();
    mutate(report);
    expect(() => parseDiagnosticReport(report, request)).toThrow('报告无效');
  });
  it('rejects fake skipped execution and timeout dispatch evidence', () => {
    const empty = fixture('no_evidence');
    empty.stages[1].duration_ms = 0;
    expect(() => parseDiagnosticReport(empty, request)).toThrow();
    const timeout = fixture('timed_out');
    timeout.timeout_wait_cancelled = false;
    expect(() => parseDiagnosticReport(timeout, request)).toThrow();
    const rejected = fixture('rejected');
    rejected.evidence.found_ids = ['agent-tracing'];
    expect(() => parseDiagnosticReport(rejected, request)).toThrow();
  });
});

describe('bounded diagnostic response', () => {
  it('makes only the fixed no-store POST and validates returned report', async () => {
    const fetcher = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(fixture()), {
        headers: { 'Content-Type': 'application/json; charset=utf-8' },
      }),
    );
    await expect(
      requestDiagnostics(request, new AbortController().signal, fetcher),
    ).resolves.toEqual(fixture());
    expect(fetcher).toHaveBeenCalledWith(
      '/api/playground/diagnostics',
      expect.objectContaining({
        method: 'POST',
        redirect: 'error',
        cache: 'no-store',
        body: JSON.stringify(request),
      }),
    );
  });
  it.each(['bytes', 'utf8', 'shape', 'status', 'unexpected-2xx', 'type'])(
    'rejects %s without returning a partial report',
    async (kind) => {
      const body =
        kind === 'bytes'
          ? ' '.repeat(32769)
          : kind === 'utf8'
            ? new Uint8Array([0xff])
            : kind === 'shape'
              ? '{}'
              : JSON.stringify(fixture());
      const response = new Response(body, {
        status: kind === 'status' ? 500 : kind === 'unexpected-2xx' ? 201 : 200,
        headers: { 'content-type': kind === 'type' ? 'text/html' : 'application/json' },
      });
      await expect(
        requestDiagnostics(
          request,
          new AbortController().signal,
          vi.fn().mockResolvedValue(response),
        ),
      ).rejects.toThrow();
    },
  );
  it('cancels oversized streaming bodies without waiting for an uncooperative cancel', async () => {
    const cancel = vi.fn(() => new Promise<void>(() => {}));
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array(32769));
      },
      cancel,
    });
    await expect(
      requestDiagnostics(
        request,
        new AbortController().signal,
        vi
          .fn()
          .mockResolvedValue(
            new Response(body, { headers: { 'content-type': 'application/json' } }),
          ),
      ),
    ).rejects.toThrow();
    expect(cancel).toHaveBeenCalledOnce();
  });
  it('abort while reading retires the reader without accepting a report', async () => {
    const cancel = vi.fn();
    const controller = new AbortController();
    const body = new ReadableStream<Uint8Array>({ cancel });
    const pending = requestDiagnostics(
      request,
      controller.signal,
      vi
        .fn()
        .mockResolvedValue(new Response(body, { headers: { 'content-type': 'application/json' } })),
    );
    await Promise.resolve();
    controller.abort();
    await expect(pending).rejects.toThrow();
    expect(cancel).toHaveBeenCalledOnce();
  });
  it('does not read a late fetch response after abort', async () => {
    const controller = new AbortController();
    const cancel = vi.fn();
    let release!: (response: Response) => void;
    const pending = requestDiagnostics(
      request,
      controller.signal,
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            release = resolve;
          }),
      ),
    );
    controller.abort();
    release(
      new Response(new ReadableStream({ cancel }), {
        headers: { 'content-type': 'application/json' },
      }),
    );
    await expect(pending).rejects.toThrow();
    expect(cancel).toHaveBeenCalledOnce();
  });
});
