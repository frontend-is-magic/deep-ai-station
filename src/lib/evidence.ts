import type { EvidenceRecord, Language, Progress } from './types';

const fields = [
  'lesson_id',
  'language',
  'revision',
  'command',
  'success',
  'failure',
  'pending',
  'updated_at',
] as const;

export function validEvidenceRecord(value: unknown): value is EvidenceRecord {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const record = value as EvidenceRecord;
  return (
    Object.keys(record).length === fields.length &&
    Object.keys(record).every((field) => fields.includes(field as (typeof fields)[number])) &&
    typeof record.lesson_id === 'string' &&
    record.lesson_id.length <= 100 &&
    /^(agent|fullstack)-[a-z0-9-]+$/.test(record.lesson_id) &&
    ['typescript', 'go', 'python'].includes(record.language) &&
    (!record.lesson_id.startsWith('agent-') || record.language === 'python') &&
    typeof record.revision === 'string' &&
    record.revision.length <= 200 &&
    [record.command, record.success, record.failure, record.pending].every(
      (text) => typeof text === 'string' && text.length <= 2000,
    ) &&
    typeof record.updated_at === 'string' &&
    record.updated_at.length <= 40 &&
    Number.isFinite(Date.parse(record.updated_at))
  );
}

export function validEvidenceRecords(value: unknown): value is EvidenceRecord[] {
  return (
    Array.isArray(value) &&
    value.length <= 24 &&
    Array.from(value).every(validEvidenceRecord) &&
    new Set(value.map((record) => `${record.lesson_id}:${record.language}`)).size === value.length
  );
}

export function evidenceFor(
  progress: Progress,
  lessonId: string,
  language: Language,
): EvidenceRecord | undefined {
  if (!validEvidenceRecords(progress.evidence ?? [])) return undefined;
  return progress.evidence?.find(
    (record) => record.lesson_id === lessonId && record.language === language,
  );
}

export function saveEvidence(progress: Progress, record: EvidenceRecord): Progress {
  const previous = progress.evidence === undefined ? [] : progress.evidence;
  if (!validEvidenceRecord(record) || !validEvidenceRecords(previous)) return progress;
  const index = previous.findIndex(
    (item) => item.lesson_id === record.lesson_id && item.language === record.language,
  );
  if (index < 0 && previous.length >= 24) return progress;
  const evidence = [...previous];
  if (index < 0) evidence.push({ ...record });
  else evidence[index] = { ...record };
  return { ...progress, evidence };
}

function plainText(value: string): string {
  const longest = Math.max(0, ...Array.from(value.matchAll(/`+/g), (match) => match[0].length));
  const fence = '`'.repeat(Math.max(3, longest + 1));
  return `${fence}text\n${value.length ? value : '未填写'}\n${fence}`;
}

export function evidenceMarkdown(
  lesson: { id: string; title: string },
  record: EvidenceRecord,
): string {
  const languages: Record<Language, string> = {
    python: 'Python',
    typescript: 'TypeScript',
    go: 'Go',
  };
  return [
    '# 毕业实践证据',
    '这是学习者自行记录的实践证据，未经平台核验；保存或导出不会证明验收通过，也不会自动完成课程。请勿记录密钥、访问码或其他认证信息。',
    '## 来源课程',
    plainText(`${lesson.title}\n课程 ID：${lesson.id}`),
    '## 语言',
    plainText(languages[record.language]),
    '## 更新时间',
    plainText(record.updated_at),
    '## 源码版本',
    plainText(record.revision),
    '## 验证命令',
    plainText(record.command),
    '## 成功结果',
    plainText(record.success),
    '## 失败案例',
    plainText(record.failure),
    '## 未验证与阻塞',
    plainText(record.pending),
    '',
  ].join('\n\n');
}
