import { useEffect, useState } from 'react';
import { useAtom } from 'jotai';
import { ArrowUpRight, Bookmark, Check, Radio, RefreshCw, Search } from 'lucide-react';
import { api } from '@/lib/api';
import type { FeedResponse, Track } from '@/lib/types';
import { progressAtom, toggleBookmark } from '@/lib/state';
import { formatDate } from '@/lib/utils';
import { ErrorPanel, Loading, PageHeading } from '@/components/common';
import { Button } from '@/components/ui/button';
import FeedCourseLink from '@/components/FeedCourseLink';

export default function Feed({ tracks }: { tracks: Track[] }) {
  const [feed, setFeed] = useState<FeedResponse | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [track, setTrack] = useState('all');
  const [query, setQuery] = useState('');
  const [onlySaved, setOnlySaved] = useState(false);
  const [progress, setProgress] = useAtom(progressAtom);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError('');
    api<FeedResponse>(`/feed?refresh=${refresh > 0}`, { signal: controller.signal })
      .then(setFeed)
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [refresh]);
  const available = onlySaved
    ? [
        ...new Map(
          [...(feed?.items || []), ...(progress.savedItems || [])].map((item) => [item.id, item]),
        ).values(),
      ]
    : feed?.items || [];
  const items =
    available.filter(
      (item) =>
        (track === 'all' || item.track === track) &&
        (!onlySaved || progress.bookmarks.includes(item.id)) &&
        (item.title + item.summary + item.tags.join(''))
          .toLowerCase()
          .includes(query.toLowerCase()),
    ) || [];
  return (
    <div className="page">
      <PageHeading
        eyebrow="DISCOVER / STAY CURIOUS"
        title="探索信息流"
        description="把值得关注的变化，连接到正在学习的知识。"
      >
        <Button variant="outline" disabled={loading} onClick={() => setRefresh((x) => x + 1)}>
          <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
          {loading && feed ? '正在同步' : '同步官方订阅'}
        </Button>
      </PageHeading>
      <div className="feed-intro">
        <Radio size={20} />
        <div>
          <strong>一手来源，保留出处</strong>
          <p>精选资料是常青文档；点击同步获取官方 RSS / Atom 内容。源不可用时保留精选资料。</p>
        </div>
        <span className="badge">
          {feed?.mode === 'live+curated' ? '已尝试实时同步' : '精选资料'}
        </span>
      </div>
      <div className="filter-bar">
        <div className="segmented-control">
          {[
            ['all', '全部'],
            ['agent', 'AI Agent'],
            ['fullstack', 'AI 全栈'],
          ].map(([id, label]) => (
            <button
              key={id}
              className={track === id ? 'selected' : ''}
              onClick={() => setTrack(id)}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="filter-actions">
          <div className="input-with-icon compact">
            <Search size={16} />
            <input
              aria-label="搜索信息流"
              placeholder="搜索主题…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <Button variant={onlySaved ? 'dark' : 'outline'} onClick={() => setOnlySaved((x) => !x)}>
            <Bookmark size={15} />
            已收藏
          </Button>
        </div>
      </div>
      {error && <ErrorPanel message={error} retry={() => setRefresh((x) => x + 1)} />}
      {loading && !feed ? (
        <Loading />
      ) : (
        <>
          <div className="feed-count">
            <span>
              {items.length} 条{onlySaved ? '已收藏' : '值得阅读的'}内容
            </span>
            <span>
              {feed?.sources.filter((x) => x.status === 'live' || x.status === 'cached').length ||
                0}{' '}
              / {feed?.sources.length || 0} 个实时源可用
            </span>
          </div>
          <div className="feed-grid">
            {items.map((item) => {
              const saved = progress.bookmarks.includes(item.id);
              return (
                <article className="feed-card" key={item.id}>
                  <div className="feed-card-meta">
                    <span className={`source-symbol ${item.track}`}>{item.source.slice(0, 1)}</span>
                    <strong>{item.source}</strong>
                    <span>{item.published ? formatDate(item.published) : '常青资料'}</span>
                    <button
                      className={`save-button ${saved ? 'saved' : ''}`}
                      aria-label={`${saved ? '取消收藏' : '收藏'}：${item.title}`}
                      disabled={!saved && progress.bookmarks.length >= 200}
                      title={
                        !saved && progress.bookmarks.length >= 200
                          ? '已达到 200 条收藏上限'
                          : undefined
                      }
                      onClick={() => setProgress((p) => toggleBookmark(p, item))}
                    >
                      {saved ? <Check size={17} /> : <Bookmark size={17} />}
                    </button>
                  </div>
                  <span className="feed-kind">
                    {item.kind === 'news' ? '官方动态' : '学习资料'} /{' '}
                    {item.track === 'agent' ? 'AGENT ENGINEERING' : 'FULLSTACK ENGINEERING'}
                  </span>
                  <a href={item.url} target="_blank" rel="noreferrer">
                    <h2>
                      {item.title}
                      <ArrowUpRight size={18} />
                    </h2>
                  </a>
                  <p>{item.summary}</p>
                  <div className="feed-tags">
                    {item.tags.map((tag) => (
                      <span key={tag}>{tag}</span>
                    ))}
                  </div>
                  <FeedCourseLink item={item} tracks={tracks} />
                </article>
              );
            })}
          </div>
          {!items.length && (
            <div className="state-panel">
              <Bookmark size={28} />
              <h2>这里还没有内容</h2>
              <p>
                {onlySaved
                  ? '收藏值得回看的资料，它们会出现在这里。'
                  : '调整筛选条件或搜索关键词。'}
              </p>
            </div>
          )}
          {feed && refresh > 0 && (
            <div className="source-status">
              <span>订阅源状态：</span>
              {feed.sources.map((source) => (
                <span key={source.id}>
                  <span
                    className={`tiny-dot ${source.status === 'unavailable' ? 'offline' : ''}`}
                  />
                  {source.name} ·{' '}
                  {source.status === 'unavailable'
                    ? source.cached
                      ? '暂不可用 · 保留缓存'
                      : '暂不可用'
                    : source.status === 'cached'
                      ? '缓存'
                      : source.status === 'live'
                        ? '已同步'
                        : '未请求'}
                </span>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
