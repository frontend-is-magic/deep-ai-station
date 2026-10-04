import type { Card } from './state';

export interface LessonCardProps {
  card: Card;
  completed: boolean;
  favorite: boolean;
  onCompletedChange: (value: boolean) => void;
  onFavoriteChange: (value: boolean) => void;
}
export function LessonCard({
  card,
  completed,
  favorite,
  onCompletedChange,
  onFavoriteChange,
}: LessonCardProps) {
  return (
    <article
      aria-label={card.title}
      data-card-id={card.id}
      className="panel flex min-w-0 flex-col gap-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs font-medium text-stone-500">
        <span className="font-mono">{card.id}</span>
        <span>{completed ? '已完成' : '待完成'}</span>
      </div>
      <h2 className="text-lg font-semibold leading-7">{card.title}</h2>
      <p className="flex-1 text-sm leading-7 text-stone-600">{card.description}</p>
      <div className="flex flex-wrap gap-3 border-t border-stone-100 pt-3">
        <label className="check-label">
          <input
            type="checkbox"
            checked={completed}
            onChange={(event) => onCompletedChange(event.target.checked)}
          />
          已完成
        </label>
        <label className="check-label">
          <input
            type="checkbox"
            checked={favorite}
            onChange={(event) => onFavoriteChange(event.target.checked)}
          />
          已收藏
        </label>
      </div>
    </article>
  );
}
