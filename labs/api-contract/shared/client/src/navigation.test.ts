import { describe, expect, it } from 'vitest';
import { detailHref, parseLocation, searchHref } from './navigation';

const parse = (href: string) => {
  const url = new URL(href, 'http://127.0.0.1');
  return parseLocation(url);
};
describe('URL is the submitted query source', () => {
  it('restores a direct query and its canonical location', () => {
    expect(parse('/search?q=%E5%B7%A5%E5%85%B7')).toEqual({
      kind: 'search',
      question: '工具',
      canonical: '/search?q=%E5%B7%A5%E5%85%B7',
    });
    expect(parse('/?q=HTTP%20API')).toEqual({
      kind: 'search',
      question: 'HTTP API',
      canonical: '/search?q=HTTP+API',
    });
    expect(parse('/search?%71=%20%E5%B7%A5%E5%85%B7%20')).toEqual({
      kind: 'search',
      question: '工具',
      canonical: searchHref('工具'),
    });
  });
  it('distinguishes an unsubmitted page from an empty submitted value', () => {
    expect(parse('/search')).toEqual({ kind: 'search', question: null, canonical: '/search' });
    expect(parseLocation({ pathname: '/search', search: '?', hash: '' })).toEqual({
      kind: 'search',
      question: null,
      canonical: '/search',
    });
    expect(parse('/search?q=')).toEqual({ kind: 'invalid' });
    expect(parse('/search?q=%20')).toEqual({ kind: 'invalid' });
  });
  it('keeps detail return context separate from the ID', () => {
    expect(parse('/lessons/tools?q=HTTP')).toEqual({
      kind: 'detail',
      id: 'tools',
      question: 'HTTP',
      canonical: '/lessons/tools?q=HTTP',
    });
    expect(parse('/lessons/missing')).toEqual({
      kind: 'detail',
      id: 'missing',
      question: null,
      canonical: '/lessons/missing',
    });
    expect(detailHref('tools', 'C++')).toBe('/lessons/tools?q=C%2B%2B');
  });
  it('preserves literal plus and FEFF and counts Unicode code points', () => {
    expect(parse('/search?q=C%2B%2B')).toMatchObject({ question: 'C++' });
    expect(parse('/search?q=%EF%BB%BF')).toMatchObject({ question: '\ufeff' });
    expect(parse(searchHref('😀'.repeat(500)))).toMatchObject({ question: '😀'.repeat(500) });
    expect(parse(searchHref('😀'.repeat(501)))).toEqual({ kind: 'invalid' });
  });
  it.each([
    '?q=工具&q=工具',
    '?q=工具&x=1',
    '?x=1',
    '?q=工具&',
    '?q',
    '?q=%',
    '?q=%C0%AF',
    '?q=%ED%A0%80',
    '?view=compact',
    '?q=' + 'x'.repeat(8192),
  ])('rejects the whole invalid query %s', (search) => {
    expect(parseLocation({ pathname: '/search', search, hash: '' })).toEqual({ kind: 'invalid' });
  });
  it.each([
    '/search/',
    '/Search',
    '/lessons/tools/',
    '/lessons/%74ools',
    '/lessons/a/b',
    '/unknown',
  ])('does not turn unknown path %s into an API request', (pathname) => {
    expect(parseLocation({ pathname, search: '?bad=%', hash: '#bad' })).toEqual({
      kind: 'not-found',
    });
  });
  it('rejects a fragment on known pages', () => {
    expect(parse('/search?q=工具#part')).toEqual({ kind: 'invalid' });
  });
});
