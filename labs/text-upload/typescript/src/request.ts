import type { ContentfulStatusCode } from 'hono/utils/http-status';

export class ApiError extends Error {
  constructor(
    readonly status: ContentfulStatusCode,
    readonly code: string,
  ) {
    super(code);
  }
}
export type HeaderValues = (name: string) => string[];
export type MediaType = 'text/plain' | 'text/markdown';
export interface UploadInput {
  filename: string;
  media_type: MediaType;
  content: Uint8Array;
}
export const asciiLower = (value: string): string =>
  value.replace(/[A-Z]/g, (char) => char.toLowerCase());
export const asciiTrim = (value: string): string => value.replace(/^[ \t]+|[ \t]+$/g, '');
export const MAX_FILE_BYTES = 4096;

export async function readBody(request: Request): Promise<Uint8Array> {
  const reader = request.body?.getReader();
  if (!reader) return new Uint8Array();
  const chunks: Uint8Array[] = [];
  let length = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      length += value.byteLength;
      if (length > MAX_FILE_BYTES) throw new ApiError(413, 'request_too_large');
      chunks.push(Uint8Array.from(value));
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
  return Buffer.concat(chunks, length);
}

export function validateUpload(headers: HeaderValues, content: Uint8Array): UploadInput {
  const mediaHeaders = headers('content-type');
  const filenameHeaders = headers('x-filename');
  if (mediaHeaders.length > 1 || filenameHeaders.length > 1)
    throw new ApiError(400, 'ambiguous_upload_headers');
  if (headers('content-encoding').length > 0) throw new ApiError(415, 'unsupported_media_type');
  const media = asciiLower(mediaHeaders[0] ?? '');
  const match =
    /^[ \t]*(text\/(?:plain|markdown))[ \t]*(?:;[ \t]*charset[ \t]*=[ \t]*(?:"utf-8"|utf-8)[ \t]*)?$/.exec(
      media,
    );
  if (!match || match[0] !== media) throw new ApiError(415, 'unsupported_media_type');
  const mediaType = match[1] as MediaType;
  const filename = asciiTrim(filenameHeaders[0] ?? '');
  const filenameMatch = /^[A-Za-z0-9][A-Za-z0-9_-]{0,59}\.(?:[tT][xX][tT]|[mM][dD])$/.exec(
    filename,
  );
  const extension = asciiLower(filename.slice(filename.lastIndexOf('.') + 1));
  if (
    !filenameMatch ||
    filenameMatch[0] !== filename ||
    (extension === 'txt' ? mediaType !== 'text/plain' : mediaType !== 'text/markdown')
  )
    throw new ApiError(422, 'invalid_filename');
  let text: string;
  try {
    text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(content);
  } catch {
    throw new ApiError(422, 'invalid_text');
  }
  if (
    text.length === 0 ||
    /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f\ufeff]/u.test(text) ||
    /^[ \t\r\n]*$/.test(text)
  )
    throw new ApiError(422, 'invalid_text');
  return { filename, media_type: mediaType, content };
}
