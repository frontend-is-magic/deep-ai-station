import { readLabJson } from './resources.js';
import { ApiError, asciiLower, asciiTrim, type HeaderValues } from './request.js';

/** Seconds, matching the shared contract; production Date.now is converted at the entry point. */
export type Clock = () => number;
export interface Session {
  user_id: string;
  can_write: boolean;
  expires_at: number;
  revoked: boolean;
}
interface SessionFixture extends Omit<Session, 'expires_at'> {
  token: string;
  expires_after_seconds: number;
}
export interface SessionStore {
  get(token: string): Session | null;
}
export class MemorySessionStore implements SessionStore {
  private readonly sessions = new Map<string, Session>();
  constructor(
    now: number,
    fixtures = readLabJson<{ sessions: SessionFixture[] }>('fixtures.json').sessions,
  ) {
    for (const session of fixtures)
      this.sessions.set(session.token, {
        user_id: session.user_id,
        can_write: session.can_write,
        expires_at: now + session.expires_after_seconds,
        revoked: session.revoked,
      });
  }
  get(token: string): Session | null {
    const session = this.sessions.get(token);
    return session ? { ...session } : null;
  }
}
export interface Principal {
  readonly user_id: string;
  readonly can_write: boolean;
  readonly token: string;
}
const tokenPattern = /^[A-Za-z0-9._~+/-]+={0,}$/;
export class Authenticator {
  constructor(
    private readonly store: SessionStore,
    private readonly clock: Clock,
  ) {}
  authenticate(headers: HeaderValues): Principal {
    const authorization = headers('authorization');
    let targetCookies = 0;
    for (const header of headers('cookie')) {
      for (const raw of header.split(';')) {
        const equals = raw.indexOf('=');
        if (asciiTrim(equals < 0 ? raw : raw.slice(0, equals)) === '__Host-lab_session')
          targetCookies++;
      }
    }
    if (
      authorization.length > 1 ||
      authorization.some((value) => value.includes(',')) ||
      targetCookies > 1 ||
      (authorization.length > 0 && targetCookies > 0)
    )
      throw new ApiError(400, 'ambiguous_credentials');
    const header = authorization[0] ?? '';
    const match = /^([^ ]+) +(.+)$/.exec(header);
    const token = match?.[2] ?? '';
    if (
      !match ||
      match[0] !== header ||
      asciiLower(match[1]) !== 'bearer' ||
      token.length < 1 ||
      token.length > 128 ||
      tokenPattern.exec(token)?.[0] !== token
    )
      throw new ApiError(401, 'authentication_required');
    return this.resolve(token);
  }
  resolve(token: string): Principal {
    let session: Session | null;
    try {
      session = this.store.get(token);
    } catch {
      throw new ApiError(503, 'session_store_unavailable');
    }
    if (!session || session.revoked || this.clock() >= session.expires_at)
      throw new ApiError(401, 'authentication_required');
    return Object.freeze({ user_id: session.user_id, can_write: session.can_write, token });
  }
  requireCurrentWrite(principal: Principal): void {
    const current = this.resolve(principal.token);
    if (current.user_id !== principal.user_id) throw new ApiError(401, 'authentication_required');
    this.requireWrite(current);
  }
  requireWrite(principal: Principal): void {
    if (!principal.can_write) throw new ApiError(403, 'forbidden');
  }
}
