import { t, useLanguage } from './i18n';
import { useEffect, useState } from 'react';
import { api, background, whenVisible, type SystemStatus } from './api';

export const SYSTEM_STATUS_POLL_MS = 30000;
/** Warns planners when calculations cannot start or finish. Stays silent if the status itself cannot be read. */
export function SystemStatusBanner() {
  useLanguage();
  const [status, setStatus] = useState<SystemStatus | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let alive = true,
      cancel = () => {};
    async function read() {
      try {
        const value = await api<SystemStatus>('system-status', {
          signal: controller.signal,
          headers: background,
        });
        if (alive) setStatus(value);
      } catch {
        if (alive) setStatus(null);
      } finally {
        if (alive) cancel = whenVisible(() => void read(), SYSTEM_STATUS_POLL_MS);
      }
    }
    void read();
    return () => {
      alive = false;
      controller.abort();
      cancel();
    };
  }, []);
  const worker = status?.worker === 'NOT_RUNNING',
    routing = status?.routing === 'UNAVAILABLE',
    waited = status?.oldest_queued_seconds ?? 0,
    queued = typeof waited === 'number' && waited > 60;
  if (!worker && !routing && !queued) return null;
  return (
    <div className={`system-status ${worker || routing ? 'error' : 'notice'}`}>
      {/* Only the stable service message is announced; the queue age below changes on every poll. */}
      <div role={worker || routing ? 'alert' : 'status'}>
        <strong>{t('Calculation service needs attention')}</strong>
        {worker && (
          <p>
            {t(
              'The calculation worker is not running. New calculations will wait in the queue until the worker is started.',
            )}
          </p>
        )}
        {routing && (
          <p>
            {t(
              'The routing service is unavailable. Calculations and road-access checks will fail until the routing service is running.',
            )}
          </p>
        )}
      </div>
      {queued && (
        <p>
          {t(
            '{0} calculation(s) queued; the oldest has waited {1} min.',
            status?.queued_jobs ?? 0,
            Math.max(1, Math.round(waited / 60)),
          )}
        </p>
      )}
    </div>
  );
}
