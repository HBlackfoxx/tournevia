import { afterEach, expect, it } from 'vitest';
import style from './style.css?raw';
import screens from './screens.css?raw';
import { setTheme } from './theme';

function tokens(block: string | undefined) {
  return Object.fromEntries(
    [...(block ?? '').matchAll(/(--[a-z0-9-]+): ([^;]+);/g)].map((m) => [m[1], m[2]]),
  );
}
const light = tokens(/^:root \{\n {2}color-scheme: light;\n([\s\S]*?)\n\}/m.exec(style)?.[1]);
const systemDark = tokens(
  /:root:not\(\[data-theme='light'\]\) \{\n {4}color-scheme: dark;\n([\s\S]*?)\n {2}\}/.exec(style)?.[1],
);
const chosenDark = tokens(
  /^:root\[data-theme='dark'\] \{\n {2}color-scheme: dark;\n([\s\S]*?)\n\}/m.exec(style)?.[1],
);

afterEach(() => setTheme('system'));

it('defines every colour token in both themes, with one set of dark values', () => {
  expect(Object.keys(light).length).toBeGreaterThan(20);
  expect(chosenDark).toEqual(systemDark);
  expect(Object.keys(systemDark)).toEqual(Object.keys(light));
  const used = new Set([...`${style}\n${screens}`.matchAll(/var\((--[a-z0-9-]+)/g)].map((m) => m[1]));
  expect([...used].filter((name) => !(name in light))).toEqual([]);
});

it('applies a chosen appearance to the page and remembers it in this browser', () => {
  setTheme('dark');
  expect(document.documentElement.dataset.theme).toBe('dark');
  expect(localStorage.getItem('tournevia-theme')).toBe('dark');
  setTheme('system');
  expect(document.documentElement.dataset.theme).toBeUndefined();
  expect(localStorage.getItem('tournevia-theme')).toBeNull();
});
