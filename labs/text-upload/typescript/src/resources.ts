import { existsSync, readFileSync } from 'node:fs';

export function readLabJson<T>(name: string): T {
  const packaged = new URL(`../${name}`, import.meta.url);
  const source = existsSync(packaged) ? packaged : new URL(`../../shared/${name}`, import.meta.url);
  return JSON.parse(readFileSync(source, 'utf8')) as T;
}
