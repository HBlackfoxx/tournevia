import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, it, expect, vi } from 'vitest';
import fixture from '../../backend/tests/fixtures/paris-ten.json';
import { PlanningPanel, JOB_POLL_ACTIVE_MS, JOB_POLL_IDLE_MS } from './PlanningPanel';
import type { Depot, Vehicle, Order, Job, Plan } from './api';
import { saved } from './test-server';
import { setLanguage } from './i18n';

const depot = { id: 'depot', version: 1, data: { location: { reviewed: true } } } as Depot;
const vehicles = [
  {
    ...saved({ ...fixture.vehicles[0], id: 'vehicle', version: 2 }),
    data: { ...saved(fixture.vehicles[0]).data, active: true, fleet_number: 'VAN-1' },
  },
] as Vehicle[];
const orders = [
  {
    id: 'order',
    version: 3,
    data: {
      ...fixture.orders[0].data,
      active: true,
      external_id: 'ORDER-1',
      location: { ...fixture.orders[0].data.location, reviewed: true },
    },
  },
] as Order[];
const job: Job = {
  id: 'job',
  organization_id: 'company',
  kind: 'OPTIMIZE',
  status: 'QUEUED',
  stage: 'QUEUED',
  created_at: '2026-09-23T08:00:00Z',
  started_at: null,
  finished_at: null,
  plan_id: null,
  error: null,
};
const props = { day: '2026-09-23', depot, vehicles, orders, busy: false };
const ok = (data: unknown) => ({ ok: true, json: async () => data });
vi.mock('./MapPanel', () => ({ MapPanel: () => <div>Route map</div> }));
beforeEach(() => sessionStorage.clear());
function selectSources() {
  fireEvent.click(screen.getByLabelText('VAN-1'));
  fireEvent.click(screen.getByLabelText('ORDER-1'));
}

describe('durable calculation UI', () => {
  it('names a selected vehicle that does not work that day and sends the dispatcher to Fleet', () => {
    // 23 September 2026 is a Wednesday: a day off that date, then no Wednesday hours at all.
    const dayOff = {
      ...vehicles[0],
      data: { ...vehicles[0].data, exceptions: [{ date: '2026-09-23', hours: null }] },
    } as Vehicle;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ok([])),
    );
    const fleet = vi.fn();
    const view = render(<PlanningPanel {...props} vehicles={[dayOff]} onFleet={fleet} />);
    selectSources();
    expect(screen.getByText('1 selected vehicle does not work that day')).toBeVisible();
    expect(screen.getByText('VAN-1 · day off')).toBeVisible();
    expect(screen.getByText('Start new calculation')).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Open Fleet' }));
    expect(fleet).toHaveBeenCalledOnce();
    view.unmount();
    const noWednesday = {
      ...vehicles[0],
      data: { ...vehicles[0].data, hours: { ...vehicles[0].data.hours, wednesday: null } },
    } as Vehicle;
    render(<PlanningPanel {...props} vehicles={[noWednesday]} />);
    selectSources();
    expect(screen.getByText('VAN-1 · no hours on Wednesday')).toBeVisible();
  });
  it('submits all 250 reviewed selections across five pages, including saved off-page versions', async () => {
    const fetch = vi.fn(async (_url: string, options: RequestInit) =>
      ok(options.method === 'POST' ? job : []),
    );
    vi.stubGlobal('fetch', fetch);
    const all = Array.from({ length: 251 }, (_, i) => ({
      ...orders[0],
      id: `order-${i}`,
      version: i + 1,
      data: { ...orders[0].data, external_id: `ORDER-${i}` },
    }));
    const view = render(<PlanningPanel {...props} orders={all.slice(0, 50)} />);
    fireEvent.click(screen.getByText('Select active vehicles on this page'));
    for (let page = 0; page < 5; page++) {
      view.rerender(<PlanningPanel {...props} orders={[]} vehicles={[]} busy />);
      expect(screen.getByText('Start new calculation')).toBeDisabled();
      view.rerender(<PlanningPanel {...props} orders={all.slice(page * 50, page * 50 + 50)} />);
      fireEvent.click(screen.getByText('Select reviewed deliveries on this page'));
    }
    expect(screen.getByText(/250 \/ 250 deliveries selected across pages/)).toBeInTheDocument();
    view.rerender(<PlanningPanel {...props} orders={all.slice(250)} />);
    expect(screen.getByLabelText('ORDER-250')).toBeDisabled();
    expect(screen.getByText('Select reviewed deliveries on this page')).toBeDisabled();
    fireEvent.click(screen.getByText('Start new calculation'));
    await waitFor(() => expect(fetch.mock.calls.some((c) => c[1].method === 'POST')).toBe(true));
    const sent = JSON.parse(fetch.mock.calls.find((c) => c[1].method === 'POST')![1].body as string);
    expect(sent.order_ids).toEqual(all.slice(0, 250).map((o) => o.id));
    expect(sent.source_versions).toEqual(
      Object.fromEntries([depot, ...vehicles, ...all.slice(0, 250)].map((r) => [r.id, r.version])),
    );
    fireEvent.click(screen.getByText('Clear delivery selection'));
    expect(screen.getByText('Start new calculation')).toBeDisabled();
  });
  it('refreshes visible selected records and rejects inactive sources or too many truck profiles', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ok([])),
    );
    const fleet = Array.from({ length: 26 }, (_, i) => ({
      ...vehicles[0],
      id: `v-${i}`,
      data: { ...vehicles[0].data, fleet_number: `V-${i}`, routing: { height_mm: 2000 + (i % 4) } },
    })) as Vehicle[];
    const view = render(<PlanningPanel {...props} vehicles={fleet.slice(0, 25)} />);
    fireEvent.click(screen.getByText('Select active vehicles on this page'));
    fireEvent.click(screen.getByLabelText('ORDER-1'));
    expect(screen.getByText('Select vehicles with at most three different truck profiles.')).toBeVisible();
    expect(screen.getByText('Start new calculation')).toBeDisabled();
    view.rerender(<PlanningPanel {...props} vehicles={fleet.slice(25)} />);
    expect(screen.getByLabelText('V-25')).toBeDisabled();
    fireEvent.click(screen.getByText('Clear vehicle selection'));
    fireEvent.click(screen.getByLabelText('V-25'));
    expect(screen.getByText('Start new calculation')).toBeEnabled();
    view.rerender(
      <PlanningPanel
        {...props}
        vehicles={fleet.slice(25)}
        orders={[{ ...orders[0], version: 4, data: { ...orders[0].data, active: false } }]}
      />,
    );
    await waitFor(() => expect(screen.getByText('Start new calculation')).toBeDisabled());
    // An ineligible selected row remains removable.
    fireEvent.click(screen.getByLabelText('ORDER-1 · inactive'));
    expect(screen.getByText(/0 \/ 250 deliveries selected across pages/)).toBeInTheDocument();
  });
  it('reuses the exact submission and key after a lost response and reload', async () => {
    let submissions = 0;
    const fetch = vi.fn(async (url: string, options: RequestInit) => {
      if (options.method === 'POST') {
        if (++submissions === 1) throw new Error('Response lost');
        return ok(job);
      }
      return ok([]);
    });
    vi.stubGlobal('fetch', fetch);
    const view = render(<PlanningPanel {...props} />);
    selectSources();
    fireEvent.click(screen.getByText('Start new calculation'));
    expect(await screen.findByRole('alert')).toHaveTextContent('The server could not be reached');
    view.unmount();
    render(<PlanningPanel {...props} />);
    fireEvent.click(screen.getByText('Retry submission'));
    await waitFor(() => expect(submissions).toBe(2));
    const calls = fetch.mock.calls.filter((c) => c[1].method === 'POST');
    expect(calls[0][1].body).toBe(calls[1][1].body);
    expect(calls[0][1].headers).toEqual(calls[1][1].headers);
    expect(JSON.parse(calls[0][1].body as string).source_versions).toEqual({
      depot: 1,
      vehicle: 2,
      order: 3,
    });
    await waitFor(() => expect(sessionStorage.getItem('vrp-pending:2026-09-23')).toBeNull());
  });
  it('blocks unreviewed selections and clears a rejected version attempt', async () => {
    const fetch = vi.fn(async (_url: string, options: RequestInit) =>
      options.method === 'POST'
        ? { ok: false, status: 412, json: async () => ({ detail: 'Sources changed. Reload records.' }) }
        : ok([]),
    );
    vi.stubGlobal('fetch', fetch);
    const view = render(
      <PlanningPanel {...props} orders={[{ ...orders[0], data: { ...orders[0].data, location: null } }]} />,
    );
    expect(screen.getByLabelText('ORDER-1 · needs point review')).toBeDisabled();
    expect(screen.getByText('Start new calculation')).toBeDisabled();
    view.unmount();
    render(<PlanningPanel {...props} />);
    selectSources();
    fireEvent.click(screen.getByText('Start new calculation'));
    expect(await screen.findByRole('alert')).toHaveTextContent('Sources changed');
    expect(screen.queryByText('Retry submission')).not.toBeInTheDocument();
    expect(sessionStorage.getItem('vrp-pending:2026-09-23')).toBeNull();
  });
  it('shows an all-unassigned saved draft as no dispatch success', async () => {
    const complete = { ...job, status: 'SUCCEEDED', stage: 'DONE', plan_id: 'plan' };
    const plan = {
      id: 'plan',
      version: 1,
      validation: 'PASS',
      input_data: { depot, orders, vehicles, data_kind: 'SYNTHETIC' },
      result: {
        routes: [],
        unassigned: [{ order_id: 'order', reason: 'SOLVER_UNASSIGNED' }],
        validation: 'PASS',
        blocking_issues: [],
        objective: 'TRAVEL_TIME',
        routing_policy: 'TRUCK_STANDARD',
        dataset: 'Synthetic test',
      },
    } as unknown as Plan;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => ok(url.includes('/plans/') ? plan : [complete])),
    );
    render(<PlanningPanel {...props} />);
    expect(
      await screen.findByText('No work served. This calculation is not a dispatch success.'),
    ).toBeInTheDocument();
    expect(screen.getByText(/Unassigned deliveries \(1\)/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /export|finalize/i })).not.toBeInTheDocument();
  });
  it('aborts status reads on unmount and never automatically reruns failures', async () => {
    const fetch = vi.fn(async (_url: string, _options: RequestInit) =>
      ok([
        {
          ...job,
          status: 'FAILED',
          stage: 'FAILED',
          error: { code: 'ENGINE_FAILED', message: 'Engine unavailable' },
        },
      ]),
    );
    vi.stubGlobal('fetch', fetch);
    const view = render(<PlanningPanel {...props} />);
    expect(await screen.findByText('Engine unavailable')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Review inputs and retry' })).toBeInTheDocument();
    expect(fetch.mock.calls.every((c) => c[1].method !== 'POST')).toBe(true);
    view.unmount();
    expect(fetch.mock.calls[0][1].signal?.aborted).toBe(true);
  });
});

it('keeps status connection failures visible when recent calculations are collapsed', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      throw new Error('Network unavailable');
    }),
  );
  render(<PlanningPanel {...props} />);
  expect(await screen.findByText(/Calculation status could not be refreshed/)).toBeVisible();
});

it('opens recent runs as distinct rows with readable status and never resubmits them', async () => {
  const failed = {
    ...job,
    id: 'failed-run',
    status: 'FAILED',
    stage: 'FAILED',
    error: { code: 'ENGINE_FAILED', message: 'Routing service unavailable' },
  };
  const fetch = vi.fn(async () => ok([job, failed]));
  vi.stubGlobal('fetch', fetch);
  render(<PlanningPanel {...props} />);
  await screen.findByRole('heading', { name: 'Waiting for worker' });
  fireEvent.click(screen.getByText('Recent calculations'));
  expect(screen.getAllByText('10:00:00')).toHaveLength(2);
  fireEvent.click(screen.getByRole('button', { name: 'Open the calculation of 10:00:00 · Failed' }));
  expect(await screen.findByText('Routing service unavailable')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Open the calculation of 10:00:00 · Failed' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  fireEvent.click(screen.getByRole('button', { name: /^Open the calculation of 10:00:00 · (?!Failed)/ }));
  expect(await screen.findByRole('heading', { name: 'Waiting for worker' })).toBeVisible();
  expect(fetch.mock.calls.every((call) => !(call as unknown as [string, RequestInit])[1]?.method)).toBe(true);
});

it('cancels a waiting calculation on request and shows why it stopped', async () => {
  const cancelled = {
    ...job,
    status: 'FAILED',
    stage: 'FAILED',
    finished_at: '2026-09-23T08:01:00Z',
    error: { code: 'CANCELLED', message: 'Calculation cancelled. No result was saved.' },
  };
  const fetch = vi.fn(async (url: string, options?: RequestInit) =>
    ok(options?.method === 'POST' ? cancelled : url.includes('/jobs?') ? [job] : []),
  );
  vi.stubGlobal('fetch', fetch);
  render(<PlanningPanel {...props} />);
  await screen.findByRole('heading', { name: 'Waiting for worker' });
  expect(screen.queryByText(/Stage:/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Cancel calculation' }));
  expect(await screen.findByText('Calculation cancelled. No result was saved.')).toBeVisible();
  // A cancel is not a failure: no "contact your operator" advice.
  expect(screen.getByRole('heading', { name: 'Calculation cancelled' })).toBeVisible();
  expect(screen.getByText('Nothing was saved. Start a new calculation when you are ready.')).toBeVisible();
  expect(screen.queryByText(/contact your installation operator/)).not.toBeInTheDocument();
  const post = fetch.mock.calls.find((call) => call[1]?.method === 'POST')!;
  expect(post[0]).toBe('/api/v1/jobs/job/cancel');
  expect(screen.queryByRole('button', { name: 'Cancel calculation' })).not.toBeInTheDocument();
});

const tick = async (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
it('polls jobs fast only while a calculation is active, marks polls as background and pauses while hidden', async () => {
  vi.useFakeTimers();
  try {
    let rows: Job[] = [job];
    const fetch = vi.fn(async (url: string, _options: RequestInit) => ok(url.includes('/jobs?') ? rows : []));
    vi.stubGlobal('fetch', fetch);
    const polls = () => fetch.mock.calls.filter((c) => c[0].includes('/jobs?'));
    render(<PlanningPanel {...props} />);
    await tick(0);
    expect(polls()).toHaveLength(1);
    expect(polls()[0][1].headers).toMatchObject({ 'X-VRP-Background': '1' });
    await tick(JOB_POLL_ACTIVE_MS);
    expect(polls()).toHaveLength(2);
    rows = [
      {
        ...job,
        status: 'FAILED',
        stage: 'FAILED',
        error: { code: 'ENGINE_FAILED', message: 'Stopped', request_id: 'job' },
      },
    ];
    await tick(JOB_POLL_ACTIVE_MS);
    expect(polls()).toHaveLength(3);
    await tick(JOB_POLL_IDLE_MS - 1000);
    expect(polls()).toHaveLength(3);
    await tick(1000);
    expect(polls()).toHaveLength(4);
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    await tick(JOB_POLL_IDLE_MS * 3);
    expect(polls()).toHaveLength(4);
    hidden.mockReturnValue(false);
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(polls()).toHaveLength(5);
    expect(polls().every((c) => (c[1].headers as Record<string, string>)['X-VRP-Background'] === '1')).toBe(
      true,
    );
  } finally {
    vi.useRealTimers();
  }
});

it('keeps unsaved route moves when another job starts on the open plan and flags the version conflict', async () => {
  vi.useFakeTimers();
  try {
    const input = fixture as unknown as Plan['input_data'];
    const route = {
      vehicle_id: input.vehicles[0].id,
      profile: input.vehicles[0].data.routing,
      departure: 28800,
      end: 33000,
      stops: input.orders.slice(0, 2).map((o, i) => ({
        order_id: o.id,
        arrival: 29000 + i * 1000,
        service_start: 29000 + i * 1000,
        departure: 29500 + i * 1000,
        load_after: [0, 0, 0, 0, 0],
      })),
      legs: [],
      distance_m: 1000,
      driving_seconds: 1000,
      waiting_seconds: 0,
      service_seconds: 2000,
      fixed_minor: 100,
      distance_minor: 100,
      time_minor: 100,
      known_cost_minor: 300,
    };
    let saved = {
      id: 'plan',
      organization_id: input.organization_id,
      version: 1,
      service_date: input.service_date,
      created_at: '2026-09-23T08:00:00Z',
      updated_at: '2026-09-23T08:00:00Z',
      status: 'DRAFT',
      validation: 'PASS',
      active_job_id: null as string | null,
      result_stale: false,
      finalized_at: null,
      edit_error: null,
      sources_current: true,
      input_data: input,
      result: {
        status: 'DRAFT',
        data_kind: 'SYNTHETIC',
        organization_id: input.organization_id,
        objective: input.objective,
        routing_policy: input.routing_policy,
        dataset: 'Synthetic test fixture',
        routes: [route],
        unassigned: input.orders.slice(2).map((o) => ({ order_id: o.id, reason: 'SOLVER_UNASSIGNED' })),
        validation: 'PASS',
        blocking_issues: [],
        operational_ready: false,
        toll_cost_minor: null,
        total_cost_minor: null,
      },
    } as unknown as Plan;
    const summary = {
      id: 'plan',
      organization_id: input.organization_id,
      version: 1,
      service_date: input.service_date,
      created_at: '2026-09-23T08:00:00Z',
      updated_at: '2026-09-23T08:00:00Z',
      status: 'DRAFT',
      validation: 'PASS',
      result_stale: false,
      active_job_id: null,
      finalized_at: null,
    };
    let rows: Job[] = [];
    const fetch = vi.fn(async (url: string, _options: RequestInit) => {
      const path = new URL(url, 'http://localhost').pathname;
      return ok(path === '/api/v1/plans/plan' ? saved : path === '/api/v1/plans' ? [summary] : rows);
    });
    vi.stubGlobal('fetch', fetch);
    const reads = () => fetch.mock.calls.filter((c) => c[0] === '/api/v1/plans/plan');
    const view = render(<PlanningPanel {...props} day={input.service_date} view="saved" />);
    await tick(0);
    fireEvent.click(screen.getByTitle('plan'));
    await tick(0);
    view.rerender(<PlanningPanel {...props} day={input.service_date} view="planning" />);
    expect(reads()).toHaveLength(1);
    expect(reads()[0][1].headers).not.toHaveProperty('X-VRP-Background');
    const moved = input.orders[2];
    fireEvent.click(screen.getByRole('button', { name: `Inspect ${moved.data.external_id}` }));
    fireEvent.change(screen.getByLabelText('Destination vehicle'), {
      target: { value: input.vehicles[0].id },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Apply stop move' }));
    expect(screen.getByText(/Unsaved assignment/)).toBeInTheDocument();
    // A colleague's recalculation of the same plan appears in the next job poll.
    rows = [{ ...job, id: 'other-edit', kind: 'EDIT', status: 'QUEUED', target_plan_id: 'plan' } as Job];
    saved = { ...saved, version: 2, active_job_id: 'other-edit' };
    await tick(JOB_POLL_IDLE_MS);
    expect(reads().length).toBeGreaterThan(1);
    expect(reads().at(-1)![1].headers).toMatchObject({ 'X-VRP-Background': '1' });
    expect(screen.getByText(/Unsaved assignment/)).toBeInTheDocument();
    expect(screen.getByText(/saved plan changed after you began editing/)).toBeInTheDocument();
    expect(
      screen.getByRole('region', { name: `Route ${input.vehicles[0].data.fleet_number}` }),
    ).toHaveTextContent(moved.data.external_id);
  } finally {
    vi.useRealTimers();
  }
});

it('stops re-reading a plan the server refuses but keeps retrying unavailable service responses', async () => {
  vi.useFakeTimers();
  try {
    const done = { ...job, status: 'SUCCEEDED', stage: 'DONE', plan_id: 'gone' } as Job;
    let status = 404;
    const fetch = vi.fn(async (url: string, _options: RequestInit) =>
      url.includes('/plans/gone')
        ? { ok: false, status, json: async () => ({ detail: 'Plan not found.' }) }
        : ok([done]),
    );
    vi.stubGlobal('fetch', fetch);
    const reads = () => fetch.mock.calls.filter((c) => c[0].includes('/plans/gone'));
    const view = render(<PlanningPanel {...props} />);
    await tick(0);
    expect(screen.getByText('Plan not found.')).toBeInTheDocument();
    expect(reads()).toHaveLength(1);
    await tick(10000);
    expect(reads()).toHaveLength(1);
    view.unmount();
    status = 503;
    render(<PlanningPanel {...props} />);
    await tick(0);
    const before = reads().length;
    await tick(2000);
    expect(reads().length).toBe(before + 1);
  } finally {
    vi.useRealTimers();
  }
});

it('announces to screen readers when a calculation it was watching stops', async () => {
  vi.useFakeTimers();
  try {
    let rows: Job[] = [{ ...job, status: 'RUNNING', stage: 'MATRIX', started_at: '2026-09-23T08:00:01Z' }];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => ok(url.includes('/jobs?') ? rows : [])),
    );
    render(<PlanningPanel {...props} />);
    await tick(0);
    expect(screen.queryByText('Calculation could not finish.')).not.toBeInTheDocument();
    rows = [
      {
        ...rows[0],
        status: 'FAILED',
        stage: 'FAILED',
        finished_at: '2026-09-23T08:00:30Z',
        error: { code: 'ENGINE_FAILED', message: 'Routing service unavailable', request_id: 'job' },
      },
    ];
    await tick(JOB_POLL_ACTIVE_MS);
    expect(screen.getByText('Calculation could not finish.')).toBeInTheDocument();
    // Worded when shown: switching the language afterwards translates it too.
    act(() => setLanguage('fr'));
    expect(screen.getByText('Le calcul n’a pas pu aboutir.')).toBeInTheDocument();
  } finally {
    act(() => setLanguage('en'));
    vi.useRealTimers();
  }
});

it('opens the plan named in the URL and reports which plan is shown', async () => {
  const requested = '0123abcd-0000-4000-8000-000000000001';
  const fetch = vi.fn(async (url: string) =>
    url.includes(`/plans/${requested}`)
      ? { ok: false, status: 404, json: async () => ({ detail: 'Plan not found.' }) }
      : ok([]),
  );
  vi.stubGlobal('fetch', fetch);
  const shown = vi.fn();
  render(<PlanningPanel {...props} requestedPlanId={requested} onPlanShown={shown} />);
  await waitFor(() =>
    expect(fetch.mock.calls.some((c) => String(c[0]).endsWith(`/plans/${requested}`))).toBe(true),
  );
  expect(shown).toHaveBeenLastCalledWith(requested);
});

it('names the places a refused calculation concerns, such as two points too far apart', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (_url: string, options: RequestInit) =>
      options.method === 'POST'
        ? {
            ok: false,
            status: 422,
            json: async () => ({
              code: 'AREA_TOO_WIDE',
              detail:
                "The depot and the selected deliveries are farther apart than this installation's routing allows in one calculation. Select deliveries closer together, or ask your installation operator about the distance limit.",
              identifiers: ['depot', 'ORDER-1'],
            }),
          }
        : ok([]),
    ),
  );
  render(<PlanningPanel {...props} />);
  selectSources();
  fireEvent.click(screen.getByText('Start new calculation'));
  expect(await screen.findByRole('alert')).toHaveTextContent(/farther apart.*Concerned: Depot, ORDER-1/);
});

it('says when deliveries name no loading place while the vehicles leave from different places', () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ok([])),
  );
  const north = {
    ...vehicles[0],
    id: 'v-north',
    data: { ...vehicles[0].data, fleet_number: 'VAN-N', start: 'NORD' },
  };
  const selection = (chosen: Vehicle[]) => ({
    vehicles: chosen,
    setVehicles: vi.fn(),
    orders,
    setOrders: vi.fn(),
  });
  const view = render(<PlanningPanel {...props} selection={selection([vehicles[0]])} />);
  // One starting place: nothing to say.
  expect(screen.queryByText(/name(s)? no loading place/)).not.toBeInTheDocument();
  view.rerender(<PlanningPanel {...props} selection={selection([vehicles[0], north as Vehicle])} />);
  expect(
    screen.getByText(/1 selected delivery names no loading place: any selected vehicle may carry it/),
  ).toBeVisible();
});

it('asks for the main depot review only when a selected vehicle starts or ends its day there', () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ok([])),
  );
  const unreviewed = { ...depot, data: { code: 'MAIN', location: { reviewed: false } } } as Depot;
  const north = { ...vehicles[0], data: { ...vehicles[0].data, start: 'NORD', end: null } } as Vehicle;
  const selection = (chosen: Vehicle[]) => ({
    vehicles: chosen,
    setVehicles: vi.fn(),
    orders,
    setOrders: vi.fn(),
  });
  const view = render(<PlanningPanel {...props} depot={unreviewed} selection={selection([north])} />);
  expect(screen.queryByText('Review the depot point before calculating.')).not.toBeInTheDocument();
  expect(screen.getByText('Start new calculation')).toBeEnabled();
  // Ending the day at the main depot brings its point back into the calculation.
  const back = { ...north, data: { ...north.data, end: 'MAIN' } } as Vehicle;
  view.rerender(<PlanningPanel {...props} depot={unreviewed} selection={selection([back])} />);
  expect(screen.getByText('Review the depot point before calculating.')).toBeVisible();
  expect(screen.getByText('Start new calculation')).toBeDisabled();
});

it('stops saying the draft is ready for review once the plan shown is Ready', async () => {
  vi.useFakeTimers();
  try {
    let rows: Job[] = [{ ...job, status: 'RUNNING', stage: 'SOLVE', started_at: '2026-09-23T08:00:01Z' }];
    // While an edit is still being saved the panel re-reads the plan; the next read finds it Ready.
    let current = { status: 'DRAFT', active_job_id: 'edit' };
    const plan = () =>
      ({
        id: 'plan',
        version: 1,
        ...current,
        validation: 'PASS',
        input_data: { depot, orders, vehicles, data_kind: 'SYNTHETIC' },
        result: {
          routes: [],
          unassigned: [{ order_id: 'order', reason: 'SOLVER_UNASSIGNED' }],
          validation: 'PASS',
          blocking_issues: [],
          objective: 'TRAVEL_TIME',
          routing_policy: 'TRUCK_STANDARD',
          dataset: 'Synthetic test',
        },
      }) as unknown as Plan;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => ok(url.includes('/plans/') ? plan() : url.includes('/jobs?') ? rows : [])),
    );
    render(<PlanningPanel {...props} />);
    await tick(0);
    rows = [
      {
        ...rows[0],
        status: 'SUCCEEDED',
        stage: 'DONE',
        finished_at: '2026-09-23T08:00:30Z',
        plan_id: 'plan',
      },
    ];
    await tick(JOB_POLL_ACTIVE_MS);
    expect(screen.getByText('Calculation completed. The draft is ready for review.')).toBeInTheDocument();
    current = { status: 'READY', active_job_id: null as unknown as string };
    await tick(1500);
    await tick(0);
    expect(
      screen.queryByText('Calculation completed. The draft is ready for review.'),
    ).not.toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
});
