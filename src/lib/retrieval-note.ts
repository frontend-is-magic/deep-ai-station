import { validateProgress } from './state';
import { retrievalStrategyLabels } from './retrievalEvaluation';
import type {
  RetrievalConfiguration,
  RetrievalEvaluationResponse,
  RetrievalMetrics,
} from './retrievalEvaluation';
import type { Lesson, Progress } from './types';

export type EvaluationLesson = Pick<Lesson, 'id' | 'track' | 'title'>;
export const EVALUATION_REFLECTION_LIMIT = 2000;
const NOTE_LIMIT = 10000;
const NOTE_KEYS_LIMIT = 1000;
const CASE_LIMIT = 1000;

type PreparedNote = {
  status: 'ready' | 'saved' | 'too-long' | 'missing-reflection' | 'invalid';
  entry: string;
  nextNote: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}
function boundedText(value: unknown, limit: number): value is string {
  return typeof value === 'string' && value.length <= limit && value.trim().length > 0;
}
function validLesson(value: unknown): value is EvaluationLesson {
  return (
    isRecord(value) &&
    (value.track === 'agent' || value.track === 'fullstack') &&
    typeof value.id === 'string' &&
    value.id.length <= 100 &&
    /^(agent|fullstack)-[a-z0-9]+(?:-[a-z0-9]+)*$/.exec(value.id)?.[0] === value.id &&
    value.id.startsWith(`${value.track}-`) &&
    boundedText(value.title, 300)
  );
}
function validConfiguration(value: unknown): value is RetrievalConfiguration {
  return (
    isRecord(value) &&
    (value.strategy === 'title' || value.strategy === 'weighted') &&
    typeof value.top_k === 'number' &&
    Number.isInteger(value.top_k) &&
    value.top_k >= 1 &&
    value.top_k <= 5
  );
}
function validMetrics(value: unknown): value is RetrievalMetrics {
  return (
    isRecord(value) &&
    ['positive_cases', 'negative_cases'].every(
      (key) =>
        typeof value[key] === 'number' &&
        Number.isInteger(value[key]) &&
        value[key] >= 0 &&
        value[key] <= CASE_LIMIT,
    ) &&
    ['precision_at_k', 'recall_at_k', 'mrr', 'no_result_accuracy'].every(
      (key) =>
        typeof value[key] === 'number' &&
        Number.isFinite(value[key]) &&
        value[key] >= 0 &&
        value[key] <= 1,
    )
  );
}
function validReport(
  value: unknown,
  lesson: EvaluationLesson,
): value is RetrievalEvaluationResponse {
  if (
    !isRecord(value) ||
    value.track !== lesson.track ||
    value.model_calls !== 0 ||
    !boundedText(value.dataset_version, 200) ||
    !boundedText(value.corpus_revision, 200) ||
    typeof value.run_id !== 'string' ||
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.exec(
      value.run_id,
    )?.[0] !== value.run_id ||
    !isRecord(value.configurations) ||
    !validConfiguration(value.configurations.baseline) ||
    !validConfiguration(value.configurations.candidate) ||
    !isRecord(value.metrics) ||
    !validMetrics(value.metrics.baseline) ||
    !validMetrics(value.metrics.candidate) ||
    !Array.isArray(value.cases) ||
    value.cases.length < 1 ||
    value.cases.length > CASE_LIMIT
  )
    return false;
  const { baseline, candidate } = value.metrics;
  // Check the sample counts, without revalidating or reproducing ranked case results.
  let negatives = 0;
  for (const item of value.cases) {
    if (!isRecord(item) || typeof item.is_negative !== 'boolean') return false;
    if (item.is_negative) negatives += 1;
  }
  return (
    baseline.positive_cases === candidate.positive_cases &&
    baseline.negative_cases === candidate.negative_cases &&
    baseline.negative_cases === negatives &&
    baseline.positive_cases === value.cases.length - negatives
  );
}
function validNotes(value: unknown): value is Record<string, string> {
  return (
    isRecord(value) &&
    Reflect.ownKeys(value).length === Object.keys(value).length &&
    Object.keys(value).length <= NOTE_KEYS_LIMIT &&
    Object.values(value).every((note) => typeof note === 'string' && note.length <= NOTE_LIMIT)
  );
}
function plainText(value: string): string {
  const longest = Math.max(0, ...Array.from(value.matchAll(/`+/g), (match) => match[0].length));
  const fence = '`'.repeat(Math.max(3, longest + 1));
  return `${fence}text\n${value}\n${fence}`;
}
function summary(label: string, config: RetrievalConfiguration, metrics: RetrievalMetrics): string {
  return [
    `### ${label}`,
    `策略：${retrievalStrategyLabels[config.strategy]}（${config.strategy}）；top_k=${String(config.top_k)}`,
    `precision@k：${String(metrics.precision_at_k)}；recall@k：${String(metrics.recall_at_k)}；MRR：${String(metrics.mrr)}；无结果准确率：${String(metrics.no_result_accuracy)}`,
    `正例数：${String(metrics.positive_cases)}；负例数：${String(metrics.negative_cases)}`,
  ].join('\n\n');
}

export function prepareEvaluationNote(
  progress: Progress,
  result: RetrievalEvaluationResponse,
  lesson: EvaluationLesson,
  reflection: string,
): PreparedNote {
  const invalid: PreparedNote = { status: 'invalid', entry: '', nextNote: '' };
  if (
    !isRecord(progress) ||
    !validNotes(progress.notes) ||
    !validateProgress(progress) ||
    !validLesson(lesson) ||
    !validReport(result, lesson)
  )
    return invalid;
  const previous = Object.hasOwn(progress.notes, lesson.id) ? progress.notes[lesson.id] : '';
  const unchanged = { entry: '', nextNote: previous };
  const marker = `### 检索评测记录 ${result.run_id.toLowerCase()}`;
  // Match the whole marker line, never a UUID mentioned in a learner's prose.
  if (previous.split(/\r\n?|\n/).includes(marker)) return { ...unchanged, status: 'saved' };
  if (typeof reflection !== 'string') return { ...unchanged, status: 'invalid' };
  if (!reflection.trim()) return { ...unchanged, status: 'missing-reflection' };
  if (reflection.length > EVALUATION_REFLECTION_LIMIT) return { ...unchanged, status: 'too-long' };
  const entry = [
    marker,
    '免费词法检索实验 · 不调用模型',
    plainText(
      [
        `课程：${lesson.title}`,
        `课程 ID：${lesson.id}`,
        `学习路线：${lesson.track}`,
        `数据集版本：${result.dataset_version}`,
        `课程语料版本：${result.corpus_revision}`,
        '模型调用次数：0',
      ].join('\n'),
    ),
    summary('A · 基线配置', result.configurations.baseline, result.metrics.baseline),
    summary('B · 候选配置', result.configurations.candidate, result.metrics.candidate),
    '### 我的观察与下一步',
    plainText(reflection),
    '小型词法教学集不能代表真实生成质量；请另用独立验收集验证。以下是个人观察，不会自动完成课程或语言实践。',
  ].join('\n\n');
  const nextNote = previous ? `${previous}\n\n${entry}` : entry;
  if (
    nextNote.length > NOTE_LIMIT ||
    (!Object.hasOwn(progress.notes, lesson.id) &&
      Object.keys(progress.notes).length >= NOTE_KEYS_LIMIT)
  )
    return { status: 'too-long', entry, nextNote };
  return { status: 'ready', entry, nextNote };
}

export function saveEvaluationNote(
  progress: Progress,
  result: RetrievalEvaluationResponse,
  lesson: EvaluationLesson,
  reflection: string,
): Progress {
  const prepared = prepareEvaluationNote(progress, result, lesson, reflection);
  if (prepared.status !== 'ready') return progress;
  return { ...progress, notes: { ...progress.notes, [lesson.id]: prepared.nextNote } };
}
