import { useAtom, useAtomValue, useSetAtom } from 'jotai';
import { Button } from './components/ui/button';
import { LessonCard } from './LessonCard';
import { cards, storageMessages, type Filter, type LabState } from './state';

export function App({ state }: { state: LabState }) {
  const facts = useAtomValue(state.factsAtom);
  const [filter, setFilter] = useAtom(state.filterAtom);
  const visible = useAtomValue(state.visibleCardsAtom);
  const completedCount = useAtomValue(state.completedCountAtom);
  const favoriteCount = useAtomValue(state.favoriteCountAtom);
  const completionPercent = useAtomValue(state.completionPercentAtom);
  const issue = useAtomValue(state.storageIssueAtom);
  const setCompleted = useSetAtom(state.setCompletedAtom);
  const setFavorite = useSetAtom(state.setFavoriteAtom);
  const derived = {
    filter,
    visibleIds: visible.map((card) => card.id),
    completedCount,
    favoriteCount,
    completionPercent,
  };
  return (
    <main className="mx-auto max-w-5xl space-y-7 px-4 py-8 sm:px-6 sm:py-12">
      <header className="space-y-3">
        <p className="text-sm font-semibold tracking-wide text-lime-800">
          公共前端实验 · 三张卡，一份事实
        </p>
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">组件与派生状态实验</h1>
        <p className="max-w-3xl text-sm leading-7 text-stone-600">
          操作课程卡，观察 Props、父组件事件与 Jotai
          派生状态如何连起来。筛选只改变眼前的列表，全局统计始终来自三张卡的事实。
        </p>
      </header>
      <section aria-label="全局统计" className="grid grid-cols-3 gap-3">
        {[
          ['总完成数', `${completedCount} / ${cards.length}`],
          ['总收藏数', `${favoriteCount} / ${cards.length}`],
          ['全局完成率', `${completionPercent}%`],
        ].map(([label, value]) => (
          <div
            key={label}
            className="min-w-0 rounded-xl border border-stone-200 bg-white p-3 sm:p-5"
          >
            <p className="mb-2 text-xs text-stone-600 sm:text-sm">{label}</p>
            <output aria-label={label} className="text-2xl font-semibold tabular-nums sm:text-3xl">
              {value}
            </output>
          </div>
        ))}
      </section>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <label className="block w-full text-sm font-medium sm:max-w-xs">
          显示课程
          <select
            aria-label="显示课程"
            className="field mt-2"
            value={filter}
            onChange={(event) => setFilter(event.target.value as Filter)}
          >
            <option value="all">全部课程</option>
            <option value="pending">未完成课程</option>
            <option value="favorites">收藏课程</option>
          </select>
        </label>
        <p className="text-sm text-stone-500">
          当前可见 {visible.length} 张 · 全局分母 {cards.length} 张
        </p>
      </div>
      <section aria-label="课程卡片" className="grid min-w-0 gap-4 md:grid-cols-3">
        {visible.map((card) => (
          <LessonCard
            key={card.id}
            card={card}
            completed={facts.completedIds.includes(card.id)}
            favorite={facts.favoriteIds.includes(card.id)}
            onCompletedChange={(value) => setCompleted({ id: card.id, value })}
            onFavoriteChange={(value) => setFavorite({ id: card.id, value })}
          />
        ))}
        {visible.length === 0 && (
          <div className="panel space-y-4 md:col-span-3">
            <p>当前筛选没有课程</p>
            <Button onClick={() => setFilter('all')}>显示全部课程</Button>
          </div>
        )}
      </section>
      <p
        role="status"
        className={
          'rounded-lg p-4 text-sm leading-6 ' +
          (issue ? 'bg-amber-50 text-amber-900' : 'bg-lime-50 text-stone-700')
        }
      >
        {issue ? storageMessages[issue] : '只保存完成与收藏事实；筛选和统计由本页派生。'}
      </p>
      <section aria-label="事实与派生值" className="panel space-y-4">
        <h2 className="text-lg font-semibold">事实与派生值</h2>
        <p className="text-sm leading-7 text-stone-600">
          factsAtom
          读取唯一事实；visibleCardsAtom、completedCountAtom、favoriteCountAtom、completionPercentAtom
          计算视图。改变筛选后，对照右侧列表与全局统计。
        </p>
        <div className="grid min-w-0 gap-4 sm:grid-cols-2">
          <div className="min-w-0">
            <h3 className="mb-2 text-sm font-medium">持久事实 · factsAtom</h3>
            <pre aria-label="持久事实 JSON" tabIndex={0} className="json-panel">
              {JSON.stringify(facts, null, 2)}
            </pre>
          </div>
          <div className="min-w-0">
            <h3 className="mb-2 text-sm font-medium">派生视图 · 不持久化</h3>
            <pre aria-label="派生视图 JSON" tabIndex={0} className="json-panel">
              {JSON.stringify(derived, null, 2)}
            </pre>
          </div>
        </div>
      </section>
      <footer className="space-y-2 text-xs leading-6 text-stone-500">
        <p>
          本实验仅使用当前 origin 的 frontend-state-lab:v1
          键，不调用模型或后端。刷新恢复完成与收藏，筛选回到“全部课程”。
        </p>
        <p>
          这里的勾选不改变平台课程、实践或证据。延伸练习：阅读
          README，自行新增“已收藏且未完成”统计；初始版尚未实现，也不会自动评分。
        </p>
      </footer>
    </main>
  );
}
