import type { ContentfulStatusCode } from 'hono/utils/http-status';

export class ApiError extends Error {
  constructor(
    readonly status: ContentfulStatusCode,
    readonly code: string,
  ) {
    super(code);
  }
}
export const asciiLower = (value: string): string =>
  value.replace(/[A-Z]/g, (char) => char.toLowerCase());
export const asciiTrim = (value: string): string => value.replace(/^[ \t]+|[ \t]+$/g, '');

export async function parseBody(
  request: Request,
  logout: boolean,
): Promise<{ archived?: boolean }> {
  const reader = request.body?.getReader();
  const chunks: Uint8Array[] = [];
  let length = 0;
  if (reader) {
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        length += value.byteLength;
        if (length > 4096) throw new ApiError(413, 'request_too_large');
        chunks.push(value);
      }
    } catch (error) {
      if (error instanceof ApiError) throw error;
      throw new ApiError(422, 'invalid_input');
    } finally {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  }
  const media = asciiLower(asciiTrim((request.headers.get('content-type') ?? '').split(';', 1)[0]));
  if (media !== 'application/json') throw new ApiError(415, 'unsupported_media_type');
  const bytes = Buffer.concat(chunks, length);
  let source: string;
  let value: unknown;
  try {
    source = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes);
    value = JSON.parse(source);
  } catch {
    throw new ApiError(422, 'invalid_input');
  }
  if (typeof value !== 'object' || value === null || Array.isArray(value))
    throw new ApiError(422, 'invalid_input');
  const fields = Object.keys(value);
  if (
    logout
      ? fields.length !== 0
      : fields.length !== 1 ||
        fields[0] !== 'archived' ||
        typeof (value as { archived?: unknown }).archived !== 'boolean'
  ) {
    throw new ApiError(422, 'invalid_input');
  }
  // JSON.parse has already validated grammar and the one-boolean-field schema.
  // Count member colons outside quoted strings so escaped duplicate keys cannot disappear.
  let quoted = false;
  let escaped = false;
  let members = 0;
  for (const char of source) {
    if (quoted) {
      if (escaped) escaped = false;
      else if (char === '\\') escaped = true;
      else if (char === '"') quoted = false;
    } else if (char === '"') quoted = true;
    else if (char === ':') members += 1;
  }
  if (members !== (logout ? 0 : 1)) throw new ApiError(422, 'invalid_input');
  return value;
}
