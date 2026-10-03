export type Scenario = 'success' | 'error' | 'timeout' | 'hold';
export type StreamEvent =
  | { event: 'start'; data: { run_id: string; scenario: Scenario } }
  | { event: 'delta'; data: { run_id: string; seq: number; text: string } }
  | { event: 'done'; data: { run_id: string; seq: number } }
  | { event: 'error'; data: { run_id: string; code: string; message: string } };
export type Terminal =
  { kind: 'done'; runId: string } | { kind: 'error'; runId: string; code: string; message: string };
export class ProtocolError extends Error {}
export function consumeSse(
  response: Response,
  scenario: Scenario,
  onEvent: (event: StreamEvent) => void,
): Promise<Terminal>;
