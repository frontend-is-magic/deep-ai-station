import { closeSync, lstatSync, mkdirSync, openSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export type StorageCode =
  | 'invalid_input'
  | 'database_exists'
  | 'database_missing'
  | 'unsupported_schema'
  | 'repository_unavailable'
  | 'result_unconfirmed';
export class StorageError extends Error {
  constructor(readonly code: StorageCode) {
    super(code);
  }
}
export function storageMode(args: string[]): 'memory' | 'sqlite' | 'init' | 'help' {
  if (args.length === 0) return 'memory';
  if (args.length === 1 && args[0] === 'init') return 'init';
  if (args.length === 1 && args[0] === '--help') return 'help';
  if (args.length === 3 && args.join('\0') === 'serve\0--storage\0sqlite') return 'sqlite';
  throw new StorageError('invalid_input');
}
function missing(error: unknown): boolean {
  return (error as NodeJS.ErrnoException)?.code === 'ENOENT';
}
export function databasePath(cwd: string, initialize = false): string {
  const directory = resolve(cwd, '.data');
  try {
    const info = lstatSync(directory);
    if (!info.isDirectory() || info.isSymbolicLink())
      throw new StorageError('repository_unavailable');
  } catch (error) {
    if (!missing(error)) throw new StorageError('repository_unavailable');
    if (!initialize) throw new StorageError('database_missing');
    try {
      mkdirSync(directory, { mode: 0o700 });
    } catch {
      throw new StorageError('repository_unavailable');
    }
  }
  const path = resolve(directory, 'uploads.sqlite3');
  if (initialize) {
    try {
      closeSync(openSync(path, 'wx', 0o600));
    } catch (error) {
      throw new StorageError(
        (error as NodeJS.ErrnoException)?.code === 'EEXIST'
          ? 'database_exists'
          : 'repository_unavailable',
      );
    }
  } else {
    try {
      const info = lstatSync(path);
      if (!info.isFile() || info.isSymbolicLink()) throw new StorageError('repository_unavailable');
    } catch (error) {
      throw new StorageError(missing(error) ? 'database_missing' : 'repository_unavailable');
    }
  }
  return path;
}
export function databaseURI(path: string): string {
  // Fixed absolute path only; neither CLI nor HTTP accepts a URI.
  return `${pathToFileURL(path).href}?mode=rw`;
}
export function storageErrorCode(error: unknown): StorageCode {
  return error instanceof StorageError ? error.code : 'repository_unavailable';
}
