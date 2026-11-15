import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import fixture from '../../backend/tests/fixtures/paris-ten.json';
import { SavedPlans } from './SavedPlans';
import { PlanningPanel } from './PlanningPanel';
import type { Plan, PlanListItem } from './api';

vi.mock('./MapPanel', () => ({ MapPanel: () => <div>Route map</div> }));
const day = fixture.service_date;
const summary = (id: string): PlanListItem => ({
  id,
  organization_id: fixture.organization_id,
  version: 7,
  service_date: day,
  created_at: `${day}T08:00:00Z`,
  updated_at: `${day}T09:00:00Z`,
  status: 'READY',
  validation: 'PASS',
  result_stale: false,
  active_job_id: null,
  finalized_at: `${day}T09:00:00Z`,
  served_deliveries: 8,
  unassigned_deliveries: 2,
  vehicles_used: 2,
  distance_m: 28_600,
  known_cost_minor: 20_100,
});
const ok = (data: unknown) => ({ ok: true, json: async () => data });

it('reopens a plan beyond 50 records with no recent job and resets history on a date change', async () => {
  const rows = Array.from({ length: 61 }, (_, i) => summary(`saved-${i}`));
  const plan = {
    ...rows[60],
    status: 'DRAFT',
    input_data: fixture,
    result: { routes: [], unassigned: [], blocking_issues: [], validation: 'PASS', dataset: 'Test' },
  } as unknown as Plan;
  const fetch = vi.fn(async (url: string) => {
    const parsed = new URL(url, 'http://localhost');
    if (parsed.pathname === '/api/v1/plans')
      return ok(
        rows.slice(
          Number(parsed.searchParams.get('offset')),
          Number(parsed.searchParams.get('offset')) + Number(parsed.searchParams.get('limit')),
        ),
      );
    if (parsed.pathname === '/api/v1/plans/saved-60') return ok(plan);
    return ok([]);
  });
  vi.stubGlobal('fetch', fetch);
  const props = {
    depot: fixture.depot,
    vehicles: fixture.vehicles,
    orders: fixture.orders,
    busy: false,
  } as unknown as Parameters<typeof PlanningPanel>[0];
  const view = render(<PlanningPanel view="saved" {...props} key={day} day={day} />);
  for (let page = 0; page < 3; page++) {
    await screen.findByTitle(`saved-${page * 20}`);
    fireEvent.click(screen.getByRole('button', { name: 'Older plans' }));
  }
  fireEvent.click(await screen.findByTitle('saved-60'));
  expect(await screen.findByText(/Saved synthetic draft · version 7/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Older plans' })).toBeDisabled();
  expect(screen.getByTitle('saved-60')).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(screen.getByRole('button', { name: 'Newer plans' }));
  await screen.findByTitle('saved-40');
  expect(screen.getByText(/Saved synthetic draft · version 7/)).toBeInTheDocument();
  view.rerender(<PlanningPanel view="saved" {...props} key="2026-09-27" day="2026-09-27" />);
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/plans?service_date=2026-09-27&limit=21&offset=0',
      expect.anything(),
    ),
  );
  expect(screen.queryByText(/Saved synthetic draft · version 7/)).not.toBeInTheDocument();
});

it('shows list failures, retries and ignores stale responses after a refresh', async () => {
  let resolveOld: (value: unknown) => void = () => {};
  const fetch = vi
    .fn()
    .mockRejectedValueOnce(new Error('History unavailable'))
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }),
    )
    .mockResolvedValueOnce(ok([summary('current')]));
  vi.stubGlobal('fetch', fetch);
  const select = vi.fn();
  const view = render(<SavedPlans day={day} refresh="1" onSelect={select} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('The server could not be reached');
  fireEvent.click(screen.getByRole('button', { name: 'Refresh saved plans' }));
  await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
  view.rerender(<SavedPlans day={day} refresh="2" onSelect={select} />);
  fireEvent.click(await screen.findByTitle('current'));
  expect(select).toHaveBeenCalledWith('current');
  resolveOld(ok([summary('obsolete')]));
  await waitFor(() => expect(fetch.mock.calls[1][1].signal.aborted).toBe(true));
  expect(screen.queryByTitle('obsolete')).not.toBeInTheDocument();
});

it('does not show an unrelated failed calculation on an older saved plan', async () => {
  const p = {
    ...summary('older'),
    status: 'DRAFT',
    input_data: fixture,
    result: { routes: [], unassigned: [], blocking_issues: [], validation: 'PASS', dataset: 'Test' },
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      if (url.includes('/jobs?'))
        return ok([
          {
            id: 'newer-job',
            status: 'FAILED',
            stage: 'FAILED',
            created_at: `${day}T10:00:00Z`,
            error: { code: 'UNRELATED', message: 'Other calculation failed' },
          },
        ]);
      if (url.includes('/plans?')) return ok([summary('older')]);
      return ok(p);
    }),
  );
  const props = {
    depot: fixture.depot,
    vehicles: fixture.vehicles,
    orders: fixture.orders,
    busy: false,
    day,
  } as unknown as Parameters<typeof PlanningPanel>[0];
  const view = render(<PlanningPanel {...props} view="saved" />);
  fireEvent.click(await screen.findByTitle('older'));
  await screen.findByText(/Saved synthetic draft/);
  view.rerender(<PlanningPanel {...props} />);
  expect(screen.queryByText(/Other calculation failed/)).not.toBeInTheDocument();
});

it('filters on the server by status and by plan or delivery ID, and shows the totals of each plan', async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    const urls: URL[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        urls.push(new URL(url, 'http://localhost'));
        return ok([summary('a1b2c3d4-0000-4000-8000-000000000001')]);
      }),
    );
    render(<SavedPlans day={day} refresh="" onSelect={vi.fn()} />);
    expect(await screen.findByText('8 of 10')).toBeInTheDocument();
    expect(screen.getByText(/29\skm/)).toBeInTheDocument();
    expect(screen.getByText('€201')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Ready' }));
    await waitFor(() => expect(urls.at(-1)!.searchParams.get('status')).toBe('READY'));
    fireEvent.change(screen.getByLabelText('Find the plans that include a delivery'), {
      target: { value: ' DEMO-ORDER-004 ' },
    });
    await vi.advanceTimersByTimeAsync(350);
    await waitFor(() => expect(urls.at(-1)!.searchParams.get('search')).toBe('DEMO-ORDER-004'));
    expect(urls.at(-1)!.searchParams.get('offset')).toBe('0');
  } finally {
    vi.useRealTimers();
  }
});

it('shows an update time only for a plan changed after it was made', async () => {
  const fresh = { ...summary('fresh'), updated_at: summary('fresh').created_at };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) =>
      ok(new URL(url, 'http://localhost').pathname === '/api/v1/plans' ? [summary('edited'), fresh] : []),
    ),
  );
  const props = {
    depot: fixture.depot,
    vehicles: fixture.vehicles,
    orders: fixture.orders,
    busy: false,
  } as unknown as Parameters<typeof PlanningPanel>[0];
  render(<PlanningPanel view="saved" {...props} day={day} />);
  const row = async (id: string) => (await screen.findByTitle(id)).closest('tr')!;
  expect(await row('fresh')).toHaveTextContent('Not changed');
  expect(await row('edited')).not.toHaveTextContent('Not changed');
  // Created at 10:00 and updated at 11:00, Paris time.
  expect(await row('edited')).toHaveTextContent(/2026, 10:00.*2026, 11:00/);
});
