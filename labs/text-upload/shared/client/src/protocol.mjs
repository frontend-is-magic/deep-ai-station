const filenamePattern = /^[A-Za-z0-9][A-Za-z0-9_-]{0,59}\.(txt|md)$/i;
const idPattern = /^doc-[0-9]{6}$/;
const digestPattern = /^[0-9a-f]{64}$/;
const completeMatch = (pattern, value) =>
  typeof value === 'string' && pattern.exec(value)?.[0] === value;
const objectWithKeys = (value, keys) =>
  value !== null &&
  typeof value === 'object' &&
  Object.getPrototypeOf(value) === Object.prototype &&
  Object.keys(value).length === keys.length &&
  keys.every((key) => Object.hasOwn(value, key));
const mediaForName = (name) =>
  name.slice(-3).toLowerCase() === 'txt' ? 'text/plain' : 'text/markdown';

export const identities = Object.freeze(
  [
    ['alice', 'Alice · 可写', 'alice', true],
    ['alice-second', 'Alice · 另一会话', 'alice', true],
    ['alice-readonly', 'Alice · 只读', 'alice', false],
    ['bob', 'Bob · 可写', 'bob', true],
    ['expired', 'Alice · 已过期', 'alice', true],
    ['revoked', 'Alice · 已撤销', 'alice', true],
  ].map(([id, label, owner, canWrite]) => Object.freeze({ id, label, owner, canWrite })),
);

const errorStatuses = Object.freeze({
  ambiguous_credentials: 400,
  ambiguous_upload_headers: 400,
  authentication_required: 401,
  forbidden: 403,
  document_not_found: 404,
  route_not_found: 404,
  method_not_allowed: 405,
  quota_exceeded: 409,
  request_too_large: 413,
  unsupported_media_type: 415,
  invalid_input: 422,
  invalid_filename: 422,
  invalid_text: 422,
  session_store_unavailable: 503,
  repository_unavailable: 503,
  result_unconfirmed: 503,
  request_failed: 500,
});
const notices = Object.freeze({
  ambiguous_credentials: '服务器拒绝了不明确的教学身份。',
  ambiguous_upload_headers: '服务器拒绝了不明确的上传头。',
  authentication_required: '当前教学会话无效、已到期或已撤销。',
  forbidden: '当前教学身份没有上传权限。',
  document_not_found: '当前身份无法读取此文档。',
  route_not_found: '服务器未提供此接口。',
  method_not_allowed: '服务器不接受此请求方法。',
  quota_exceeded: '服务器拒绝上传：每个 owner 最多 3 份文档、合计 8192 bytes。',
  request_too_large: '服务器拒绝上传：文件超过 4096 bytes。',
  unsupported_media_type: '服务器拒绝了此文件媒体类型。',
  invalid_input: '服务器拒绝了无效请求。',
  invalid_filename: '服务器拒绝了此文件名。',
  invalid_text: '服务器拒绝了空白、无效 UTF-8 或含禁止字符的正文。',
  session_store_unavailable: '服务器暂时无法检查教学会话。',
  repository_unavailable: '服务器报告仓储暂不可用。',
  result_unconfirmed: '服务器无法确认这次写入结果。',
  request_failed: '服务器未能完成请求。',
  file_too_large: '浏览器尚未发送：文件大小必须在 0 至 4096 bytes 之间。',
  file_name_not_supported: '浏览器尚未发送：请选择符合 ASCII 命名规则的 .txt 或 .md 文件。',
  file_read_failed: '浏览器尚未发送：无法读取所选文件。',
  file_size_changed: '浏览器尚未发送：读取的字节数与所选文件大小不一致。',
  digest_failed: '浏览器尚未发送：无法计算文件 SHA-256。',
  not_sent_stopped: '已停止等待，浏览器尚未发送上传。',
  not_sent_timeout: '本地文件处理超时，浏览器尚未发送上传。',
  upload_unconfirmed:
    '上传结果未确认；服务器可能已保存，请先刷新列表核对。再次上传可能创建重复文档。此提示仅保留在本页，刷新页面后会丢失。',
  network_failed: '无法取得服务器响应，请手动重试读取。',
  request_timeout: '读取请求超时，请手动重试。',
  invalid_response: '服务器响应不符合本实验协议，未采用其内容。',
  content_integrity_failed: '附件的响应头、字节数或 SHA-256 不匹配，未展示或下载。',
  content_not_utf8: '附件不是有效 UTF-8，未展示正文。',
  download_failed: '浏览器未能完成附件下载。',
});
export function noticeText(code) {
  return Object.hasOwn(notices, code) ? notices[code] : notices.invalid_response;
}
export function inspectFile(file) {
  if (!Number.isSafeInteger(file?.size) || file.size < 0 || file.size > 4096) {
    return { ok: false, notice: 'file_too_large' };
  }
  if (!completeMatch(filenamePattern, file.name)) {
    return { ok: false, notice: 'file_name_not_supported' };
  }
  return {
    ok: true,
    file: Object.freeze({ name: file.name, size: file.size, mediaType: mediaForName(file.name) }),
  };
}
export function parseMetadata(value) {
  if (
    !objectWithKeys(value, ['id', 'filename', 'media_type', 'size_bytes', 'sha256']) ||
    !completeMatch(idPattern, value.id) ||
    value.id === 'doc-000000' ||
    !completeMatch(filenamePattern, value.filename) ||
    value.media_type !== mediaForName(value.filename) ||
    !Number.isInteger(value.size_bytes) ||
    value.size_bytes < 1 ||
    value.size_bytes > 4096 ||
    !completeMatch(digestPattern, value.sha256)
  ) {
    return null;
  }
  return Object.freeze({
    id: value.id,
    filename: value.filename,
    media_type: value.media_type,
    size_bytes: value.size_bytes,
    sha256: value.sha256,
  });
}
export function parseListing(value) {
  if (!objectWithKeys(value, ['documents']) || !Array.isArray(value.documents)) return null;
  if (value.documents.length > 3) return null;
  const documents = value.documents.map(parseMetadata);
  if (documents.some((document) => document === null)) return null;
  if (documents.reduce((total, document) => total + document.size_bytes, 0) > 8192) return null;
  if (documents.some((document, index) => index > 0 && document.id <= documents[index - 1].id)) {
    return null;
  }
  return Object.freeze(documents);
}
export function parseApiError(status, value) {
  if (
    !objectWithKeys(value, ['error']) ||
    typeof value.error !== 'string' ||
    !Object.hasOwn(errorStatuses, value.error) ||
    errorStatuses[value.error] !== status
  ) {
    return null;
  }
  return Object.freeze({ status, code: value.error });
}
export function attachmentName(metadata) {
  return `upload-${metadata.id}.${metadata.media_type === 'text/plain' ? 'txt' : 'md'}`;
}
export function privateHeadersMatch(response) {
  return (
    !response.redirected &&
    response.headers.get('cache-control')?.trim().toLowerCase() === 'no-store' &&
    response.headers.get('x-content-type-options')?.trim().toLowerCase() === 'nosniff'
  );
}
export function contentHeadersMatch(response, metadata) {
  return (
    privateHeadersMatch(response) &&
    response.headers.get('content-type')?.trim().toLowerCase() === 'application/octet-stream' &&
    response.headers.get('content-disposition') ===
      `attachment; filename="${attachmentName(metadata)}"`
  );
}
export function decodePreview(bytes) {
  try {
    // Preserve BOM as text if a mismatching server supplied one; never re-encode this value.
    return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes);
  } catch {
    return null;
  }
}
export class ProtocolError extends Error {
  constructor(code = 'invalid_response') {
    super(code);
    this.code = code;
  }
}
export async function readBoundedBody(response, limit, signal) {
  if (signal.aborted) throw new ProtocolError();
  if (!response.body) return new Uint8Array();
  const reader = response.body.getReader();
  const cancel = () => {
    try {
      Promise.resolve(reader.cancel()).catch(() => {});
    } catch {
      // A closed stream has no remaining body to release.
    }
  };
  signal.addEventListener('abort', cancel, { once: true });
  const chunks = [];
  let length = 0;
  let complete = false;
  try {
    for (;;) {
      const part = await reader.read();
      if (signal.aborted) throw new ProtocolError();
      if (part.done) break;
      if (!(part.value instanceof Uint8Array) || length + part.value.byteLength > limit) {
        throw new ProtocolError();
      }
      length += part.value.byteLength;
      chunks.push(part.value.slice());
    }
    const bytes = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) {
      bytes.set(chunk, offset);
      offset += chunk.byteLength;
    }
    complete = true;
    return bytes;
  } finally {
    signal.removeEventListener('abort', cancel);
    if (!complete) cancel();
    try {
      reader.releaseLock();
    } catch {
      // Cancellation can still be finishing; it is intentionally not awaited.
    }
  }
}
export async function readProtocolJson(response, signal) {
  if (
    !privateHeadersMatch(response) ||
    !/^application\/json(?:\s*;\s*charset=utf-8)?$/i.test(
      response.headers.get('content-type')?.trim() ?? '',
    )
  ) {
    try {
      Promise.resolve(response.body?.cancel()).catch(() => {});
    } catch {
      // Reject the response without adopting its diagnostic body.
    }
    throw new ProtocolError();
  }
  const bytes = await readBoundedBody(response, 4096, signal);
  if (signal.aborted) throw new ProtocolError();
  try {
    const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes);
    return JSON.parse(text);
  } catch {
    throw new ProtocolError();
  }
}
