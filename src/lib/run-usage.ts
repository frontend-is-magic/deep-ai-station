import { readRunUsage } from './state';

export interface RunUsageSnapshot {
  usage: Record<string, number> | null;
  usageComplete?: boolean;
}
/** SSE usage is already cumulative: replace it instead of adding or merging fields. */
export function updateRunUsage(
  previous: RunUsageSnapshot | null,
  data: Record<string, unknown>,
  terminal = false,
): RunUsageSnapshot {
  const parsed = readRunUsage(data.usage);
  const invalidUsage = Object.hasOwn(data, 'usage') && data.usage !== null && parsed === null;
  const usage = data.usage === null ? null : (parsed ?? previous?.usage ?? null);
  return {
    usage,
    usageComplete:
      terminal && !invalidUsage
        ? typeof data.usage_complete === 'boolean'
          ? data.usage_complete
          : previous?.usageComplete
        : false,
  };
}
export function incompleteRunUsage(previous: RunUsageSnapshot | null): RunUsageSnapshot {
  return { usage: previous?.usage ?? null, usageComplete: false };
}
