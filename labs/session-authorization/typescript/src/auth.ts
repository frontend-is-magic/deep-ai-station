import { timingSafeEqual } from 'node:crypto';
import { generateCookie } from 'hono/cookie';
import { readLabJson } from './repository.js';
import { ApiError, asciiLower, asciiTrim } from './request.js';

export type Clock = () => number;
export interface Session {
  user_id: string;
  can_write: boolean;
  expires_at: number;
  revoked: boolean;
  csrf_token: string;
}
interface SessionFixture extends Omit<Session, 'expires_at'> {
  token: string;
  expires_after_seconds: number;
}
export interface SessionStore {
  get(token: string): Session | null;
  revoke(token: string): void;
}
export class MemorySessionStore implements SessionStore {
  private readonly sessions = new Map<string, Session>();
  constructor(
    now: number,
    fixtures = readLabJson<{ sessions: SessionFixture[] }>('fixtures.json').sessions,
  ) {
    for (const session of fixtures) {
      this.sessions.set(session.token, {
        user_id: session.user_id,
        can_write: session.can_write,
        expires_at: now + session.expires_after_seconds * 1000,
        revoked: session.revoked,
        csrf_token: session.csrf_token,
      });
    }
  }
  get(token: string): Session | null {
    const session = this.sessions.get(token);
    return session ? { ...session } : null;
  }
  revoke(token: string): void {
    const session = this.sessions.get(token);
    if (session) session.revoked = true;
  }
}
export interface Principal {
  readonly user_id: string;
  readonly can_write: boolean;
  readonly csrf_token: string;
  readonly token: string;
  readonly source: 'bearer' | 'cookie';
}
export type HeaderValues = (name: string) => string[];
const cookieName = '__Host-lab_session';
const tokenPattern = /^[A-Za-z0-9._~+/-]+={0,}$/;
function validToken(token: string): boolean {
  return token.length >= 1 && token.length <= 128 && tokenPattern.exec(token)?.[0] === token;
}

export class Authenticator {
  constructor(
    private readonly store: SessionStore,
    private readonly clock: Clock,
    private readonly allowCookie: boolean,
    private readonly allowedOrigin: string,
  ) {}
  authenticate(headers: HeaderValues): Principal {
    const authorization = headers('authorization');
    const cookies: string[] = [];
    for (const header of headers('cookie')) {
      for (const raw of header.split(';')) {
        const equals = raw.indexOf('=');
        const key = asciiTrim(equals < 0 ? raw : raw.slice(0, equals));
        if (key === cookieName) cookies.push(asciiTrim(equals < 0 ? '' : raw.slice(equals + 1)));
      }
    }
    if (
      authorization.length > 1 ||
      authorization.some((value) => value.includes(',')) ||
      cookies.length > 1 ||
      (authorization.length > 0 && cookies.length > 0)
    )
      throw new ApiError(400, 'ambiguous_credentials');
    let token = '';
    let source: Principal['source'] = 'bearer';
    if (authorization.length === 1) {
      const match = /^([^ ]+) +(.+)$/.exec(authorization[0]);
      if (match && match[0] === authorization[0] && asciiLower(match[1]) === 'bearer')
        token = match[2];
    } else if (cookies.length === 1 && this.allowCookie) {
      token = cookies[0];
      source = 'cookie';
    }
    if (!validToken(token)) throw new ApiError(401, 'authentication_required');
    let session: Session | null;
    try {
      session = this.store.get(token);
    } catch {
      throw new ApiError(503, 'session_store_unavailable');
    }
    if (!session || session.revoked || this.clock() >= session.expires_at)
      throw new ApiError(401, 'authentication_required');
    return Object.freeze({
      user_id: session.user_id,
      can_write: session.can_write,
      csrf_token: session.csrf_token,
      token,
      source,
    });
  }
  csrf(principal: Principal, headers: HeaderValues): void {
    if (principal.source !== 'cookie') return;
    const origins = headers('origin');
    const tokens = headers('x-csrf-token');
    if (
      origins.length !== 1 ||
      origins[0] !== this.allowedOrigin ||
      tokens.length !== 1 ||
      tokens[0].length > 128
    )
      throw new ApiError(403, 'csrf_failed');
    const actual = Buffer.from(tokens[0]);
    const expected = Buffer.from(principal.csrf_token);
    if (actual.length !== expected.length || !timingSafeEqual(actual, expected))
      throw new ApiError(403, 'csrf_failed');
  }
  logout(principal: Principal): void {
    try {
      this.store.revoke(principal.token);
    } catch {
      throw new ApiError(503, 'session_store_unavailable');
    }
  }
}
export function sessionCookie(value: string, clear = false): string {
  return generateCookie(cookieName, clear ? '' : value, {
    secure: true,
    httpOnly: true,
    sameSite: 'Strict',
    path: '/',
    ...(clear ? { maxAge: 0 } : {}),
  });
}
