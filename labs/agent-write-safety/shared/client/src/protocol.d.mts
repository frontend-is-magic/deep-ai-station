export type Capability = 'read' | 'prepare' | 'approve' | 'execute';
export type Principal = { owner_id: string; requester_id: string; capabilities: Capability[] };
export type Fixture = {
  token: string;
  owner_id: string;
  requester_id: string;
  capabilities: string[];
  expires_after_seconds: number;
  revoked: boolean;
};
export type Document = { id: string; title: string; content: string; version: number };
export type Draft = { document_id: string; expected_version: number; content: string };
export type Receipt = {
  operation_id: string;
  document_id: string;
  version: number;
  content: string;
  intent_hash: string;
  applied_at: number;
};
export type Operation = {
  operation_id: string;
  owner_id: string;
  requester_id: string;
  tool: 'publish_revision';
  document_id: string;
  expected_version: number;
  before_content: string;
  content: string;
  intent_hash: string;
  status: 'prepared' | 'approved' | 'applied' | 'revoked' | 'expired';
  prepared_at: number;
  approved_at: number | null;
  expires_at: number | null;
  receipt: Receipt | null;
};
export type RecoveryRecords = Record<string, Array<{ id: string; uncertain: boolean }>>;
export type Ticket = { identity: string; generation: number; controller: AbortController };
export const UUID: RegExp;
export const UNCONFIRMED_MESSAGE: string;
export class LabError extends Error {
  code: string;
  status: number;
  constructor(code?: string, status?: number);
}
export function isOperationId(value: unknown): value is string;
export function parsePrincipal(value: unknown, fixture: Fixture): Principal;
export function parseDocument(value: unknown): Document;
export function parseDocuments(value: unknown): Document[];
export function parseOperation(value: unknown, ownerId: string, operationId: string): Operation;
export function parseExecution(
  value: unknown,
  ownerId: string,
  operationId: string,
): { operation: Operation; replayed: boolean };
export function draftFromDocument(document: Document): Draft;
export function draftFromOperation(operation: Operation): Draft;
export function sameDraft(draft: Draft | null, operation: Operation | null): boolean;
export function canExecute(
  principal: Principal | null,
  operation: Operation | null,
  draft: Draft | null,
): boolean;
export function canReplay(principal: Principal | null, operation: Operation | null): boolean;
export function preparePayload(
  operationId: string,
  draft: Draft,
): { operation_id: string; tool: string; arguments: Draft };
export function draftProblem(draft: Draft | null): string;
export function hasUnconfirmedOperations(records: RecoveryRecords, ownerId: string): boolean;
export function rememberOperation(
  records: RecoveryRecords,
  ownerId: string,
  operationId: string,
  uncertain: boolean,
): RecoveryRecords;
export function createRequestGate(): {
  begin(identity: string): Ticket;
  isLatest(ticket: Ticket): boolean;
  isCurrent(ticket: Ticket): boolean;
  finish(ticket: Ticket): void;
  cancel(): void;
};
export function requestJson<T>(
  ticket: Ticket,
  path: string,
  parse: (value: unknown) => T,
  body?: unknown,
): Promise<T>;
