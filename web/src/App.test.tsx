import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { savedVehicles } from './test-server';
import { describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { sessionExpired } from './api';
import fixture from '../../backend/tests/fixtures/paris-ten.json';

vi.mock('./MapPanel', () => ({ MapPanel: ({ style }: { style: string }) => <div>Map style: {style}</div> }));

function healthyFetch() {
  return vi.fn(
    async (url: string): Promise<{ ok: boolean; status?: number; json: () => Promise<unknown> }> => {
      const data = url.endsWith('/health')
        ? { status: 'ok' }
        : url.endsWith('/ready')
          ? { status: 'ready' }
          : url.endsWith('/setup')
            ? { required: false, enabled: false }
            : url.endsWith('/session')
              ? {
                  identity: {
                    user_id: null,
                    organization_id: 'company',
                    username: 'local-demo',
                    display_name: 'Local demo',
                    role: 'admin',
                  },
                  csrf_token: null,
                  expires_at: null,
                }
              : url.endsWith('/configuration')
                ? {
                    mode: 'local_demo',
                    data_kind: 'SYNTHETIC',
                    map_style_url: 'https://maps.example/style.json',
                  }
                : url.includes('/vehicles?') || url.includes('/orders?') || url.includes('/jobs?')
                  ? []
                  : {
                      id: 'demo',
                      organization_id: 'company',
                      version: 1,
                      data: {
                        name: 'Synthetic Paris depot',
                        code: 'PARIS-DEMO',
                        timezone: 'Europe/Paris',
                        location: { latitude: 48.835, longitude: 2.375, reviewed: true },
                      },
                    };
      return { ok: true, json: async () => data };
    },
  );
}

describe('workspace connection', () => {
  it('shows first-use setup before requesting company records or a session', async () => {
    const normal = healthyFetch();
    const fetch = vi.fn(async (url: string) =>
      url.endsWith('/setup')
        ? { ok: true, json: async () => ({ required: true, enabled: true }) }
        : normal(url),
    );
    vi.stubGlobal('fetch', fetch);
    render(<App />);
    expect(await screen.findByRole('heading', { name: 'Set up your company and depot' })).toBeInTheDocument();
    expect(fetch.mock.calls.map((c) => c[0])).not.toContain('/api/v1/session');
    expect(screen.queryByText('Synthetic Paris depot')).not.toBeInTheDocument();
  });

  it('shows the empty company with planning disabled until sources are selected and no demo banner', async () => {
    const normal = healthyFetch();
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.endsWith('/configuration'))
          return {
            ok: true,
            json: async () => ({
              mode: 'local',
              data_kind: 'UNVERIFIED',
              company_name: 'Acme Delivery',
              map_style_url: 'https://maps.example/style.json',
            }),
          };
        if (url.endsWith('/depot'))
          return {
            ok: true,
            json: async () => ({
              id: 'company-depot',
              version: 1,
              data: { name: 'Acme entrance', location: { latitude: 48.8, longitude: 2.3, reviewed: true } },
            }),
          };
        return normal(url);
      }),
    );
    render(<App />);
    expect(await screen.findByText('Acme Delivery')).toBeInTheDocument();
    expect(await screen.findByText('Add or import deliveries')).toBeInTheDocument();
    expect(screen.queryByText('Sample workspace.')).not.toBeInTheDocument();
    expect(screen.queryByText('Synthetic Paris depot')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open planning' }));
    expect(screen.getByRole('button', { name: 'Start new calculation' })).toBeDisabled();
  });
  it('requires sign-in in shared mode and exposes only planner controls after login', async () => {
    const normal = healthyFetch();
    let signedIn = false;
    const account = {
      identity: {
        user_id: 'planner-a',
        organization_id: 'company',
        username: 'planner',
        display_name: 'Planner A',
        role: 'planner',
      },
      csrf_token: 'token',
      expires_at: '2026-09-23T18:00:00Z',
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string, options: RequestInit = {}) => {
        if (url.endsWith('/session')) {
          if (options.method === 'POST') signedIn = true;
          return signedIn
            ? { ok: true, json: async () => account }
            : { ok: false, status: 401, json: async () => ({ detail: 'Sign in to continue.' }) };
        }
        if (url.endsWith('/configuration'))
          return {
            ok: true,
            json: async () => ({
              mode: 'shared',
              data_kind: 'SYNTHETIC',
              map_style_url: 'https://maps.example/style.json',
            }),
          };
        return normal(url);
      }),
    );
    render(<App />);
    fireEvent.change(await screen.findByLabelText('Username'), { target: { value: 'planner' } });
    expect(screen.queryByText('Synthetic Paris depot')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'synthetic passphrase' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByText('Planner A')).toBeInTheDocument();
    expect(await screen.findByText(/Synthetic Paris depot/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit depot' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Manage users' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Deliveries' }));
    expect(screen.getByRole('button', { name: 'Add delivery' })).toBeInTheDocument();
  });

  it.each([
    ['the same user', 'planner-a', true],
    ['another user', 'planner-b', false],
  ])(
    'keeps the open workspace behind a sign-in dialog when the session expires, resuming for %s',
    async (_who, nextUser, resumes) => {
      const normal = healthyFetch();
      let signedIn = 'planner-a';
      const account = (user_id: string) => ({
        identity: {
          user_id,
          organization_id: 'company',
          username: user_id,
          display_name: user_id === 'planner-a' ? 'Planner A' : 'Planner B',
          role: 'planner',
        },
        csrf_token: 'token',
        expires_at: '2026-09-23T18:00:00Z',
      });
      vi.stubGlobal(
        'fetch',
        vi.fn(async (url: string, options: RequestInit = {}) => {
          if (url.endsWith('/session')) {
            if (options.method === 'POST') signedIn = nextUser;
            return { ok: true, json: async () => account(signedIn) };
          }
          if (url.endsWith('/configuration'))
            return {
              ok: true,
              json: async () => ({
                mode: 'shared',
                data_kind: 'SYNTHETIC',
                map_style_url: 'https://maps.example',
              }),
            };
          return normal(url);
        }),
      );
      render(<App />);
      expect(await screen.findByText('Planner A')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Deliveries' }));
      expect(screen.getByRole('button', { name: 'Add delivery' })).toBeInTheDocument();
      sessionStorage.setItem('vrp-edit:plan', 'retry-key');
      act(() => sessionExpired());
      const dialog = await screen.findByRole('dialog', { name: 'Your session has expired' });
      // The workspace and its current view are still mounted underneath, but inert.
      expect(screen.getByRole('button', { name: 'Add delivery', hidden: true })).toBeInTheDocument();
      expect(sessionStorage.getItem('vrp-edit:plan')).toBe('retry-key');
      fireEvent.change(within(dialog).getByLabelText('Username'), { target: { value: nextUser } });
      fireEvent.change(within(dialog).getByLabelText('Password'), {
        target: { value: 'synthetic passphrase' },
      });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Sign in' }));
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
      if (resumes) {
        expect(screen.getByRole('button', { name: 'Add delivery' })).toBeInTheDocument();
        expect(sessionStorage.getItem('vrp-edit:plan')).toBe('retry-key');
      } else {
        expect(await screen.findByText('Planner B')).toBeInTheDocument();
        expect(sessionStorage.getItem('vrp-edit:plan')).toBeNull();
      }
    },
  );

  it('calls health and readiness before displaying the depot and configurable style', async () => {
    const fetch = healthyFetch();
    vi.stubGlobal('fetch', fetch);
    render(<App />);
    expect(await screen.findByText(/Synthetic Paris depot/)).toBeInTheDocument();
    expect(screen.getByText(/Connected/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    expect(
      (await screen.findAllByText('Map style: https://maps.example/style.json')).some(
        (el) => el.closest('[hidden]') === null,
      ),
    ).toBe(true);
    expect(screen.getByText('Sample workspace.')).toBeInTheDocument();
    expect(fetch.mock.calls.slice(0, 6).map((call) => call[0])).toEqual([
      '/api/v1/health',
      '/api/v1/ready',
      '/api/v1/setup',
      '/api/v1/session',
      '/api/v1/depot',
      '/api/v1/configuration',
    ]);
  });

  it('does not present liveness alone as a ready workspace and can retry', async () => {
    const fetch = healthyFetch();
    fetch.mockImplementationOnce(async () => ({ ok: true, json: async () => ({ status: 'ok' }) }));
    fetch.mockImplementationOnce(async () => ({ ok: false, json: async () => ({ status: 'unavailable' }) }));
    vi.stubGlobal('fetch', fetch);
    render(<App />);
    fireEvent.click(await screen.findByRole('button', { name: 'Retry connection' }));
    expect(await screen.findByText(/Synthetic Paris depot/)).toBeInTheDocument();
  });

  it('keeps the workspace on a failed connection check without claiming it is connected', async () => {
    const fetch = healthyFetch();
    vi.stubGlobal('fetch', fetch);
    render(<App />);
    await screen.findByText(/Synthetic Paris depot/);
    // Background source/job reads may race the check; fail its actual endpoint.
    const healthy = healthyFetch();
    fetch.mockImplementation(async (url) => {
      if (url.endsWith('/health')) throw new Error('offline');
      return healthy(url);
    });
    fireEvent.click(screen.getByRole('button', { name: 'Check connection' }));
    await screen.findByText('Connection check failed');
    expect(screen.getByText(/Synthetic Paris depot/)).toBeInTheDocument();
    expect(screen.queryByText(/Connected/)).not.toBeInTheDocument();
  });

  it('preserves the selected day, source selection and unsaved editor through reconnect and recovery', async () => {
    const normal = healthyFetch();
    let offline = false;
    const fetch = vi.fn(async (url: string) => {
      if (offline && url.endsWith('/health')) throw new Error('offline');
      if (url.includes('/vehicles?')) return { ok: true, json: async () => savedVehicles(fixture.vehicles) };
      if (url.includes('/orders?')) return { ok: true, json: async () => fixture.orders };
      return normal(url);
    });
    vi.stubGlobal('fetch', fetch);
    render(<App />);
    await screen.findByText('Connected');
    fireEvent.change(screen.getByLabelText('Planning date'), { target: { value: '2026-09-25' } });
    fireEvent.click(screen.getByRole('button', { name: 'Deliveries' }));
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Select DEMO-ORDER-001' }));
    fireEvent.click(screen.getByRole('button', { name: 'Edit DEMO-ORDER-001' }));
    fireEvent.change(screen.getByLabelText('Priority (0–100)'), { target: { value: '37' } });
    const originalInput = screen.getByLabelText('Priority (0–100)');
    offline = true;
    fireEvent.click(screen.getByRole('button', { name: 'Check connection' }));
    await screen.findByText('Connection check failed');
    expect(screen.getByLabelText('Priority (0–100)')).toBe(originalInput);
    expect(originalInput).toHaveValue(37);
    expect(screen.getByLabelText('Planning date')).toHaveValue('2026-09-25');
    offline = false;
    fireEvent.click(screen.getByRole('button', { name: 'Check connection' }));
    await waitFor(() => expect(screen.queryByText('Connection check failed')).not.toBeInTheDocument());
    expect(screen.getByLabelText('Priority (0–100)')).toBe(originalInput);
    expect(screen.getByRole('checkbox', { name: 'Select DEMO-ORDER-001' })).toBeChecked();
    expect(screen.getByLabelText('Planning date')).toHaveValue('2026-09-25');
    expect(
      screen.queryByText('Complete these first-use steps to calculate your first plan.'),
    ).not.toBeInTheDocument();
  });

  it('clears the retained workspace when reconnect finds an expired session', async () => {
    const fetch = healthyFetch();
    vi.stubGlobal('fetch', fetch);
    render(<App />);
    await screen.findByText('Connected');
    const normal = healthyFetch();
    fetch.mockImplementation(async (url) =>
      url.endsWith('/session')
        ? { ok: false, status: 401, json: async () => ({ detail: 'Sign in to continue.' }) }
        : normal(url),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Check connection' }));
    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Deliveries' })).not.toBeInTheDocument();
  });

  it('aborts pending requests when unmounted', async () => {
    const fetch = vi.fn((_url: string, _options: RequestInit) => new Promise(() => {}));
    vi.stubGlobal('fetch', fetch);
    const view = render(<App />);
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    view.unmount();
    expect(fetch.mock.calls[0][1].signal?.aborted).toBe(true);
  });
});
