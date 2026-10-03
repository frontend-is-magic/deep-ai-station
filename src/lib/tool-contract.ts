import type { Lesson } from './types';

export const TOOL_CONTRACT_VERSION = 'tool-contract-v1';
export const TOOL_CONTRACT_ARGUMENTS_BYTES = 4096;
export const TOOL_CONTRACT_LESSON_IDS = ['agent-structured-output', 'agent-tool-contract'] as const;
export type ToolContractLessonId = (typeof TOOL_CONTRACT_LESSON_IDS)[number];
export type ToolContractLesson = Pick<Lesson, 'id' | 'track' | 'title'> & {
  id: ToolContractLessonId;
  track: 'agent';
};
export type ToolContractToolName = 'knowledge_search' | 'lesson_read';
export type ToolContractError =
  'invalid_arguments_json' | 'invalid_arguments' | 'unknown_tool' | 'lesson_not_in_track';
export interface ToolContractRequest {
  track: 'agent';
  lesson_id: ToolContractLessonId;
  tool_name: string;
  arguments_json: string;
}
export interface ToolContractCourseItem {
  id: string;
  title: string;
  objective: string;
  summary: string;
  source: string;
}
export interface ToolContractReadLesson extends ToolContractCourseItem {
  explanation: string[];
  steps: string[];
  criteria: string[];
}
type ObservationBase = { read_only: true; operation_id: string };
export type ToolContractObservation = ObservationBase &
  (
    | { items: ToolContractCourseItem[] }
    | { lesson: ToolContractReadLesson }
    | { error: ToolContractError }
  );
export interface ToolContractReport extends ToolContractRequest {
  contract_version: typeof TOOL_CONTRACT_VERSION;
  run_id: string;
  model_calls: 0;
  outcome: 'success' | 'rejected';
  observation: ToolContractObservation;
}
export interface ToolContractCatalog {
  contract_version: typeof TOOL_CONTRACT_VERSION;
  tools: {
    name: ToolContractToolName;
    description: string;
    parameters: Record<string, unknown>;
  }[];
  supported_lesson_ids: ToolContractLessonId[];
  examples: { label: string; tool_name: string; arguments_json: string }[];
  limits: {
    arguments_bytes: typeof TOOL_CONTRACT_ARGUMENTS_BYTES;
    read_only: true;
    model_calls: 0;
  };
}

const requestKeys = ['track', 'lesson_id', 'tool_name', 'arguments_json'];
const itemKeys = ['id', 'title', 'objective', 'summary', 'source'];
const uuidV4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
function isRecord(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}
function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return (
    Reflect.ownKeys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key))
  );
}
function text(value: unknown, maximum: number, nonempty = true): value is string {
  return (
    typeof value === 'string' &&
    Array.from(value).length <= maximum &&
    (!nonempty || value.trim().length > 0)
  );
}
function toolName(value: unknown): value is string {
  return text(value, 100, false) && value.length > 0 && argumentText(value);
}
function argumentText(value: unknown): value is string {
  if (typeof value !== 'string' || value.length > TOOL_CONTRACT_ARGUMENTS_BYTES) return false;
  // TextEncoder replaces lone surrogates, whereas the server rejects invalid UTF-8 input.
  for (let index = 0; index < value.length; index += 1) {
    const unit = value.charCodeAt(index);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = value.charCodeAt(++index);
      if (!(next >= 0xdc00 && next <= 0xdfff)) return false;
    } else if (unit >= 0xdc00 && unit <= 0xdfff) return false;
  }
  return new TextEncoder().encode(value).length <= TOOL_CONTRACT_ARGUMENTS_BYTES;
}
function lessonId(value: unknown): value is ToolContractLessonId {
  return TOOL_CONTRACT_LESSON_IDS.some((id) => id === value);
}
function validRequest(value: unknown): value is ToolContractRequest {
  return (
    isRecord(value) &&
    value.track === 'agent' &&
    lessonId(value.lesson_id) &&
    toolName(value.tool_name) &&
    argumentText(value.arguments_json)
  );
}
export function toolContractLessonEligible(value: unknown): value is ToolContractLesson {
  return isRecord(value) && value.track === 'agent' && lessonId(value.id) && text(value.title, 300);
}
function publicTool(value: unknown): value is ToolContractToolName {
  return value === 'knowledge_search' || value === 'lesson_read';
}
function courseId(value: unknown): value is string {
  return text(value, 100) && /^agent-[a-z0-9]+(?:-[a-z0-9]+)*$/.exec(value)?.[0] === value;
}
function safeSource(value: unknown): value is string {
  if (!text(value, 2000)) return false;
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password;
  } catch {
    return false;
  }
}
function courseItem(value: unknown, expanded = false): value is ToolContractCourseItem {
  return (
    isRecord(value) &&
    exactKeys(value, expanded ? [...itemKeys, 'explanation', 'steps', 'criteria'] : itemKeys) &&
    courseId(value.id) &&
    text(value.title, 300) &&
    text(value.objective, 2000) &&
    text(value.summary, 800) &&
    safeSource(value.source)
  );
}
function strings(value: unknown, maximum: number): value is string[] {
  return (
    Array.isArray(value) &&
    value.length >= 1 &&
    value.length <= maximum &&
    value.every((item) => text(item, 2000))
  );
}
function successfulArguments(request: ToolContractRequest): Record<string, unknown> | undefined {
  try {
    const argumentsValue: unknown = JSON.parse(request.arguments_json);
    if (!isRecord(argumentsValue)) return;
    const field = request.tool_name === 'knowledge_search' ? 'query' : 'lesson_id';
    if (
      !exactKeys(argumentsValue, [field]) ||
      // Keep schema length/type checks here; Python strip() and JS trim() use
      // different whitespace sets. The server owns semantic query validation.
      !text(argumentsValue[field], 100, false) ||
      argumentsValue[field].length === 0 ||
      !argumentText(argumentsValue[field])
    )
      return;
    // Both tool schemas have exactly one property. A top-level comma reveals
    // duplicate keys that JSON.parse would otherwise silently collapse.
    let quoted = false;
    let escaped = false;
    let depth = 0;
    for (const character of request.arguments_json) {
      if (quoted) {
        if (escaped) escaped = false;
        else if (character === '\\') escaped = true;
        else if (character === '"') quoted = false;
      } else if (character === '"') quoted = true;
      else if (character === '{' || character === '[') depth += 1;
      else if (character === '}' || character === ']') depth -= 1;
      else if (character === ',' && depth === 1) return;
    }
    return argumentsValue;
  } catch {
    return;
  }
}
function validObservation(
  value: unknown,
  report: ToolContractRequest & { run_id: string; outcome: unknown },
): boolean {
  if (!isRecord(value) || value.read_only !== true || value.operation_id !== `${report.run_id}:1`)
    return false;
  if (report.outcome === 'rejected') {
    if (!exactKeys(value, ['read_only', 'operation_id', 'error'])) return false;
    if (value.error === 'invalid_arguments_json') return true;
    if (value.error === 'unknown_tool') return !publicTool(report.tool_name);
    if (value.error === 'invalid_arguments') return publicTool(report.tool_name);
    return value.error === 'lesson_not_in_track' && report.tool_name === 'lesson_read';
  }
  if (report.outcome !== 'success' || !publicTool(report.tool_name)) return false;
  const argumentsValue = successfulArguments(report);
  if (!argumentsValue) return false;
  if (report.tool_name === 'knowledge_search') {
    return (
      exactKeys(value, ['read_only', 'operation_id', 'items']) &&
      Array.isArray(value.items) &&
      value.items.length <= 3 &&
      value.items.every((item) => courseItem(item)) &&
      new Set(value.items.map((item) => item.id)).size === value.items.length
    );
  }
  const lesson = value.lesson;
  return (
    exactKeys(value, ['read_only', 'operation_id', 'lesson']) &&
    courseItem(lesson, true) &&
    isRecord(lesson) &&
    lesson.id === argumentsValue.lesson_id &&
    strings(lesson.explanation, 2) &&
    strings(lesson.steps, 20) &&
    strings(lesson.criteria, 20)
  );
}

export function parseToolContractReport(
  value: unknown,
  expectedRequest: ToolContractRequest,
): ToolContractReport {
  if (
    !validRequest(expectedRequest) ||
    !exactKeys(expectedRequest as unknown as Record<string, unknown>, requestKeys) ||
    !isRecord(value) ||
    !exactKeys(value, [
      ...requestKeys,
      'contract_version',
      'run_id',
      'model_calls',
      'outcome',
      'observation',
    ]) ||
    !validRequest(value) ||
    requestKeys.some((key) => value[key] !== expectedRequest[key as keyof ToolContractRequest]) ||
    value.contract_version !== TOOL_CONTRACT_VERSION ||
    value.model_calls !== 0 ||
    typeof value.run_id !== 'string' ||
    uuidV4.exec(value.run_id)?.[0] !== value.run_id ||
    !validObservation(value.observation, { ...value, run_id: value.run_id, outcome: value.outcome })
  )
    throw new Error('工具契约实验结果不符合当前请求，请重新运行。');
  return value as unknown as ToolContractReport;
}

function parameterSchema(
  value: unknown,
  name: ToolContractToolName,
): value is Record<string, unknown> {
  if (!isRecord(value)) return false;
  const field = name === 'knowledge_search' ? 'query' : 'lesson_id';
  if (
    Object.keys(value).some(
      (key) => !['type', 'additionalProperties', 'properties', 'required', 'title'].includes(key),
    ) ||
    value.type !== 'object' ||
    value.additionalProperties !== false ||
    !isRecord(value.properties) ||
    !exactKeys(value.properties, [field]) ||
    !Array.isArray(value.required) ||
    value.required.length !== 1 ||
    value.required[0] !== field ||
    (value.title !== undefined && !text(value.title, 300))
  )
    return false;
  const property = value.properties[field];
  return (
    isRecord(property) &&
    Object.keys(property).every((key) =>
      ['type', 'minLength', 'maxLength', 'title'].includes(key),
    ) &&
    property.type === 'string' &&
    property.minLength === 1 &&
    property.maxLength === 100 &&
    (property.title === undefined || text(property.title, 300))
  );
}
export function parseToolContractCatalog(value: unknown): ToolContractCatalog {
  if (
    !isRecord(value) ||
    !exactKeys(value, [
      'contract_version',
      'tools',
      'supported_lesson_ids',
      'examples',
      'limits',
    ]) ||
    value.contract_version !== TOOL_CONTRACT_VERSION ||
    !Array.isArray(value.tools) ||
    value.tools.length !== 2 ||
    !value.tools.every(
      (tool) =>
        isRecord(tool) &&
        exactKeys(tool, ['name', 'description', 'parameters']) &&
        publicTool(tool.name) &&
        text(tool.description, 2000) &&
        parameterSchema(tool.parameters, tool.name),
    ) ||
    new Set(value.tools.map((tool) => tool.name)).size !== 2 ||
    !Array.isArray(value.supported_lesson_ids) ||
    value.supported_lesson_ids.length !== 2 ||
    !value.supported_lesson_ids.every(lessonId) ||
    new Set(value.supported_lesson_ids).size !== 2 ||
    !Array.isArray(value.examples) ||
    value.examples.length < 1 ||
    value.examples.length > 30 ||
    !value.examples.every(
      (example) =>
        isRecord(example) &&
        exactKeys(example, ['label', 'tool_name', 'arguments_json']) &&
        text(example.label, 300) &&
        toolName(example.tool_name) &&
        argumentText(example.arguments_json),
    ) ||
    !isRecord(value.limits) ||
    !exactKeys(value.limits, ['arguments_bytes', 'read_only', 'model_calls']) ||
    value.limits.arguments_bytes !== TOOL_CONTRACT_ARGUMENTS_BYTES ||
    value.limits.read_only !== true ||
    value.limits.model_calls !== 0
  )
    throw new Error('工具契约实验目录无效，请刷新后重试。');
  return value as unknown as ToolContractCatalog;
}
