import type { Capabilities, LiveProvider } from './types';

const labels = new Map([
  ['demo', '教学演示'],
  ['deepseek', 'DeepSeek'],
  ['openai', 'OpenAI（历史供应商）'],
  ['anthropic', 'Anthropic（历史供应商）'],
]);

export function providerLabel(provider: string): string {
  return labels.get(provider) || `${provider}（历史供应商）`;
}

export function providerForReplay(
  provider: string,
  capabilities: Capabilities | null,
): LiveProvider {
  return provider !== 'demo' &&
    capabilities?.providers.some(
      (candidate) => candidate.id === 'deepseek' && candidate.enabled === true,
    )
    ? 'deepseek'
    : 'demo';
}
