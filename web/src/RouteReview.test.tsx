import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, it, expect, vi } from 'vitest';
import { act } from 'react';
import { setLanguage } from './i18n';
import { ConfirmHost } from './confirm';
import fixture from '../../backend/tests/fixtures/paris-ten.json';
import {
  RouteReview,
  assignmentWarnings,
  parisTime,
  mentions,
  routeColors,
  tightestLoad,
} from './RouteReview';
import type { Plan } from './api';

vi.mock('./MapPanel', () => ({
  MapPanel: ({
    onSelect,
    selected,
    stale,
  }: {
    onSelect: (id: string) => void;
    selected: string;
    stale: boolean;
  }) => (
    <div>
      <button onClick={() => onSelect(fixture.orders[0].id)}>Select first map stop</button>
      <span>
        Map selection {selected} {stale ? 'stale' : 'current'}
      </span>
    </div>
  ),
}));
function plan(): Plan {
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
  return {
    id: 'plan',
    organization_id: input.organization_id,
    version: 1,
    service_date: input.service_date,
    created_at: '2026-09-23T08:00:00Z',
    updated_at: '2026-09-23T08:00:00Z',
    status: 'DRAFT',
    validation: 'PASS',
    active_job_id: null,
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
  };
}
const first = fixture.orders[0],
  second = fixture.orders[1];
beforeEach(() => sessionStorage.clear());
afterEach(() => {
  act(() => setLanguage('en'));
  vi.unstubAllGlobals();
});
function view(p = plan(), onJob = vi.fn()) {
  return {
    onJob,
    ...render(
      <RouteReview plan={p} mapStyle="https://maps.example/style" onJob={onJob} onRefresh={vi.fn()} />,
    ),
  };
}

describe('manual route review', () => {
  it('explains unassigned deliveries by the route limits that were reached, and counts one stop', () => {
    const limited = plan();
    limited.input_data.vehicles[0].data.limits = { max_stops: 2 };
    view(limited);
    expect(
      screen.getByText(
        `Route limits were reached: ${limited.input_data.vehicles[0].data.fleet_number} 2/2 stops. Raise or remove them in Fleet if these deliveries should be served, then recalculate.`,
      ),
    ).toBeVisible();
    cleanup();
    const single = plan();
    single.result.routes[0].stops = single.result.routes[0].stops.slice(0, 1);
    single.result.unassigned = single.input_data.orders
      .slice(1)
      .map((o) => ({ order_id: o.id, reason: 'SOLVER_UNASSIGNED' }));
    view(single);
    expect(screen.getByText('1 stop')).toBeVisible();
    expect(screen.queryByText(/Route limits were reached/)).not.toBeInTheDocument();
  });

  it('requires an explicit decision in the dialog before discarding unsaved moves', async () => {
    const refresh = vi.fn();
    render(
      <>
        <RouteReview plan={plan()} mapStyle="" onJob={vi.fn()} onRefresh={refresh} />
        <ConfirmHost />
      </>,
    );
    fireEvent.click(screen.getByText('Select first map stop'));
    fireEvent.click(screen.getByLabelText(`Move ${first.data.external_id} down`));
    fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
    const question = await screen.findByRole('alertdialog', {
      name: 'Discard unsaved stop moves and reload the saved plan?',
    });
    expect(within(question).getByRole('button', { name: 'Keep editing' })).toHaveFocus();
    expect(refresh).not.toHaveBeenCalled();
    fireEvent.click(within(question).getByRole('button', { name: 'Keep editing' }));
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
    expect(screen.getByText(/Unsaved assignment/)).toBeInTheDocument();
    expect(refresh).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Discard moves and reload' }));
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    expect(screen.queryByText(/Unsaved assignment/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: `1. ${first.data.external_id}` })).toBeInTheDocument();
  });
  it('distinguishes arrival from service and marks cost components stale after an edit', () => {
    const p = plan(),
      route = p.result.routes[0];
    route.stops[0].arrival = 28800;
    route.stops[0].service_start = 29400;
    route.stops[0].departure = 30000;
    route.fixed_minor = 123;
    route.distance_minor = 456;
    route.time_minor = 789;
    route.known_cost_minor = 1368;
    view(p);
    fireEvent.click(screen.getByText('Select first map stop'));
    expect(screen.getAllByText('08:00 / 08:10').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/08:20 \/ 5 min/).length).toBeGreaterThan(0);
    expect(
      screen.getByText(/Modeled operating cost: €13.68 · fixed €1.23 · distance €4.56 · time €7.89/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByText('Select first map stop'));
    fireEvent.click(screen.getByLabelText(`Move ${first.data.external_id} down`));
    expect(screen.queryByText('08:00 / 08:10')).not.toBeInTheDocument();
    expect(screen.getByText(/Previous saved cost: €13.68/)).toBeInTheDocument();
  });
  it('requires partial acceptance for this version and sends versioned finalization', async () => {
    const p = structuredClone(plan());
    p.input_data.data_kind = 'ROUTED';
    p.result.data_kind = 'ROUTED';
    const fetch = vi.fn(async (_url: string, _options: RequestInit) => ({
      ok: true,
      json: async () => ({ ...p, status: 'READY' }),
    }));
    vi.stubGlobal('fetch', fetch);
    const refresh = vi.fn();
    const v = render(<RouteReview plan={p} mapStyle="" onJob={vi.fn()} onRefresh={refresh} />);
    expect(screen.queryByRole('button', { name: 'Mark ready' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Review unassigned' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Export CSV' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('I accept the unassigned deliveries in this partial plan'));
    fireEvent.click(screen.getByRole('button', { name: 'Mark ready' }));
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/plans/plan/finalize');
    expect(fetch.mock.calls[0][1].headers).toMatchObject({ 'If-Match': '"1"' });
    expect(JSON.parse(fetch.mock.calls[0][1].body as string)).toEqual({
      accept_unassigned: true,
      replace_conflicting_ready: false,
    });
    v.rerender(<RouteReview plan={{ ...p, version: 3 }} mapStyle="" onJob={vi.fn()} onRefresh={refresh} />);
    expect(screen.queryByRole('button', { name: 'Mark ready' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Review unassigned' })).toBeEnabled();
    expect(screen.getByRole('checkbox', { name: /I accept/ })).not.toBeChecked();
  });
  it('offers download only for a ready company plan and blocks it while editing', () => {
    const p = structuredClone(plan());
    p.input_data.data_kind = 'ROUTED';
    p.result.data_kind = 'ROUTED';
    p.status = 'READY';
    p.result.status = 'READY';
    p.result.operational_ready = true;
    const open = vi.fn();
    vi.stubGlobal('open', open);
    view(p);
    expect(screen.getByRole('button', { name: 'Export CSV' })).toBeEnabled();
    expect(screen.queryByRole('checkbox', { name: /I accept/ })).not.toBeInTheDocument();
    // Route sheets open in their own tab, for every vehicle or for one route.
    fireEvent.click(screen.getByRole('button', { name: 'Print the route sheets' }));
    expect(open).toHaveBeenCalledWith(
      `#/sheets?plan=plan&version=1&date=${p.service_date}`,
      '_blank',
      'noopener',
    );
    const vehicle = p.input_data.vehicles[0];
    fireEvent.click(screen.getByRole('button', { name: `Route sheet for ${vehicle.data.fleet_number}` }));
    expect(open).toHaveBeenLastCalledWith(
      `#/sheets?plan=plan&version=1&date=${p.service_date}&vehicle=${vehicle.id}`,
      '_blank',
      'noopener',
    );
    fireEvent.click(screen.getByText('Select first map stop'));
    fireEvent.click(screen.getByLabelText(`Move ${first.data.external_id} down`));
    expect(screen.queryByRole('button', { name: 'Export CSV' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Route sheets' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Route sheet for/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Mark ready' })).not.toBeInTheDocument();
  });
  it('explains a conflicting Ready plan and replaces it only when asked', async () => {
    const p = structuredClone(plan());
    p.input_data.data_kind = 'ROUTED';
    p.result.data_kind = 'ROUTED';
    const fetch = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        json: async () => ({
          code: 'READY_CONFLICT',
          detail: 'Another Ready plan for 2026-09-23 already dispatches…',
        }),
      })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ ...p, status: 'READY' }) });
    vi.stubGlobal('fetch', fetch);
    const refresh = vi.fn();
    render(<RouteReview plan={p} mapStyle="" onJob={vi.fn()} onRefresh={refresh} />);
    fireEvent.click(screen.getByLabelText('I accept the unassigned deliveries in this partial plan'));
    fireEvent.click(screen.getByRole('button', { name: 'Mark ready' }));
    expect(
      await screen.findByText(
        'Another Ready plan for this day already dispatches some of the same vehicles or deliveries.',
      ),
    ).toBeInTheDocument();
    expect(refresh).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Replace the other Ready plan' }));
    await waitFor(() => expect(refresh).toHaveBeenCalledOnce());
    expect(JSON.parse(fetch.mock.calls[1][1].body as string)).toEqual({
      accept_unassigned: true,
      replace_conflicting_ready: true,
    });
  });
  it('blocks Ready and export once the plan inputs changed', () => {
    const p = structuredClone(plan());
    p.input_data.data_kind = 'ROUTED';
    p.result.data_kind = 'ROUTED';
    p.sources_current = false;
    const v = render(<RouteReview plan={p} mapStyle="" onJob={vi.fn()} onRefresh={vi.fn()} />);
    expect(screen.getByText(/changed after this calculation/)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('I accept the unassigned deliveries in this partial plan'));
    expect(screen.getByRole('button', { name: 'Mark ready' })).toBeDisabled();
    const ready = {
      ...p,
      status: 'READY' as const,
      result: { ...p.result, status: 'READY' as const, operational_ready: true },
    };
    v.rerender(<RouteReview plan={ready} mapStyle="" onJob={vi.fn()} onRefresh={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Export CSV' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Print the route sheets' })).toBeDisabled();
  });
  it('downloads the spreadsheet layout when the interface is French', async () => {
    const p = structuredClone(plan());
    p.input_data.data_kind = 'ROUTED';
    p.result.data_kind = 'ROUTED';
    p.status = 'READY';
    p.result.status = 'READY';
    p.result.operational_ready = true;
    const fetch = vi.fn(async (_url: string, _options: RequestInit) => ({
      ok: true,
      blob: async () => new Blob(['x']),
    }));
    vi.stubGlobal('fetch', fetch);
    vi.stubGlobal('URL', { ...URL, createObjectURL: () => 'blob:x', revokeObjectURL: () => undefined });
    act(() => setLanguage('fr'));
    view(p);
    fireEvent.click(screen.getByRole('button', { name: 'Exporter le CSV' }));
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/plans/plan/export.csv?layout=spreadsheet');
  });
  it('synchronizes map selection and submits the explicit reordered sequence with its version', async () => {
    const fetch = vi.fn(async (_url: string, _options: RequestInit) => ({
      ok: true,
      json: async () => ({ id: 'edit-job' }),
    }));
    vi.stubGlobal('fetch', fetch);
    const { onJob } = view();
    fireEvent.click(screen.getByText('Select first map stop'));
    expect(screen.getByRole('button', { name: `1. ${first.data.external_id}` }).closest('tr')).toHaveClass(
      'selected-stop',
    );
    fireEvent.click(screen.getByText('Select first map stop'));
    fireEvent.click(screen.getByLabelText(`Move ${first.data.external_id} down`));
    expect(screen.getByText(/Unsaved assignment/)).toBeInTheDocument();
    fireEvent.click(screen.getByText('Recalculate these routes'));
    await waitFor(() => expect(onJob).toHaveBeenCalled());
    expect(fetch.mock.calls[0][1].headers).toMatchObject({ 'If-Match': '"1"' });
    expect(JSON.parse(fetch.mock.calls[0][1].body as string).routes[0].order_ids).toEqual([
      second.id,
      first.id,
    ]);
    expect(screen.queryByRole('button', { name: 'Export CSV' })).not.toBeInTheDocument();
  });
  it('keeps the original edit version after a concurrent server change', () => {
    const p = plan();
    const { rerender } = view(p);
    fireEvent.click(screen.getByText('Select first map stop'));
    fireEvent.click(screen.getByLabelText(`Move ${first.data.external_id} down`));
    rerender(
      <RouteReview
        plan={{ ...p, version: 3 }}
        mapStyle="https://maps.example/style"
        onJob={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('saved plan changed');
    expect(screen.getByText('Recalculate these routes')).toBeDisabled();
  });
  it('retries an uncertain edit with the same original sequence and key after reload', async () => {
    let count = 0;
    const fetch = vi.fn(async (_url: string, _options: RequestInit) => {
      if (++count === 1) throw new Error('Lost response');
      return { ok: true, json: async () => ({ id: 'edit-job' }) };
    });
    vi.stubGlobal('fetch', fetch);
    const firstView = view();
    fireEvent.click(screen.getByText('Select first map stop'));
    fireEvent.click(screen.getByLabelText(`Move ${first.data.external_id} down`));
    fireEvent.click(screen.getByText('Recalculate these routes'));
    expect(await screen.findByRole('alert')).toHaveTextContent('The server could not be reached');
    firstView.unmount();
    const { onJob } = view({ ...plan(), version: 3 });
    fireEvent.click(screen.getByText('Retry edit submission'));
    await waitFor(() => expect(onJob).toHaveBeenCalled());
    expect(fetch.mock.calls[0][1].body).toBe(fetch.mock.calls[1][1].body);
    expect(fetch.mock.calls[0][1].headers).toEqual(fetch.mock.calls[1][1].headers);
  });
  it('marks pending and failed geometry stale and disables pending edits', () => {
    const p = { ...plan(), validation: 'PENDING' as const, active_job_id: 'job', result_stale: true };
    const v = view(p);
    expect(screen.getByText(/Recalculation pending/)).toBeInTheDocument();
    fireEvent.click(screen.getByText('Select first map stop'));
    expect(screen.getByLabelText(`Move ${first.data.external_id} down`)).toBeDisabled();
    v.rerender(
      <RouteReview
        plan={{ ...p, validation: 'FAIL', active_job_id: null }}
        mapStyle="https://maps.example/style"
        onJob={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    expect(screen.getByText(/edit was not applied/)).toBeInTheDocument();
  });
  it('formats elapsed time correctly across Paris daylight-saving transitions', () => {
    expect(parisTime('2026-03-29', 0)).toBe('00:00');
    expect(parisTime('2026-03-29', 7200)).toBe('03:00');
    expect(parisTime('2026-10-25', 0)).toBe('00:00');
    expect(parisTime('2026-10-25', 10800)).toBe('02:00');
  });
});

it('moves an unassigned stop to an exact position without dropping or duplicating work', async () => {
  const fetch = vi.fn(async () => ({ ok: true, json: async () => ({ id: 'edit' }) }));
  vi.stubGlobal('fetch', fetch);
  const p = plan();
  view(p);
  const id = p.input_data.orders[2].id;
  fireEvent.click(screen.getByRole('button', { name: `Inspect ${p.input_data.orders[2].data.external_id}` }));
  fireEvent.change(screen.getByLabelText('Destination vehicle'), {
    target: { value: p.input_data.vehicles[0].id },
  });
  fireEvent.change(screen.getByLabelText('Insert position'), { target: { value: first.id } });
  fireEvent.click(screen.getByRole('button', { name: 'Apply stop move' }));
  fireEvent.click(screen.getByText('Recalculate these routes'));
  await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  const body = JSON.parse((fetch.mock.calls[0] as unknown as [string, RequestInit])[1].body as string);
  expect(body.routes[0].order_ids).toEqual([id, first.id, second.id]);
  const all = [
    ...body.routes.flatMap((r: { order_ids: string[] }) => r.order_ids),
    ...body.unassigned_order_ids,
  ];
  expect(new Set(all).size).toBe(10);
  expect(all).toHaveLength(10);
});
it('dragging before another stop preserves an explicit sequence and pending map warning', async () => {
  const fetch = vi.fn(async () => ({ ok: true, json: async () => ({ id: 'edit' }) }));
  vi.stubGlobal('fetch', fetch);
  view();
  const from = screen.getByRole('button', { name: `2. ${second.data.external_id}` }).closest('tr')!,
    to = screen.getByRole('button', { name: `1. ${first.data.external_id}` }).closest('tr')!;
  fireEvent.dragStart(from, { dataTransfer: { setData: vi.fn() } });
  fireEvent.drop(to);
  expect(screen.getByText(/Unsaved assignment/)).toBeInTheDocument();
  fireEvent.click(screen.getByText('Recalculate these routes'));
  await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  expect(
    JSON.parse((fetch.mock.calls[0] as unknown as [string, RequestInit])[1].body as string).routes[0]
      .order_ids,
  ).toEqual([second.id, first.id]);
});
it.each(['FAIL', 'PENDING'] as const)('never enables Ready or CSV for %s validation', (validation) => {
  const p = plan();
  p.input_data.data_kind = 'ROUTED';
  p.validation = validation;
  p.result.blocking_issues = validation === 'FAIL' ? ['CAPACITY: modeled load exceeded'] : [];
  view(p);
  fireEvent.click(screen.getByLabelText('I accept the unassigned deliveries in this partial plan'));
  expect(screen.getByRole('button', { name: 'Mark ready' })).toBeDisabled();
  expect(screen.queryByRole('button', { name: 'Export CSV' })).not.toBeInTheDocument();
  if (validation === 'FAIL') expect(screen.getByText('CAPACITY: modeled load exceeded')).toBeInTheDocument();
});
it('blocks zero served work even when partial acceptance is checked', () => {
  const p = plan();
  p.input_data.data_kind = 'ROUTED';
  p.result.routes = [];
  p.result.unassigned = p.input_data.orders.map((o) => ({ order_id: o.id, reason: 'SOLVER_UNASSIGNED' }));
  view(p);
  fireEvent.click(screen.getByLabelText('I accept the unassigned deliveries in this partial plan'));
  expect(screen.getByRole('button', { name: 'Mark ready' })).toBeDisabled();
  expect(screen.getByText(/No work served/)).toBeInTheDocument();
});

it('moving to another vehicle opens that route and clears its apparent validation', () => {
  const p = plan();
  view(p);
  fireEvent.click(screen.getByText('Select first map stop'));
  fireEvent.click(screen.getAllByRole('button', { name: 'Move this stop' })[0]);
  fireEvent.change(screen.getByLabelText('Destination vehicle'), {
    target: { value: p.input_data.vehicles[1].id },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Apply stop move' }));
  const target = screen.getByRole('region', { name: `Route ${p.input_data.vehicles[1].data.fleet_number}` });
  expect(within(target).getByText('Pending validation')).toBeInTheDocument();
  expect(within(target).getByRole('button', { name: `1. ${first.data.external_id}` })).toBeVisible();
  expect(screen.queryByText('No blocking issues')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Export CSV' })).not.toBeInTheDocument();
});
it('stamps a Ready plan with its version and approval time, route sheets first', () => {
  const p = plan();
  p.input_data.data_kind = 'ROUTED';
  p.status = 'READY';
  p.version = 3;
  p.finalized_at = '2026-11-03T16:31:00Z';
  p.result.unassigned = [];
  view(p);
  const band = screen.getByRole('region', { name: 'Ready · version 3' });
  expect(within(band).getByText(/Marked ready 3 Nov 2026, 17:31\./)).toBeVisible();
  const [print, csv] = within(band).getAllByRole('button');
  expect(print).toHaveAccessibleName('Print the route sheets');
  expect(print).not.toHaveClass('secondary');
  expect(csv).toHaveAccessibleName('Export CSV');
  expect(csv).toHaveClass('secondary');
  // Approval lives in the band, not in the page header.
  expect(screen.queryByRole('button', { name: 'Mark ready' })).not.toBeInTheDocument();
});
it('an inconsistent Ready response with failed validation never enables export', () => {
  const p = plan();
  p.status = 'READY';
  p.validation = 'FAIL';
  p.input_data.data_kind = 'ROUTED';
  view(p);
  expect(screen.getByRole('button', { name: 'Export CSV' })).toBeDisabled();
});

it('never presents a pending recalculation as validated', () => {
  const p = plan();
  p.input_data.data_kind = 'ROUTED';
  p.validation = 'PENDING';
  p.active_job_id = 'recalculating';
  p.result.unassigned = [];
  view(p);
  expect(screen.getByText('This result needs recalculation and validation before approval.')).toBeVisible();
  expect(screen.queryByText(/validation passed/)).not.toBeInTheDocument();
  expect(screen.queryByText('No blocking issues')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Mark ready' })).toBeDisabled();
});

it('shows the capacity dimension a route uses most, not only weight', () => {
  const capacity = {
    weight_kg: 1000,
    volume_dm3: 10000,
    pallet_eur_units: 4,
    pallet_ind_units: 2,
    loading_length_mm: 6000,
  };
  const demand = {
    weight_kg: 100,
    volume_dm3: 500,
    pallet_eur_units: 3,
    pallet_ind_units: 0,
    loading_length_mm: 1200,
  };
  expect(tightestLoad([demand], capacity)).toEqual({ index: 2, used: 3, capacity: 4, share: 0.75 });
  expect(tightestLoad([demand, demand], capacity).share).toBe(1.5); // Over capacity stays visible.
  expect(tightestLoad([], capacity).share).toBe(0);
});
it('says an untouched result is a candidate within the search budget and shows the exact limitation', () => {
  view();
  expect(
    screen.getByText(/^Candidate found within the search budget: 2 of 10 deliveries assigned/),
  ).toBeInTheDocument();
  expect(
    screen.getByText(
      'Planning estimate. Verify current road access and driver requirements. Tolls are not included.',
    ),
  ).toBeInTheDocument();
  expect(screen.queryByText(/optimal/i)).not.toBeInTheDocument();
  const edited = { ...plan(), version: 3 };
  view(edited);
  expect(screen.getByText(/^2 of 10 deliveries assigned/)).toBeInTheDocument();
});
it('titles blocking issues in plain language and keeps the identifiers to inspect', () => {
  const p = structuredClone(plan());
  p.validation = 'FAIL';
  p.result.validation = 'FAIL';
  p.result.blocking_issues = ['CAPACITY: weight overloaded (VAN-1)', 'NEWCODE: something unexpected'];
  view(p);
  expect(screen.getByRole('heading', { name: 'Load exceeds the vehicle capacity' })).toBeInTheDocument();
  expect(screen.getByText('CAPACITY: weight overloaded (VAN-1)')).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Blocking issue' })).toBeInTheDocument();
});
it('identifies routes by letter in the legend and can hide or show them all', () => {
  view();
  const legend = screen.getByRole('group', { name: 'Routes shown on the map' });
  const boxes = within(legend).getAllByRole('checkbox');
  expect(within(legend).getByText('A')).toBeInTheDocument();
  fireEvent.click(within(legend).getByRole('button', { name: 'Hide all' }));
  expect(boxes.every((box) => !(box as HTMLInputElement).checked)).toBe(true);
  fireEvent.click(within(legend).getByRole('button', { name: 'Show all' }));
  expect(boxes.every((box) => (box as HTMLInputElement).checked)).toBe(true);
});
it('warns about capacity, stop limits and equipment before a manual assignment is recalculated', () => {
  const input = structuredClone(plan().input_data);
  const truck = input.vehicles[0];
  truck.data.skills = [];
  truck.data.limits = { ...truck.data.limits, max_stops: 1 };
  truck.data.capacity = { ...truck.data.capacity, pallet_eur_units: 1 };
  const ids = input.orders.slice(0, 2).map((o) => o.id);
  const warnings = assignmentWarnings([{ vehicle_id: truck.id, order_ids: ids }], input);
  expect(warnings).toContain('TRUCK-DEMO-01 would exceed its Euro pallets capacity.');
  expect(warnings).toContain('TRUCK-DEMO-01 would exceed its limit of 1 stops.');
  expect(warnings).toContain('TRUCK-DEMO-01 lacks equipment required by DEMO-ORDER-001.');
  expect(
    assignmentWarnings([{ vehicle_id: input.vehicles[0].id, order_ids: [] }], plan().input_data),
  ).toEqual([]);
});
it('lets an editor keep their moves on a plan version that changed meanwhile', () => {
  const p = structuredClone(plan());
  const v = render(<RouteReview plan={p} mapStyle="" onJob={vi.fn()} onRefresh={vi.fn()} />);
  fireEvent.click(screen.getByText('Select first map stop'));
  fireEvent.click(screen.getByLabelText(`Move ${first.data.external_id} down`));
  v.rerender(<RouteReview plan={{ ...p, version: 2 }} mapStyle="" onJob={vi.fn()} onRefresh={vi.fn()} />);
  expect(screen.getByText(/The saved plan changed after you began editing/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Keep my moves on the latest version' }));
  expect(screen.queryByText(/The saved plan changed after you began editing/)).not.toBeInTheDocument();
  expect(screen.getByText('Manual route changes are pending.')).toBeInTheDocument();
});
it('matches blocking-issue identifiers exactly', () => {
  expect(mentions('WINDOW: A10 starts late', 'A1')).toBe(false);
  expect(mentions('WINDOW: A1-B starts late', 'A1')).toBe(false);
  expect(mentions('WINDOW: A1 starts late', 'A1')).toBe(true);
  expect(mentions('Order A1.', 'A1')).toBe(true);
  expect(mentions('A10, A1', 'A1')).toBe(true);
  expect(mentions('anything', '')).toBe(false);
});
it('inspects the delivery named in a blocking issue, not one whose identifier is a prefix', () => {
  const p = structuredClone(plan());
  p.input_data.orders[0].data.external_id = 'A1';
  p.input_data.orders[1].data.external_id = 'A10';
  p.validation = 'FAIL';
  p.result.blocking_issues = ['WINDOW: A10 starts after its hard window'];
  view(p);
  fireEvent.click(screen.getByRole('button', { name: 'Inspect vehicle and stops' }));
  expect(screen.getByRole('button', { name: '2. A10' }).closest('tr')).toHaveClass('selected-stop');
  expect(screen.getByRole('button', { name: '1. A1' }).closest('tr')).not.toHaveClass('selected-stop');
});
it('gives each of up to 25 vehicles its own route color and uses it for the list chip', () => {
  expect(routeColors).toHaveLength(25);
  expect(new Set(routeColors.map((c) => c.toLowerCase())).size).toBe(25);
  const channel = (c: string, i: number) => {
    const v = parseInt(c.slice(1 + 2 * i, 3 + 2 * i), 16) / 255;
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
  };
  const contrast = (c: string) =>
    1.05 / (0.2126 * channel(c, 0) + 0.7152 * channel(c, 1) + 0.0722 * channel(c, 2) + 0.05);
  // White route letters on pins and legend tags stay readable on every colour, in both themes.
  expect(routeColors.filter((c) => contrast(c) < 4.5)).toEqual([]);
  const p = plan();
  view(p);
  p.input_data.vehicles.forEach((v, i) =>
    expect(
      screen.getByRole('region', { name: `Route ${v.data.fleet_number}` }).querySelector('.route-dot'),
    ).toHaveStyle({ background: routeColors[i] }),
  );
});
it('closes the phone plan actions menu on Escape or a press outside, focus back on its button', () => {
  view();
  const toggle = screen.getByRole('button', { name: 'Plan actions' });
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute('aria-expanded', 'true');
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  expect(toggle).toHaveFocus();
  fireEvent.click(toggle);
  fireEvent.pointerDown(document.body);
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
});
it('explains deliveries left unassigned because no truck road is near the pin', () => {
  const p = plan();
  p.result.unassigned = [{ order_id: p.input_data.orders[2].id, reason: 'NO_ROAD_NEAR_PIN' }];
  view(p);
  expect(
    screen.getByText(/No road usable by the compatible trucks within 50 m of the pin/),
  ).toBeInTheDocument();
});

it('says a plan of example records is for trying Tournevia, not for driving', () => {
  const p = structuredClone(plan());
  view(p).unmount();
  expect(screen.queryByText(/A plan of example deliveries and vehicles/)).toBeNull();
  p.input_data.vehicles = p.input_data.vehicles.map((v) => ({ ...v, example: true }));
  view(p);
  expect(screen.getByText(/A plan of example deliveries and vehicles/)).toBeVisible();
});

it('shows each stop’s margin before its window ends, the day’s smallest, the waiting and the truck', () => {
  const p = plan(),
    route = p.result.routes[0];
  // Windows end at 16:00 Paris time (57 600 s after midnight). The first stop waits 18 min for its
  // window; the second arrives 5 min before its window ends.
  route.stops[0].arrival = 28800 - 1080;
  route.stops[0].service_start = 28800;
  route.waiting_seconds = 1080;
  route.stops[1].arrival = 57600 - 300;
  route.stops[1].service_start = 57600 - 300;
  view(p);
  const row = (name: string) => screen.getByRole('button', { name }).closest('tr')!;
  expect(row(`1. ${first.data.external_id}`)).toHaveTextContent(
    /08:00\s*waits 18 min.*08:00–16:00\s*Margin 8 h 18 min\s*8 h 18 min$/,
  );
  expect(row(`2. ${second.data.external_id}`)).toHaveTextContent(/5 min · tight$/);
  const figure = screen.getByText('Smallest margin').parentElement!;
  expect(figure).toHaveTextContent(new RegExp(`^5 min ${second.data.external_id}`));
  expect(figure).toHaveClass('attention');
  expect(
    screen.getByText(
      new RegExp(`1 stop is reached 10 min or less before its window ends: ${second.data.external_id}\\.`),
    ),
  ).toBeVisible();
  expect(screen.getByText('18 min waiting')).toBeInTheDocument();
  expect(screen.getByTitle(`Waits 18 min for the window of ${first.data.external_id}`)).toHaveClass(
    'day-wait',
  );
  expect(screen.getByText('H 3.6 · W 2.55 · L 9.6 m · 19 t gross')).toBeInTheDocument();
  // After a manual move the times are the previous result's until recalculation.
  fireEvent.click(screen.getByText('Select first map stop'));
  fireEvent.click(screen.getByLabelText(`Move ${first.data.external_id} down`));
  expect(screen.getByText('Previous smallest margin')).toBeInTheDocument();
  expect(row(`2. ${first.data.external_id}`)).toHaveTextContent(/08:00–16:00\s*—$/);
});

it('says the smallest margin and the margin column in French', () => {
  act(() => setLanguage('fr'));
  const p = plan();
  p.result.routes[0].stops[1].arrival = 57600;
  p.result.routes[0].stops[1].service_start = 57600;
  view(p);
  expect(screen.getByText('Marge minimale').parentElement).toHaveTextContent(
    new RegExp(`^0 min ${second.data.external_id}`),
  );
  expect(screen.getByRole('columnheader', { name: 'Marge' })).toBeInTheDocument();
  expect(screen.getByText('H 3,6 · l 2,55 · L 9,6 m · poids total 19 t')).toBeInTheDocument();
});

it('calls a vehicle without deliveries not used, with nothing to show on the map', () => {
  // The fixture has two vehicles; the plan routes only the first.
  view();
  const unused = screen.getByRole('region', { name: `Route ${fixture.vehicles[1].data.fleet_number}` });
  expect(within(unused).getByText('Not used')).toBeInTheDocument();
  expect(within(unused).queryByRole('button', { name: 'On map' })).not.toBeInTheDocument();
  const used = screen.getByRole('region', { name: `Route ${fixture.vehicles[0].data.fleet_number}` });
  expect(within(used).getByText('No blocking issues')).toBeInTheDocument();
  expect(within(used).getByRole('button', { name: 'On map' })).toBeInTheDocument();
});

it('says the safety margin a plan was calculated with, and why a delivery may have stayed unassigned', () => {
  const p = plan();
  p.input_data.safety_margin_seconds = 900;
  view(p);
  expect(screen.getByText('Safety margin').nextElementSibling).toHaveTextContent(
    '15 min before each window ends',
  );
  expect(screen.getByText(/Calculated with a 15 min safety margin before each window ends/)).toBeVisible();
  cleanup();
  const old = plan();
  // A plan calculated before the margin existed comes back from the server with 0.
  old.input_data.safety_margin_seconds = 0;
  view(old);
  expect(screen.getByText('Safety margin').nextElementSibling).toHaveTextContent(
    'None: planned up to the last minute',
  );
  expect(screen.queryByText(/Calculated with a .* safety margin/)).not.toBeInTheDocument();
});

it('says where each truck loads when the plan has more than one place', () => {
  const p = plan();
  view(p);
  // One depot: nothing to say.
  expect(screen.queryByText(/^Loaded at/)).not.toBeInTheDocument();
  cleanup();
  const north = {
    ...p.input_data.depot,
    id: '00000000-0000-0000-0000-0000000000d2',
    main: false,
    data: { ...p.input_data.depot.data, code: 'NORD', name: 'Dépôt Saint-Denis' },
  };
  const two = plan();
  two.input_data.depots = [north];
  two.input_data.vehicles[0].data.start = 'NORD';
  two.input_data.vehicles[0].data.end = p.input_data.depot.data.code;
  view(two);
  expect(
    screen.getByText(`Loaded at Dépôt Saint-Denis · ends at ${p.input_data.depot.data.name}`),
  ).toBeVisible();
  expect(screen.getByText(`Loaded at ${p.input_data.depot.data.name} · returns there`)).toBeVisible();
});
