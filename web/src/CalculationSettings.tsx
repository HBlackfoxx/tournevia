import { useState } from 'react';
import { api } from './api';
import { t, useLanguage } from './i18n';

/**
 * The company's safety margin: every new calculation plans each delivery at least this many minutes
 * before its window ends. Saved plans keep the margin they were calculated with.
 */
export function CalculationSettings({ minutes, onChanged }: { minutes: number; onChanged: () => void }) {
  useLanguage();
  const [value, setValue] = useState(String(minutes)),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [saved, setSaved] = useState('');
  const chosen = Number(value);
  const valid = /^\d{1,3}$/.test(value.trim()) && chosen <= 120;
  return (
    <section className="workspace calculation-settings">
      <h2>{t('Calculation')}</h2>
      <p>{t('What every new calculation keeps to. Saved plans keep what they were calculated with.')}</p>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          if (!valid) return;
          setBusy(true);
          setError('');
          setSaved('');
          try {
            await api('workspace/safety-margin', {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ minutes: chosen }),
            });
            setSaved(t('New calculations keep {0} min before each window ends.', chosen));
            onChanged();
          } catch (e) {
            setError(e instanceof Error ? e.message : t('Request could not be completed'));
          } finally {
            setBusy(false);
          }
        }}
      >
        <label htmlFor="safety-margin">{t('Safety margin before each window ends')}</label>
        <span className="input-unit">
          <input
            id="safety-margin"
            type="number"
            inputMode="numeric"
            min={0}
            max={120}
            step={1}
            value={value}
            disabled={busy}
            aria-invalid={!valid}
            aria-describedby="safety-margin-hint"
            onChange={(e) => {
              setValue(e.target.value);
              setSaved('');
            }}
          />
          <span>{t('min')}</span>
        </span>
        <p id="safety-margin-hint" className="field-hint">
          {t(
            'Each delivery is planned to start at least this long before its window ends, so a delay on the road does not make it late. When the roads allow no such time, it is planned later within its window and its margin shows less. A delivery that can only be reached in its last minutes may stay unassigned: lower the margin to plan it. 0 plans up to the last minute.',
          )}
        </p>
        {!valid && (
          <p role="alert" className="error">
            {t('Enter whole minutes from 0 to 120.')}
          </p>
        )}
        <button disabled={busy || !valid || chosen === minutes}>
          {busy ? t('Saving…') : t('Save the safety margin')}
        </button>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        {saved && <p role="status">{saved}</p>}
      </form>
    </section>
  );
}
