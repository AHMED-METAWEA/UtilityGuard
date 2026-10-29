import { useSyncExternalStore } from 'react';

export type ThemeChoice = 'light' | 'dark' | 'system';
const KEY = 'ug-theme';
const listeners = new Set<() => void>();
const media = typeof window !== 'undefined' ? window.matchMedia('(prefers-color-scheme: dark)') : null;

const read = (): ThemeChoice => {
  try { const v = localStorage.getItem(KEY); if (v === 'light' || v === 'dark' || v === 'system') return v; } catch { /* storage blocked */ }
  return 'system';
};
let choice: ThemeChoice = read();

export const resolve = (c: ThemeChoice = choice): 'light' | 'dark' => (c === 'system' ? (media?.matches ? 'dark' : 'light') : c);

export function applyTheme() {
  document.documentElement.dataset.theme = resolve();
  listeners.forEach((l) => l());
}
export function setTheme(next: ThemeChoice) {
  choice = next;
  try { localStorage.setItem(KEY, next); } catch { /* storage blocked */ }
  applyTheme();
}
media?.addEventListener('change', () => { if (choice === 'system') applyTheme(); });

const subscribe = (l: () => void) => { listeners.add(l); return () => listeners.delete(l); };
export const useTheme = () => ({
  choice: useSyncExternalStore(subscribe, () => choice),
  theme: useSyncExternalStore(subscribe, () => resolve()),
  setTheme,
});
