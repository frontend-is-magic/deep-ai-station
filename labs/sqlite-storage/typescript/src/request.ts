export const MAX_INPUT_BYTES = 4096;

export type Command =
  | { op: 'migrate' }
  | {
      op: 'set_progress';
      owner_id: string;
      lesson_id: string;
      completed: boolean;
      at: string;
    }
  | {
      op: 'list_progress' | 'list_audit';
      owner_id: string;
      cursor: number;
      limit: number;
    };

export type ErrorCode =
  'invalid_input' | 'unsupported_schema' | 'database_busy' | 'storage_failure';

export class LabError extends Error {
  constructor(readonly code: ErrorCode) {
    super(code);
  }
}

const identifier = /^[a-z][a-z0-9-]{0,63}$/;
const timestamp = /^[1-9][0-9]{3}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$/;

function isIdentifier(value: unknown): value is string {
  return typeof value === 'string' && identifier.exec(value)?.[0] === value;
}

function isTimestamp(value: unknown): value is string {
  if (typeof value !== 'string' || !timestamp.test(value)) return false;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) && date.toISOString() === value;
}

function hasFields(value: Record<string, unknown>, fields: string[]): boolean {
  return (
    Object.keys(value).length === fields.length &&
    fields.every((field) => Object.hasOwn(value, field))
  );
}

export function parseCommand(bytes: Uint8Array): Command {
  if (bytes.byteLength > MAX_INPUT_BYTES) throw new LabError('invalid_input');
  let value: unknown;
  try {
    // Retain a BOM so JSON.parse rejects it rather than silently changing the input.
    value = JSON.parse(new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes));
  } catch {
    throw new LabError('invalid_input');
  }
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new LabError('invalid_input');
  }
  const object = value as Record<string, unknown>;
  if (object.op === 'migrate' && hasFields(object, ['op'])) return { op: 'migrate' };
  if (
    object.op === 'set_progress' &&
    hasFields(object, ['op', 'owner_id', 'lesson_id', 'completed', 'at']) &&
    isIdentifier(object.owner_id) &&
    isIdentifier(object.lesson_id) &&
    typeof object.completed === 'boolean' &&
    isTimestamp(object.at)
  ) {
    return {
      op: 'set_progress',
      owner_id: object.owner_id,
      lesson_id: object.lesson_id,
      completed: object.completed,
      at: object.at,
    };
  }
  if (
    (object.op === 'list_progress' || object.op === 'list_audit') &&
    hasFields(object, ['op', 'owner_id', 'cursor', 'limit']) &&
    isIdentifier(object.owner_id) &&
    typeof object.cursor === 'number' &&
    Number.isSafeInteger(object.cursor) &&
    object.cursor >= 0 &&
    typeof object.limit === 'number' &&
    Number.isInteger(object.limit) &&
    object.limit >= 1 &&
    object.limit <= 50
  ) {
    return {
      op: object.op,
      owner_id: object.owner_id,
      cursor: object.cursor,
      limit: object.limit,
    };
  }
  throw new LabError('invalid_input');
}

export function publicError(error: unknown): ErrorCode {
  if (error instanceof LabError) return error.code;
  if (typeof error === 'object' && error !== null && 'errcode' in error) {
    const code = error.errcode;
    if (typeof code === 'number' && ((code & 255) === 5 || (code & 255) === 6)) {
      return 'database_busy';
    }
  }
  return 'storage_failure';
}
