export interface Source {
  id: string;
  title: string;
  url: string | null;
  kind?: 'course-excerpt' | 'conflict-fixture';
}

interface BaseAnswer {
  run_id: string;
  mode: 'demo' | 'deepseek' | 'no-evidence';
  answer: string;
  sources: Source[];
  usage: Record<string, number> | null;
}

interface KnowledgeAnswer extends BaseAnswer {
  workflow?: undefined;
}

export interface AgentAnswer extends BaseAnswer {
  workflow: 'research-agent';
  outcome: 'complete' | 'insufficient_evidence' | 'conflicting_evidence';
  model_calls: number;
  tool_calls: number;
  usage_complete: boolean;
  trace: { id: string; title: string; detail: string; status: 'success' | 'error' }[];
  citations: { document_id: string; quote: string }[];
}

export type Answer = KnowledgeAnswer | AgentAnswer;

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function text(value: unknown, maximum: number): value is string {
  return typeof value === 'string' && Boolean(value.trim()) && Array.from(value).length <= maximum;
}

function count(value: unknown, maximum: number): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 && value <= maximum;
}

function keys(value: Record<string, unknown>, allowed: string[]): boolean {
  return Object.keys(value).every((key) => allowed.includes(key));
}

export function safeHttpsUrl(value: unknown): string | null {
  if (typeof value !== 'string' || value.length > 2048 || value.trim() !== value) return null;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}

export function parseHealth(value: unknown): 'knowledge' | 'agent' {
  if (
    !object(value) ||
    value.status !== 'ok' ||
    typeof value.framework !== 'string' ||
    !['fastapi', 'hono', 'gin'].includes(value.framework) ||
    !count(value.documents, 20) ||
    (value.workflow !== undefined && value.workflow !== 'research-agent') ||
    (value.workflow === 'research-agent' && value.framework !== 'fastapi')
  )
    throw new Error('无法确认 API 能力');
  return value.workflow === 'research-agent' ? 'agent' : 'knowledge';
}

export function parseAnswer(value: unknown): Answer {
  const invalid = () => new Error('响应格式不符合 API 契约，本次结果未保存。');
  const baseKeys = ['run_id', 'mode', 'answer', 'sources', 'usage'];
  const agentKeys = [
    'workflow',
    'outcome',
    'model_calls',
    'tool_calls',
    'usage_complete',
    'trace',
    'citations',
  ];
  if (
    !object(value) ||
    !text(value.run_id, 128) ||
    typeof value.mode !== 'string' ||
    !['demo', 'deepseek', 'no-evidence'].includes(value.mode) ||
    !text(value.answer, 20000) ||
    !Array.isArray(value.sources) ||
    value.sources.length > 20 ||
    !keys(value, value.workflow === 'research-agent' ? [...baseKeys, ...agentKeys] : baseKeys)
  )
    throw invalid();

  const sourceIds = new Set<string>();
  const sources = value.sources.map((source): Source => {
    if (
      !object(source) ||
      !keys(source, ['id', 'title', 'url', 'kind']) ||
      !text(source.id, 128) ||
      !text(source.title, 500) ||
      sourceIds.has(source.id) ||
      (source.url !== null && !safeHttpsUrl(source.url)) ||
      (value.workflow !== 'research-agent' && source.url === null) ||
      (value.workflow === 'research-agent' && source.kind === undefined) ||
      (source.kind !== undefined &&
        source.kind !== 'course-excerpt' &&
        source.kind !== 'conflict-fixture') ||
      (source.kind === 'conflict-fixture' && source.url !== null)
    )
      throw invalid();
    sourceIds.add(source.id);
    return {
      id: source.id,
      title: source.title,
      url: source.url === null ? null : safeHttpsUrl(source.url),
      ...(source.kind ? { kind: source.kind } : {}),
    };
  });

  let usage: Record<string, number> | null = null;
  if (value.usage !== null) {
    if (
      !object(value.usage) ||
      !keys(value.usage, ['prompt_tokens', 'completion_tokens', 'total_tokens']) ||
      Object.values(value.usage).some((value) => !count(value, 300000000))
    )
      throw invalid();
    if (Object.keys(value.usage).length) usage = { ...value.usage } as Record<string, number>;
  }
  const base: KnowledgeAnswer = {
    run_id: value.run_id,
    mode: value.mode as BaseAnswer['mode'],
    answer: value.answer,
    sources,
    usage,
  };
  if (value.workflow !== 'research-agent') return base;
  if (
    typeof value.outcome !== 'string' ||
    !['complete', 'insufficient_evidence', 'conflicting_evidence'].includes(value.outcome) ||
    !count(value.model_calls, 3) ||
    !count(value.tool_calls, 2) ||
    typeof value.usage_complete !== 'boolean' ||
    !Array.isArray(value.trace) ||
    value.trace.length > 12 ||
    !Array.isArray(value.citations) ||
    value.citations.length > 20 ||
    (value.mode === 'demo' && value.model_calls !== 0) ||
    (value.mode === 'deepseek' && value.model_calls === 0) ||
    (value.mode === 'no-evidence' &&
      (value.model_calls !== 0 || value.outcome !== 'insufficient_evidence')) ||
    (value.model_calls === 0 && usage !== null) ||
    (value.model_calls > 0 &&
      value.usage_complete &&
      ['prompt_tokens', 'completion_tokens', 'total_tokens'].some(
        (field) => usage?.[field] === undefined,
      ))
  )
    throw invalid();

  const traceIds = new Set<string>();
  const trace = value.trace.map((step): AgentAnswer['trace'][number] => {
    if (
      !object(step) ||
      !keys(step, ['id', 'title', 'detail', 'status']) ||
      !text(step.id, 128) ||
      traceIds.has(step.id) ||
      !text(step.title, 500) ||
      !text(step.detail, 20000) ||
      (step.status !== 'success' && step.status !== 'error')
    )
      throw invalid();
    traceIds.add(step.id);
    return { id: step.id, title: step.title, detail: step.detail, status: step.status };
  });
  const citationIds = new Set<string>();
  const citations = value.citations.map((citation): AgentAnswer['citations'][number] => {
    if (
      !object(citation) ||
      !keys(citation, ['document_id', 'quote']) ||
      !text(citation.document_id, 128) ||
      !sourceIds.has(citation.document_id) ||
      !text(citation.quote, 20000)
    )
      throw invalid();
    const key = JSON.stringify([citation.document_id, citation.quote]);
    if (citationIds.has(key)) throw invalid();
    citationIds.add(key);
    return { document_id: citation.document_id, quote: citation.quote };
  });
  const citedSources = new Set(citations.map((citation) => citation.document_id));
  if (
    (value.outcome === 'insufficient_evidence' && (sources.length > 0 || citations.length > 0)) ||
    (value.outcome !== 'insufficient_evidence' &&
      (sources.length === 0 || sources.some((source) => !citedSources.has(source.id)))) ||
    (value.outcome === 'conflicting_evidence' &&
      sources.filter((source) => source.kind === 'conflict-fixture').length < 2)
  )
    throw invalid();
  return {
    ...base,
    workflow: 'research-agent',
    outcome: value.outcome as AgentAnswer['outcome'],
    model_calls: value.model_calls,
    tool_calls: value.tool_calls,
    usage_complete: value.usage_complete,
    trace,
    citations,
  };
}

export function responseError(value: unknown): string {
  return object(value) && text(value.error, 300) ? value.error : '请求失败，请稍后重试';
}
