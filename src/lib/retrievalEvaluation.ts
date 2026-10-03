import { api } from './api';
import type { TrackId } from './types';

export type RetrievalStrategy = 'title' | 'weighted';
export interface RetrievalConfiguration {
  strategy: RetrievalStrategy;
  top_k: number;
}
export interface RetrievalMetrics {
  positive_cases: number;
  negative_cases: number;
  precision_at_k: number;
  recall_at_k: number;
  mrr: number;
  no_result_accuracy: number;
}
export interface RetrievalCaseResult {
  results: {
    id: string;
    title: string;
    score: number;
    matched_terms: string[];
    is_relevant: boolean;
  }[];
  metrics: {
    precision_at_k: number | null;
    recall_at_k: number | null;
    reciprocal_rank: number | null;
    no_result_accuracy: number | null;
  };
}
export interface RetrievalEvaluationCase {
  id: string;
  query: string;
  is_negative: boolean;
  relevant: { id: string; title: string }[];
  baseline: RetrievalCaseResult;
  candidate: RetrievalCaseResult;
}
export interface RetrievalEvaluationResponse {
  track: TrackId;
  dataset_version: string;
  corpus_revision: string;
  run_id: string;
  model_calls: 0;
  notice: string;
  configurations: {
    baseline: RetrievalConfiguration;
    candidate: RetrievalConfiguration;
  };
  metrics: { baseline: RetrievalMetrics; candidate: RetrievalMetrics };
  cases: RetrievalEvaluationCase[];
}

export const retrievalStrategyLabels: Record<RetrievalStrategy, string> = {
  title: '仅标题匹配',
  weighted: '加权词法匹配',
};

export function requestRetrievalEvaluation(
  track: TrackId,
  baseline: RetrievalConfiguration,
  candidate: RetrievalConfiguration,
  signal: AbortSignal,
) {
  return api<RetrievalEvaluationResponse>('/playground/retrieval-evaluation', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ track, baseline, candidate }),
    signal,
  });
}

export function missedLessons(item: RetrievalEvaluationCase, side: 'baseline' | 'candidate') {
  const retrieved = new Set(item[side].results.map((result) => result.id));
  return item.relevant.filter((lesson) => !retrieved.has(lesson.id));
}

export function retrievalEvaluationJson(result: RetrievalEvaluationResponse) {
  return `${JSON.stringify(result, null, 2)}\n`;
}
