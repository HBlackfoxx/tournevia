import '@testing-library/jest-dom/vitest';
import { cleanup } from '@testing-library/react';
import { setLanguage } from './i18n';
import { setTheme } from './theme';
import { beforeEach, afterEach, vi } from 'vitest';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

beforeEach(() => setLanguage('en'));

// The workspace keeps its location in the URL; every test starts from a clean address.
afterEach(() => window.history.replaceState(null, '', '/'));
afterEach(() => setTheme('system'));
