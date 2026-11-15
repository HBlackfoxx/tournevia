import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { LanguageSettings, ThemeToggle } from './LanguageSettings';
import { setTheme } from './theme';
import { getLanguage, setLanguage, t } from './i18n';
import { setSession, type SessionRecord } from './api';
import { Workspace } from './Workspace';
import fixture from '../../backend/tests/fixtures/paris-ten.json';
import type { Depot, Order } from './api';
import { savedVehicles, sourceResponse } from './test-server';
vi.mock('./MapPanel', () => ({ MapPanel: () => null }));
const record: SessionRecord = {
  identity: {
    user_id: 'planner',
    organization_id: 'company',
    username: 'planner',
    display_name: 'Camille',
    role: 'planner',
    language: 'en',
  },
  csrf_token: 'token',
  expires_at: null,
};
afterEach(() => {
  act(() => setLanguage('en'));
  setSession(null);
});
it('saves the preference to the current account and uses it on the next login', async () => {
  setSession(record);
  const fetch = vi.fn(async () => ({ ok: true, json: async () => ({ ...record.identity, language: 'fr' }) }));
  vi.stubGlobal('fetch', fetch);
  render(<LanguageSettings named />);
  fireEvent.change(screen.getByLabelText('Interface language'), { target: { value: 'fr' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save language' }));
  expect(await screen.findByText('Langue enregistrée.')).toBeVisible();
  expect(document.documentElement.lang).toBe('fr');
  expect(fetch.mock.calls[0]).toEqual([
    '/api/v1/preferences',
    expect.objectContaining({
      method: 'PUT',
      body: '{"language":"fr"}',
      headers: expect.objectContaining({ 'X-CSRF-Token': 'token' }),
    }),
  ]);
  act(() => setSession(record));
  expect(getLanguage()).toBe('en');
  act(() => setSession({ ...record, identity: { ...record.identity, language: 'fr' } }));
  expect(getLanguage()).toBe('fr');
});
it('changes the appearance at once without touching the account', () => {
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  render(<LanguageSettings named />);
  fireEvent.change(screen.getByLabelText('Appearance'), { target: { value: 'dark' } });
  expect(document.documentElement.dataset.theme).toBe('dark');
  expect(screen.getByLabelText('Appearance')).toHaveValue('dark');
  fireEvent.change(screen.getByLabelText('Appearance'), { target: { value: 'system' } });
  expect(document.documentElement.dataset.theme).toBeUndefined();
  expect(fetch).not.toHaveBeenCalled();
});
it('does not change the active language when saving fails', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: false, status: 503, json: async () => ({ detail: 'Unavailable' }) })),
  );
  render(<LanguageSettings named />);
  fireEvent.change(screen.getByLabelText('Interface language'), { target: { value: 'fr' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save language' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not save your language');
  expect(getLanguage()).toBe('en');
});
it('keeps a dispatcher selection while switching language and does not expose administrator settings', async () => {
  sessionStorage.clear();
  setSession(record);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, options: RequestInit) => ({
      ok: true,
      json: async () =>
        path.endsWith('preferences')
          ? { ...record.identity, ...JSON.parse(String(options.body)) }
          : path.includes('/depot')
            ? fixture.depot
            : (sourceResponse(path, savedVehicles(fixture.vehicles), fixture.orders as Order[]) ?? []),
    })),
  );
  render(
    <Workspace
      role="planner"
      session={record}
      depot={fixture.depot as Depot}
      configuration={{
        mode: 'local',
        company_name: 'Example',
        data_kind: 'UNVERIFIED',
        examples: false,
        map_style_url: '',
        safety_margin_minutes: 15,
      }}
    />,
  );
  await screen.findByLabelText(fixture.orders[0].data.external_id);
  fireEvent.click(screen.getByRole('button', { name: 'Deliveries' }));
  fireEvent.click(screen.getByLabelText('Select ' + fixture.orders[0].data.external_id));
  fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
  expect(screen.queryByRole('button', { name: 'Users' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /^Depot$/ })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Interface language'), { target: { value: 'fr' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save language' }));
  await screen.findByText('Langue enregistrée.');
  fireEvent.click(screen.getByRole('button', { name: 'Livraisons' }));
  expect(screen.getByLabelText('Sélectionner ' + fixture.orders[0].data.external_id)).toBeChecked();
  expect(t('End must be after start.')).toBe('La fin doit être postérieure au début.');
  expect(t('Unknown server detail')).toBe('Unknown server detail');
});
it('sets the page language from the saved preference at startup', async () => {
  try {
    localStorage.setItem('tournevia-language', 'fr');
    document.documentElement.lang = 'en';
    vi.resetModules();
    const fresh = await import('./i18n');
    expect(fresh.getLanguage()).toBe('fr');
    expect(document.documentElement.lang).toBe('fr');
    expect(fresh.locale()).toBe('fr-FR');
  } finally {
    localStorage.removeItem('tournevia-language');
  }
});
it('changes the own password with the current one and explains a refusal', async () => {
  setSession(record);
  const fetch = vi.fn(async (_path: string, options: RequestInit) =>
    JSON.parse(String(options.body)).current_password === 'right current password'
      ? { ok: true, status: 200, json: async () => ({ password_changed: true }) }
      : {
          ok: false,
          status: 403,
          json: async () => ({ code: 'PASSWORD_INCORRECT', detail: 'Your current password is incorrect.' }),
        },
  );
  vi.stubGlobal('fetch', fetch);
  render(<LanguageSettings named />);
  const fill = (current: string, next: string, repeat = next) => {
    fireEvent.change(screen.getByLabelText('Current password'), { target: { value: current } });
    fireEvent.change(screen.getByLabelText('New password'), { target: { value: next } });
    fireEvent.change(screen.getByLabelText('Confirm new password'), { target: { value: repeat } });
    fireEvent.click(screen.getByRole('button', { name: 'Change password' }));
  };
  fill('right current password', 'a new synthetic passphrase', 'a different passphrase!');
  expect(screen.getByRole('alert')).toHaveTextContent('The new password and its confirmation are different.');
  expect(fetch).not.toHaveBeenCalled();
  fill('wrong', 'a new synthetic passphrase');
  expect(await screen.findByRole('alert')).toHaveTextContent('Your current password is incorrect.');
  expect(screen.getByLabelText('Current password')).toHaveValue('');
  fill('right current password', 'a new synthetic passphrase');
  expect(await screen.findByText('Password changed. Your other sessions were signed out.')).toBeVisible();
  expect(fetch.mock.calls.at(-1)).toEqual([
    '/api/v1/session/password',
    expect.objectContaining({ method: 'PUT', headers: expect.objectContaining({ 'X-CSRF-Token': 'token' }) }),
  ]);
});
it('offers no password change without a named account', () => {
  render(<LanguageSettings named={false} />);
  expect(screen.queryByLabelText('Current password')).toBeNull();
});

it('switches between light and dark in one click and remembers it', () => {
  render(<ThemeToggle />);
  fireEvent.click(screen.getByRole('button', { name: 'Switch to dark theme' }));
  expect(document.documentElement.dataset.theme).toBe('dark');
  expect(localStorage.getItem('tournevia-theme')).toBe('dark');
  fireEvent.click(screen.getByRole('button', { name: 'Switch to light theme' }));
  expect(document.documentElement.dataset.theme).toBe('light');
  setTheme('system');
});
