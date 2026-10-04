import { useEffect, useId, useRef, useState, useSyncExternalStore } from 'react';
import { useAtom, useStore } from 'jotai';
import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { feedCourseFor } from '@/lib/feed-course';
import {
  createFeedReadingDraft,
  FEED_READING_QUESTION_LIMIT,
  FEED_READING_UNDERSTANDING_LIMIT,
  prepareFeedReadingNote,
  saveFeedReadingNote,
  type FeedReadingDraft,
  type PreparedFeedReadingNote,
} from '@/lib/feed-reading-note';
import { editLatestProgress, RESET_NOTICE } from '@/lib/progress-write';
import { getStorageIssue, progressAtom, subscribeStorageIssue } from '@/lib/state';
import type { FeedItem, Track } from '@/lib/types';
import { languageNames } from '@/lib/utils';

type Editor = {
  draft: FeedReadingDraft;
  understanding: string;
  question: string;
  preview: string | null;
  phase: 'editing' | 'preview' | 'saved';
};
const sourceIdentity = (item: FeedItem) =>
  JSON.stringify([item.id, item.url, item.track, item.kind]);
const serverStorageIssue = () => null;
const SOURCE_NOTICE = '资料或配套课时已变更，本次摘记未保存；请重新打开填写。';
const CAPACITY_NOTICE =
  '笔记空间不足或摘记过长，本次未追加；已保留草稿和预览。每篇笔记最多 10,000 字符、最多 1,000 篇，请整理后再试。';

export default function FeedReadingNote({ item, tracks }: { item: FeedItem; tracks: Track[] }) {
  const [progress, setProgress] = useAtom(progressAtom);
  const store = useStore();
  const storageIssue = useSyncExternalStore(
    subscribeStorageIssue,
    getStorageIssue,
    serverStorageIssue,
  );
  const [editor, setEditor] = useState<Editor | null>(null);
  const [feedback, setFeedback] = useState('');
  const activeDraft = useRef<string | null>(null);
  const previewedEntry = useRef<string | null>(null);
  const consumed = useRef(false);
  const returnFocus = useRef(false);
  const formId = useId();
  const course = editor
    ? feedCourseFor(editor.draft.item, tracks, { ...progress, language: editor.draft.language })
    : undefined;
  const resetChanged = !!editor && progress.history_reset_id !== editor.draft.resetId;
  const invalidContext =
    !!editor &&
    (resetChanged ||
      sourceIdentity(item) !== sourceIdentity(editor.draft.item) ||
      !course ||
      course.lesson.id !== editor.draft.lessonId ||
      course.language !== editor.draft.language);
  const draftId = editor?.draft.id;

  useEffect(() => {
    if (!invalidContext || !draftId || activeDraft.current !== draftId) return;
    activeDraft.current = null;
    previewedEntry.current = null;
    setEditor(null);
    setFeedback(resetChanged ? RESET_NOTICE : SOURCE_NOTICE);
  }, [invalidContext, draftId, resetChanged]);

  useEffect(() => {
    if (draftId || !returnFocus.current) return;
    returnFocus.current = false;
    document.getElementById(`${formId}-open`)?.focus();
  }, [draftId, formId]);

  function close() {
    activeDraft.current = null;
    previewedEntry.current = null;
    consumed.current = false;
    returnFocus.current = true;
    setEditor(null);
    setFeedback('');
  }

  function invalidate(message: string) {
    activeDraft.current = null;
    previewedEntry.current = null;
    setEditor(null);
    setFeedback(message);
  }

  function begin() {
    const latest = editLatestProgress(store.get(progressAtom), (current) => current).progress;
    setProgress(latest);
    // IDs and time are created only for this explicit user action, never render/effect/retry.
    const draft = createFeedReadingDraft(
      item,
      tracks,
      latest,
      crypto.randomUUID(),
      new Date().toISOString(),
    );
    if (!draft) {
      invalidate(SOURCE_NOTICE);
      return;
    }
    activeDraft.current = draft.id;
    previewedEntry.current = null;
    consumed.current = false;
    setEditor({ draft, understanding: '', question: '', preview: null, phase: 'editing' });
    setFeedback('');
  }

  function change(field: 'understanding' | 'question', value: string) {
    previewedEntry.current = null;
    setFeedback('');
    setEditor((current) =>
      current && current.phase !== 'saved'
        ? { ...current, [field]: value, preview: null, phase: 'editing' }
        : current,
    );
  }

  function complete() {
    consumed.current = true;
    previewedEntry.current = null;
    setEditor((current) =>
      current
        ? { ...current, understanding: '', question: '', preview: null, phase: 'saved' }
        : current,
    );
    setFeedback('');
  }

  function preview() {
    if (!editor || consumed.current || activeDraft.current !== editor.draft.id) return;
    if (sourceIdentity(item) !== sourceIdentity(editor.draft.item)) {
      invalidate(SOURCE_NOTICE);
      return;
    }
    const checked = editLatestProgress(store.get(progressAtom), (current) => current, {
      resetId: editor.draft.resetId,
    });
    setProgress(checked.progress);
    if (checked.reset) {
      invalidate(RESET_NOTICE);
      return;
    }
    const prepared = prepareFeedReadingNote(
      checked.progress,
      tracks,
      editor.draft,
      editor.understanding,
      editor.question,
    );
    if (prepared.status === 'saved') {
      complete();
    } else if (prepared.status === 'invalid') {
      invalidate(SOURCE_NOTICE);
    } else if (prepared.status === 'missing-fields') {
      setFeedback('请填写“我的理解”和“准备验证的问题”，再预览将追加的内容。');
    } else if (prepared.entry) {
      previewedEntry.current = prepared.entry;
      setEditor({ ...editor, preview: prepared.entry, phase: 'preview' });
      setFeedback(prepared.status === 'too-long' ? CAPACITY_NOTICE : '');
    } else {
      setFeedback(CAPACITY_NOTICE);
    }
  }

  function save() {
    if (
      !editor ||
      editor.phase !== 'preview' ||
      !editor.preview ||
      consumed.current ||
      activeDraft.current !== editor.draft.id ||
      previewedEntry.current !== editor.preview
    )
      return;
    if (sourceIdentity(item) !== sourceIdentity(editor.draft.item)) {
      invalidate(SOURCE_NOTICE);
      return;
    }
    const outcome: { status: PreparedFeedReadingNote['status'] | 'reset' | 'changed' } = {
      status: 'invalid',
    };
    setProgress((previous) => {
      const checked = editLatestProgress(
        previous,
        (current) => {
          const prepared = prepareFeedReadingNote(
            current,
            tracks,
            editor.draft,
            editor.understanding,
            editor.question,
          );
          outcome.status = prepared.status;
          if (prepared.status !== 'ready') return current;
          if (prepared.entry !== editor.preview) {
            outcome.status = 'changed';
            return current;
          }
          return saveFeedReadingNote(
            current,
            tracks,
            editor.draft,
            editor.understanding,
            editor.question,
          );
        },
        { resetId: editor.draft.resetId },
      );
      if (checked.reset) outcome.status = 'reset';
      return checked.progress;
    });
    if (outcome.status === 'ready' || outcome.status === 'saved') {
      complete();
    } else if (outcome.status === 'reset') {
      invalidate(RESET_NOTICE);
    } else if (outcome.status === 'invalid') {
      invalidate(SOURCE_NOTICE);
    } else if (outcome.status === 'changed') {
      previewedEntry.current = null;
      setEditor({ ...editor, preview: null, phase: 'editing' });
      setFeedback('资料或课时说明已更新，请重新预览后追加。');
    } else {
      setFeedback(
        outcome.status === 'too-long' ? CAPACITY_NOTICE : '请填写两项个人摘记后重新预览。',
      );
    }
  }

  const active = editor && !invalidContext ? editor : null;
  const saved = active?.phase === 'saved';
  const message = invalidContext
    ? resetChanged
      ? RESET_NOTICE
      : SOURCE_NOTICE
    : saved
      ? storageIssue
        ? '已追加到本页内存，尚未保存到浏览器；请导出学习记录备份。'
        : '已追加到本课笔记，可随学习记录导出备份。'
      : feedback;

  return (
    <div className="mt-3 min-w-0">
      {!active ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={begin}
          id={`${formId}-open`}
          aria-expanded={false}
          aria-controls={formId}
        >
          写阅读摘记
        </Button>
      ) : (
        <section
          id={formId}
          aria-label="阅读摘记"
          data-source-id={active.draft.item.id}
          data-draft-id={active.draft.id}
          data-language={active.draft.language}
          className="min-w-0 space-y-4 rounded-lg border border-lime-200 bg-lime-50/40 p-3 sm:p-4"
        >
          <div className="min-w-0 space-y-1 text-xs leading-relaxed text-muted-foreground">
            <h3 className="text-sm font-semibold text-foreground">个人阅读摘记</h3>
            <p className="[overflow-wrap:anywhere]">资料：{active.draft.item.title}</p>
            <p className="[overflow-wrap:anywhere]">来源：{active.draft.item.source}</p>
            <p className="[overflow-wrap:anywhere]">配套课时：{course?.lesson.title}</p>
            <p>
              本条参考语言：{languageNames[active.draft.language]}
              {course?.sharedFrontend ? '（公共前端）' : ''}
            </p>
            <p>
              两项均为个人原文，不代表平台核验或实践完成。追加到当前浏览器的课程笔记；不要填写密钥或认证信息。
            </p>
          </div>
          {!saved && (
            <>
              <div>
                <label htmlFor={`${formId}-understanding`} className="block text-sm font-medium">
                  我的理解
                </label>
                <textarea
                  id={`${formId}-understanding`}
                  required
                  rows={3}
                  maxLength={FEED_READING_UNDERSTANDING_LIMIT}
                  value={active.understanding}
                  onChange={(event) => change('understanding', event.target.value)}
                  className="mt-2 block w-full min-w-0 rounded-lg border border-border bg-white p-3 text-sm"
                />
                <p className="mt-1 text-xs text-muted-foreground">
                  {active.understanding.length}/{FEED_READING_UNDERSTANDING_LIMIT} 字符 · 必填
                </p>
              </div>
              <div>
                <label htmlFor={`${formId}-question`} className="block text-sm font-medium">
                  准备验证的问题
                </label>
                <textarea
                  id={`${formId}-question`}
                  required
                  rows={3}
                  maxLength={FEED_READING_QUESTION_LIMIT}
                  value={active.question}
                  onChange={(event) => change('question', event.target.value)}
                  className="mt-2 block w-full min-w-0 rounded-lg border border-border bg-white p-3 text-sm"
                />
                <p className="mt-1 text-xs text-muted-foreground">
                  {active.question.length}/{FEED_READING_QUESTION_LIMIT} 字符 · 必填
                </p>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={preview}
                disabled={!active.understanding.trim() || !active.question.trim()}
                className="h-auto max-w-full whitespace-normal py-2 text-left"
              >
                预览将追加的内容
              </Button>
              {active.preview && (
                <pre
                  aria-label="阅读摘记预览"
                  className="max-w-full whitespace-pre-wrap break-words rounded-lg bg-white p-3 text-xs leading-relaxed [overflow-wrap:anywhere]"
                >
                  {active.preview}
                </pre>
              )}
            </>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <Button
              type="button"
              size="sm"
              onClick={save}
              disabled={saved || active.phase !== 'preview' || !active.preview}
              className="h-auto max-w-full whitespace-normal py-2 text-left"
            >
              {saved ? (storageIssue ? '已追加到本页内存' : '已追加到本课笔记') : '追加到本课笔记'}
            </Button>
            {saved && (
              <Button type="button" size="sm" variant="outline" onClick={begin}>
                再写一条
              </Button>
            )}
            <Button type="button" size="sm" variant="outline" onClick={close}>
              {saved ? '收起摘记' : '放弃草稿'}
            </Button>
            {course && (
              <Link to={course.href} className="text-xs underline underline-offset-2">
                去本课验证
              </Link>
            )}
            {saved && course && (
              <Link to={course.href} className="text-xs underline underline-offset-2">
                查看本课笔记
              </Link>
            )}
          </div>
          {!saved && (
            <p className="text-xs leading-relaxed text-muted-foreground">
              修改文字后须重新预览。草稿仅在本页保留，刷新会丢失。
            </p>
          )}
          <p role="status" className="break-words text-xs leading-relaxed text-muted-foreground">
            {message}
          </p>
        </section>
      )}
      {!active && (
        <p role="status" className="mt-2 break-words text-xs leading-relaxed text-muted-foreground">
          {message}
        </p>
      )}
    </div>
  );
}
