import type {
  IdentityId,
  TeachingIdentity,
  DocumentMetadata,
  NoticeCode,
  SelectedFile,
  UploadFile,
} from './protocol.mjs';

export type UploadPhase =
  | 'idle'
  | 'reading'
  | 'hashing'
  | 'posting'
  | 'confirmed'
  | 'rejected'
  | 'unconfirmed'
  | 'stopped'
  | 'local_error';
export interface UploadState {
  readonly phase: UploadPhase;
  // Controller-local counter, not sent to server and never presented as a receipt.
  readonly attemptId: number | null;
  readonly metadata: DocumentMetadata | null;
  readonly notice: NoticeCode | null;
}
export interface ListingState {
  readonly phase: 'idle' | 'loading' | 'ready' | 'error';
  // A failed/loading refresh can retain last observed records of THIS identity only.
  readonly documents: readonly DocumentMetadata[];
  readonly notice: NoticeCode | null;
}
export interface ContentState {
  readonly phase: 'idle' | 'loading' | 'preview' | 'downloaded' | 'error';
  readonly action: 'preview' | 'download' | null;
  readonly documentId: string | null;
  readonly text: string | null;
  readonly notice: NoticeCode | null;
}
export interface UploadClientState {
  readonly identity: TeachingIdentity;
  readonly generation: number;
  // Increment only when the actual DOM FileList must be cleared, not on a valid selection.
  readonly fileInputResetKey: number;
  readonly selectedFile: SelectedFile | null;
  readonly selectionNotice: NoticeCode | null;
  readonly upload: UploadState;
  readonly listing: ListingState;
  readonly content: ContentState;
  // Only current owner's marker is exposed; never reset by GET/new POST success/ack.
  readonly ownerHasUnconfirmedUpload: boolean;
  readonly onePostPermission: boolean;
  readonly canUpload: boolean;
  readonly canAllowAnotherPost: boolean;
}
export interface UploadDependencies {
  readonly fetch: (url: string, init: RequestInit) => Promise<Response>;
  readonly readFile: (file: UploadFile, signal: AbortSignal) => Promise<ArrayBuffer>;
  readonly sha256: (bytes: Uint8Array, signal: AbortSignal) => Promise<string>;
  // Synchronous sink: create/click/revoke in try/finally; retain no bytes or Blob URL.
  // Controller checks its ticket immediately before invoking this sink.
  readonly download: (bytes: Uint8Array, safeFilename: string) => void;
  readonly setTimer: (callback: () => void, milliseconds: number) => unknown;
  readonly clearTimer: (handle: unknown) => void;
}
export interface UploadController {
  // Pure, stable cached immutable snapshot, suitable for one Jotai subscription bridge.
  getSnapshot(): UploadClientState;
  // Subscribe does not fetch and does not call listener immediately.
  subscribe(listener: () => void): () => void;
  // Idempotent explicit first list GET. Constructor has no effects.
  start(): void;
  setIdentity(id: IdentityId): void;
  // Local reading/hashing can be superseded. Already dispatched POST rejects selection.
  selectFile(file: UploadFile | null): void;
  upload(): void;
  stopWaiting(): void;
  refreshList(): void;
  preview(documentId: string): void;
  download(documentId: string): void;
  clearPreview(): void;
  // No request. Clears selected file and grants at most one future POST to this identity.
  allowAnotherPost(): void;
  // Terminal/idempotent: invalidate tickets before abort, clear timers and private buffers.
  // Commands after disposal are no-ops; create a fresh controller for a new mount.
  dispose(): void;
}
export declare function createUploadController(options?: {
  readonly initialIdentity?: IdentityId;
  readonly dependencies?: Partial<UploadDependencies>;
  // Test seam only; UI uses 10_000. Clock/timers and ignored abort are independently injectable.
  readonly deadlineMs?: number;
}): UploadController;
