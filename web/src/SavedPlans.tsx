import { t, useLanguage } from './i18n';
import { useEffect, useState } from 'react';
import { api, type PlanListItem } from './api';
import { clock, dateTime, euros, kilometres, serviceDay } from './format';

const pageSize = 20;
const checks: Record<string, string> = { PASS: 'Passed', FAIL: 'Failed', PENDING: 'Pending' };

export function SavedPlans({
  day,
  refresh,
  selected,
  onSelect,
}: {
  day: string;
  refresh: string;
  selected?: string;
  onSelect: (id: string) => void;
}) {
  useLanguage();
  const [filter, setFilter] = useState<'all' | 'READY' | 'DRAFT'>('all'),
    [query, setQuery] = useState(''),
    [search, setSearch] = useState('');
  const [offset, setOffset] = useState(0),
    [rows, setRows] = useState<PlanListItem[]>([]);
  // Filters run on the server, so a match on any page is found; typing settles before a request.
  useEffect(() => {
    const timer = setTimeout(() => setSearch(query.trim()), 300);
    return () => clearTimeout(timer);
  }, [query]);
  useEffect(() => setOffset(0), [filter, search, day]);
  const [busy, setBusy] = useState(true),
    [error, setError] = useState(''),
    [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setError('');
    const params = new URLSearchParams({
      service_date: day,
      limit: String(pageSize + 1),
      offset: String(offset),
    });
    if (filter !== 'all') params.set('status', filter);
    if (search) params.set('search', search);
    void api<PlanListItem[]>(`plans?${params}`, { signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) setRows(result);
      })
      .catch((e) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : t('Could not load saved plans.'));
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [day, offset, refresh, retry, filter, search]);
  return (
    <section aria-label={t('Saved plans')} className="saved-plans">
      <div className="section-heading">
        <h3>{t('Saved plans for {0}', serviceDay(day))}</h3>
        <button className="secondary" disabled={busy} onClick={() => setRetry((n) => n + 1)}>
          {t('Refresh saved plans')}
        </button>
      </div>
      <nav className="tabs" aria-label={t('Plan filters')}>
        {['all', 'READY', 'DRAFT'].map((state) => (
          <button
            key={state}
            aria-pressed={filter === state}
            onClick={() => setFilter(state as 'all' | 'READY' | 'DRAFT')}
          >
            {state === 'all' ? t('All plans') : state === 'READY' ? t('Ready') : t('Draft')}
          </button>
        ))}
      </nav>
      <label className="saved-plan-search">
        {t('Find the plans that include a delivery')}
        <input
          type="search"
          value={query}
          maxLength={100}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t('Exact delivery ID')}
        />
      </label>
      {busy ? (
        <p role="status">{t('Loading saved plans…')}</p>
      ) : error ? (
        <p role="alert" className="error">
          {error}
        </p>
      ) : (
        <>
          {!rows.length ? (
            <p>{t('No saved plans on this page.')}</p>
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>{t('Plan')}</th>
                    <th>{t('Created')}</th>
                    <th>{t('Updated')}</th>
                    <th>{t('Status')}</th>
                    <th>{t('Validation')}</th>
                    <th className="num">{t('Deliveries served')}</th>
                    <th className="num">{t('Vehicles')}</th>
                    <th className="num">{t('Distance')}</th>
                    <th className="num">{t('Modeled cost')}</th>
                    <th className="num">{t('Version')}</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.slice(0, pageSize).map((row) => (
                    <tr key={row.id} className={row.id === selected ? 'selected-stop' : ''}>
                      <td>
                        <button
                          className="secondary"
                          aria-pressed={row.id === selected}
                          title={row.id}
                          onClick={() => onSelect(row.id)}
                        >
                          {t('Plan of {0}', clock(row.created_at))}
                        </button>
                      </td>
                      <td>{dateTime(row.created_at)}</td>
                      {/* A plan never edited since it was made has no separate update to show. */}
                      <td>
                        {dateTime(row.updated_at) === dateTime(row.created_at) ? (
                          <>
                            <span aria-hidden="true">—</span>
                            <span className="sr-only">{t('Not changed')}</span>
                          </>
                        ) : (
                          dateTime(row.updated_at)
                        )}
                      </td>
                      <td>
                        <span className={`badge ${row.status === 'READY' ? 'ready' : ''}`}>
                          {row.status === 'READY' ? t('Ready') : t('Draft')}
                        </span>
                      </td>
                      <td>
                        {t(checks[row.validation] ?? row.validation)}
                        {row.active_job_id
                          ? t(' · recalculating')
                          : row.result_stale
                            ? t(' · stale result')
                            : ''}
                      </td>
                      <td className="num">
                        {t(
                          '{0} of {1}',
                          row.served_deliveries,
                          row.served_deliveries + row.unassigned_deliveries,
                        )}
                      </td>
                      <td className="num">{row.vehicles_used}</td>
                      <td className="num">{kilometres(row.distance_m)}</td>
                      <td className="num">{euros(row.known_cost_minor, 0)}</td>
                      <td className="num">{row.version}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
      <div className="info">
        <strong>{t('About saved plans')}</strong>
        <p>
          {t(
            'Reopening a plan does not change its review status. Editing clears Ready, and export is available only for the current eligible Ready result.',
          )}
        </p>
      </div>
      <div className="actions" hidden={offset === 0 && rows.length <= pageSize}>
        <button
          className="secondary"
          disabled={busy || offset === 0}
          onClick={() => setOffset((n) => Math.max(0, n - pageSize))}
        >
          {t('Newer plans')}
        </button>
        <span>{t('Page {0}', offset / pageSize + 1)}</span>
        <button
          className="secondary"
          disabled={busy || !!error || rows.length <= pageSize}
          onClick={() => setOffset((n) => n + pageSize)}
        >
          {t('Older plans')}
        </button>
      </div>
    </section>
  );
}
