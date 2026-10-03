import { existsSync, readFileSync } from 'node:fs';

export function readLabJson<T>(name: string): T {
  const packaged = new URL(`../${name}`, import.meta.url);
  const source = existsSync(packaged) ? packaged : new URL(`../../shared/${name}`, import.meta.url);
  return JSON.parse(readFileSync(source, 'utf8')) as T;
}
export interface Document {
  id: string;
  title: string;
  archived: boolean;
}
export interface OwnedDocument extends Document {
  owner: string;
}
export interface Repository {
  list(owner: string): Document[];
  find(owner: string, id: string): Document | null;
  update(owner: string, id: string, archived: boolean): Document | null;
}
export function publicDocument(value: Document): Document {
  return { id: value.id, title: value.title, archived: value.archived };
}
export class MemoryRepository implements Repository {
  private readonly documents: OwnedDocument[];
  constructor(documents = readLabJson<{ documents: OwnedDocument[] }>('fixtures.json').documents) {
    this.documents = documents.map((document) => ({ ...document }));
  }
  list(owner: string): Document[] {
    return this.documents
      .filter((document) => document.owner === owner)
      .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
      .map(publicDocument);
  }
  find(owner: string, id: string): Document | null {
    const document = this.documents.find((value) => value.id === id && value.owner === owner);
    return document ? publicDocument(document) : null;
  }
  update(owner: string, id: string, archived: boolean): Document | null {
    const document = this.documents.find((value) => value.id === id && value.owner === owner);
    if (!document) return null;
    document.archived = archived;
    return publicDocument(document);
  }
}
