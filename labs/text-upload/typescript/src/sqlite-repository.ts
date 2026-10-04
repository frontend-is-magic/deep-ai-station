import { createHash } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { DatabaseSync, type SQLOutputValue } from 'node:sqlite';
import {
  QuotaExceeded,
  type Metadata,
  type Repository,
  type StoredDocument,
} from './repository.js';
import { validateUpload, type UploadInput } from './request.js';
import { databasePath, databaseURI, StorageError } from './storage.js';

type Row = Record<string, SQLOutputValue>;
interface Hooks {
  beforeOpen?: () => void;
  afterBegin?: () => void;
  afterInsert?: () => void;
  commit?: (db: DatabaseSync) => void;
  afterCommit?: () => void;
  afterClose?: () => void;
}
const CONTRACT = 'text-upload-sqlite-v1';
function fail(): never {
  throw new StorageError('repository_unavailable');
}
function integer(value: unknown, min: number, max: number): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < min || value > max)
    fail();
  return value;
}
function blob(value: unknown): Uint8Array {
  if (!(value instanceof Uint8Array)) fail();
  return Uint8Array.from(value);
}
function text(value: unknown): string {
  try {
    return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(blob(value));
  } catch {
    return fail();
  }
}
function statements(): string[] {
  const packaged = new URL('../schema.sql', import.meta.url);
  const source = existsSync(packaged)
    ? packaged
    : new URL('../../shared/schema.sql', import.meta.url);
  return readFileSync(source, 'utf8')
    .split(';')
    .map((item) => item.trim())
    .filter(Boolean);
}
function normalize(sql: string): string {
  return sql.trim().replace(/;$/, '').trim();
}
function document(row: Row): StoredDocument {
  const id = integer(row.id, 1, 999999);
  const owner = text(row.owner_id);
  if (!['alice', 'bob'].includes(owner)) fail();
  const filename = text(row.filename);
  const media = text(row.media_type);
  const sha = text(row.sha256);
  const content = blob(row.content);
  const size = integer(row.size_bytes, 1, 4096);
  const checked = validateUpload(
    (name) => (name === 'content-type' ? [media] : name === 'x-filename' ? [filename] : []),
    content,
  );
  if (
    checked.filename !== filename ||
    checked.media_type !== media ||
    content.byteLength !== size ||
    !/^[0-9a-f]{64}$/.test(sha) ||
    createHash('sha256').update(content).digest('hex') !== sha
  )
    fail();
  return {
    metadata: {
      id: `doc-${String(id).padStart(6, '0')}`,
      filename,
      media_type: checked.media_type,
      size_bytes: size,
      sha256: sha,
    },
    content,
  };
}
const SELECT_DOCUMENT = `SELECT id, CAST(owner_id AS BLOB) AS owner_id,
  CAST(filename AS BLOB) AS filename, CAST(media_type AS BLOB) AS media_type,
  size_bytes, CAST(sha256 AS BLOB) AS sha256, content FROM documents`;
function validate(db: DatabaseSync): number {
  if (
    db.prepare('PRAGMA application_id').get()?.application_id !== 1146442545 ||
    db.prepare('PRAGMA user_version').get()?.user_version !== 1
  )
    throw new StorageError('unsupported_schema');
  if (db.prepare('PRAGMA journal_mode').get()?.journal_mode !== 'delete') fail();
  const bounds = db
    .prepare('SELECT length(CAST(sql AS BLOB)) AS bytes FROM sqlite_schema LIMIT 4')
    .all();
  if (bounds.length !== 3) fail();
  for (const row of bounds) integer(row.bytes, 1, 8192);
  const objects = db
    .prepare(
      'SELECT type, name, tbl_name, CAST(sql AS BLOB) AS sql FROM sqlite_schema ORDER BY name',
    )
    .all();
  const sql = statements();
  const expected = [
    ['table', 'documents', 'documents', sql[1]],
    ['index', 'documents_owner_id_id', 'documents', sql[2]],
    ['table', 'storage_meta', 'storage_meta', sql[0]],
  ];
  for (let i = 0; i < expected.length; i++) {
    const row = objects[i];
    if (
      row.type !== expected[i][0] ||
      row.name !== expected[i][1] ||
      row.tbl_name !== expected[i][2] ||
      normalize(text(row.sql)) !== expected[i][3]
    )
      fail();
  }
  const metaBounds = db
    .prepare(
      `SELECT singleton, typeof(storage_contract) AS contract_type,
    length(CAST(storage_contract AS BLOB)) AS contract_bytes, typeof(next_id) AS next_type
    FROM storage_meta LIMIT 2`,
    )
    .all();
  if (
    metaBounds.length !== 1 ||
    metaBounds[0].singleton !== 1 ||
    metaBounds[0].contract_type !== 'text' ||
    metaBounds[0].next_type !== 'integer'
  )
    fail();
  integer(metaBounds[0].contract_bytes, 1, 100);
  const meta = db
    .prepare('SELECT CAST(storage_contract AS BLOB) AS contract, next_id FROM storage_meta')
    .get()!;
  if (text(meta.contract) !== CONTRACT) throw new StorageError('unsupported_schema');
  const nextId = integer(meta.next_id, 1, 1000000);
  const rows = db
    .prepare(
      `SELECT id, typeof(owner_id) AS owner_type, length(CAST(owner_id AS BLOB)) AS owner_bytes,
    typeof(filename) AS filename_type, length(CAST(filename AS BLOB)) AS filename_bytes,
    typeof(media_type) AS media_type_type, length(CAST(media_type AS BLOB)) AS media_type_bytes,
    typeof(sha256) AS sha256_type, length(CAST(sha256 AS BLOB)) AS sha256_bytes,
    typeof(size_bytes) AS size_type, CASE WHEN typeof(size_bytes) = 'integer' THEN size_bytes ELSE NULL END AS size_bytes, typeof(content) AS content_type,
    length(content) AS content_bytes FROM documents ORDER BY id LIMIT 7`,
    )
    .all();
  if (rows.length > 6 || nextId !== rows.length + 1) fail();
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    if (row.id !== i + 1 || row.size_type !== 'integer' || row.content_type !== 'blob') fail();
    for (const [field, max] of [
      ['owner', 5],
      ['filename', 64],
      ['media_type', 13],
      ['sha256', 64],
    ] as const) {
      if (row[`${field}_type`] !== 'text') fail();
      integer(row[`${field}_bytes`], 1, max);
    }
    if (integer(row.size_bytes, 1, 4096) !== integer(row.content_bytes, 1, 4096)) fail();
  }
  const usage = new Map<string, { count: number; size: number }>();
  for (const row of db.prepare(`${SELECT_DOCUMENT} ORDER BY id LIMIT 7`).all()) {
    const saved = document(row);
    const owner = text(row.owner_id);
    const total = usage.get(owner) ?? { count: 0, size: 0 };
    total.count++;
    total.size += saved.metadata.size_bytes;
    if (total.count > 3 || total.size > 8192) fail();
    usage.set(owner, total);
  }
  return nextId;
}

export class SqliteRepository implements Repository {
  constructor(
    private readonly cwd = process.cwd(),
    private readonly hooks: Hooks = {},
  ) {}
  private transaction<T>(write: boolean, work: (db: DatabaseSync) => T, initialize = false): T {
    let db: DatabaseSync | undefined;
    let commitStarted = false;
    let failure: unknown;
    let failed = false;
    let result: T | undefined;
    try {
      const path = databasePath(this.cwd, initialize);
      this.hooks.beforeOpen?.();
      db = new DatabaseSync(databaseURI(path), { timeout: 0 });
      db.exec('PRAGMA busy_timeout = 0');
      if (initialize) db.exec('PRAGMA journal_mode = DELETE');
      if (write) db.exec('PRAGMA synchronous = FULL');
      db.exec(write ? 'BEGIN IMMEDIATE' : 'BEGIN');
      this.hooks.afterBegin?.();
      result = work(db);
      if (write) commitStarted = true;
      if (write && this.hooks.commit) this.hooks.commit(db);
      else db.exec('COMMIT');
      if (write) this.hooks.afterCommit?.();
    } catch (error) {
      failed = true;
      failure = error;
      try {
        db?.exec('ROLLBACK');
      } catch {
        /* Preserve the operation failure. */
      }
    } finally {
      if (db) {
        try {
          db.close();
          this.hooks.afterClose?.();
        } catch (error) {
          if (!failed) {
            failed = true;
            failure = error;
          }
        }
      }
    }
    if (failed) {
      if (write && commitStarted) throw new StorageError('result_unconfirmed');
      throw failure;
    }
    return result as T;
  }
  initialize(): { schema_version: 1; storage_contract: 'text-upload-sqlite-v1' } {
    return this.transaction(
      true,
      (db) => {
        for (const sql of statements()) db.exec(sql);
        validate(db);
        return { schema_version: 1, storage_contract: CONTRACT };
      },
      true,
    );
  }
  check(): void {
    this.transaction(false, (db) => {
      validate(db);
    });
  }
  commit(owner: string, upload: UploadInput, beforeWrite: () => void = () => {}): Metadata {
    return this.transaction(true, (db) => {
      beforeWrite();
      const next = validate(db);
      if (!['alice', 'bob'].includes(owner)) fail();
      const content = Uint8Array.from(upload.content);
      const checked = validateUpload(
        (name) =>
          name === 'content-type'
            ? [upload.media_type]
            : name === 'x-filename'
              ? [upload.filename]
              : [],
        content,
      );
      if (checked.filename !== upload.filename || checked.media_type !== upload.media_type) fail();
      const quota = db
        .prepare(
          'SELECT COUNT(*) AS count, COALESCE(SUM(size_bytes), 0) AS size FROM documents WHERE owner_id = ?',
        )
        .get(owner)!;
      if (
        integer(quota.count, 0, 3) >= 3 ||
        integer(quota.size, 0, 8192) + content.byteLength > 8192
      )
        throw new QuotaExceeded();
      const sha = createHash('sha256').update(content).digest('hex');
      db.prepare(
        'INSERT INTO documents (id, owner_id, filename, media_type, size_bytes, sha256, content) VALUES (?, ?, ?, ?, ?, ?, ?)',
      ).run(next, owner, checked.filename, checked.media_type, content.byteLength, sha, content);
      this.hooks.afterInsert?.();
      db.prepare('UPDATE storage_meta SET next_id = ? WHERE singleton = 1').run(next + 1);
      return {
        id: `doc-${String(next).padStart(6, '0')}`,
        filename: checked.filename,
        media_type: checked.media_type,
        size_bytes: content.byteLength,
        sha256: sha,
      };
    });
  }
  list(owner: string): Metadata[] {
    return this.transaction(false, (db) => {
      validate(db);
      return db
        .prepare(`${SELECT_DOCUMENT} WHERE owner_id = ? ORDER BY id`)
        .all(owner)
        .map((row) => document(row).metadata);
    });
  }
  get(owner: string, id: string): StoredDocument | null {
    return this.transaction(false, (db) => {
      validate(db);
      if (!/^doc-[0-9]{6}$/.test(id) || id.length !== 10) return null;
      const row = db
        .prepare(`${SELECT_DOCUMENT} WHERE owner_id = ? AND id = ?`)
        .get(owner, Number(id.slice(4)));
      return row ? document(row) : null;
    });
  }
}
