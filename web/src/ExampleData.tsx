import { useState } from 'react';
import { api, type ExamplesAdded } from './api';
import { plural, t, useLanguage } from './i18n';
import { serviceDay } from './format';
import { ask } from './confirm';

/** Ten fictional deliveries for `day`, with the example depot and vehicles if they are missing. */
export function addExamples(day: string) {
  return api<ExamplesAdded>('examples', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ service_date: day }),
  });
}

/** What was just added, for the notice after adding examples. */
export function addedMessage(added: ExamplesAdded) {
  return [
    t('{0} example deliveries added for {1}.', added.deliveries.length, serviceDay(added.service_date)),
    added.vehicles.length ? t('Example vehicles added: {0}.', added.vehicles.join(', ')) : '',
    t('Select them in Planning to try a calculation.'),
  ]
    .filter(Boolean)
    .join(' ');
}

/** Settings › Example data: add fictional deliveries to try the planner, or remove every example. */
export function ExampleSettings({
  present,
  day,
  onChanged,
}: {
  present: boolean;
  day: string;
  onChanged: (message: string) => void;
}) {
  useLanguage();
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [date, setDate] = useState(day);
  async function run(action: () => Promise<string>) {
    setBusy(true);
    setError('');
    try {
      onChanged(await action());
    } catch (e) {
      setError(e instanceof Error ? e.message : t('Request could not be completed'));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="workspace example-settings">
      <h2>{t('Example data')}</h2>
      <p>
        {t(
          'Fictional deliveries and vehicles in Paris, to try Tournevia with your own settings. They are marked Example, are planned only with other examples, and their route sheets and CSV files say they are not for driving.',
        )}
      </p>
      <p className={present ? 'info' : ''}>
        {present ? t('This workspace has example data.') : t('This workspace has no example data.')}
      </p>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="actions">
        <label className="example-day">
          {t('Day of the example deliveries')}
          <input
            type="date"
            required
            value={date}
            disabled={busy}
            onChange={(e) => setDate(e.target.value)}
          />
        </label>
        <button
          className="secondary"
          disabled={busy || !/^\d{4}-\d{2}-\d{2}$/.test(date)}
          onClick={() => void run(async () => addedMessage(await addExamples(date)))}
        >
          {t('Add example deliveries')}
        </button>
        {present && (
          <button
            className="danger-button"
            disabled={busy}
            onClick={() =>
              void (async () => {
                if (
                  !(await ask(t('Remove all example data?'), {
                    detail: t(
                      'Every example depot, vehicle and delivery is deleted, with the plans of example vehicles. Your own records stay.',
                    ),
                    confirm: t('Remove example data'),
                    cancel: t('Keep them'),
                  }))
                )
                  return;
                await run(async () => {
                  const { removed } = await api<{ removed: number }>('examples/remove', { method: 'POST' });
                  return plural(removed, '{0} example record removed.', '{0} example records removed.');
                });
              })()
            }
          >
            {t('Remove example data')}
          </button>
        )}
      </div>
    </section>
  );
}
