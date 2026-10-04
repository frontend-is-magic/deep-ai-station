import { createHash } from 'node:crypto';
import type { MediaType, UploadInput } from './request.js';

export interface Metadata {
  id: string;
  filename: string;
  media_type: MediaType;
  size_bytes: number;
  sha256: string;
}
export interface StoredDocument {
  metadata: Metadata;
  content: Uint8Array;
}
export interface Repository {
  commit(owner: string, upload: UploadInput, beforeWrite: () => void): Metadata;
  list(owner: string): Metadata[];
  get(owner: string, id: string): StoredDocument | null;
}
export class QuotaExceeded extends Error {}
export function publicMetadata(value: Metadata): Metadata {
  return {
    id: value.id,
    filename: value.filename,
    media_type: value.media_type,
    size_bytes: value.size_bytes,
    sha256: value.sha256,
  };
}
export class MemoryRepository implements Repository {
  private readonly documents = new Map<
    string,
    { owner: string; metadata: Metadata; content: Uint8Array }
  >();
  private nextId = 1;
  constructor(private readonly hooks: { beforeCommit?: () => void } = {}) {}
  commit(owner: string, upload: UploadInput, beforeWrite: () => void = () => {}): Metadata {
    beforeWrite();
    // Copy and prepare before publishing. Callers retain no reference to the owned bytes.
    const content = Uint8Array.from(upload.content);
    const entries = [...this.documents.values()].filter((entry) => entry.owner === owner);
    const size = entries.reduce((sum, entry) => sum + entry.content.byteLength, 0);
    if (entries.length >= 3 || size + content.byteLength > 8192) throw new QuotaExceeded();
    const id = `doc-${String(this.nextId).padStart(6, '0')}`;
    const metadata = Object.freeze({
      id,
      filename: upload.filename,
      media_type: upload.media_type,
      size_bytes: content.byteLength,
      sha256: createHash('sha256').update(content).digest('hex'),
    });
    this.hooks.beforeCommit?.();
    // No await: quota check, publication and ID advancement form one short JS turn.
    this.documents.set(id, { owner, metadata, content });
    this.nextId++;
    return publicMetadata(metadata);
  }
  list(owner: string): Metadata[] {
    return [...this.documents.values()]
      .filter((entry) => entry.owner === owner)
      .map((entry) => publicMetadata(entry.metadata))
      .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  }
  get(owner: string, id: string): StoredDocument | null {
    const entry = this.documents.get(id);
    return entry?.owner === owner
      ? { metadata: publicMetadata(entry.metadata), content: Uint8Array.from(entry.content) }
      : null;
  }
}
