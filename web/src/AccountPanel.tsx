import { plural, t, useLanguage } from './i18n';
import { useEffect, useState } from 'react';
import {
  api,
  ApiError,
  clearSessionData,
  setSession,
  sessionExpired,
  type SessionRecord,
  type UserRecord,
} from './api';
import { usernameRule } from './formValues';
import { ask } from './confirm';
import { Icon } from './Brand';

export function Login({
  onSignedIn,
  resumeUserId,
}: {
  onSignedIn: (record: SessionRecord) => void;
  /** After a session expiry: the same user's private retry data is kept, anyone else's is cleared. */
  resumeUserId?: string | null;
}) {
  useLanguage();
  const [showPassword, setShowPassword] = useState(false);
  const [username, setUsername] = useState(''),
    [password, setPassword] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  return (
    <section className="workspace" aria-label={t('Sign in')}>
      <h2>{t('Sign in')}</h2>
      <p className="login-company">{t('Company workspace')}</p>
      {error && (
        <div role="alert" className="error">
          <strong>{t('Sign-in failed')}</strong>
          <p>{error}</p>
        </div>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setBusy(true);
          setError('');
          void api<SessionRecord>('session', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password }),
          })
            .then((record) => {
              if (!resumeUserId || record.identity.user_id !== resumeUserId) clearSessionData();
              setSession(record);
              setPassword('');
              onSignedIn(record);
            })
            .catch((e) => {
              setPassword('');
              setError(e instanceof Error ? e.message : t('Sign-in failed.'));
            })
            .finally(() => setBusy(false));
        }}
      >
        <label>
          {t('Username')}
          <input
            autoComplete="username"
            value={username}
            maxLength={80}
            required
            onChange={(e) => setUsername(e.target.value)}
          />
        </label>
        <label>
          {t('Password')}
          <span className="password-field">
            <input
              type={showPassword ? 'text' : 'password'}
              autoComplete="current-password"
              aria-label={t('Password')}
              value={password}
              maxLength={128}
              required
              onChange={(e) => setPassword(e.target.value)}
            />
            <button
              type="button"
              className="text-button"
              aria-label={showPassword ? t('Hide password') : t('Show password')}
              onClick={() => setShowPassword((v) => !v)}
            >
              <Icon name="eye" />
            </button>
          </span>
        </label>
        <button className="sign-in-button" disabled={busy}>
          {busy ? t('Signing in…') : t('Sign in')}
        </button>
        <p className="help login-recovery">
          {t('Contact your administrator if you cannot sign in.')}
          <br />
          {t('An administrator can set a new password for you.')}
        </p>
      </form>
    </section>
  );
}

export function AccountPanel({
  session,
  onSignedOut,
  compact = false,
  canSignOut = () => true,
}: {
  session: SessionRecord;
  onSignedOut: () => void;
  compact?: boolean;
  /** Lets the workspace ask before signing out over unsaved work. */
  canSignOut?: () => boolean | Promise<boolean>;
}) {
  useLanguage();
  const [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const [managing, setManaging] = useState(false);
  if (!session.identity.user_id) return null;
  return (
    <section className={compact ? 'account-compact' : 'workspace'} aria-label={t('Account')}>
      <div className="section-heading">
        <div className="account-identity">
          <span className="avatar">
            {(session.identity.display_name ?? t('User'))
              .split(/\s+/)
              .map((w) => w[0])
              .slice(0, 2)
              .join('')}
          </span>
          <div>
            <strong>{session.identity.display_name}</strong>
            <small>{session.identity.role === 'admin' ? t('Administrator') : t('Planner')}</small>
          </div>
        </div>
        <button
          className="secondary"
          disabled={busy}
          onClick={async () => {
            if (!(await canSignOut())) return;
            setBusy(true);
            setError('');
            void api('session', { method: 'DELETE' })
              .then(() => {
                clearSessionData();
                onSignedOut();
              })
              .catch((e) => {
                if (e instanceof ApiError && e.status === 401) {
                  clearSessionData();
                  onSignedOut();
                } else setError(e instanceof Error ? e.message : t('Sign-out failed; retry.'));
              })
              .finally(() => setBusy(false));
          }}
          title={t('Sign out')}
        >
          <Icon name="signout" />
          <span className="nav-label">{t('Sign out')}</span>
        </button>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {!compact && session.identity.role === 'admin' && (
        <>
          <button className="secondary" onClick={() => setManaging((v) => !v)}>
            {managing ? t('Close users') : t('Manage users')}
          </button>
          {managing && <Users currentId={session.identity.user_id} />}
        </>
      )}
    </section>
  );
}

export function Users({ currentId }: { currentId: string }) {
  useLanguage();
  const [editing, setEditing] = useState<string | null>(null),
    [adding, setAdding] = useState(false),
    [query, setQuery] = useState('');
  const [users, setUsers] = useState<UserRecord[]>([]),
    [reload, setReload] = useState(0),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [busy, setBusy] = useState(false);
  const [username, setUsername] = useState(''),
    [name, setName] = useState(''),
    [password, setPassword] = useState(''),
    [role, setRole] = useState<'admin' | 'planner'>('planner');
  useEffect(() => {
    const controller = new AbortController();
    void api<UserRecord[]>('users', { signal: controller.signal })
      .then(setUsers)
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [reload]);
  async function update(user: UserRecord, change: Partial<UserRecord>) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await api(`users/${user.id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          display_name: user.display_name,
          role: user.role,
          active: user.active,
          ...change,
        }),
      });
      if (user.id === currentId) {
        sessionExpired();
        return;
      }
      setReload((n) => n + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : t('User update failed.'));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div aria-label={t('Manage users')}>
      <section className="workspace users-card">
        <div className="section-heading">
          <div>
            <h2>{t('Users')}</h2>
            <p>{t('Accounts are created and managed by an administrator.')}</p>
          </div>
          <span className="badge">{t('Administrator only')}</span>
          <button className={adding ? 'secondary' : undefined} onClick={() => setAdding((v) => !v)}>
            {adding ? t('Cancel') : t('Add user')}
          </button>
        </div>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        {notice && <p role="status">{notice}</p>}
        <div className="users-search">
          <label>
            <span className="sr-only">{t('Search users')}</span>
            <input
              type="search"
              placeholder={t('Search name or username')}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <small>{plural(users.length, '{0} user', '{0} users')}</small>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{t('Display name')}</th>
                <th>{t('Username')}</th>
                <th>{t('Role')}</th>
                <th>{t('Status')}</th>
                <th>
                  <span className="sr-only">{t('Edit')}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {users
                .filter((u) => `${u.display_name} ${u.username}`.toLowerCase().includes(query.toLowerCase()))
                .map((u) => (
                  <tr key={u.id}>
                    <td>
                      <div className="user-name">
                        <span className="avatar">
                          {u.display_name
                            .split(/\s+/)
                            .map((w) => w[0])
                            .slice(0, 2)
                            .join('')}
                        </span>
                        <div>
                          <strong>{u.display_name}</strong>
                          {u.id === currentId && <small>{t('Signed in')}</small>}
                        </div>
                      </div>
                    </td>
                    <td>{u.username}</td>
                    <td>
                      <span className="badge">{u.role === 'admin' ? t('Administrator') : t('Planner')}</span>
                    </td>
                    <td>
                      <span className="badge">{u.active ? t('Active') : t('Disabled')}</span>
                    </td>
                    <td>
                      <button
                        className="secondary"
                        onClick={() => setEditing(editing === u.id ? null : u.id)}
                      >
                        {t('Edit user')} <span className="sr-only">{u.username}</span>
                      </button>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
        {users
          .filter((u) => u.id === editing)
          .map((u) => (
            <section className="user-edit" key={u.id}>
              <h3>
                {t('Edit')} {u.display_name}
              </h3>
              <p className="help">
                {t(
                  'Changing a name, role or active state signs this user out. Renaming yourself requires signing in again.',
                )}
              </p>
              <select
                aria-label={t('Role for {0}', u.username)}
                value={u.role}
                disabled={busy || u.id === currentId}
                onChange={async (e) => {
                  // A role change signs the user out at once, so it is confirmed like disabling.
                  const role = e.target.value as UserRecord['role'];
                  if (
                    await ask(
                      role === 'admin'
                        ? t('Make {0} an administrator?', u.username)
                        : t('Make {0} a planner?', u.username),
                      {
                        detail: t('Their sessions end immediately; they sign in again with the new role.'),
                        confirm: t('Change role'),
                        cancel: t('Cancel'),
                      },
                    )
                  )
                    void update(u, { role });
                }}
              >
                <option value="planner">{t('Planner')}</option>
                <option value="admin">{t('Administrator')}</option>
              </select>
              <button
                className="secondary"
                disabled={busy || u.id === currentId}
                onClick={async () => {
                  if (
                    !u.active ||
                    (await ask(t('Disable {0}?', u.username), {
                      detail: t('Their sessions end immediately. You can enable the account again later.'),
                      confirm: t('Disable user'),
                      cancel: t('Cancel'),
                    }))
                  )
                    void update(u, { active: !u.active });
                }}
              >
                {u.active ? t('Disable') : t('Enable')} {u.username}
              </button>
              <form
                key={u.display_name}
                onSubmit={(e) => {
                  e.preventDefault();
                  const display_name = String(new FormData(e.currentTarget).get('display_name') ?? '').trim();
                  if (display_name && display_name !== u.display_name) void update(u, { display_name });
                }}
              >
                <div className="actions">
                  <label>
                    {t('Display name for')} {u.username}
                    <input
                      name="display_name"
                      defaultValue={u.display_name}
                      required
                      maxLength={120}
                      pattern=".*\S.*"
                      disabled={busy}
                    />
                  </label>
                  <button className="secondary" disabled={busy}>
                    {t('Save name for')} {u.username}
                  </button>
                </div>
              </form>
              {u.id !== currentId && (
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    const form = e.currentTarget;
                    setBusy(true);
                    setError('');
                    setNotice('');
                    void api(`users/${u.id}/password`, {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({
                        new_password: String(new FormData(form).get('new_password') ?? ''),
                      }),
                    })
                      .then(() =>
                        setNotice(
                          t(
                            'New password set for {0}; their sessions ended. Give it to them through your normal company process.',
                            u.username,
                          ),
                        ),
                      )
                      .catch((e) => setError(e instanceof Error ? e.message : t('User update failed.')))
                      .finally(() => {
                        form.reset();
                        setBusy(false);
                      });
                  }}
                >
                  <div className="actions">
                    <label>
                      {t('New password for')} {u.username}
                      <input
                        name="new_password"
                        type="password"
                        autoComplete="new-password"
                        minLength={15}
                        maxLength={128}
                        required
                        disabled={busy}
                      />
                    </label>
                    <button className="secondary" disabled={busy}>
                      {t('Set password for')} {u.username}
                    </button>
                  </div>
                </form>
              )}
            </section>
          ))}
        <div hidden={!adding}>
          <h3>{t('Add a user')}</h3>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setError('');
              if (usernameRule(username)) {
                setError(t(usernameRule(username)));
                return;
              }
              setBusy(true);
              void api('users', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, display_name: name, password, role }),
              })
                .then(() => {
                  setUsername('');
                  setName('');
                  setPassword('');
                  setReload((n) => n + 1);
                })
                .catch((e) => {
                  setPassword('');
                  setError(e.message);
                })
                .finally(() => setBusy(false));
            }}
          >
            <div className="planning-columns">
              <label>
                {t('Display name')}
                <input value={name} maxLength={120} required onChange={(e) => setName(e.target.value)} />
              </label>
              <label>
                {t('Username')}
                <input
                  value={username}
                  maxLength={80}
                  autoComplete="off"
                  required
                  onChange={(e) => setUsername(e.target.value)}
                />
              </label>
              <label>
                {t('Password')}
                <input
                  type="password"
                  value={password}
                  minLength={15}
                  maxLength={128}
                  autoComplete="new-password"
                  required
                  onChange={(e) => setPassword(e.target.value)}
                />
              </label>
              <label>
                {t('Role')}
                <select value={role} onChange={(e) => setRole(e.target.value as 'admin' | 'planner')}>
                  <option value="planner">{t('Planner')}</option>
                  <option value="admin">{t('Administrator')}</option>
                </select>
              </label>
            </div>
            <button disabled={busy}>{t('Create user')}</button>
          </form>
        </div>
      </section>
      <section className="workspace">
        <h3>{t('Sign-in and password recovery')}</h3>
        <p>
          {t(
            'Share account instructions through your normal company process. Email invitations and social sign-in are not part of this workflow.',
          )}
        </p>
        <p>
          {t(
            'Users sign in with a username and password and change it in Personal preferences. If they forget it, an administrator sets a new one from Edit user. An administrator who cannot sign in needs another administrator or the installation operator.',
          )}
        </p>
        <hr />
        <small>{t('Private installation authorization does not appear in normal sign-in.')}</small>
      </section>
    </div>
  );
}
