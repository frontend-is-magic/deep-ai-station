import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { Provider } from 'jotai';
import { App } from './App';
import { createLabState, type FactStorage } from './state';
import './style.css';

let storage: FactStorage | null = null;
try {
  storage = window.localStorage;
} catch {
  /* The memory-only model supplies the visible notice. */
}
// Initialize once per page, outside React rendering. StrictMode never writes facts on mount.
const state = createLabState(storage);
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Provider store={state.store}>
      <App state={state} />
    </Provider>
  </StrictMode>,
);
