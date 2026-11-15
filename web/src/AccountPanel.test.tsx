import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AccountPanel, Login } from './AccountPanel';
import { api, clearSessionData, setSession, type SessionRecord, type UserRecord } from './api';

const session: SessionRecord = {
  identity: {
    language: 'en',
    user_id: 'user-a',
    organization_id: 'company-a',
    username: 'planner',
    display_name: 'Planner A',
    role: 'planner',
  },
  csrf_token: 'csrf-value',
  expires_at: '2026-09-23T18:00:00Z',
};
afterEach(() => clearSessionData());

it.each([false, true])('renames an existing user while preserving permissions; self=%s', async (self) => {
  const admin = { ...session, identity: { ...session.identity, role: 'admin' as const } };
  let user: UserRecord = {
    id: self ? 'user-a' : 'user-b',
    organization_id: 'company-a',
    username: 'account',
    display_name: 'Old name',
    role: self ? 'admin' : 'planner',
    active: true,
  };
  const fetch = vi.fn(async (_url: string, options: RequestInit) => {
    if (options.method === 'PUT') {
      user = { ...user, ...JSON.parse(options.body as string) };
      return { ok: true, json: async () => user };
    }
    return { ok: true, json: async () => [user] };
  });
  vi.stubGlobal('fetch', fetch);
  const expired = vi.fn();
  window.addEventListener('vrp-session-expired', expired);
  setSession(admin);
  sessionStorage.setItem('vrp-edit:old', 'pending private work');
  render(<AccountPanel session={admin} onSignedOut={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Manage users' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Edit user account' }));
  fireEvent.change(await screen.findByLabelText('Display name for account'), {
    target: { value: 'Updated name' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save name for account' }));
  await waitFor(() => expect(fetch.mock.calls.some((c) => c[1].method === 'PUT')).toBe(true));
  expect(JSON.parse(fetch.mock.calls.find((c) => c[1].method === 'PUT')![1].body as string)).toEqual({
    display_name: 'Updated name',
    role: self ? 'admin' : 'planner',
    active: true,
  });
  if (self) {
    // Changing your own account revokes your sessions; sign in again to continue the same work.
    await waitFor(() => expect(expired).toHaveBeenCalledOnce());
    expect(sessionStorage.getItem('vrp-edit:old')).toBe('pending private work');
  } else {
    await screen.findByText('Updated name', { selector: 'strong' });
    expect(expired).not.toHaveBeenCalled();
  }
  window.removeEventListener('vrp-session-expired', expired);
});

it('asks before signing out over unsaved work', async () => {
  const fetch = vi.fn(async () => ({ ok: true, json: async () => ({ signed_out: true }) }));
  vi.stubGlobal('fetch', fetch);
  const signedOut = vi.fn();
  let allow = false;
  render(
    <AccountPanel
      compact
      session={session}
      onSignedOut={signedOut}
      canSignOut={() => Promise.resolve(allow)}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
  await act(async () => {});
  expect(fetch).not.toHaveBeenCalled();
  allow = true;
  fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
  await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
});

it('keeps sign-in credentials out of browser storage and uses the session CSRF token for later writes', async () => {
  const fetch = vi.fn(async () => ({ ok: true, json: async () => session }));
  vi.stubGlobal('fetch', fetch);
  const signed = vi.fn();
  render(<Login onSignedIn={signed} />);
  fireEvent.change(screen.getByLabelText('Username'), { target: { value: 'planner' } });
  fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'synthetic passphrase' } });
  fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
  await waitFor(() => expect(signed).toHaveBeenCalledOnce());
  expect(screen.getByLabelText('Password')).toHaveValue('');
  expect(JSON.stringify(sessionStorage)).not.toContain('csrf-value');
  await api('orders', { method: 'POST' });
  expect((fetch.mock.calls as unknown as [string, RequestInit][]).at(-1)?.[1].headers).toMatchObject({
    'X-CSRF-Token': 'csrf-value',
  });
});

it('hides user administration from planners and clears pending work only after confirmed logout', async () => {
  setSession(session);
  sessionStorage.setItem('vrp-edit:plan-a', 'private assignment');
  const fetch = vi.fn(async () => ({
    ok: false,
    status: 503,
    json: async () => ({ detail: 'Unavailable' }),
  }));
  vi.stubGlobal('fetch', fetch);
  const signedOut = vi.fn();
  render(<AccountPanel session={session} onSignedOut={signedOut} />);
  expect(screen.queryByRole('button', { name: 'Manage users' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
  await screen.findByRole('alert');
  expect(signedOut).not.toHaveBeenCalled();
  expect(sessionStorage.getItem('vrp-edit:plan-a')).toBeTruthy();
  fetch.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ detail: 'ok' }) });
  fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
  await waitFor(() => expect(signedOut).toHaveBeenCalledOnce());
  expect(sessionStorage.getItem('vrp-edit:plan-a')).toBeNull();
});

it('signals expiry and keeps retry data for the same user when an authenticated endpoint rejects the session', async () => {
  setSession(session);
  sessionStorage.setItem('vrp-pending:day', 'private selection');
  const expired = vi.fn();
  window.addEventListener('vrp-session-expired', expired);
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: false, status: 401, json: async () => ({ detail: 'Sign in' }) })),
  );
  await expect(api('vehicles')).rejects.toThrow('Sign in');
  expect(expired).toHaveBeenCalledOnce();
  expect(sessionStorage.getItem('vrp-pending:day')).toBe('private selection');
  window.removeEventListener('vrp-session-expired', expired);
});

it('renders the account name from the current session, including after identity changes', () => {
  const props = { compact: true, onSignedOut: vi.fn() };
  const view = render(<AccountPanel {...props} session={session} />);
  expect(screen.getByText('Planner A', { selector: 'strong' })).toBeVisible();
  expect(screen.queryByText('Temporary test administrator')).not.toBeInTheDocument();
  view.rerender(
    <AccountPanel
      {...props}
      session={{
        ...session,
        identity: { ...session.identity, display_name: 'Camille Dupont', role: 'admin' },
      }}
    />,
  );
  expect(screen.queryByText('Planner A')).not.toBeInTheDocument();
  expect(screen.getByText('Camille Dupont', { selector: 'strong' })).toBeVisible();
  expect(screen.getByText('Administrator')).toBeVisible();
});

it('lets an administrator set a new password for another user, but not for themselves', async () => {
  const admin = { ...session, identity: { ...session.identity, role: 'admin' as const } };
  const users: UserRecord[] = [
    {
      id: 'user-a',
      organization_id: 'company-a',
      username: 'me',
      display_name: 'Me',
      role: 'admin',
      active: true,
    },
    {
      id: 'user-b',
      organization_id: 'company-a',
      username: 'camille',
      display_name: 'Camille',
      role: 'planner',
      active: true,
    },
  ];
  const fetch = vi.fn(async (url: string, options: RequestInit = {}) =>
    options.method === 'POST'
      ? { ok: true, json: async () => ({ password_reset: true }) }
      : { ok: true, json: async () => (url.endsWith('users') ? users : {}) },
  );
  vi.stubGlobal('fetch', fetch);
  setSession(admin);
  render(<AccountPanel session={admin} onSignedOut={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Manage users' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Edit user me' }));
  expect(screen.queryByLabelText('New password for me')).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'Edit user camille' }));
  const field = screen.getByLabelText('New password for camille');
  expect(field).toHaveAttribute('minLength', '15');
  fireEvent.change(field, { target: { value: 'a long synthetic passphrase' } });
  fireEvent.click(screen.getByRole('button', { name: 'Set password for camille' }));
  expect(await screen.findByRole('status')).toHaveTextContent('New password set for camille');
  const call = fetch.mock.calls.find((c) => c[1]?.method === 'POST')!;
  expect(call[0]).toBe('/api/v1/users/user-b/password');
  expect(JSON.parse(call[1]!.body as string)).toEqual({ new_password: 'a long synthetic passphrase' });
  expect(screen.getByLabelText('New password for camille')).toHaveValue('');
});

it('asks before changing a role, since it signs that user out, and changes nothing when kept', async () => {
  const admin = { ...session, identity: { ...session.identity, role: 'admin' as const } };
  const camille: UserRecord = {
    id: 'user-b',
    organization_id: 'company-a',
    username: 'camille',
    display_name: 'Camille',
    role: 'planner',
    active: true,
  };
  const fetch = vi.fn(async (url: string, options: RequestInit = {}) =>
    options.method === 'PUT'
      ? { ok: true, json: async () => ({ ...camille, ...JSON.parse(options.body as string) }) }
      : { ok: true, json: async () => (url.endsWith('users') ? [camille] : {}) },
  );
  vi.stubGlobal('fetch', fetch);
  const answer = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
  setSession(admin);
  render(<AccountPanel session={admin} onSignedOut={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Manage users' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Edit user camille' }));
  const role = screen.getByLabelText('Role for camille');
  fireEvent.change(role, { target: { value: 'admin' } });
  await waitFor(() => expect(answer).toHaveBeenCalledTimes(1));
  expect(answer.mock.calls[0][0]).toContain('Make camille an administrator?');
  expect(fetch.mock.calls.some((c) => c[1]?.method === 'PUT')).toBe(false); // Kept.
  expect(role).toHaveValue('planner');
  fireEvent.change(role, { target: { value: 'admin' } });
  await waitFor(() => expect(fetch.mock.calls.some((c) => c[1]?.method === 'PUT')).toBe(true));
  const put = fetch.mock.calls.find((c) => c[1]?.method === 'PUT')!;
  expect(JSON.parse(put[1]!.body as string)).toMatchObject({ role: 'admin' });
  answer.mockRestore();
});
