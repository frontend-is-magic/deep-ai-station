/** Fixed public teaching protocol; no backend extension. */
export type OwnerId = 'alice' | 'bob';
export type IdentityId =
  'alice' | 'alice-second' | 'alice-readonly' | 'bob' | 'expired' | 'revoked';
export type MediaType = 'text/plain' | 'text/markdown';
export interface TeachingIdentity {
  readonly id: IdentityId;
  readonly label: string;
  readonly owner: OwnerId;
  readonly canWrite: boolean;
}
// Public teaching fixtures are fixed package data; no token/address input or override.
export declare const identities: readonly TeachingIdentity[];
export interface DocumentMetadata {
  readonly id: string;
  readonly filename: string;
  readonly media_type: MediaType;
  readonly size_bytes: number;
  readonly sha256: string;
}
export type ApiErrorCode =
  | 'ambiguous_credentials'
  | 'authentication_required'
  | 'forbidden'
  | 'ambiguous_upload_headers'
  | 'invalid_input'
  | 'unsupported_media_type'
  | 'invalid_filename'
  | 'invalid_text'
  | 'request_too_large'
  | 'quota_exceeded'
  | 'document_not_found'
  | 'route_not_found'
  | 'method_not_allowed'
  | 'session_store_unavailable'
  | 'repository_unavailable'
  | 'result_unconfirmed'
  | 'request_failed';
export interface KnownApiError {
  readonly status: number;
  readonly code: ApiErrorCode;
}
export type NoticeCode =
  | ApiErrorCode
  | 'file_too_large'
  | 'file_name_not_supported'
  | 'file_read_failed'
  | 'file_size_changed'
  | 'digest_failed'
  | 'not_sent_stopped'
  | 'not_sent_timeout'
  | 'upload_unconfirmed'
  | 'network_failed'
  | 'request_timeout'
  | 'invalid_response'
  | 'content_integrity_failed'
  | 'content_not_utf8'
  | 'download_failed';
// This function only returns fixed strings. It never formats server error bodies/stacks.
export declare function noticeText(code: NoticeCode): string;

// Public pure protocol boundary. No product parser is an oracle for independent tests.
export declare function parseMetadata(value: unknown): DocumentMetadata | null;
export declare function parseListing(value: unknown): readonly DocumentMetadata[] | null;
export declare function parseApiError(status: number, value: unknown): KnownApiError | null;
export declare function contentHeadersMatch(
  response: Response,
  metadata: DocumentMetadata,
): boolean;
export declare function attachmentName(metadata: DocumentMetadata): string;
export declare function decodePreview(bytes: Uint8Array): string | null;
export interface UploadFile {
  readonly name: string;
  readonly size: number;
  arrayBuffer(): Promise<ArrayBuffer>;
}
export interface SelectedFile {
  readonly name: string;
  readonly size: number;
  readonly mediaType: MediaType;
}
export declare function inspectFile(
  file: UploadFile,
):
  | { readonly ok: true; readonly file: SelectedFile }
  | { readonly ok: false; readonly notice: 'file_too_large' | 'file_name_not_supported' };

export declare class ProtocolError extends Error {
  readonly code: NoticeCode;
  constructor(code?: NoticeCode);
}
export declare function privateHeadersMatch(response: Response): boolean;
export declare function readBoundedBody(
  response: Response,
  limit: number,
  signal: AbortSignal,
): Promise<Uint8Array>;
export declare function readProtocolJson(response: Response, signal: AbortSignal): Promise<unknown>;
