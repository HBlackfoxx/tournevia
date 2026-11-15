import { expect, it } from 'vitest';
import fr from './locales/fr.json';
import messages from './locales/server-messages.json';

// Generated from the backend by scripts/export_contracts.py (checked in CI with --check).
it('has a French translation for every server message the interface can show', () => {
  expect(messages.length).toBeGreaterThan(100);
  expect(messages.filter((message) => !(message in fr))).toEqual([]);
});

it('translates server messages that end with details by their fixed start', async () => {
  const { setLanguage, t } = await import('./i18n');
  setLanguage('fr');
  expect(t('Independent re-validation failed: TRK-01 exceeds payload.')).toBe(
    'La revalidation indépendante a échoué : TRK-01 exceeds payload.',
  );
  expect(t('Use a value of at most {0}.', 90)).toBe('Utilisez une valeur d’au plus 90.');
  expect(t('A string nobody translated.')).toBe('A string nobody translated.');
});
