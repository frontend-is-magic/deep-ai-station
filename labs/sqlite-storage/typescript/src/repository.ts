import { existsSync, readFileSync } from 'node:fs';
import { DatabaseSync, type SQLOutputValue } from 'node:sqlite';
import { LabError, type Command } from './request.js';

export interface ProgressItem {
  id: number;
  owner_id: string;
  lesson_id: string;
  completed: boolean;
  created_at: string;
  updated_at: string;
}

export interface AuditItem {
  id: number;
  progress_id: number;
  completed: boolean;
  at: string;
}

export interface Page<T> {
  items: T[];
  next_cursor: number | null;
}

type SetProgress = Extract<Command, { op: 'set_progress' }>;
type List = Extract<Command, { op: 'list_progress' | 'list_audit' }>;
type Row = Record<string, SQLOutputValue>;

const packagedMigrations = new URL('../migrations/', import.meta.url);
const migrations = existsSync(packagedMigrations)
  ? packagedMigrations
  : new URL('../../shared/migrations/', import.meta.url);

function progressItem(row: Row): ProgressItem {
  return {
    id: Number(row.id),
    owner_id: String(row.owner_id),
    lesson_id: String(row.lesson_id),
    completed: row.completed === 1,
    created_at: String(row.created_at),
    updated_at: String(row.updated_at),
  };
}

function page<T extends { id: number }>(rows: T[], limit: number): Page<T> {
  const hasNext = rows.length > limit;
  const items = rows.slice(0, limit);
  return { items, next_cursor: hasNext ? items[items.length - 1].id : null };
}

export class Repository {
  private readonly db: DatabaseSync;

  constructor(
    path: string,
    private readonly hooks: { afterProgressWrite?: () => void } = {},
  ) {
    this.db = new DatabaseSync(path, { timeout: 1000 });
    try {
      this.db.exec('PRAGMA foreign_keys = ON; PRAGMA busy_timeout = 1000;');
    } catch (error) {
      this.db.close();
      throw error;
    }
  }

  close(): void {
    this.db.close();
  }

  private version(): number {
    return Number(this.db.prepare('PRAGMA user_version').get()?.user_version);
  }

  private requireCurrentSchema(): void {
    if (this.version() !== 2) throw new LabError('unsupported_schema');
  }

  private transaction<T>(work: () => T): T {
    this.db.exec('BEGIN IMMEDIATE');
    try {
      const result = work();
      this.db.exec('COMMIT');
      return result;
    } catch (error) {
      try {
        this.db.exec('ROLLBACK');
      } catch {
        // Preserve the original error if SQLite already rolled the transaction back.
      }
      throw error;
    }
  }

  migrate(): { schema_version: 2 } {
    return this.transaction(() => {
      const current = this.version();
      if (![0, 1, 2].includes(current)) throw new LabError('unsupported_schema');
      for (let next = current + 1; next <= 2; next += 1) {
        const sql = readFileSync(
          new URL(`${String(next).padStart(3, '0')}.sql`, migrations),
          'utf8',
        );
        this.db.exec(sql);
        // Versions come only from this fixed migration loop, never from CLI input.
        this.db.exec(`PRAGMA user_version = ${next}`);
      }
      return { schema_version: 2 };
    });
  }

  setProgress(command: SetProgress): { item: ProgressItem; changed: boolean } {
    return this.transaction(() => {
      this.requireCurrentSchema();
      const existing = this.db
        .prepare('SELECT * FROM progress WHERE owner_id = ? AND lesson_id = ?')
        .get(command.owner_id, command.lesson_id);
      if (existing && (existing.completed === 1) === command.completed) {
        return { item: progressItem(existing), changed: false };
      }
      if (existing) {
        this.db
          .prepare('UPDATE progress SET completed = ?, updated_at = ? WHERE id = ?')
          .run(Number(command.completed), command.at, existing.id);
      } else {
        this.db
          .prepare(
            'INSERT INTO progress (owner_id, lesson_id, completed, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
          )
          .run(
            command.owner_id,
            command.lesson_id,
            Number(command.completed),
            command.at,
            command.at,
          );
      }
      const item = this.db
        .prepare('SELECT * FROM progress WHERE owner_id = ? AND lesson_id = ?')
        .get(command.owner_id, command.lesson_id)!;
      this.hooks.afterProgressWrite?.();
      this.db
        .prepare('INSERT INTO audit (progress_id, completed, at) VALUES (?, ?, ?)')
        .run(item.id, Number(command.completed), command.at);
      return { item: progressItem(item), changed: true };
    });
  }

  listProgress(command: List): Page<ProgressItem> {
    this.requireCurrentSchema();
    const rows = this.db
      .prepare('SELECT * FROM progress WHERE owner_id = ? AND id > ? ORDER BY id ASC LIMIT ?')
      .all(command.owner_id, command.cursor, command.limit + 1);
    return page(rows.map(progressItem), command.limit);
  }

  listAudit(command: List): Page<AuditItem> {
    this.requireCurrentSchema();
    const rows = this.db
      .prepare(
        'SELECT audit.id, audit.progress_id, audit.completed, audit.at FROM audit JOIN progress ON progress.id = audit.progress_id WHERE progress.owner_id = ? AND audit.id > ? ORDER BY audit.id ASC LIMIT ?',
      )
      .all(command.owner_id, command.cursor, command.limit + 1);
    return page(
      rows.map((row) => ({
        id: Number(row.id),
        progress_id: Number(row.progress_id),
        completed: row.completed === 1,
        at: String(row.at),
      })),
      command.limit,
    );
  }

  execute(command: Command): unknown {
    switch (command.op) {
      case 'migrate':
        return this.migrate();
      case 'set_progress':
        return this.setProgress(command);
      case 'list_progress':
        return this.listProgress(command);
      case 'list_audit':
        return this.listAudit(command);
    }
  }
}
