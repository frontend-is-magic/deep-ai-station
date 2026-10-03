import { useEffect, useState } from 'react';
import { useAtomValue } from 'jotai';
import { Link, useSearchParams } from 'react-router-dom';
import {
  ArrowRight,
  Bookmark,
  BookOpen,
  CheckCircle2,
  ExternalLink,
  StickyNote,
} from 'lucide-react';
import { api } from '@/lib/api';
import { progressAtom } from '@/lib/state';
import type { FeedResponse, Track } from '@/lib/types';
import { PageHeading } from '@/components/common';
import { Button } from '@/components/ui/button';
import FeedCourseLink from '@/components/FeedCourseLink';
import QuizReviewList from '@/components/QuizReviewList';

export default function Library({ tracks }: { tracks: Track[] }) {
  const progress = useAtomValue(progressAtom);
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get('tab');
  const tab = ['notes', 'bookmarks', 'quiz-review'].includes(requestedTab || '')
    ? requestedTab
    : 'completed';
  function selectTab(value: string) {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      if (value === 'completed') next.delete('tab');
      else next.set('tab', value);
      return next;
    });
  }
  const [feed, setFeed] = useState<FeedResponse | null>(null);
  useEffect(() => {
    const c = new AbortController();
    api<FeedResponse>('/feed', { signal: c.signal })
      .then(setFeed)
      .catch(() => {});
    return () => c.abort();
  }, []);
  const lessons = tracks.flatMap((t) => t.lessons);
  const completed = lessons.filter((l) => progress.completed.includes(l.id));
  const notes = lessons.filter((l) => progress.notes[l.id]?.trim());
  const saved = [
    ...new Map(
      [...(feed?.items || []), ...(progress.savedItems || [])].map((item) => [item.id, item]),
    ).values(),
  ].filter((item) => progress.bookmarks.includes(item.id));
  return (
    <div className="page">
      <PageHeading
        eyebrow="PERSONAL / YOUR KNOWLEDGE"
        title="我的学习库"
        description="把每一个小进步，累积成可以复用的能力。"
      />
      <div className="library-stats">
        <div>
          <CheckCircle2 size={23} />
          <strong>{completed.length}</strong>
          <span>已完成课时</span>
        </div>
        <div>
          <StickyNote size={23} />
          <strong>{notes.length}</strong>
          <span>课程笔记</span>
        </div>
        <div>
          <Bookmark size={23} />
          <strong>{progress.bookmarks.length}</strong>
          <span>收藏资料</span>
        </div>
      </div>
      <div className="segmented-control library-tabs max-w-full flex-wrap">
        {[
          ['completed', '完成记录'],
          ['notes', '我的笔记'],
          ['bookmarks', '收藏资料'],
          ['quiz-review', '测验回顾'],
        ].map(([id, name]) => (
          <button
            className={tab === id ? 'selected' : ''}
            aria-pressed={tab === id}
            key={id}
            onClick={() => selectTab(id)}
          >
            {name}
          </button>
        ))}
      </div>
      {tab === 'quiz-review' && <QuizReviewList tracks={tracks} />}
      {tab === 'completed' && (
        <div className="library-list">
          {completed.map((lesson) => (
            <Link to={`/lesson/${lesson.id}`} key={lesson.id}>
              <CheckCircle2 size={21} />
              <div>
                <span>{lesson.track === 'agent' ? 'AI Agent 工程' : 'AI 全栈工程'}</span>
                <h2>{lesson.title}</h2>
                <p>{lesson.objective}</p>
              </div>
              <ArrowRight size={18} />
            </Link>
          ))}
          {!completed.length && (
            <Empty
              icon={<BookOpen />}
              title="每一次完成，都值得记录"
              description="读完课程、完成实践并通过验收，你的进度会出现在这里。"
            />
          )}
        </div>
      )}
      {tab === 'notes' && (
        <div className="notes-grid">
          {notes.map((lesson) => (
            <article key={lesson.id}>
              <Link to={`/lesson/${lesson.id}`}>
                <h2>
                  {lesson.title}
                  <ArrowRight size={16} />
                </h2>
              </Link>
              <p>{progress.notes[lesson.id]}</p>
              <span>当前浏览器自动保存</span>
            </article>
          ))}
          {!notes.length && (
            <Empty
              icon={<StickyNote />}
              title="把理解写下来"
              description="在课程右侧记录思考，形成属于你的知识笔记。"
            />
          )}
        </div>
      )}
      {tab === 'bookmarks' && (
        <div className="library-list">
          {saved.map((item) => (
            <article className="library-bookmark" key={item.id}>
              <a
                className="library-bookmark-source"
                href={item.url}
                target="_blank"
                rel="noreferrer"
              >
                <Bookmark size={20} aria-hidden="true" />
                <div>
                  <span>{item.source}</span>
                  <h2>{item.title}</h2>
                  <p>{item.summary}</p>
                </div>
                <ExternalLink size={17} aria-hidden="true" />
              </a>
              <FeedCourseLink item={item} tracks={tracks} />
            </article>
          ))}
          {!saved.length && (
            <Empty
              icon={<Bookmark />}
              title="保存值得再读的资料"
              description="在信息流中收藏官方资料，建立你的阅读清单。"
            />
          )}
          {progress.bookmarks.length > saved.length && (
            <div className="info-box">
              部分早期收藏只保存了 ID。再次同步并取消、重新收藏后，资料摘要就能在这里随时查看。
            </div>
          )}
        </div>
      )}
    </div>
  );
}
function Empty({
  icon,
  title,
  description,
}: {
  icon: React.ReactNode;
  title: string;
  description: string;
}) {
  return (
    <div className="state-panel">
      {icon}
      <h2>{title}</h2>
      <p>{description}</p>
      <Button asChild>
        <Link to="/">
          开始学习
          <ArrowRight size={16} />
        </Link>
      </Button>
    </div>
  );
}
