import type { Progress, RunReason, RunRecord, RunStatus, RunTrace, TrackId } from './types';

export const MAX_RUN_HISTORY = 20;
const boundedText = (value: unknown, maximum: number): value is string =>
  typeof value === 'string' && value.length > 0 && value.length <= maximum;
const usageFields = new Set([
  'prompt_tokens',
  'completion_tokens',
  'total_tokens',
  'input_tokens',
  'output_tokens',
  'cache_creation_input_tokens',
  'cache_read_input_tokens',
]);
const failedReasons = new Set<RunReason>([
  'server_error',
  'transport_error',
  'stream_ended',
  'output_limit',
]);
const cancelledReasons = new Set<RunReason>(['user_stop', 'context_changed']);

export function validHistoryResetId(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    value.length === 36 &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value)
  );
}

function validServerRunId(value: unknown): value is string {
  return boundedText(value, 100) && !/[^A-Za-z0-9._:-]/.test(value);
}

function validRunUsage(value: unknown): value is Record<string, number> {
  return (
    typeof value === 'object' &&
    value !== null &&
    !Array.isArray(value) &&
    Object.keys(value).length > 0 &&
    Object.entries(value).every(
      ([key, count]) =>
        usageFields.has(key) && Number.isInteger(count) && count >= 0 && count <= 300_000_000,
    )
  );
}

export function readRunUsage(value: unknown): Record<string, number> | null {
  return validRunUsage(value) ? value : null;
}

function validTerminalPair(status: unknown, reason: unknown): boolean {
  if (status === undefined || status === 'completed') return reason === undefined;
  if (status === 'failed') return failedReasons.has(reason as RunReason);
  if (status === 'cancelled') return cancelledReasons.has(reason as RunReason);
  return false;
}

/** The old v1 fields retain their original import rules; only new fields add constraints. */
export function validRunRecord(value: unknown): value is RunRecord {
  if (!value || typeof value !== 'object') return false;
  const x = value as RunRecord;
  return (
    Object.keys(x).every((key) =>
      [
        'id',
        'prompt',
        'answer',
        'provider',
        'track',
        'lesson_id',
        'workflow',
        'trace',
        'usage',
        'usage_complete',
        'steps',
        'tool_count',
        'date',
        'duration_ms',
        'status',
        'reason',
        'server_run_id',
      ].includes(key),
    ) &&
    typeof x.id === 'string' &&
    typeof x.prompt === 'string' &&
    x.prompt.length <= 4000 &&
    typeof x.answer === 'string' &&
    x.answer.length <= 50000 &&
    ['agent', 'fullstack'].includes(x.track) &&
    (x.lesson_id === undefined ||
      (typeof x.lesson_id === 'string' &&
        x.lesson_id.length <= 100 &&
        x.lesson_id.startsWith(`${x.track}-`) &&
        /^[a-z0-9-]+$/.test(x.lesson_id))) &&
    (x.workflow === undefined || ['retrieval', 'agent'].includes(x.workflow)) &&
    (x.usage === undefined || x.usage === null || validRunUsage(x.usage)) &&
    (x.usage_complete === undefined || typeof x.usage_complete === 'boolean') &&
    (x.steps === undefined || (Number.isInteger(x.steps) && x.steps >= 1 && x.steps <= 3)) &&
    (x.tool_count === undefined ||
      (Number.isInteger(x.tool_count) && x.tool_count >= 0 && x.tool_count <= 2)) &&
    (x.trace === undefined ||
      (Array.isArray(x.trace) &&
        x.trace.length <= 12 &&
        x.trace.every(
          (step) =>
            step &&
            Object.keys(step).every((key) => ['id', 'title', 'detail', 'status'].includes(key)) &&
            boundedText(step.title, 100) &&
            boundedText(step.detail, 500) &&
            ['running', 'success', 'error'].includes(step.status) &&
            (step.id === undefined || boundedText(step.id, 100)),
        ))) &&
    typeof x.provider === 'string' &&
    typeof x.date === 'string' &&
    Number.isFinite(Date.parse(x.date)) &&
    Number.isFinite(x.duration_ms) &&
    validTerminalPair(x.status, x.reason) &&
    (x.server_run_id === undefined || validServerRunId(x.server_run_id)) &&
    (x.status !== 'cancelled' || x.usage_complete !== true)
  );
}

/** Legacy records were saved only on success; do not invent other terminal metadata. */
export function runRecordStatus(record: Pick<RunRecord, 'status'>): RunStatus {
  return record.status ?? 'completed';
}

export interface TerminalRunInput {
  id: string;
  status: RunStatus;
  reason?: RunReason;
  server_run_id?: unknown;
  prompt: unknown;
  answer: unknown;
  provider: string;
  track: TrackId;
  lesson_id?: unknown;
  workflow?: unknown;
  trace?: unknown;
  usage?: unknown;
  usage_complete?: unknown;
  steps?: unknown;
  tool_count?: unknown;
  date: string;
  duration_ms: number;
}

function boundedSnapshot(value: string, maximum: number): string {
  const result = value.slice(0, maximum);
  // Avoid introducing a lone high surrogate by cutting a valid UTF-16 pair.
  return result.length < value.length && /[\uD800-\uDBFF]$/.test(result)
    ? result.slice(0, -1)
    : result;
}

function traceSnapshot(value: unknown): RunTrace[] {
  if (!Array.isArray(value)) return [];
  const result: RunTrace[] = [];
  for (const item of value) {
    if (!item || typeof item !== 'object') continue;
    if (
      typeof item.title !== 'string' ||
      !item.title.length ||
      typeof item.detail !== 'string' ||
      !item.detail.length ||
      !['running', 'success', 'error'].includes(item.status)
    )
      continue;
    result.push({
      title: boundedSnapshot(item.title, 100),
      detail: boundedSnapshot(item.detail, 500),
      status: item.status,
      ...(boundedText(item.id, 100) ? { id: item.id } : {}),
    });
    if (result.length === 12) break;
  }
  return result;
}

/** Build only the durable whitelist: never spread a request, event, or Error object. */
export function buildTerminalRun(input: TerminalRunInput): RunRecord | null {
  if (
    !input ||
    typeof input !== 'object' ||
    !validHistoryResetId(input.id) ||
    !['completed', 'failed', 'cancelled'].includes(input.status) ||
    !validTerminalPair(input.status, input.reason) ||
    typeof input.prompt !== 'string' ||
    typeof input.date !== 'string' ||
    !Number.isFinite(Date.parse(input.date)) ||
    !Number.isFinite(input.duration_ms)
  )
    return null;
  const usage = readRunUsage(input.usage);
  const incomplete =
    input.status === 'cancelled' ||
    input.reason === 'transport_error' ||
    input.reason === 'stream_ended';
  const lesson = input.lesson_id;
  const record: RunRecord = {
    id: input.id,
    status: input.status,
    ...(input.reason === undefined ? {} : { reason: input.reason }),
    ...(validServerRunId(input.server_run_id) ? { server_run_id: input.server_run_id } : {}),
    prompt: boundedSnapshot(input.prompt, 4000),
    answer: typeof input.answer === 'string' ? boundedSnapshot(input.answer, 50000) : '',
    provider: input.provider,
    track: input.track,
    ...(typeof lesson === 'string' &&
    lesson.length <= 100 &&
    lesson.startsWith(`${input.track}-`) &&
    !/[^a-z0-9-]/.test(lesson)
      ? { lesson_id: lesson }
      : {}),
    ...(input.workflow === 'agent' || input.workflow === 'retrieval'
      ? { workflow: input.workflow }
      : {}),
    trace: traceSnapshot(input.trace),
    usage: usage ? { ...usage } : null,
    ...(incomplete || input.usage_complete !== undefined
      ? { usage_complete: !incomplete && usage !== null && input.usage_complete === true }
      : {}),
    ...(typeof input.steps === 'number' &&
    Number.isInteger(input.steps) &&
    input.steps >= 1 &&
    input.steps <= 3
      ? { steps: input.steps }
      : {}),
    ...(typeof input.tool_count === 'number' &&
    Number.isInteger(input.tool_count) &&
    input.tool_count >= 0 &&
    input.tool_count <= 2
      ? { tool_count: input.tool_count }
      : {}),
    date: input.date,
    duration_ms: Math.max(0, input.duration_ms),
  };
  return validRunRecord(record) ? record : null;
}

/** Call with latestProgress in the functional updater, after comparing its reset epoch. */
export function appendTerminalRun(progress: Progress, value: unknown): Progress {
  if (
    !validRunRecord(value) ||
    !Array.isArray(progress.runs) ||
    progress.runs.length > MAX_RUN_HISTORY ||
    !progress.runs.every(validRunRecord) ||
    progress.runs.some((record) => record.id === value.id)
  )
    return progress;
  const record: RunRecord = {
    ...value,
    ...(value.trace ? { trace: value.trace.map((step) => ({ ...step })) } : {}),
    ...(value.usage ? { usage: { ...value.usage } } : {}),
  };
  return { ...progress, runs: [record, ...progress.runs].slice(0, MAX_RUN_HISTORY) };
}
