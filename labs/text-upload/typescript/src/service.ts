import type { Principal } from './auth.js';
import { ApiError, type UploadInput } from './request.js';
import {
  publicMetadata,
  QuotaExceeded,
  type Metadata,
  type Repository,
  type StoredDocument,
} from './repository.js';

export class UploadService {
  constructor(private readonly repository: Repository) {}
  private call<T>(operation: () => T): T {
    try {
      return operation();
    } catch (error) {
      if (error instanceof QuotaExceeded) throw new ApiError(409, 'quota_exceeded');
      throw new ApiError(503, 'repository_unavailable');
    }
  }
  commit(principal: Principal, upload: UploadInput): Metadata {
    return publicMetadata(this.call(() => this.repository.commit(principal.user_id, upload)));
  }
  list(principal: Principal): { documents: Metadata[] } {
    return {
      documents: this.call(() => this.repository.list(principal.user_id)).map(publicMetadata),
    };
  }
  find(principal: Principal, id: string): StoredDocument {
    const document = this.call(() => this.repository.get(principal.user_id, id));
    if (!document) throw new ApiError(404, 'document_not_found');
    return { metadata: publicMetadata(document.metadata), content: document.content };
  }
}
