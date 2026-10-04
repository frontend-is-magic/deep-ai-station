import { Authenticator, type Principal } from './auth.js';
import { ApiError, type UploadInput } from './request.js';
import {
  publicMetadata,
  QuotaExceeded,
  type Metadata,
  type Repository,
  type StoredDocument,
} from './repository.js';
import { StorageError } from './storage.js';

export class UploadService {
  constructor(
    private readonly repository: Repository,
    private readonly auth: Authenticator,
  ) {}
  private call<T>(operation: () => T): T {
    try {
      return operation();
    } catch {
      throw new ApiError(503, 'repository_unavailable');
    }
  }
  commit(principal: Principal, upload: UploadInput): Metadata {
    let calls = 0;
    let authFailure: ApiError | null = null;
    const cancel = {};
    const beforeWrite = () => {
      calls++;
      if (calls !== 1) throw cancel;
      try {
        this.auth.requireCurrentWrite(principal);
      } catch (error) {
        // Only this trusted call may populate the private latch; repository errors cannot.
        authFailure =
          error instanceof ApiError &&
          error.status === 401 &&
          error.code === 'authentication_required'
            ? new ApiError(401, 'authentication_required')
            : error instanceof ApiError && error.status === 403 && error.code === 'forbidden'
              ? new ApiError(403, 'forbidden')
              : new ApiError(503, 'session_store_unavailable');
        throw cancel;
      }
    };
    try {
      const result = this.repository.commit(principal.user_id, upload, beforeWrite);
      if (authFailure || calls !== 1) throw cancel;
      return publicMetadata(result);
    } catch (error) {
      if (authFailure) throw authFailure;
      if (calls > 1) throw new ApiError(503, 'repository_unavailable');
      if (error instanceof QuotaExceeded) throw new ApiError(409, 'quota_exceeded');
      if (error instanceof StorageError && error.code === 'result_unconfirmed')
        throw new ApiError(503, 'result_unconfirmed');
      throw new ApiError(503, 'repository_unavailable');
    }
  }
  list(principal: Principal): { documents: Metadata[] } {
    return {
      documents: this.call(() => this.repository.list(principal.user_id)).map(publicMetadata),
    };
  }
  find(principal: Principal, id: string): StoredDocument {
    const saved = this.call(() => this.repository.get(principal.user_id, id));
    if (!saved) throw new ApiError(404, 'document_not_found');
    return { metadata: publicMetadata(saved.metadata), content: saved.content };
  }
}
