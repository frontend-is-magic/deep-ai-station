import { validateProgress } from './state';
import type { Progress } from './types';
import {
  diagnosticAnswer,
  diagnosticJudgmentMatches,
  diagnosticLessonEligible,
  parseDiagnosticReport,
  validDiagnosticJudgment,
  type DiagnosticJudgment,
  type DiagnosticLesson,
  type DiagnosticReport,
} from './run-diagnostics';

export const DIAGNOSTIC_REFLECTION_LIMIT = 2000;
export interface PreparedDiagnosticNote {
  status: 'ready' | 'saved' | 'too-long' | 'missing-reflection' | 'invalid';
  entry: string;
  nextNote: string;
}
function plainText(value: string) {
  const longest = Math.max(0, ...Array.from(value.matchAll(/`+/g), (match) => match[0].length));
  const fence = '`'.repeat(Math.max(3, longest + 1));
  return `${fence}text\n${value}\n${fence}`;
}
export function prepareDiagnosticNote(
  progress: Progress,
  report: DiagnosticReport,
  lesson: DiagnosticLesson,
  judgment: DiagnosticJudgment,
  reflection: string,
  resetId: Progress['history_reset_id'],
): PreparedDiagnosticNote {
  const invalid: PreparedDiagnosticNote = { status: 'invalid', entry: '', nextNote: '' };
  if (
    !validateProgress(progress) ||
    progress.history_reset_id !== resetId ||
    !diagnosticLessonEligible(lesson) ||
    !validDiagnosticJudgment(judgment)
  )
    return invalid;
  try {
    parseDiagnosticReport(report, {
      track: lesson.track,
      lesson_id: lesson.id,
      scenario: report?.scenario,
    });
  } catch {
    return invalid;
  }
  const previous = Object.hasOwn(progress.notes, lesson.id) ? progress.notes[lesson.id] : '';
  const marker = `### 运行诊断记录 ${report.run_id}`;
  if (previous.split(/\r\n?|\n/).includes(marker))
    return { status: 'saved', entry: '', nextNote: previous };
  if (typeof reflection !== 'string') return invalid;
  if (!reflection.trim()) return { status: 'missing-reflection', entry: '', nextNote: previous };
  if (reflection.length > DIAGNOSTIC_REFLECTION_LIMIT)
    return { status: 'too-long', entry: '', nextNote: previous };
  const entry = [
    marker,
    '免费运行诊断演练 · 只读课程工具 · 模型调用次数：0',
    plainText(`课程：${lesson.title}\n课程 ID：${lesson.id}\n学习路线：${lesson.track}`),
    '### 实际运行报告',
    plainText(JSON.stringify(report, null, 2)),
    '### 我的判断与机械核对',
    plainText(
      `我的判断：${JSON.stringify(judgment)}\n实际报告：${JSON.stringify(diagnosticAnswer(report))}\n机械核对：${diagnosticJudgmentMatches(report, judgment) ? '一致' : '不一致'}；不代表已掌握课程。`,
    ),
    '### 我的观察与下一步',
    plainText(reflection),
    '这是固定本地编排诊断；dispatch 次数不是成功执行数，局部 finally 不证明外部资源已清理。不会自动完成课程或语言实践。',
  ].join('\n\n');
  const nextNote = previous ? `${previous}\n\n${entry}` : entry;
  const full =
    nextNote.length > 10000 ||
    (!Object.hasOwn(progress.notes, lesson.id) && Object.keys(progress.notes).length >= 1000);
  return { status: full ? 'too-long' : 'ready', entry, nextNote };
}
export function saveDiagnosticNote(
  progress: Progress,
  report: DiagnosticReport,
  lesson: DiagnosticLesson,
  judgment: DiagnosticJudgment,
  reflection: string,
  resetId: Progress['history_reset_id'],
): Progress {
  const prepared = prepareDiagnosticNote(progress, report, lesson, judgment, reflection, resetId);
  return prepared.status === 'ready'
    ? { ...progress, notes: { ...progress.notes, [lesson.id]: prepared.nextNote } }
    : progress;
}
