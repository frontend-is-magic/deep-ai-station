// Test-only prop updates exercise the real lesson when the same ID receives a revised quiz.
import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Provider } from 'jotai';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import LessonPage from '../../src/pages/Lesson';
import type { Track } from '../../src/lib/types';
import '../../src/index.css';

declare global {
  interface Window {
    mountQuizReviewHarness: (input: { tracks: Track[]; lessonId: string }) => void;
    updateQuizReviewHarness?: (tracks: Track[]) => void;
  }
}

function Harness({ initialTracks }: { initialTracks: Track[] }) {
  const [tracks, setTracks] = useState(initialTracks);
  useEffect(() => {
    window.updateQuizReviewHarness = setTracks;
    return () => {
      delete window.updateQuizReviewHarness;
    };
  }, []);
  return (
    <Routes>
      <Route path="/lesson/:lessonId" element={<LessonPage tracks={tracks} />} />
    </Routes>
  );
}

const root = createRoot(document.getElementById('root')!);
window.mountQuizReviewHarness = ({ tracks, lessonId }) => {
  root.render(
    <StrictMode>
      <Provider>
        <MemoryRouter initialEntries={[`/lesson/${lessonId}`]}>
          <Harness initialTracks={tracks} />
        </MemoryRouter>
      </Provider>
    </StrictMode>,
  );
};
