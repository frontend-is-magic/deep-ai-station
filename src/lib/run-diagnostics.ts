import type { Lesson, TrackId } from './types';

export const DIAGNOSTICS_VERSION = 'run-diagnostics-v1';
export const DIAGNOSTICS_RESPONSE_BYTES = 32768;
export const diagnosticScenarios = [
  'success',
  'no_evidence',
  'invalid_arguments',
  'timeout',
] as const;
export const diagnosticOutcomes = ['success', 'no_evidence', 'rejected', 'timed_out'] as const;
export const diagnosticStages = ['search', 'read', 'summary'] as const;
export type DiagnosticScenario = (typeof diagnosticScenarios)[number];
export type DiagnosticOutcome = (typeof diagnosticOutcomes)[number];
export type DiagnosticStageName = (typeof diagnosticStages)[number];
export type DiagnosticLesson = Pick<Lesson, 'id' | 'track' | 'title'>;
export interface DiagnosticRequest {
  track: TrackId;
  lesson_id: string;
  scenario: DiagnosticScenario;
}
export interface DiagnosticJudgment {
  outcome: DiagnosticOutcome;
  ended_at_stage: DiagnosticStageName;
  tool_dispatch_count: 0 | 1 | 2;
}
export interface DiagnosticStage {
  id: string;
  parent_id: string;
  name: DiagnosticStageName;
  status: 'passed' | 'rejected' | 'timed_out' | 'skipped';
  start_ms: number | null;
  end_ms: number | null;
  duration_ms: number | null;
  tool_name: 'knowledge_search' | 'lesson_read' | null;
  operation_id: string | null;
  tool_dispatch_count: 0 | 1;
  error_code: 'invalid_arguments' | 'deadline_exceeded' | null;
}
export interface DiagnosticReport extends DiagnosticRequest {
  contract_version: typeof DIAGNOSTICS_VERSION;
  run_id: string;
  read_only: true;
  model_calls: 0;
  outcome: DiagnosticOutcome;
  ended_at_stage: DiagnosticStageName;
  tool_dispatch_count: 1 | 2;
  root: {
    id: string;
    parent_id: null;
    status: 'passed' | 'rejected' | 'timed_out';
    start_ms: 0;
    end_ms: number;
    duration_ms: number;
  };
  stages: DiagnosticStage[];
  evidence: {
    found_ids: string[];
    read_lesson: { id: string; title: string; summary: string; source: string } | null;
    summary: string | null;
  };
  cleanup_completed: true;
  timeout_wait_cancelled: boolean;
}
export const outcomeLabels: Record<DiagnosticOutcome, string> = {
  success: '成功整理',
  no_evidence: '没有证据',
  rejected: '工具拒绝',
  timed_out: '本地等待超时',
};
export const stageLabels: Record<DiagnosticStageName, string> = {
  search: '检索 search',
  read: '读取 read',
  summary: '整理 summary',
};
export const scenarioLabels: Record<DiagnosticScenario, string> = {
  success: '正常检索与整理',
  no_evidence: '查询没有匹配',
  invalid_arguments: '工具参数不合法',
  timeout: '读取前本地等待超时',
};
function object(value: unknown): value is Record<string, unknown> {
  return (
    !!value &&
    typeof value === 'object' &&
    !Array.isArray(value) &&
    [Object.prototype, null].includes(Object.getPrototypeOf(value))
  );
}
function keys(value: Record<string, unknown>, expected: string[]) {
  return (
    Reflect.ownKeys(value).length === expected.length &&
    expected.every((key) => Object.hasOwn(value, key))
  );
}
function text(value: unknown, max: number): value is string {
  return (
    typeof value === 'string' &&
    value.length > 0 &&
    Array.from(value).length <= max &&
    !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)
  );
}
function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0;
}
function duration(start: number, end: number, elapsed: unknown) {
  return finite(elapsed) && start <= end && Math.abs(elapsed - (end - start)) <= 1e-7;
}
function requireValue(condition: unknown): asserts condition {
  if (!condition) throw new Error('运行诊断报告无效，请重新运行。');
}
export function diagnosticLessonEligible(value: unknown): value is DiagnosticLesson {
  return (
    object(value) &&
    text(value.title, 300) &&
    ((value.track === 'agent' && value.id === 'agent-tracing') ||
      (value.track === 'fullstack' && value.id === 'fullstack-observability'))
  );
}
function validRequest(value: DiagnosticRequest) {
  return (
    diagnosticScenarios.includes(value.scenario) &&
    diagnosticLessonEligible({ id: value.lesson_id, track: value.track, title: '课程' })
  );
}
function courseId(value: unknown, track: TrackId): value is string {
  return (
    text(value, 100) && new RegExp(`^${track}-[a-z0-9]+(?:-[a-z0-9]+)*$`).exec(value)?.[0] === value
  );
}
function source(value: unknown): value is string {
  if (!text(value, 2048)) return false;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password;
  } catch {
    return false;
  }
}
export function parseDiagnosticReport(
  value: unknown,
  request: DiagnosticRequest,
): DiagnosticReport {
  requireValue(
    validRequest(request) &&
      object(value) &&
      keys(value, [
        'contract_version',
        'run_id',
        'track',
        'lesson_id',
        'scenario',
        'read_only',
        'model_calls',
        'outcome',
        'ended_at_stage',
        'tool_dispatch_count',
        'root',
        'stages',
        'evidence',
        'cleanup_completed',
        'timeout_wait_cancelled',
      ]),
  );
  requireValue(
    value.contract_version === DIAGNOSTICS_VERSION &&
      value.read_only === true &&
      value.model_calls === 0 &&
      value.cleanup_completed === true &&
      typeof value.run_id === 'string' &&
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.exec(
        value.run_id,
      )?.[0] === value.run_id &&
      value.track === request.track &&
      value.lesson_id === request.lesson_id &&
      value.scenario === request.scenario &&
      diagnosticOutcomes.some((item) => item === value.outcome) &&
      diagnosticStages.some((item) => item === value.ended_at_stage) &&
      (value.tool_dispatch_count === 1 || value.tool_dispatch_count === 2) &&
      typeof value.timeout_wait_cancelled === 'boolean',
  );
  const root = value.root;
  requireValue(
    object(root) &&
      keys(root, ['id', 'parent_id', 'status', 'start_ms', 'end_ms', 'duration_ms']) &&
      root.id === `${value.run_id}:root` &&
      root.parent_id === null &&
      root.start_ms === 0 &&
      finite(root.end_ms) &&
      duration(0, root.end_ms, root.duration_ms),
  );
  requireValue(Array.isArray(value.stages) && value.stages.length === 3);
  let previousEnd = 0;
  let dispatches = 0;
  for (const [index, stage] of value.stages.entries()) {
    const name = diagnosticStages[index];
    requireValue(
      object(stage) &&
        keys(stage, [
          'id',
          'parent_id',
          'name',
          'status',
          'start_ms',
          'end_ms',
          'duration_ms',
          'tool_name',
          'operation_id',
          'tool_dispatch_count',
          'error_code',
        ]) &&
        stage.name === name &&
        stage.id === `${value.run_id}:${name}` &&
        stage.parent_id === root.id &&
        ['passed', 'rejected', 'timed_out', 'skipped'].some((status) => status === stage.status) &&
        (stage.tool_dispatch_count === 0 || stage.tool_dispatch_count === 1),
    );
    if (stage.status === 'skipped') {
      requireValue(
        stage.start_ms === null &&
          stage.end_ms === null &&
          stage.duration_ms === null &&
          stage.tool_dispatch_count === 0 &&
          stage.tool_name === null &&
          stage.operation_id === null &&
          stage.error_code === null,
      );
      continue;
    }
    requireValue(
      finite(stage.start_ms) &&
        finite(stage.end_ms) &&
        stage.start_ms >= previousEnd &&
        stage.end_ms <= root.end_ms &&
        duration(stage.start_ms, stage.end_ms, stage.duration_ms),
    );
    previousEnd = stage.end_ms;
    if (stage.tool_dispatch_count === 1) {
      dispatches += 1;
      requireValue(
        index < 2 &&
          stage.operation_id === `${value.run_id}:${index + 1}` &&
          stage.tool_name === (index === 0 ? 'knowledge_search' : 'lesson_read'),
      );
    } else requireValue(stage.tool_name === null && stage.operation_id === null);
    requireValue(
      stage.error_code ===
        (stage.status === 'rejected'
          ? 'invalid_arguments'
          : stage.status === 'timed_out'
            ? 'deadline_exceeded'
            : null),
    );
    requireValue(stage.status !== 'rejected' || stage.tool_dispatch_count === 1);
    requireValue(
      stage.status !== 'timed_out' || (name === 'read' && stage.tool_dispatch_count === 0),
    );
    requireValue(
      stage.status !== 'passed' || stage.tool_dispatch_count === (name === 'summary' ? 0 : 1),
    );
  }
  requireValue(dispatches === value.tool_dispatch_count);
  const evidence = value.evidence;
  requireValue(
    object(evidence) &&
      keys(evidence, ['found_ids', 'read_lesson', 'summary']) &&
      Array.isArray(evidence.found_ids) &&
      evidence.found_ids.length <= 3 &&
      evidence.found_ids.every((id) => courseId(id, request.track)) &&
      new Set(evidence.found_ids).size === evidence.found_ids.length,
  );
  const statuses = value.stages.map((stage) => stage.status).join(',');
  if (value.outcome === 'success') {
    const read = evidence.read_lesson;
    requireValue(
      statuses === 'passed,passed,passed' &&
        root.status === 'passed' &&
        value.ended_at_stage === 'summary' &&
        value.tool_dispatch_count === 2 &&
        evidence.found_ids.length > 0 &&
        !value.timeout_wait_cancelled &&
        object(read) &&
        keys(read, ['id', 'title', 'summary', 'source']) &&
        read.id === evidence.found_ids[0] &&
        text(read.title, 200) &&
        text(read.summary, 800) &&
        source(read.source) &&
        text(evidence.summary, 1001) &&
        evidence.summary === `${read.title}：${read.summary}`,
    );
  } else {
    requireValue(evidence.read_lesson === null && evidence.summary === null);
    if (value.outcome === 'no_evidence')
      requireValue(
        statuses === 'passed,skipped,skipped' &&
          root.status === 'passed' &&
          value.ended_at_stage === 'search' &&
          value.tool_dispatch_count === 1 &&
          evidence.found_ids.length === 0 &&
          !value.timeout_wait_cancelled,
      );
    if (value.outcome === 'timed_out')
      requireValue(
        statuses === 'passed,timed_out,skipped' &&
          root.status === 'timed_out' &&
          value.ended_at_stage === 'read' &&
          value.tool_dispatch_count === 1 &&
          evidence.found_ids.length > 0 &&
          value.timeout_wait_cancelled,
      );
    if (value.outcome === 'rejected')
      requireValue(
        root.status === 'rejected' &&
          !value.timeout_wait_cancelled &&
          ((statuses === 'rejected,skipped,skipped' &&
            value.ended_at_stage === 'search' &&
            value.tool_dispatch_count === 1 &&
            evidence.found_ids.length === 0) ||
            (statuses === 'passed,rejected,skipped' &&
              value.ended_at_stage === 'read' &&
              value.tool_dispatch_count === 2 &&
              evidence.found_ids.length > 0)),
      );
  }
  return value as unknown as DiagnosticReport;
}
export function validDiagnosticJudgment(value: unknown): value is DiagnosticJudgment {
  return (
    object(value) &&
    keys(value, ['outcome', 'ended_at_stage', 'tool_dispatch_count']) &&
    diagnosticOutcomes.some((item) => item === value.outcome) &&
    diagnosticStages.some((item) => item === value.ended_at_stage) &&
    [0, 1, 2].includes(value.tool_dispatch_count as number)
  );
}
export function diagnosticAnswer(report: DiagnosticReport): DiagnosticJudgment {
  return {
    outcome: report.outcome,
    ended_at_stage: report.ended_at_stage,
    tool_dispatch_count: report.tool_dispatch_count,
  };
}
export function diagnosticJudgmentMatches(report: DiagnosticReport, judgment: DiagnosticJudgment) {
  const answer = diagnosticAnswer(report);
  return (
    validDiagnosticJudgment(judgment) &&
    Object.entries(answer).every(
      ([key, value]) => judgment[key as keyof DiagnosticJudgment] === value,
    )
  );
}

// Read the actual stream with a hard byte bound, including when Content-Length is absent.
export async function requestDiagnostics(
  request: DiagnosticRequest,
  signal: AbortSignal,
  fetcher: typeof fetch = fetch,
): Promise<DiagnosticReport> {
  requireValue(validRequest(request));
  const response = await fetcher('/api/playground/diagnostics', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(request),
    signal,
    cache: 'no-store',
    redirect: 'error',
  });
  if (
    signal.aborted ||
    response.redirected ||
    response.status !== 200 ||
    response.headers.get('content-type')?.split(';')[0].trim().toLowerCase() !==
      'application/json' ||
    !response.body
  ) {
    void response.body?.cancel().catch(() => undefined);
    throw new Error('运行诊断演练暂不可用，请重新运行。');
  }
  const reader = response.body.getReader();
  const cancel = () => {
    void reader.cancel().catch(() => undefined);
  };
  signal.addEventListener('abort', cancel, { once: true });
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const next = await reader.read();
      if (signal.aborted) throw new Error('诊断等待已停止。');
      if (next.done) break;
      size += next.value.byteLength;
      requireValue(size <= DIAGNOSTICS_RESPONSE_BYTES);
      chunks.push(next.value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.length;
    }
    return parseDiagnosticReport(
      JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)),
      request,
    );
  } finally {
    signal.removeEventListener('abort', cancel);
    cancel();
    reader.releaseLock();
  }
}
