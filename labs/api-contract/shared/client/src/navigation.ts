import { normalizeQuestion, validLessonId } from './protocol';

export interface SearchTarget {
  kind: 'search';
  question: string | null;
  canonical: string;
}
export interface DetailTarget {
  kind: 'detail';
  id: string;
  question: string | null;
  canonical: string;
}
export type ValidTarget = SearchTarget | DetailTarget;
export type NavigationTarget = ValidTarget | { kind: 'invalid' | 'not-found' };
export interface NavigationLocation {
  pathname: string;
  search: string;
  hash: string;
}

export function searchHref(question: string | null): string {
  return '/search' + (question === null ? '' : '?' + new URLSearchParams({ q: question }));
}
export function detailHref(id: string, question: string | null): string {
  if (!validLessonId(id)) throw new Error('Invalid lesson ID');
  return `/lessons/${id}` + (question === null ? '' : '?' + new URLSearchParams({ q: question }));
}

export function parseLocation(location: NavigationLocation): NavigationTarget {
  const { pathname, search, hash } = location;
  const searchPage = pathname === '/' || pathname === '/search';
  const detailId = pathname.startsWith('/lessons/') ? pathname.slice('/lessons/'.length) : null;
  if (!searchPage && (detailId === null || !validLessonId(detailId))) return { kind: 'not-found' };
  if (hash || search.length > 8192 || (search && !search.startsWith('?')))
    return { kind: 'invalid' };
  let question: string | null = null;
  if (search && search !== '?') {
    const parts = search.slice(1).split('&');
    if (parts.length !== 1 || !parts[0].includes('=')) return { kind: 'invalid' };
    const separator = parts[0].indexOf('=');
    try {
      const key = decodeURIComponent(parts[0].slice(0, separator).replace(/\+/g, ' '));
      const value = decodeURIComponent(parts[0].slice(separator + 1).replace(/\+/g, ' '));
      if (key !== 'q') return { kind: 'invalid' };
      question = normalizeQuestion(value);
      if (question === null) return { kind: 'invalid' };
    } catch {
      return { kind: 'invalid' };
    }
  }
  if (searchPage) return { kind: 'search', question, canonical: searchHref(question) };
  return { kind: 'detail', id: detailId!, question, canonical: detailHref(detailId!, question) };
}
