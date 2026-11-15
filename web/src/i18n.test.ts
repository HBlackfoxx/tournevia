import { act } from 'react';
import { expect, it } from 'vitest';
import { plural, setLanguage, t } from './i18n';
import fr from './locales/fr.json';

const sources = import.meta.glob(['./*.tsx', './*.ts', '!./*.test.tsx', '!./*.test.ts', '!./api-types.ts'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

const resolved = (text: string) =>
  JSON.parse(`"${text.replace(/\\'/g, "'").replace(/(?<!\\)"/g, '\\"')}"`) as string;
/** First-argument string literals of t(...), and both wordings of plural(count, one, other), with
 * JavaScript escapes resolved. */
function literalKeys(source: string): string[] {
  const keys: string[] = [];
  for (const match of source.matchAll(/\bt\(\s*(['"])((?:\\.|(?!\1).)*)\1\s*[,)]/gs))
    keys.push(resolved(match[2]));
  for (const match of source.matchAll(
    /\bplural\([^;]*?,\s*(['"])((?:\\.|(?!\1).)*)\1\s*,\s*(['"])((?:\\.|(?!\3).)*)\3\s*\)/gs,
  ))
    keys.push(resolved(match[2]), resolved(match[4]));
  return keys;
}

it('has a French translation for every literal interface string', () => {
  const missing = Object.entries(sources).flatMap(([file, source]) =>
    literalKeys(source)
      .filter((key) => !(key in fr))
      .map((key) => `${file}: ${key}`),
  );
  expect(missing).toEqual([]);
});

it('keeps every translation placeholder of its English source', () => {
  const placeholders = (text: string) => [...text.matchAll(/\{\d+\}/g)].map((m) => m[0]).sort();
  const mismatched = Object.entries(fr as Record<string, string>).filter(
    ([english, french]) => JSON.stringify(placeholders(english)) !== JSON.stringify(placeholders(french)),
  );
  expect(mismatched).toEqual([]);
});

it('keeps French punctuation on the line of the word before it, and leaves filled-in values alone', () => {
  act(() => setLanguage('fr'));
  expect(t('Capacity:')).toBe('Capacité :');
  expect(t('{0} late', 'A : B')).toBe('en retard de A : B');
  act(() => setLanguage('en'));
  expect(t('Capacity:')).toBe('Capacity:');
});

it('counts the way each language does: French agrees with deliveries and keeps 0 singular', () => {
  act(() => setLanguage('fr'));
  expect(plural(10, '{0} delivery selected', '{0} deliveries selected')).toBe('10 livraisons sélectionnées');
  expect(plural(10, '{0} vehicle selected', '{0} vehicles selected')).toBe('10 véhicules sélectionnés');
  expect(plural(0, 'Add {0} vehicle', 'Add {0} vehicles')).toBe('Ajouter 0 véhicule');
  act(() => setLanguage('en'));
  expect(plural(0, 'Add {0} vehicle', 'Add {0} vehicles')).toBe('Add 0 vehicles');
});
