import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { useAtom, useStore } from 'jotai';
import { Link } from 'react-router-dom';
import { Download, FlaskConical, Loader2, Play } from 'lucide-react';
import { Button } from '@/components/ui/button';
import RetrievalEvaluationNote from '@/components/RetrievalEvaluationNote';
import type { EvaluationLesson } from '@/lib/retrieval-note';
import type { Progress, TrackId } from '@/lib/types';
import { progressAtom } from '@/lib/state';
import { editLatestProgress, RESET_NOTICE } from '@/lib/progress-write';
import {
  missedLessons,
  requestRetrievalEvaluation,
  retrievalEvaluationJson,
  retrievalStrategyLabels,
  type RetrievalConfiguration,
  type RetrievalEvaluationCase,
  type RetrievalEvaluationResponse,
  type RetrievalMetrics,
  type RetrievalStrategy,
} from '@/lib/retrievalEvaluation';

type Side = 'baseline' | 'candidate';
const sideLabels: Record<Side, string> = { baseline: 'A 基线', candidate: 'B 对照' };
const sides: Side[] = ['baseline', 'candidate'];
const metricLabels = {
  precision_at_k: 'Macro Precision@k',
  recall_at_k: 'Macro Recall@k',
  mrr: 'MRR@k',
  no_result_accuracy: '负例无结果准确率',
} as const;
const metricKeys = Object.keys(metricLabels) as (keyof typeof metricLabels)[];
const selectStyle = 'mt-1 block w-full rounded-lg border border-border bg-white p-2 text-sm';

function ConfigurationEditor({
  side,
  configuration,
  disabled,
  onChange,
}: {
  side: Side;
  configuration: RetrievalConfiguration;
  disabled: boolean;
  onChange: (configuration: RetrievalConfiguration) => void;
}) {
  return (
    <fieldset disabled={disabled} className="min-w-0 rounded-xl border border-border p-4">
      <legend className="px-1 text-sm font-semibold">{sideLabels[side]}</legend>
      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_80px] gap-3">
        <label className="min-w-0 text-xs text-muted-foreground">
          {sideLabels[side]}策略
          <select
            aria-label={`${sideLabels[side]}策略`}
            value={configuration.strategy}
            onChange={(event) =>
              onChange({ ...configuration, strategy: event.target.value as RetrievalStrategy })
            }
            className={selectStyle}
          >
            {Object.entries(retrievalStrategyLabels).map(([value, label]) => (
              <option value={value} key={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-muted-foreground">
          {sideLabels[side]} top_k
          <select
            aria-label={`${sideLabels[side]} top_k`}
            value={configuration.top_k}
            onChange={(event) => onChange({ ...configuration, top_k: Number(event.target.value) })}
            className={selectStyle}
          >
            {[1, 2, 3, 4, 5].map((value) => (
              <option value={value} key={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
      </div>
    </fieldset>
  );
}

function MetricCard({
  side,
  configuration,
  metrics,
}: {
  side: Side;
  configuration: RetrievalConfiguration;
  metrics: RetrievalMetrics;
}) {
  return (
    <section aria-label={`${sideLabels[side]}评测指标`} className="min-w-0 rounded-xl bg-muted p-4">
      <h3 className="text-sm font-semibold">{sideLabels[side]}</h3>
      <p className="mt-1 text-xs text-muted-foreground">
        {retrievalStrategyLabels[configuration.strategy]} · top_k={configuration.top_k}
      </p>
      <dl className="mt-4 grid grid-cols-2 gap-4">
        {metricKeys.map((key) => (
          <div key={key} className="min-w-0">
            <dt className="text-xs text-muted-foreground">{metricLabels[key]}</dt>
            <dd className="mt-1 text-xl font-semibold tabular-nums">{metrics[key].toFixed(3)}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-xs text-muted-foreground">
        {metrics.positive_cases} 个正例，{metrics.negative_cases} 个负例
      </p>
    </section>
  );
}

function LessonLinks({ lessons }: { lessons: { id: string; title: string }[] }) {
  return (
    <ul className="mt-1 space-y-1">
      {lessons.map((lesson) => (
        <li key={lesson.id}>
          <Link className="underline underline-offset-2" to={`/lesson/${lesson.id}`}>
            {lesson.title}
          </Link>
        </li>
      ))}
    </ul>
  );
}

function CaseResults({ item, side }: { item: RetrievalEvaluationCase; side: Side }) {
  const result = item[side];
  const missed = missedLessons(item, side);
  return (
    <section aria-label={`${sideLabels[side]}逐题结果`} className="min-w-0 rounded-lg bg-muted p-3">
      <h4 className="font-semibold">{sideLabels[side]}</h4>
      <p className="mt-2 text-xs text-muted-foreground">
        {item.is_negative
          ? `无结果判断：${result.metrics.no_result_accuracy === 1 ? '正确' : '误召回'}`
          : `Precision@k ${result.metrics.precision_at_k?.toFixed(3)} · Recall@k ${result.metrics.recall_at_k?.toFixed(3)} · RR@k ${result.metrics.reciprocal_rank?.toFixed(3)}`}
      </p>
      {result.results.length ? (
        <ol className="mt-3 space-y-3">
          {result.results.map((lesson, index) => (
            <li key={lesson.id}>
              <div className="font-medium">
                #{index + 1}{' '}
                <Link className="underline underline-offset-2" to={`/lesson/${lesson.id}`}>
                  {lesson.title}
                </Link>
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                词法评分 {lesson.score} · {lesson.is_relevant ? '标注相关' : '标注无关'}
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                匹配词：{lesson.matched_terms.join('、') || '无'}
              </p>
            </li>
          ))}
        </ol>
      ) : (
        <p className="mt-3 text-muted-foreground">未返回课时</p>
      )}
      {missed.length ? (
        <div className="mt-3 rounded-md border border-amber-300 bg-amber-50 p-2 text-amber-900">
          <p>漏检 {missed.length} 个标注课时</p>
          <LessonLinks lessons={missed} />
        </div>
      ) : null}
    </section>
  );
}

function EvaluationCase({ item }: { item: RetrievalEvaluationCase }) {
  const missedA = missedLessons(item, 'baseline').length;
  const missedB = missedLessons(item, 'candidate').length;
  return (
    <details className="min-w-0 rounded-xl border border-border p-4 text-sm">
      <summary className="break-words font-medium">
        {item.query}
        <span className="mt-1 block text-xs font-normal text-muted-foreground">
          {item.is_negative
            ? `负例：期望无结果 · A ${item.baseline.results.length ? '误召回' : '正确'} · B ${item.candidate.results.length ? '误召回' : '正确'}`
            : `正例 · A 漏检 ${missedA} · B 漏检 ${missedB}`}
        </span>
      </summary>
      <div className="mt-4">
        <p className="font-medium">教学标签中的相关课时</p>
        {item.relevant.length ? <LessonLinks lessons={item.relevant} /> : <p>无相关课时</p>}
      </div>
      <div className="mt-4 grid min-w-0 gap-3 xl:grid-cols-2">
        {sides.map((side) => (
          <CaseResults key={side} item={item} side={side} />
        ))}
      </div>
    </details>
  );
}

function EvaluationReport({
  result,
  children,
}: {
  result: RetrievalEvaluationResponse;
  children?: ReactNode;
}) {
  return (
    <div className="min-w-0 space-y-4">
      <div className="space-y-1 rounded-lg border border-border p-3 text-xs text-muted-foreground">
        <p>数据集版本：{result.dataset_version}</p>
        <p className="break-all">固定课程语料版本：{result.corpus_revision}</p>
        <p className="break-all">运行 ID：{result.run_id}</p>
        <p>模型调用：{result.model_calls}</p>
        <p>{result.notice}</p>
      </div>
      <div className="grid min-w-0 gap-3 lg:grid-cols-2">
        {sides.map((side) => (
          <MetricCard
            key={side}
            side={side}
            configuration={result.configurations[side]}
            metrics={result.metrics[side]}
          />
        ))}
      </div>
      <details className="text-xs text-muted-foreground">
        <summary>指标如何计算</summary>
        <p className="mt-2 leading-relaxed">
          正例逐题计算后取平均。Precision@k = 前 k 名相关数 ÷ k，返回不足 k 条仍以 k 为分母；
          Recall@k = 前 k 名相关数 ÷ 标签中的相关数；MRR@k = 前 k 名首个相关课时排名倒数的均值，
          未命中记 0。负例单独统计返回空列表的比例。词法评分用于排序，不是概率。
        </p>
      </details>
      {children}
      <h3 className="text-sm font-semibold">逐题排名与漏检分析</h3>
      <div className="min-w-0 space-y-3">
        {result.cases.map((item) => (
          <EvaluationCase key={item.id} item={item} />
        ))}
      </div>
    </div>
  );
}

interface EvaluationContext {
  track: TrackId;
  lesson?: EvaluationLesson;
}

function RetrievalEvaluationSession({ track, lesson }: EvaluationContext) {
  const [progress, setProgress] = useAtom(progressAtom);
  const store = useStore();
  const [baseline, setBaseline] = useState<RetrievalConfiguration>({ strategy: 'title', top_k: 3 });
  const [candidate, setCandidate] = useState<RetrievalConfiguration>({
    strategy: 'weighted',
    top_k: 3,
  });
  const [completed, setCompleted] = useState<{
    result: RetrievalEvaluationResponse;
    resetId: Progress['history_reset_id'];
  } | null>(null);
  const result = completed?.resetId === progress.history_reset_id ? completed?.result : null;
  const [running, setRunning] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [reflection, setReflection] = useState('');
  const active = useRef<{
    controller: AbortController;
    resetId: Progress['history_reset_id'];
  } | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const observedResetId = useRef(progress.history_reset_id);
  const discard = useCallback((nextMessage: string) => {
    const previous = active.current;
    active.current = null;
    previous?.controller.abort();
    window.clearTimeout(timer.current);
    timer.current = undefined;
    setRunning(false);
    setCompleted(null);
    setReflection('');
    setError('');
    setMessage(nextMessage);
  }, []);
  useEffect(() => {
    if (observedResetId.current === progress.history_reset_id) return;
    observedResetId.current = progress.history_reset_id;
    if (active.current && active.current.resetId === progress.history_reset_id) return;
    if (completed && completed.resetId === progress.history_reset_id) return;
    discard(RESET_NOTICE);
  }, [progress.history_reset_id, completed, discard]);
  useEffect(
    () => () => {
      const previous = active.current;
      active.current = null;
      previous?.controller.abort();
      window.clearTimeout(timer.current);
    },
    [],
  );

  function contextIsCurrent(resetId: Progress['history_reset_id']) {
    const checked = editLatestProgress(store.get(progressAtom), (current) => current, { resetId });
    if (!checked.reset) return true;
    discard(RESET_NOTICE);
    setProgress((previous) => editLatestProgress(previous, (current) => current).progress);
    return false;
  }

  function configure(side: Side, configuration: RetrievalConfiguration) {
    discard('配置已更新，请重新运行评测。');
    if (side === 'baseline') setBaseline(configuration);
    else setCandidate(configuration);
  }

  async function run() {
    if (active.current) return;
    const previous = store.get(progressAtom);
    const latest = editLatestProgress(previous, (current) => current).progress;
    const resetId = latest.history_reset_id;
    if (previous.history_reset_id !== resetId) setProgress(latest);
    const controller = new AbortController();
    const requestContext = { controller, resetId };
    active.current = requestContext;
    setRunning(true);
    setCompleted(null);
    setReflection('');
    setError('');
    setMessage('正在比较固定标注集中的课时检索结果……');
    const timeout = window.setTimeout(() => {
      if (active.current !== requestContext || !contextIsCurrent(resetId)) return;
      discard('本次评测未完成，可以重试。');
      setError('评测请求超时，请重试。');
    }, 15000);
    timer.current = timeout;
    try {
      const response = await requestRetrievalEvaluation(
        track,
        baseline,
        candidate,
        controller.signal,
      );
      if (active.current !== requestContext || controller.signal.aborted) return;
      if (!contextIsCurrent(resetId)) return;
      setCompleted({ result: response, resetId });
      setMessage(`已完成 ${response.cases.length} 道固定题目的 A/B 评测。`);
    } catch (reason) {
      if (active.current !== requestContext || !contextIsCurrent(resetId)) return;
      setMessage('本次评测未完成，可以重试。');
      setError(reason instanceof Error ? reason.message : '评测失败，请重试。');
    } finally {
      window.clearTimeout(timeout);
      if (active.current === requestContext) {
        active.current = null;
        timer.current = undefined;
        setRunning(false);
      }
    }
  }

  function download() {
    if (!result || !completed || !contextIsCurrent(completed.resetId)) return;
    let url: string | undefined;
    try {
      url = URL.createObjectURL(
        new Blob([retrievalEvaluationJson(result)], { type: 'application/json;charset=utf-8' }),
      );
      const link = document.createElement('a');
      link.href = url;
      link.download = `retrieval-evaluation-${result.track}-${result.run_id}.json`;
      link.click();
      setMessage('评测 JSON 已导出，包含配置、版本、指标和逐题结果。');
    } catch {
      setMessage('导出未完成，请重试。');
    } finally {
      if (url) URL.revokeObjectURL(url);
    }
  }

  return (
    <section
      aria-label="免费检索评测"
      className="my-6 min-w-0 space-y-4 break-words rounded-xl border border-border bg-white p-4 [overflow-wrap:anywhere] sm:p-5"
    >
      <div>
        <h2 className="flex items-center gap-2 text-base font-semibold">
          <FlaskConical size={18} className="shrink-0" />
          免费检索评测
        </h2>
        <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
          用同一组预设教学题比较两套词法检索配置。当前路线：
          {track === 'agent' ? 'AI Agent' : 'AI 全栈'}，固定 10 个正例和 2 个负例。
          仅使用本站课程语料，零模型调用，不执行代码。
        </p>
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          仅标题匹配每词计 1 分；加权匹配每词取标题 4 分、目标 2 分、正文 1 分中的最高分。
          标签尚未经独立人工审核。这是小型开发集上的词法实验，不代表向量检索效果或实际回答质量。
        </p>
      </div>
      <div className="grid min-w-0 gap-3 lg:grid-cols-2">
        {sides.map((side) => (
          <ConfigurationEditor
            key={side}
            side={side}
            configuration={side === 'baseline' ? baseline : candidate}
            disabled={running}
            onChange={(configuration) => configure(side, configuration)}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        <Button onClick={run} disabled={running} className="max-w-full whitespace-normal">
          {running ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
          {running ? '正在评测…' : '运行免费检索评测'}
        </Button>
        {result ? (
          <Button variant="outline" onClick={download} className="max-w-full whitespace-normal">
            <Download size={16} className="shrink-0" />
            导出评测 JSON
          </Button>
        ) : null}
      </div>
      <p role="status" className="text-sm text-muted-foreground">
        {message || '尚未运行。修改配置后会清除旧结果，重新运行即可对比。'}
      </p>
      {error ? (
        <p
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800"
        >
          {error}
        </p>
      ) : null}
      {completed && result ? (
        <EvaluationReport result={result}>
          {lesson && lesson.track === track && result.track === lesson.track ? (
            <RetrievalEvaluationNote
              result={result}
              resetId={completed.resetId}
              lesson={lesson}
              reflection={reflection}
              onReflectionChange={setReflection}
            />
          ) : null}
        </EvaluationReport>
      ) : null}
      {!lesson && (
        <p className="text-sm leading-relaxed text-muted-foreground">
          从课程页进入评测，可把结果和观察保存到本课笔记。评测结果仍可导出为 JSON。
        </p>
      )}
    </section>
  );
}

export default function RetrievalEvaluation({ track, lesson }: EvaluationContext) {
  return (
    <RetrievalEvaluationSession
      key={`${track}:${lesson?.id ?? 'without-lesson'}`}
      track={track}
      lesson={lesson}
    />
  );
}
