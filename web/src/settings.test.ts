import { afterEach, expect, it, vi } from 'vitest';
import { readSetting, writeSetting } from './settings';

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

it('keeps a setting under the product name and forgets it with null', () => {
  expect(writeSetting('theme', 'dark')).toBe(true);
  expect(localStorage.getItem('tournevia-theme')).toBe('dark');
  expect(readSetting('theme')).toBe('dark');
  writeSetting('theme', null);
  expect(readSetting('theme')).toBeNull();
});

it('works without storage', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  expect(readSetting('language')).toBeNull();
  expect(writeSetting('language', 'fr')).toBe(false);
});
