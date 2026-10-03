const OPERATION_KEYS = [
  'operation_id',
  'owner_id',
  'requester_id',
  'tool',
  'document_id',
  'expected_version',
  'before_content',
  'content',
  'intent_hash',
  'status',
  'prepared_at',
  'approved_at',
  'expires_at',
  'receipt',
];
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const DOCUMENT_ID = /^[a-z0-9-]{1,80}$/;
const HASH = /^[0-9a-f]{64}$/;
const CAPABILITIES = ['read', 'prepare', 'approve', 'execute'];
const encoder = new TextEncoder();
const EXACT_ID = '00000000-0000-4000-8000-000000000000';
const ERRORS = {
  ambiguous_credentials: '身份凭据不明确，请重新选择教学身份。',
  authentication_required: '当前教学会话不可用，请选择有效身份。',
  session_store_unavailable: '会话存储暂不可用，请稍后查询原操作。',
  repository_unavailable: '实验数据库暂不可用，请稍后查询原操作。',
  invalid_input: '请求不符合固定契约，请检查输入。',
  request_too_large: '请求正文超过 4096 字节，请缩短摘要。',
  unsupported_media_type: '请求媒体类型不符合契约。',
  document_not_found: '当前 owner 内找不到这份文档。',
  operation_not_found: '当前 owner 内找不到该操作。',
  route_not_found: '实验路由不存在。',
  method_not_allowed: '实验路由不支持该请求方法。',
  forbidden: '当前身份没有此操作所需的能力。',
  requester_mismatch: '只有原申请者可以执行这个操作。',
  operation_conflict: '这个操作 ID 已绑定其他意图，请先查询原操作。',
  stale_document: '目标文档版本已变化，请刷新文档并准备新意图。',
  already_applied: '该操作已经执行，请查询原回执。',
  intent_mismatch: '意图 hash 不匹配，请查询服务端保存的原意图。',
  operation_expired: '批准已过期，请准备新的操作。',
  operation_revoked: '操作已撤销，请准备新的操作。',
  approval_required: '操作尚未获得批准，不能发布。',
  result_unconfirmed: '结果尚未确认。请保留原操作 ID 并查询原操作；不要用新 ID 重试。',
};
export const UNCONFIRMED_MESSAGE = ERRORS.result_unconfirmed;

export class LabError extends Error {
  constructor(code = 'unexpected_response', status = 0) {
    super(ERRORS[code] || '无法确认服务端响应，请查询原操作。');
    this.code = Object.hasOwn(ERRORS, code) ? code : 'unexpected_response';
    this.status = status;
  }
}
function exact(value, keys) {
  return (
    !!value &&
    typeof value === 'object' &&
    !Array.isArray(value) &&
    Object.keys(value).length === keys.length &&
    keys.every((key) => Object.hasOwn(value, key))
  );
}
function text(value, min, max) {
  if (typeof value !== 'string' || value.includes('\0')) return false;
  const chars = Array.from(value);
  return (
    chars.length >= min &&
    chars.length <= max &&
    !chars.some((char) => char.length === 1 && /[\uD800-\uDFFF]/.test(char))
  );
}
function matches(value, pattern) {
  return typeof value === 'string' && pattern.exec(value)?.[0] === value;
}
function integer(value, max = Number.MAX_SAFE_INTEGER) {
  return Number.isSafeInteger(value) && value >= 1 && value <= max;
}
function finite(value) {
  return typeof value === 'number' && Number.isFinite(value);
}
function invalid() {
  throw new LabError();
}
export function isOperationId(value) {
  return matches(value, UUID);
}
export function parsePrincipal(value, fixture) {
  if (
    !exact(value, ['owner_id', 'requester_id', 'capabilities']) ||
    value.owner_id !== fixture.owner_id ||
    value.requester_id !== fixture.requester_id ||
    !Array.isArray(value.capabilities) ||
    value.capabilities.length > CAPABILITIES.length ||
    new Set(value.capabilities).size !== value.capabilities.length ||
    !value.capabilities.every((item) => CAPABILITIES.includes(item))
  )
    invalid();
  return value;
}
export function parseDocument(value) {
  if (
    !exact(value, ['id', 'title', 'content', 'version']) ||
    !matches(value.id, DOCUMENT_ID) ||
    !text(value.title, 1, 1000) ||
    !text(value.content, 1, 2000) ||
    !integer(value.version, 2147483647)
  )
    invalid();
  return value;
}
export function parseDocuments(value) {
  if (!exact(value, ['items']) || !Array.isArray(value.items) || value.items.length > 100)
    invalid();
  value.items.forEach(parseDocument);
  if (
    new Set(value.items.map((item) => item.id)).size !== value.items.length ||
    value.items.some((item, index) => index > 0 && item.id <= value.items[index - 1].id)
  )
    invalid();
  return value.items;
}
export function parseOperation(value, ownerId, operationId) {
  if (
    !exact(value, OPERATION_KEYS) ||
    !isOperationId(value.operation_id) ||
    value.operation_id !== operationId ||
    value.owner_id !== ownerId ||
    !text(value.requester_id, 1, 128) ||
    value.tool !== 'publish_revision' ||
    !matches(value.document_id, DOCUMENT_ID) ||
    !integer(value.expected_version, 2147483647) ||
    !text(value.before_content, 1, 2000) ||
    !text(value.content, 1, 2000) ||
    !matches(value.intent_hash, HASH) ||
    !finite(value.prepared_at) ||
    !['prepared', 'approved', 'applied', 'revoked', 'expired'].includes(value.status)
  )
    invalid();
  const notApproved = value.approved_at === null && value.expires_at === null;
  const approved =
    finite(value.approved_at) && finite(value.expires_at) && value.expires_at > value.approved_at;
  if (
    (!notApproved && !approved) ||
    (value.status === 'prepared' && !notApproved) ||
    (['approved', 'applied', 'expired'].includes(value.status) && !approved)
  )
    invalid();
  if (value.status === 'applied') {
    const receipt = value.receipt;
    if (
      !exact(receipt, [
        'operation_id',
        'document_id',
        'version',
        'content',
        'intent_hash',
        'applied_at',
      ]) ||
      receipt.operation_id !== value.operation_id ||
      receipt.document_id !== value.document_id ||
      receipt.version !== value.expected_version + 1 ||
      receipt.content !== value.content ||
      receipt.intent_hash !== value.intent_hash ||
      !finite(receipt.applied_at)
    )
      invalid();
  } else if (value.receipt !== null) invalid();
  return value;
}
export function parseExecution(value, ownerId, operationId) {
  if (!exact(value, ['operation', 'replayed']) || typeof value.replayed !== 'boolean') invalid();
  parseOperation(value.operation, ownerId, operationId);
  if (value.operation.status !== 'applied') invalid();
  return value;
}
export function draftFromDocument(document) {
  return {
    document_id: document.id,
    expected_version: document.version,
    content: document.content,
  };
}
export function draftFromOperation(operation) {
  return {
    document_id: operation.document_id,
    expected_version: operation.expected_version,
    content: operation.content,
  };
}
export function sameDraft(draft, operation) {
  return (
    !!draft &&
    !!operation &&
    draft.document_id === operation.document_id &&
    draft.expected_version === operation.expected_version &&
    draft.content === operation.content
  );
}
export function canExecute(principal, operation, draft) {
  return (
    !!principal &&
    principal.capabilities.includes('execute') &&
    !!operation &&
    principal.owner_id === operation.owner_id &&
    principal.requester_id === operation.requester_id &&
    operation.status === 'approved' &&
    sameDraft(draft, operation)
  );
}
export function canReplay(principal, operation) {
  return (
    !!principal &&
    principal.capabilities.includes('execute') &&
    !!operation &&
    principal.owner_id === operation.owner_id &&
    principal.requester_id === operation.requester_id &&
    operation.status === 'applied'
  );
}
export function preparePayload(operationId, draft) {
  return { operation_id: operationId, tool: 'publish_revision', arguments: { ...draft } };
}
export function draftProblem(draft) {
  if (
    !draft ||
    !matches(draft.document_id, DOCUMENT_ID) ||
    !integer(draft.expected_version, 2147483647)
  )
    return '请先读取有效目标文档与版本。';
  if (!text(draft.content, 1, 2000))
    return '摘要须为 1–2000 个 Unicode 字符，不含 NUL 或无效代理码点。';
  if (encoder.encode(JSON.stringify(preparePayload(EXACT_ID, draft))).byteLength > 4096)
    return '包含操作字段的实际 JSON 正文超过 4096 字节，请缩短摘要。';
  return '';
}
export function hasUnconfirmedOperations(records, ownerId) {
  return (records[ownerId] || []).some((record) => record.uncertain);
}
export function rememberOperation(records, ownerId, operationId, uncertain) {
  if (!isOperationId(operationId) || !text(ownerId, 1, 128)) return records;
  const previous = records[ownerId] || [];
  const next = previous.filter((record) => record.id !== operationId);
  return { ...records, [ownerId]: [...next, { id: operationId, uncertain }] };
}

// Every view request owns a generation. Ignoring AbortSignal in a transport cannot bypass this gate.
export function createRequestGate() {
  let generation = 0;
  let active;
  return {
    begin(identity) {
      active?.controller.abort();
      active = { identity, generation: ++generation, controller: new AbortController() };
      return active;
    },
    isLatest(ticket) {
      return active === ticket && ticket.generation === generation;
    },
    isCurrent(ticket) {
      return (
        active === ticket && ticket.generation === generation && !ticket.controller.signal.aborted
      );
    },
    finish(ticket) {
      if (active === ticket) active = undefined;
    },
    cancel() {
      generation += 1;
      active?.controller.abort();
      active = undefined;
    },
  };
}
export async function requestJson(ticket, path, parse, body) {
  if (!matches(path, /^\/(me|documents|operations)(\/[a-z0-9-]+)?(\/(approve|revoke|execute))?$/))
    throw new LabError();
  const response = await fetch(`/api${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: {
      Authorization: `Bearer ${ticket.identity}`,
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: ticket.controller.signal,
    cache: 'no-store',
  });
  const raw = await response.text();
  if (raw.length > 64000) throw new LabError('unexpected_response', response.status);
  let value;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new LabError('unexpected_response', response.status);
  }
  if (!response.ok) {
    throw new LabError(
      exact(value, ['error']) && typeof value.error === 'string'
        ? value.error
        : 'unexpected_response',
      response.status,
    );
  }
  return parse(value);
}
