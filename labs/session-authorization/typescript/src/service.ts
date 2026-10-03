import type { Principal } from './auth.js';
import { publicDocument, type Document, type Repository } from './repository.js';
import { ApiError } from './request.js';

export class DocumentService {
  constructor(private readonly repository: Repository) {}
  private call<T>(operation: () => T): T {
    try {
      return operation();
    } catch {
      throw new ApiError(503, 'repository_unavailable');
    }
  }
  list(principal: Principal): { items: Document[] } {
    return { items: this.call(() => this.repository.list(principal.user_id)).map(publicDocument) };
  }
  find(principal: Principal, id: string): Document {
    const item = this.call(() => this.repository.find(principal.user_id, id));
    if (!item) throw new ApiError(404, 'document_not_found');
    return publicDocument(item);
  }
  update(principal: Principal, id: string, archived: boolean): Document {
    this.find(principal, id);
    if (!principal.can_write) throw new ApiError(403, 'forbidden');
    // Synchronous repository methods keep find/authorize/update in one JS turn.
    const item = this.call(() => this.repository.update(principal.user_id, id, archived));
    if (!item) throw new ApiError(404, 'document_not_found');
    return publicDocument(item);
  }
}
