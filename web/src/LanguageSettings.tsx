import { useState } from 'react';
import { api, type SessionRecord } from './api';
import { t, useLanguage, setLanguage, type Language } from './i18n';
import { setTheme, useDark, useTheme, type Theme } from './theme';
import { Icon } from './Brand';

export function LanguageSettings({ named }: { named: boolean }) {
  const language = useLanguage();
  const [chosen, setChosen] = useState(language),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [saved, setSaved] = useState(false);
  return (
    <section className="workspace">
      <h2>{t('Personal preferences')}</h2>
      <p>{t('Your language is saved to your account and used on all your devices.')}</p>
      <form
        className="language-setting"
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError('');
          setSaved(false);
          try {
            const result = await api<SessionRecord['identity']>('preferences', {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ language: chosen }),
            });
            setLanguage(result.language ?? 'en');
            setSaved(true);
          } catch {
            setError('Could not save your language. Try again.');
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          {t('Interface language')}
          <select
            disabled={busy || !named}
            value={chosen}
            onChange={(e) => {
              setChosen(e.target.value as Language);
              setSaved(false);
            }}
          >
            <option value="en">English</option>
            <option value="fr">Français</option>
          </select>
        </label>
        <button disabled={busy || !named}>{busy ? t('Saving…') : t('Save language')}</button>
        {error && (
          <p role="alert" className="error">
            {t(error)}
          </p>
        )}
        {saved && <p role="status">{t('Language saved.')}</p>}
      </form>
      <AppearanceSetting />
      {named && <PasswordSetting />}
    </section>
  );
}

/** The signed-in user's own password. This session stays; the account's other sessions end. */
function PasswordSetting() {
  const [current, setCurrent] = useState(''),
    [next, setNext] = useState(''),
    [repeat, setRepeat] = useState(''),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [saved, setSaved] = useState(false);
  return (
    <form
      className="password-setting"
      onSubmit={(e) => {
        e.preventDefault();
        setError('');
        setSaved(false);
        if (next !== repeat) {
          setError(t('The new password and its confirmation are different.'));
          return;
        }
        setBusy(true);
        void api('session/password', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ current_password: current, new_password: next }),
        })
          .then(() => {
            setNext('');
            setRepeat('');
            setSaved(true);
          })
          .catch((e) =>
            setError(e instanceof Error ? e.message : t('Could not change your password. Try again.')),
          )
          .finally(() => {
            setCurrent('');
            setBusy(false);
          });
      }}
    >
      <h3>{t('Password')}</h3>
      <label>
        {t('Current password')}
        <input
          type="password"
          autoComplete="current-password"
          value={current}
          maxLength={128}
          required
          disabled={busy}
          onChange={(e) => setCurrent(e.target.value)}
        />
      </label>
      <label>
        {t('New password')}
        <input
          type="password"
          autoComplete="new-password"
          value={next}
          minLength={15}
          maxLength={128}
          required
          disabled={busy}
          onChange={(e) => setNext(e.target.value)}
        />
      </label>
      <label>
        {t('Confirm new password')}
        <input
          type="password"
          autoComplete="new-password"
          value={repeat}
          minLength={15}
          maxLength={128}
          required
          disabled={busy}
          onChange={(e) => setRepeat(e.target.value)}
        />
      </label>
      <p className="field-hint">
        {t(
          'Use at least 15 characters. Your other signed-in devices are signed out when the password changes.',
        )}
      </p>
      <button disabled={busy}>{busy ? t('Saving…') : t('Change password')}</button>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {saved && <p role="status">{t('Password changed. Your other sessions were signed out.')}</p>}
    </form>
  );
}

/** Appearance applies at once and stays in this browser; the account is not changed. */
function AppearanceSetting() {
  const theme = useTheme();
  return (
    <div className="appearance-setting">
      <label>
        {t('Appearance')}
        <select value={theme} onChange={(e) => setTheme(e.target.value as Theme)}>
          <option value="system">{t('Match this device')}</option>
          <option value="light">{t('Light')}</option>
          <option value="dark">{t('Dark')}</option>
        </select>
      </label>
      <p>{t('Saved in this browser only. The map keeps its light style.')}</p>
    </div>
  );
}

/** One click between light and dark; Personal preferences can return to the device setting. */
export function ThemeToggle() {
  useLanguage();
  const dark = useDark();
  const label = dark ? t('Switch to light theme') : t('Switch to dark theme');
  return (
    <button
      type="button"
      className="text-button theme-toggle"
      aria-label={label}
      title={label}
      onClick={() => setTheme(dark ? 'light' : 'dark')}
    >
      <Icon name={dark ? 'sun' : 'moon'} />
    </button>
  );
}

/** Before login, a browser preference makes setup and authentication readable. */
export function GuestLanguage() {
  const language = useLanguage();
  return (
    <label className="guest-language">
      <span className="sr-only">{t('Interface language')}</span>
      <select
        aria-label={t('Interface language')}
        value={language}
        onChange={(e) => setLanguage(e.target.value as Language)}
      >
        <option value="en">English</option>
        <option value="fr">Français</option>
      </select>
    </label>
  );
}
