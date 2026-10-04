import { useAtomValue } from 'jotai';
import { Link } from 'react-router-dom';
import { ArrowRight, BookOpen } from 'lucide-react';
import { feedCourseFor } from '@/lib/feed-course';
import { progressAtom } from '@/lib/state';
import type { FeedItem, Track } from '@/lib/types';
import { languageNames } from '@/lib/utils';
import FeedReadingNote from '@/components/FeedReadingNote';

export default function FeedCourseLink({ item, tracks }: { item: FeedItem; tracks: Track[] }) {
  const progress = useAtomValue(progressAtom);
  const course = feedCourseFor(item, tracks, progress);
  if (!course) return null;
  const language = languageNames[course.language];

  return (
    <div className="feed-course">
      <Link
        to={course.href}
        aria-label={`配套课程：${course.lesson.title}`}
        className="feed-course-link"
      >
        <BookOpen size={16} aria-hidden="true" />
        <span>配套课程：{course.lesson.title}</span>
        <ArrowRight size={16} aria-hidden="true" />
      </Link>
      <p className="feed-course-meta">
        <span>{course.sharedFrontend ? `公共前端 · ${language} 服务端参考` : language}</span>
        <span>{course.practiceRecorded ? '实践已记录' : '实践未记录'}</span>
      </p>
      <FeedReadingNote item={item} tracks={tracks} />
    </div>
  );
}
