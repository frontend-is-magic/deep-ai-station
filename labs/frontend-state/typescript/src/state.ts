import { atom, createStore } from 'jotai/vanilla';

export const STORAGE_KEY = 'frontend-state-lab:v1';
export const cards = [
  {
    id: 'lesson-1',
    title: '用 Props 展示课程卡',
    description: '将标题、说明与状态传入可复用组件。',
  },
  { id: 'lesson-2', title: '用事件更新事实', description: '父组件处理明确的完成与收藏意图。' },
  {
    id: 'lesson-3',
    title: '用派生 atom 计算视图',
    description: '从事实计算统计和筛选结果，不保存重复数据。',
  },
] as const;
export type Card = (typeof cards)[number];
export type CardId = Card['id'];
export type Filter = 'all' | 'pending' | 'favorites';
export interface Facts {
  readonly version: 1;
  readonly completedIds: readonly CardId[];
  readonly favoriteIds: readonly CardId[];
}
export type FactStorage = Pick<Storage, 'getItem' | 'setItem'>;
export type StorageIssue = 'invalid' | 'unavailable' | 'write-failed' | null;
export const storageMessages: Record<Exclude<StorageIssue, null>, string> = {
  invalid: '本地记录格式无效，未采用；本页先使用空事实，操作后可覆盖。',
  unavailable: '浏览器存储不可用，本页仅使用内存；刷新可能丢失。',
  'write-failed': '写入浏览器存储失败，已保留本页内存；刷新可能丢失。',
};
const emptyFacts = (): Facts => ({ version: 1, completedIds: [], favoriteIds: [] });
const ordered = (ids: readonly CardId[]) =>
  cards.filter((card) => ids.includes(card.id)).map((card) => card.id);
function validIds(value: unknown): value is CardId[] {
  return (
    Array.isArray(value) &&
    value.length <= cards.length &&
    new Set(value).size === value.length &&
    value.every((id) => cards.some((card) => card.id === id))
  );
}
export function parseFacts(raw: string): Facts | null {
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const record = value as Record<string, unknown>;
    if (
      Object.keys(record).length !== 3 ||
      record.version !== 1 ||
      !validIds(record.completedIds) ||
      !validIds(record.favoriteIds)
    )
      return null;
    return {
      version: 1,
      completedIds: ordered(record.completedIds),
      favoriteIds: ordered(record.favoriteIds),
    };
  } catch {
    return null;
  }
}

export function createLabState(storage: FactStorage | null) {
  let initial = emptyFacts();
  let initialIssue: StorageIssue = null;
  if (!storage) initialIssue = 'unavailable';
  else {
    try {
      const raw = storage.getItem(STORAGE_KEY);
      if (raw !== null) {
        const parsed = parseFacts(raw);
        if (parsed) initial = parsed;
        else initialIssue = 'invalid';
      }
    } catch {
      initialIssue = 'unavailable';
    }
  }
  const store = createStore();
  // Only commands can change facts. Components and derived atoms read this projection.
  const factsStateAtom = atom<Facts>(initial);
  const factsAtom = atom((get) => get(factsStateAtom));
  const filterAtom = atom<Filter>('all');
  const storageIssueAtom = atom<StorageIssue>(initialIssue);
  const completedCountAtom = atom((get) => get(factsAtom).completedIds.length);
  const favoriteCountAtom = atom((get) => get(factsAtom).favoriteIds.length);
  const completionPercentAtom = atom((get) =>
    Math.round((get(completedCountAtom) / cards.length) * 100),
  );
  const visibleCardsAtom = atom((get) => {
    const facts = get(factsAtom);
    const filter = get(filterAtom);
    return cards.filter(
      (card) =>
        filter === 'all' ||
        (filter === 'pending'
          ? !facts.completedIds.includes(card.id)
          : facts.favoriteIds.includes(card.id)),
    );
  });
  function intentAtom(field: 'completedIds' | 'favoriteIds') {
    return atom(null, (get, set, intent: { id: CardId; value: boolean }) => {
      if (!cards.some((card) => card.id === intent.id) || typeof intent.value !== 'boolean') return;
      const previous = get(factsAtom);
      if (previous[field].includes(intent.id) === intent.value) return;
      const ids = intent.value
        ? [...previous[field], intent.id]
        : previous[field].filter((id) => id !== intent.id);
      const next: Facts = { ...previous, [field]: ordered(ids) };
      set(factsStateAtom, next);
      if (!storage) {
        set(storageIssueAtom, 'unavailable');
        return;
      }
      try {
        storage.setItem(STORAGE_KEY, JSON.stringify(next));
        set(storageIssueAtom, null);
      } catch {
        set(storageIssueAtom, 'write-failed');
      }
    });
  }
  const setCompletedAtom = intentAtom('completedIds');
  const setFavoriteAtom = intentAtom('favoriteIds');
  return {
    store,
    factsAtom,
    filterAtom,
    visibleCardsAtom,
    completedCountAtom,
    favoriteCountAtom,
    completionPercentAtom,
    storageIssueAtom,
    setCompletedAtom,
    setFavoriteAtom,
  };
}
export type LabState = ReturnType<typeof createLabState>;
