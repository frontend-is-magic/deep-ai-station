import { lazy, Suspense, useEffect, useState, useSyncExternalStore } from 'react';
import { useAtom } from 'jotai';
import { Link, NavLink, Route, Routes, useLocation } from 'react-router-dom';
import {
  ArrowUpRight,
  Bookmark,
  Bot,
  Braces,
  ChevronRight,
  Compass,
  FileText,
  FlaskConical,
  Github,
  Menu,
  Radio,
  Search,
  Settings2,
  Sparkles,
  X,
} from 'lucide-react';
import { api } from './lib/api';
import {
  emptyProgress,
  progressAtom,
  validateProgress,
  getStorageIssue,
  subscribeStorageIssue,
} from './lib/state';
import type { Track } from './lib/types';
import { Dialog } from './components/ui/dialog';
import { Button } from './components/ui/button';
import { ErrorPanel, Loading } from './components/common';
import Home from './pages/Home';
import Roadmap from './pages/Roadmap';
import LessonPage from './pages/Lesson';
import Feed from './pages/Feed';
const Playground = lazy(() => import('./pages/Playground'));
import Library from './pages/Library';
import Docs from './pages/Docs';

const navigation = [
  { to: '/', label: '学习总览', icon: Compass },
  { to: '/roadmap/agent', label: 'AI Agent 路线', icon: Bot },
  { to: '/roadmap/fullstack', label: 'AI 全栈路线', icon: Braces },
  { to: '/feed', label: '探索信息流', icon: Radio },
  { to: '/playground', label: 'Playground', icon: FlaskConical },
  { to: '/library', label: '我的学习库', icon: Bookmark },
];

export default function App() {
  const [tracks, setTracks] = useState<Track[]>([]);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [menu, setMenu] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [settings, setSettings] = useState(false);
  const [notice, setNotice] = useState('');
  const [progress, setProgress] = useAtom(progressAtom);
  const storageIssue = useSyncExternalStore(subscribeStorageIssue, getStorageIssue, () => null);
  const location = useLocation();
  useEffect(() => {
    const controller = new AbortController();
    setError('');
    api<{ tracks: Track[] }>('/curriculum', { signal: controller.signal })
      .then((x) => setTracks(x.tracks))
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [attempt]);
  useEffect(() => {
    setMenu(false);
    window.scrollTo(0, 0);
  }, [location.pathname]);
  useEffect(() => {
    const handle = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setSearchOpen((x) => !x);
      }
    };
    window.addEventListener('keydown', handle);
    return () => window.removeEventListener('keydown', handle);
  }, []);
  const lessons = tracks.flatMap((track) => track.lessons);
  const results = query.trim()
    ? lessons
        .filter((lesson) =>
          (lesson.title + lesson.objective + lesson.body.join(''))
            .toLowerCase()
            .includes(query.toLowerCase().trim()),
        )
        .slice(0, 8)
    : lessons.slice(0, 4);
  const current =
    navigation.find((nav) => nav.to === location.pathname)?.label ||
    (location.pathname.startsWith('/lesson/') ? '课程学习' : '项目文档');
  function exportProgress() {
    const blob = new Blob([JSON.stringify(progress, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'deep-ai-station-progress.json';
    link.click();
    URL.revokeObjectURL(url);
  }
  async function importProgress(file?: File) {
    if (!file) return;
    try {
      if (file.size > 2_000_000) throw new Error('文件过大');
      const data: unknown = JSON.parse(await file.text());
      if (!validateProgress(data)) throw new Error('学习记录格式不正确');
      setProgress(data);
      setNotice('学习记录已导入');
    } catch (e) {
      setNotice(e instanceof Error ? e.message : '无法导入文件');
    }
  }
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      {menu && (
        <button aria-label="关闭导航" className="mobile-overlay" onClick={() => setMenu(false)} />
      )}
      <aside id="workspace-sidebar" className={`sidebar ${menu ? 'is-open' : ''}`}>
        <Link to="/" className="brand">
          <span className="brand-mark">
            <span />
            <span />
            <span />
          </span>
          <span>
            Deep AI<span className="brand-sub">STATION</span>
          </span>
        </Link>
        <div className="workspace-label">
          <span className="tiny-dot" />
          个人学习空间<span className="label-beta">BETA</span>
        </div>
        <div className="nav-label">WORKSPACE</div>
        <nav aria-label="主导航">
          {navigation.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}
            >
              <Icon size={19} />
              <span>{label}</span>
              {to === '/playground' && <span className="nav-new">LAB</span>}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-tip">
            <Sparkles size={18} />
            <p>理解原理，动手构建。</p>
            <span>下一次突破，从一小步开始。</span>
          </div>
          <NavLink to="/docs" className="nav-item">
            <FileText size={18} />
            文档与开发指南
          </NavLink>
          <a
            className="nav-item"
            href="https://github.com/frontend-is-magic/deep-ai-station"
            target="_blank"
            rel="noreferrer"
          >
            <Github size={18} />
            GitHub
            <ArrowUpRight size={14} />
          </a>
          <button
            className="nav-item"
            onClick={() => {
              setNotice('');
              setSettings(true);
            }}
          >
            <Settings2 size={18} />
            学习偏好与数据
          </button>
          <div className="profile">
            <div className="avatar">D</div>
            <div>
              <strong>Builder</strong>
              <span>保持好奇，持续构建</span>
            </div>
            <span className="tiny-dot" />
          </div>
        </div>
      </aside>
      <div className="main-shell">
        {storageIssue && (
          <div className="storage-notice" role="alert">
            {storageIssue}
            <button onClick={() => setSettings(true)}>导出备份</button>
          </div>
        )}
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="mobile-menu"
              onClick={() => setMenu((x) => !x)}
              aria-label="打开导航"
            >
              {menu ? <X size={21} /> : <Menu size={21} />}
            </button>
            <span>Workspace</span>
            <ChevronRight size={14} />
            <strong>{current}</strong>
          </div>
          <div className="topbar-actions">
            <button className="search-trigger" onClick={() => setSearchOpen(true)}>
              <Search size={16} />
              <span>搜索课程与知识</span>
              <kbd>⌘ K</kbd>
            </button>
            <span className="service-status">
              <span className={`tiny-dot ${tracks.length ? '' : 'offline'}`} />
              {tracks.length ? '课程服务在线' : '连接课程服务'}
            </span>
          </div>
        </header>
        <main id="main-content">
          {error ? (
            <ErrorPanel message={error} retry={() => setAttempt((x) => x + 1)} />
          ) : !tracks.length ? (
            <Loading />
          ) : (
            <Routes>
              <Route path="/" element={<Home tracks={tracks} />} />
              <Route path="/roadmap/:trackId" element={<Roadmap tracks={tracks} />} />
              <Route path="/lesson/:lessonId" element={<LessonPage tracks={tracks} />} />
              <Route path="/feed" element={<Feed />} />
              <Route
                path="/playground"
                element={
                  <Suspense fallback={<Loading />}>
                    <Playground tracks={tracks} />
                  </Suspense>
                }
              />
              <Route path="/library" element={<Library tracks={tracks} />} />
              <Route path="/docs" element={<Docs />} />
              <Route
                path="*"
                element={
                  <div className="state-panel">
                    <h1>这里还没有页面</h1>
                    <Button asChild>
                      <Link to="/">返回学习总览</Link>
                    </Button>
                  </div>
                }
              />
            </Routes>
          )}
        </main>
        <footer className="footer">
          <span>DEEP AI STATION</span>
          <span>Built for the curious. Made for builders.</span>
          <span>学习 · 实验 · 交付</span>
        </footer>
      </div>
      <Dialog
        open={searchOpen}
        onOpenChange={setSearchOpen}
        title="找到下一步"
        description="搜索课程标题、目标与正文内容。"
      >
        <div className="input-with-icon">
          <Search size={18} />
          <input
            autoFocus
            aria-label="搜索课程"
            placeholder="试试 MCP、FastAPI、评测…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div className="search-results">
          {results.length ? (
            results.map((lesson) => (
              <Link
                to={`/lesson/${lesson.id}`}
                key={lesson.id}
                onClick={() => setSearchOpen(false)}
              >
                <span className="tag">{lesson.track === 'agent' ? 'AGENT' : 'FULLSTACK'}</span>
                <strong>{lesson.title}</strong>
                <span>{lesson.objective}</span>
              </Link>
            ))
          ) : (
            <p className="empty-text">没有找到相关课程，试试更短的关键词。</p>
          )}
        </div>
      </Dialog>
      <Dialog
        open={settings}
        onOpenChange={setSettings}
        title="你的学习空间"
        description="进度、笔记、收藏与最近 20 次运行记录保存在当前浏览器，可导出备份。"
      >
        <div className="settings-content">
          <div className="info-box">
            学习记录不会跨设备自动同步。实验访问码仅在当前页面内存中使用，不进入导出文件。
          </div>
          <p>
            已完成 {progress.completed.filter((id) => lessons.some((l) => l.id === id)).length} 节课
            · 已收藏 {progress.bookmarks.length} 条资料
          </p>
          <div className="flex flex-wrap gap-3">
            <Button onClick={exportProgress}>导出学习记录</Button>
            <label className="import-label">
              导入记录
              <input
                type="file"
                accept=".json,application/json"
                onChange={(e) => {
                  void importProgress(e.target.files?.[0]);
                  e.target.value = '';
                }}
              />
            </label>
          </div>
          <details>
            <summary>清空当前设备记录</summary>
            <p className="text-sm text-muted-foreground my-3">
              清空会删除当前浏览器中的进度、笔记、收藏和运行历史。建议先导出备份。
            </p>
            <Button
              variant="outline"
              onClick={() => {
                setProgress(emptyProgress);
                setNotice('当前设备学习记录已清空');
              }}
            >
              确认清空
            </Button>
          </details>
          {notice && <p role="status">{notice}</p>}
        </div>
      </Dialog>
    </div>
  );
}
