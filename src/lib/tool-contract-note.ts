import { validateProgress } from './state';
import { parseToolContractReport, toolContractLessonEligible } from './tool-contract';
import type { ToolContractLesson, ToolContractReport } from './tool-contract';
import type { Progress } from './types';

export const TOOL_CONTRACT_REFLECTION_LIMIT = 2000;
const NOTE_LIMIT = 10000;
const NOTE_KEYS_LIMIT = 1000;
export type PreparedToolContractNote = {
  status: 'ready' | 'saved' | 'too-long' | 'missing-reflection' | 'invalid';
  entry: string;
  nextNote: string;
};
function validNotes(value: unknown): value is Record<string, string> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return (
    (prototype === Object.prototype || prototype === null) &&
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
export function prepareToolContractNote(
  progress: Progress,
  report: ToolContractReport,
  lesson: ToolContractLesson,
  reflection: string,
): PreparedToolContractNote {
  const invalid: PreparedToolContractNote = { status: 'invalid', entry: '', nextNote: '' };
  if (
    !progress ||
    !validNotes(progress.notes) ||
    !validateProgress(progress) ||
    !toolContractLessonEligible(lesson)
  )
    return invalid;
  try {
    parseToolContractReport(report, {
      track: lesson.track,
      lesson_id: lesson.id,
      tool_name: report?.tool_name,
      arguments_json: report?.arguments_json,
    });
  } catch {
    return invalid;
  }
  const previous = Object.hasOwn(progress.notes, lesson.id) ? progress.notes[lesson.id] : '';
  const unchanged = { entry: '', nextNote: previous };
  const marker = `### 工具契约实验记录 ${report.run_id.toLowerCase()}`;
  if (previous.split(/\r\n?|\n/).includes(marker)) return { ...unchanged, status: 'saved' };
  if (typeof reflection !== 'string') return { ...unchanged, status: 'invalid' };
  if (!reflection.trim()) return { ...unchanged, status: 'missing-reflection' };
  if (reflection.length > TOOL_CONTRACT_REFLECTION_LIMIT)
    return { ...unchanged, status: 'too-long' };
  const entry = [
    marker,
    '免费工具契约实验 · 只读课程工具 · 模型调用次数：0',
    plainText(
      [
        `课程：${lesson.title}`,
        `课程 ID：${lesson.id}`,
        `学习路线：${lesson.track}`,
        `契约版本：${report.contract_version}`,
        `工具：${report.tool_name}`,
        `结果：${report.outcome === 'success' ? 'success（成功）' : 'rejected（校验拒绝）'}`,
      ].join('\n'),
    ),
    '### 原始参数 JSON',
    plainText(report.arguments_json),
    '### 实际工具结果',
    plainText(JSON.stringify(report.observation, null, 2)),
    '### 我的观察与下一步',
    plainText(reflection),
    '本次为固定课程工具实验；operation_id 用于追踪，不代表跨请求幂等。不会自动完成课程或语言实践。',
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
export function saveToolContractNote(
  progress: Progress,
  report: ToolContractReport,
  lesson: ToolContractLesson,
  reflection: string,
): Progress {
  const prepared = prepareToolContractNote(progress, report, lesson, reflection);
  if (prepared.status !== 'ready') return progress;
  return { ...progress, notes: { ...progress.notes, [lesson.id]: prepared.nextNote } };
}
