import { useSyncExternalStore } from 'react';
import { readSetting, writeSetting } from './settings';

/** Appearance for this browser: follow the device setting, or force light or dark. */
export type Theme = 'system' | 'light' | 'dark';
const listeners = new Set<() => void>();
function stored(): Theme {
  // Without storage, or with nothing saved, the device setting applies.
  const saved = readSetting('theme');
  return saved === 'light' || saved === 'dark' ? saved : 'system';
}
let theme = stored();
function apply() {
  if (theme === 'system') delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
}
if (typeof document !== 'undefined') apply();
export function setTheme(value: Theme) {
  theme = value;
  apply();
  // Without storage the choice applies until the page is closed.
  writeSetting('theme', value === 'system' ? null : value);
  listeners.forEach((listener) => listener());
}
const media =
  typeof window !== 'undefined' && window.matchMedia
    ? window.matchMedia('(prefers-color-scheme: dark)')
    : null;
/** Whether the screen is dark now: the chosen theme, or the device setting when following it. */
export function useDark() {
  const chosen = useTheme();
  const device = useSyncExternalStore(
    (callback) => {
      media?.addEventListener('change', callback);
      return () => media?.removeEventListener('change', callback);
    },
    () => media?.matches ?? false,
    () => false,
  );
  return chosen === 'dark' || (chosen === 'system' && device);
}
export function useTheme() {
  return useSyncExternalStore(
    (callback) => {
      listeners.add(callback);
      return () => listeners.delete(callback);
    },
    () => theme,
  );
}
