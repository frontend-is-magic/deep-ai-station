import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { useAtom, useStore } from 'jotai';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { editLatestProgress, RESET_NOTICE } from '@/lib/progress-write';
import { getStorageIssue, progressAtom, subscribeStorageIssue } from '@/lib/state';
import type { Lesson, Progress, Track, TrackId } from '@/lib/types';
import {
  diagnosticAnswer,
  diagnosticJudgmentMatches,
  diagnosticLessonEligible,
  diagnosticOutcomes,
  diagnosticScenarios,
  diagnosticStages,
  outcomeLabels,
  requestDiagnostics,
  scenarioLabels,
  stageLabels,
  validDiagnosticJudgment,
  type DiagnosticJudgment,
  type DiagnosticLesson,
  type DiagnosticReport,
  type DiagnosticScenario,
} from '@/lib/run-diagnostics';
import {
  DIAGNOSTIC_REFLECTION_LIMIT,
  prepareDiagnosticNote,
  saveDiagnosticNote,
} from '@/lib/run-diagnostics-note';

const field = 'mt-2 block w-full min-w-0 rounded-lg border border-border bg-white p-3 text-sm';
const plain =
  'max-w-full whitespace-pre-wrap break-words rounded-lg bg-muted p-4 text-xs leading-relaxed [overflow-wrap:anywhere]';
const serverStorageIssue = () => null;
const capacityNotice =
  '笔记空间不足或观察过长，本次未追加；已保留观察和预览。每篇笔记最多 10,000 字符、最多 1,000 篇，请整理后再试。';
type Result = { report: DiagnosticReport; resetId: Progress['history_reset_id'] };
type Attempt = {
  controller: AbortController;
  resetId: Progress['history_reset_id'];
  timer?: number;
};
type Choice = { outcome: string; ended_at_stage: string; tool_dispatch_count: string };
const emptyChoice: Choice = { outcome: '', ended_at_stage: '', tool_dispatch_count: '' };
const statusLabels = {
  passed: '已完成',
  rejected: '已拒绝',
  timed_out: '已超时',
  skipped: '未执行',
};

function DiagnosticSession({ lesson }: { lesson: DiagnosticLesson }) {
  const [progress, setProgress] = useAtom(progressAtom);
  const store = useStore();
  const storageIssue = useSyncExternalStore(
    subscribeStorageIssue,
    getStorageIssue,
    serverStorageIssue,
  );
  const [scenario, setScenario] = useState<DiagnosticScenario>('success');
  const [running, setRunning] = useState(false);
  const [completed, setCompleted] = useState<Result | null>(null);
  const [choice, setChoice] = useState<Choice>(emptyChoice);
  const [checked, setChecked] = useState<DiagnosticJudgment | null>(null);
  const [reflection, setReflection] = useState('');
  const [preview, setPreview] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [notice, setNotice] = useState('选一个固定场景，运行后根据实际阶段证据判断。');
  const active = useRef<Attempt | null>(null);
  const currentResult = useRef<Result | null>(null);
  const previewed = useRef<string | null>(null);
  const checkedChoice = useRef<DiagnosticJudgment | null>(null);
  const consumed = useRef(false);
  const observedReset = useRef(progress.history_reset_id);
  const report =
    completed && completed.resetId === progress.history_reset_id ? completed.report : null;

  const discard = useCallback((message: string) => {
    const previous = active.current;
    active.current = null;
    currentResult.current = null;
    previewed.current = null;
    checkedChoice.current = null;
    consumed.current = false;
    window.clearTimeout(previous?.timer);
    previous?.controller.abort();
    setRunning(false);
    setCompleted(null);
    setChoice(emptyChoice);
    setChecked(null);
    setReflection('');
    setPreview(null);
    setSaved(false);
    setNotice(message);
  }, []);
  useEffect(() => {
    if (observedReset.current === progress.history_reset_id) return;
    observedReset.current = progress.history_reset_id;
    if (active.current?.resetId === progress.history_reset_id && active.current) return;
    if (currentResult.current?.resetId === progress.history_reset_id && currentResult.current)
      return;
    discard(RESET_NOTICE);
  }, [progress.history_reset_id, discard]);
  useEffect(
    () => () => {
      const previous = active.current;
      active.current = null;
      currentResult.current = null;
      window.clearTimeout(previous?.timer);
      previous?.controller.abort();
    },
    [],
  );

  function contextCurrent(resetId: Progress['history_reset_id']) {
    const latest = editLatestProgress(store.get(progressAtom), (value) => value, { resetId });
    if (!latest.reset) return true;
    discard(RESET_NOTICE);
    setProgress((previous) => editLatestProgress(previous, (value) => value).progress);
    return false;
  }
  async function run() {
    if (active.current) return;
    discard('正在运行固定只读工具与局部编排…');
    const previous = store.get(progressAtom);
    const latest = editLatestProgress(previous, (value) => value).progress;
    observedReset.current = latest.history_reset_id;
    if (previous.history_reset_id !== latest.history_reset_id) setProgress(latest);
    const attempt: Attempt = {
      controller: new AbortController(),
      resetId: latest.history_reset_id,
    };
    active.current = attempt;
    setRunning(true);
    attempt.timer = window.setTimeout(() => {
      if (active.current === attempt)
        discard('客户端等待超时，未取得可核对的完整报告；可手动重新运行。');
    }, 15000);
    try {
      const value = await requestDiagnostics(
        { track: lesson.track, lesson_id: lesson.id, scenario },
        attempt.controller.signal,
      );
      if (active.current !== attempt || !contextCurrent(attempt.resetId)) return;
      const result = { report: value, resetId: attempt.resetId };
      currentResult.current = result;
      setCompleted(result);
      setNotice('已收到实际运行报告。请先作出判断，再显式核对。');
    } catch {
      if (active.current !== attempt || !contextCurrent(attempt.resetId)) return;
      discard('未取得有效运行报告，请检查连接后手动重新运行。');
    } finally {
      window.clearTimeout(attempt.timer);
      if (active.current === attempt) {
        active.current = null;
        setRunning(false);
      }
    }
  }
  function changeScenario(value: DiagnosticScenario) {
    discard('场景已切换，请手动运行新场景。');
    setScenario(value);
  }
  function changeChoice(key: keyof Choice, value: string) {
    checkedChoice.current = null;
    previewed.current = null;
    setChoice((previous) => ({ ...previous, [key]: value }));
    setChecked(null);
    setPreview(null);
  }
  function check() {
    const result = currentResult.current;
    if (!result || !contextCurrent(result.resetId) || consumed.current) return;
    const judgment = {
      outcome: choice.outcome,
      ended_at_stage: choice.ended_at_stage,
      tool_dispatch_count:
        choice.tool_dispatch_count === '' ? null : Number(choice.tool_dispatch_count),
    };
    if (!validDiagnosticJudgment(judgment)) {
      setNotice('请先填写全部三项判断。');
      return;
    }
    checkedChoice.current = judgment;
    previewed.current = null;
    setChecked(judgment);
    setPreview(null);
    setNotice('已按本次实际报告核对；机械核对不代表已掌握课程。');
  }
  function preparePreview() {
    const result = currentResult.current;
    if (!result || !contextCurrent(result.resetId) || consumed.current) return;
    const judgment = checkedChoice.current;
    if (!judgment) {
      setNotice('请先核对我的判断，再预览笔记。');
      return;
    }
    const latest = editLatestProgress(store.get(progressAtom), (value) => value).progress;
    const prepared = prepareDiagnosticNote(
      latest,
      result.report,
      lesson,
      judgment,
      reflection,
      result.resetId,
    );
    if (prepared.status === 'saved') {
      consumed.current = true;
      setSaved(true);
      setNotice('本次运行已追加到本课笔记。');
      return;
    }
    if (prepared.entry) {
      previewed.current = prepared.entry;
      setPreview(prepared.entry);
    }
    setNotice(
      prepared.status === 'ready'
        ? '请阅读预览，再明确追加到本课笔记。'
        : prepared.status === 'too-long'
          ? capacityNotice
          : prepared.status === 'missing-reflection'
            ? '请填写我的观察与下一步。'
            : '当前报告或笔记无效，请重新运行。',
    );
  }
  function save() {
    const result = currentResult.current;
    const judgment = checkedChoice.current;
    const entry = previewed.current;
    if (!result || !judgment || !entry || consumed.current || !contextCurrent(result.resetId))
      return;
    let outcome = '';
    let reset = false;
    setProgress((previous) => {
      const edited = editLatestProgress(
        previous,
        (latest) => {
          const prepared = prepareDiagnosticNote(
            latest,
            result.report,
            lesson,
            judgment,
            reflection,
            result.resetId,
          );
          outcome = prepared.status;
          if (prepared.status === 'saved') {
            consumed.current = true;
            return latest;
          }
          if (prepared.entry !== entry) {
            outcome = 'preview-changed';
            return latest;
          }
          if (prepared.status !== 'ready') return latest;
          consumed.current = true;
          return saveDiagnosticNote(
            latest,
            result.report,
            lesson,
            judgment,
            reflection,
            result.resetId,
          );
        },
        { resetId: result.resetId },
      );
      reset = edited.reset;
      return edited.progress;
    });
    if (reset) {
      discard(RESET_NOTICE);
      return;
    }
    if (outcome === 'ready' || outcome === 'saved') {
      setSaved(true);
      setNotice(
        getStorageIssue()
          ? '已追加到本页内存，浏览器存储失败；请先导出学习记录备份。'
          : '已追加到本课笔记，可随学习记录导出备份。',
      );
    } else if (outcome === 'too-long') setNotice(capacityNotice);
    else {
      previewed.current = null;
      setPreview(null);
      setNotice('内容已变化，本次未追加；请重新预览。');
    }
  }
  const answer = report ? diagnosticAnswer(report) : null;
  return (
    <section
      aria-label="免费运行诊断演练"
      className="min-w-0 space-y-5 rounded-xl border border-border bg-white p-5 [overflow-wrap:anywhere]"
    >
      <div className="space-y-2">
        <h2 className="text-xl font-semibold">从实际证据判断一次运行</h2>
        <p className="text-sm leading-relaxed text-muted-foreground">
          固定只读课程工具，模型调用次数为 0。这里观察局部编排，不是生产
          trace，也不诊断供应商、网络或数据库故障。不会自动重试、记录模型运行历史或完成实践。
        </p>
      </div>
      <label className="block text-sm font-medium">
        诊断场景
        <select
          aria-label="诊断场景"
          className={field}
          value={scenario}
          onChange={(event) => changeScenario(event.target.value as DiagnosticScenario)}
        >
          {diagnosticScenarios.map((value) => (
            <option key={value} value={value}>
              {scenarioLabels[value]}
            </option>
          ))}
        </select>
      </label>
      <div className="flex flex-wrap gap-3">
        <Button disabled={running} onClick={() => void run()}>
          {running ? '正在运行…' : '运行诊断演练'}
        </Button>
        {running && (
          <Button
            variant="outline"
            onClick={() => discard('客户端已停止等待，未保留旧报告；不代表服务器取消已确认。')}
          >
            停止等待
          </Button>
        )}
      </div>
      <p role="status" className="text-sm leading-relaxed">
        {notice}
      </p>
      {report && (
        <>
          <section
            aria-label="实际运行报告"
            data-run-id={report.run_id}
            data-outcome={report.outcome}
            className="min-w-0 space-y-3"
          >
            <h3 className="font-semibold">实际运行报告</h3>
            <p className="text-sm">
              终态：{outcomeLabels[report.outcome]}（{report.outcome}） · 结束阶段：
              {stageLabels[report.ended_at_stage]} · 工具 dispatch 次数：
              {report.tool_dispatch_count} · 模型调用次数：0
            </p>
            <p className="text-xs text-muted-foreground">
              运行 ID：{report.run_id} · 根阶段 {report.root.status} · 总耗时{' '}
              {report.root.duration_ms.toFixed(2)} ms
            </p>
            <ol aria-label="实际阶段时间线" className="grid min-w-0 gap-3 md:grid-cols-3">
              {report.stages.map((stage) => (
                <li
                  key={stage.id}
                  className="min-w-0 space-y-2 rounded-lg border border-border p-3 text-sm"
                >
                  <strong>
                    {stageLabels[stage.name]} · {statusLabels[stage.status]}
                  </strong>
                  <p>
                    {stage.status === 'skipped'
                      ? '本阶段没有执行；时间为空。'
                      : `${stage.start_ms!.toFixed(2)} → ${stage.end_ms!.toFixed(2)} ms；耗时 ${stage.duration_ms!.toFixed(2)} ms`}
                  </p>
                  <p>
                    dispatch：{stage.tool_dispatch_count}；工具：{stage.tool_name || '无'}
                  </p>
                  {stage.operation_id && (
                    <p className="text-xs">operation_id：{stage.operation_id}</p>
                  )}
                  {stage.error_code && <p>{stage.error_code}</p>}
                </li>
              ))}
            </ol>
            <p className="text-sm">
              命中课时：{report.evidence.found_ids.join('、') || '无'}；局部 finally 已执行。
              {report.timeout_wait_cancelled && '读取前的本地等待已取消，读取工具尚未 dispatch。'}
            </p>
            {report.evidence.summary && <pre className={plain}>{report.evidence.summary}</pre>}
            <details>
              <summary className="cursor-pointer text-sm">查看原始运行报告</summary>
              <pre className={'mt-3 ' + plain}>{JSON.stringify(report, null, 2)}</pre>
            </details>
          </section>
          {!saved && (
            <fieldset className="min-w-0 space-y-3 rounded-lg border border-border p-4">
              <legend className="px-2 font-semibold">先判断，再核对</legend>
              <div className="grid min-w-0 gap-3 md:grid-cols-3">
                <label className="block min-w-0 text-sm">
                  我的终态判断
                  <select
                    aria-label="我的终态判断"
                    className={field}
                    value={choice.outcome}
                    onChange={(event) => changeChoice('outcome', event.target.value)}
                  >
                    <option value="">请选择</option>
                    {diagnosticOutcomes.map((value) => (
                      <option key={value} value={value}>
                        {outcomeLabels[value]}（{value}）
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block min-w-0 text-sm">
                  我的停止阶段判断
                  <select
                    aria-label="我的停止阶段判断"
                    className={field}
                    value={choice.ended_at_stage}
                    onChange={(event) => changeChoice('ended_at_stage', event.target.value)}
                  >
                    <option value="">请选择</option>
                    {diagnosticStages.map((value) => (
                      <option key={value} value={value}>
                        {stageLabels[value]}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block min-w-0 text-sm">
                  我的工具 dispatch 次数
                  <select
                    aria-label="我的工具 dispatch 次数"
                    className={field}
                    value={choice.tool_dispatch_count}
                    onChange={(event) => changeChoice('tool_dispatch_count', event.target.value)}
                  >
                    <option value="">请选择</option>
                    {[0, 1, 2].map((value) => (
                      <option key={value} value={value}>
                        {value}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <Button variant="outline" onClick={check}>
                核对我的判断
              </Button>
            </fieldset>
          )}
          {checked && answer && (
            <section aria-label="诊断反馈" className="space-y-2 rounded-lg bg-muted p-4 text-sm">
              <h3 className="font-semibold">
                {diagnosticJudgmentMatches(report, checked)
                  ? '本次判断与实际报告一致。'
                  : '本次判断与实际报告不一致，请对照下列证据。'}
              </h3>
              <p>
                终态：我的判断 {outcomeLabels[checked.outcome]} / 实际报告{' '}
                {outcomeLabels[answer.outcome]}
              </p>
              <p>
                停止阶段：我的判断 {stageLabels[checked.ended_at_stage]} / 实际报告{' '}
                {stageLabels[answer.ended_at_stage]}
              </p>
              <p>
                dispatch 次数：我的判断 {checked.tool_dispatch_count} / 实际报告{' '}
                {answer.tool_dispatch_count}
              </p>
              <p>
                {report.outcome === 'no_evidence'
                  ? '检索正常完成但没有命中，不等于工具异常；根阶段 passed 不代表有证据。'
                  : report.outcome === 'rejected'
                    ? '拒绝发生在工具 dispatch 之后，没有得到可用结果；dispatch 次数不是成功执行数。'
                    : report.outcome === 'timed_out'
                      ? '超时的是读取前的本地等待；读取工具未 dispatch，不能据此推断供应商或网络超时。'
                      : '检索与读取实际完成后，才整理公开课程摘要；这不是模型生成的答案。'}
              </p>
              <p>这只是本次报告的机械核对，不代表已经掌握课程。</p>
            </section>
          )}
          {scenario !== 'success' && (
            <Button variant="outline" onClick={() => changeScenario('success')}>
              切换到正常场景
            </Button>
          )}
          <section
            aria-label="保存诊断笔记"
            className="min-w-0 space-y-3 rounded-xl border border-lime-200 bg-lime-50/50 p-4"
          >
            <h3 className="font-semibold">记录你的判断与下一步</h3>
            <p className="text-xs leading-relaxed text-muted-foreground">
              核对后预览再追加，保留原笔记。个人观察必填；错误判断也可以记录反思。仅保存当前浏览器，请勿填写密钥。
            </p>
            {!saved && (
              <>
                <label className="block text-sm">
                  我的观察与下一步
                  <textarea
                    aria-label="我的观察与下一步"
                    className={field}
                    rows={3}
                    maxLength={DIAGNOSTIC_REFLECTION_LIMIT}
                    value={reflection}
                    onChange={(event) => {
                      previewed.current = null;
                      setPreview(null);
                      setReflection(event.target.value);
                    }}
                  />
                </label>
                <Button variant="outline" onClick={preparePreview}>
                  预览诊断笔记
                </Button>
              </>
            )}
            {preview && !saved && (
              <pre
                aria-label="诊断笔记预览"
                className={plain + ' max-h-96 overflow-auto'}
                tabIndex={0}
              >
                {preview}
              </pre>
            )}
            <div className="flex flex-wrap items-center gap-3">
              {(preview || saved) && (
                <Button
                  className="h-auto max-w-full whitespace-normal py-2"
                  disabled={saved}
                  onClick={save}
                >
                  {saved
                    ? storageIssue
                      ? '已追加到本页内存'
                      : '已追加到本课笔记'
                    : '追加诊断到本课笔记'}
                </Button>
              )}
              <Link to={`/lesson/${lesson.id}`} className="text-sm underline">
                查看本课笔记
              </Link>
            </div>
          </section>
        </>
      )}
    </section>
  );
}
export default function RunDiagnostics({
  track,
  lesson,
  tracks,
}: {
  track: TrackId;
  lesson?: Lesson;
  tracks: Track[];
}) {
  const supported = tracks.flatMap((parent) =>
    parent.lessons.filter(
      (item) =>
        diagnosticLessonEligible(item) &&
        item.track === parent.id &&
        tracks.filter((other) => other.id === parent.id).length === 1 &&
        tracks.flatMap((other) => other.lessons).filter((other) => other.id === item.id).length ===
          1,
    ),
  );
  const current = lesson && supported.find((item) => item.id === lesson.id && item.track === track);
  if (!current)
    return (
      <section
        aria-label="免费运行诊断演练"
        className="space-y-3 rounded-xl border border-border p-5"
      >
        <h2 className="text-lg font-semibold">请从支持的课程开始诊断演练</h2>
        <p>请选择课程，在实际运行后判断终态、停止阶段与 dispatch 次数。</p>
        {supported.map((item) => (
          <Link
            key={item.id}
            className="block underline"
            to={`/playground?track=${item.track}&lesson=${item.id}&mode=diagnostics`}
          >
            {item.title}
          </Link>
        ))}
      </section>
    );
  return <DiagnosticSession key={`${current.track}:${current.id}`} lesson={current} />;
}
