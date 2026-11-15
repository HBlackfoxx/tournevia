import { t, useLanguage } from './i18n';
import { useEffect, useRef, useState } from 'react';
import type { components } from './api-types';
import { Workspace } from './Workspace';
import { api, ApiError, clearSessionData, setSession, type SessionRecord } from './api';
import { Login } from './AccountPanel';
import { Onboarding } from './Onboarding';
import { GuestLanguage, ThemeToggle } from './LanguageSettings';
import { Brand, Icon } from './Brand';
import { readSheetRoute, RouteSheetsPage } from './RouteSheets';

type Depot = components['schemas']['DepotRecord'];
type Configuration = components['schemas']['Configuration'];
type Health = components['schemas']['Health'];
type Readiness = components['schemas']['Readiness'];
type Snapshot = { depot: Depot; configuration: Configuration; session: SessionRecord };
type Status = 'checking' | 'ready' | 'failed' | 'signed-out' | 'setup';
/** Real keyboard/pointer use keeps the session alive at most this often; background polls never do. */
export const ACTIVITY_REFRESH_MS = 5 * 60 * 1000;

async function get<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`/api/v1/${path}`, { signal, credentials: 'same-origin' });
  if (!response.ok) throw new ApiError(response.status, `${path}: ${response.status}`);
  return response.json() as Promise<T>;
}

export function App() {
  useLanguage();
  const [attempt, setAttempt] = useState(0);
  const [status, setStatus] = useState<Status>('checking');
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [setupEnabled, setSetupEnabled] = useState(false),
    [setupMap, setSetupMap] = useState(''),
    [setupLookup, setSetupLookup] = useState<string | null>(null);
  const [expired, setExpired] = useState(false);
  // Set when this browser just completed first-use setup: the workspace opens its setup guide.
  const [welcome, setWelcome] = useState(false);
  // The route sheets of a READY plan open in their own tab, without the workspace around them.
  const [sheets, setSheets] = useState(() => readSheetRoute(window.location.hash));
  useEffect(() => {
    const follow = () => setSheets(readSheetRoute(window.location.hash));
    window.addEventListener('hashchange', follow);
    return () => window.removeEventListener('hashchange', follow);
  }, []);
  const current = useRef<Snapshot | null>(null);
  current.current = snapshot;

  useEffect(() => {
    // An open workspace stays mounted behind a sign-in dialog, so unsaved route moves,
    // the open plan and selections survive; without one, show the ordinary sign-in page.
    const onExpired = () => {
      if (current.current) setExpired(true);
      else {
        setSnapshot(null);
        setStatus('signed-out');
      }
    };
    window.addEventListener('vrp-session-expired', onExpired);
    return () => window.removeEventListener('vrp-session-expired', onExpired);
  }, []);

  const named = snapshot !== null && snapshot.configuration.mode !== 'local_demo';
  useEffect(() => {
    if (!named || expired) return;
    let last = Date.now();
    const active = () => {
      if (Date.now() - last < ACTIVITY_REFRESH_MS) return;
      last = Date.now();
      void api<SessionRecord>('session').catch(() => undefined); // A 401 here opens the sign-in dialog.
    };
    window.addEventListener('pointerdown', active);
    window.addEventListener('keydown', active);
    return () => {
      window.removeEventListener('pointerdown', active);
      window.removeEventListener('keydown', active);
    };
  }, [named, expired]);

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const timeout = window.setTimeout(() => controller.abort(), 8000);
    setStatus('checking');
    async function load() {
      try {
        const health = await get<Health>('health', controller.signal);
        const ready = await get<Readiness>('ready', controller.signal);
        if (health.status !== 'ok' || ready.status !== 'ready') throw new Error(t('Unavailable'));
        const setup = await get<components['schemas']['SetupStatus']>('setup', controller.signal);
        if (setup.required) {
          if (active) {
            setSnapshot(null);
            setSetupEnabled(setup.enabled);
            setSetupMap(setup.map_style_url ?? '');
            setSetupLookup(setup.address_lookup ?? null);
            setStatus('setup');
          }
          return;
        }
        const session = await api<SessionRecord>('session', { signal: controller.signal });
        if (!active) return;
        setSession(session);
        const [depot, configuration] = await Promise.all([
          get<Depot>('depot', controller.signal),
          get<Configuration>('configuration', controller.signal),
        ]);
        if (active) {
          setSnapshot({ depot, configuration, session });
          setStatus('ready');
        }
      } catch (error) {
        if (active) {
          if (error instanceof ApiError && error.status === 401) {
            clearSessionData();
            setSnapshot(null);
            setStatus('signed-out');
          } else setStatus('failed');
        }
      } finally {
        window.clearTimeout(timeout);
      }
    }
    void load();
    return () => {
      active = false;
      controller.abort();
      window.clearTimeout(timeout);
    };
  }, [attempt]);

  if (snapshot)
    return (
      <>
        <div className={sheets ? 'sheets-frame' : 'workspace-frame'} inert={expired || undefined}>
          {sheets ? (
            <RouteSheetsPage route={sheets} />
          ) : (
            <Workspace
              key={`${snapshot.session.identity.organization_id}:${snapshot.session.identity.user_id ?? 'local-demo'}`}
              depot={snapshot.depot}
              configuration={snapshot.configuration}
              role={snapshot.session.identity.role}
              session={snapshot.session}
              connection={status === 'checking' ? 'checking' : status === 'failed' ? 'failed' : 'ready'}
              onSignedOut={() => {
                setExpired(false);
                setSnapshot(null);
                setStatus('signed-out');
              }}
              onReconnect={() => setAttempt((n) => n + 1)}
              welcome={welcome}
            />
          )}
        </div>
        {expired && (
          <SessionExpired
            userId={snapshot.session.identity.user_id}
            onSignedIn={(record) => {
              setExpired(false);
              if (record.identity.user_id === snapshot.session.identity.user_id)
                setSnapshot({ ...snapshot, session: record });
              else {
                setSnapshot(null);
                setAttempt((n) => n + 1);
              }
            }}
          />
        )}
      </>
    );
  return (
    <div className="auth-shell">
      <header className="auth-header">
        <Brand />
        <span className="auth-context">
          {status === 'setup' ? (
            <>
              <Icon name="shield" /> {t('Secure installation setup')}
            </>
          ) : (
            t('Company workspace')
          )}
        </span>
        <span className="auth-tools">
          <ThemeToggle />
          <GuestLanguage />
        </span>
      </header>
      <main>
        {status === 'checking' && (
          <div className="connection-card" role="status">
            {t('Connecting to your workspace…')}
          </div>
        )}
        {status === 'failed' && (
          <section className="connection-card">
            <h1>{t('Workspace unavailable')}</h1>
            <p role="alert">
              {t(
                'The workspace could not connect. Check that the application and database services are running, then retry.',
              )}
            </p>
            <button onClick={() => setAttempt((n) => n + 1)}>{t('Retry connection')}</button>
          </section>
        )}
        {status === 'signed-out' && (
          <div className="login-layout">
            <div className="login-story">
              <h1>
                {t('A clear plan.')}
                <br />
                {t('A smoother day.')}
              </h1>
              <p>{t('Prepare deliveries, review routes and get the current plan ready for dispatch.')}</p>
              <p className="login-timezone">{t('Company workspace · Paris time')}</p>
            </div>
            <Login onSignedIn={() => setAttempt((n) => n + 1)} />
          </div>
        )}
        {status === 'setup' && (
          <Onboarding
            mapStyle={setupMap}
            enabled={setupEnabled}
            addressLookup={setupLookup}
            onComplete={() => {
              setWelcome(true);
              setAttempt((n) => n + 1);
            }}
          />
        )}
      </main>
      <footer>
        Tournevia Community · <a href="/assets/THIRD_PARTY_NOTICES.txt">{t('Third-party notices')}</a>
      </footer>
    </div>
  );
}

function SessionExpired({
  userId,
  onSignedIn,
}: {
  userId: string | null | undefined;
  onSignedIn: (record: SessionRecord) => void;
}) {
  const dialog = useRef<HTMLDivElement>(null);
  useEffect(() => {
    dialog.current?.querySelector<HTMLInputElement>('input')?.focus();
  }, []);
  return (
    <div className="session-overlay">
      <div
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="session-expired-title"
        className="session-dialog"
      >
        <h2 id="session-expired-title">{t('Your session has expired')}</h2>
        <p>
          {t(
            'Sign in again to continue. Your open plan and any unsaved route changes are still here; nothing was submitted while you were signed out.',
          )}
        </p>
        <Login resumeUserId={userId} onSignedIn={onSignedIn} />
      </div>
    </div>
  );
}
