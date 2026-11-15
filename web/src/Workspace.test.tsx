import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { it, expect, vi } from 'vitest';
import fixture from '../../backend/tests/fixtures/paris-ten.json';
import type { Depot, Order, Vehicle } from './api';
import { Workspace, readRoute, routeHash } from './Workspace';
import { saved, savedVehicles, sourceResponse } from './test-server';

vi.mock('./MapPanel', () => ({ MapPanel: () => null }));

it('keeps selections through actual source pagination and clears them on a service-date change', async () => {
  sessionStorage.clear();
  const depot = fixture.depot as Depot,
    vehicles = savedVehicles(fixture.vehicles);
  const orders = Array.from({ length: 100 }, (_, i) => ({
    ...fixture.orders[0],
    id: `order-${i}`,
    data: { ...fixture.orders[0].data, external_id: `ORDER-${i}` },
  })) as Order[];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => {
      return {
        ok: true,
        json: async () => (path.endsWith('/depot') ? depot : (sourceResponse(path, vehicles, orders) ?? [])),
      };
    }),
  );
  render(
    <Workspace
      depot={depot}
      configuration={{
        mode: 'local_demo',
        company_name: 'Demo',
        data_kind: 'SYNTHETIC',
        examples: false,
        map_style_url: 'https://maps.example/style.json',
        safety_margin_minutes: 15,
      }}
    />,
  );
  await screen.findByLabelText('ORDER-0');
  fireEvent.click(screen.getByText('Select records on this page'));
  fireEvent.click(screen.getByText('Select active vehicles on this page'));
  fireEvent.click(screen.getByText('Select reviewed deliveries on this page'));
  fireEvent.click(screen.getByLabelText('Next orders page'));
  await screen.findByLabelText('ORDER-50');
  fireEvent.click(screen.getByText('Select reviewed deliveries on this page'));
  expect(screen.getByText(/2 \/ 25 vehicles · 100 \/ 250 deliveries/)).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText('Previous orders page'));
  expect(await screen.findByLabelText('ORDER-0')).toBeChecked();
  fireEvent.click(screen.getByRole('button', { name: 'Deliveries' }));
  expect(screen.getByRole('heading', { name: 'Deliveries', level: 1 })).toBeVisible();
  expect(await screen.findByText('All deliveries 100')).toBeVisible();
  // Search runs across the whole day, not only the page on screen.
  fireEvent.change(screen.getByLabelText('Search deliveries of this day'), { target: { value: 'order-99' } });
  expect(await screen.findByText(/1–1 of 1/)).toBeVisible();
  expect(screen.getByText('ORDER-99', { selector: 'strong' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Fleet' }));
  fireEvent.click(screen.getByRole('button', { name: 'Planning' }));
  expect(await screen.findByLabelText('ORDER-0')).toBeChecked(); // The unsearched list reloads.
  expect(screen.getByText(/2 \/ 25 vehicles · 100 \/ 250 deliveries/)).toBeVisible();
  fireEvent.change(screen.getByLabelText('Planning date'), { target: { value: '2026-10-02' } });
  await waitFor(() => expect(screen.getByText(/0 \/ 25 vehicles · 0 \/ 250 deliveries/)).toBeInTheDocument());
  expect(screen.getByText('Start new calculation')).toBeDisabled();
});

it('carries source-table selections into planning and blocks unreviewed deliveries', async () => {
  sessionStorage.clear();
  const depot = fixture.depot as Depot,
    vehicles = savedVehicles(fixture.vehicles);
  const orders = [
    {
      ...fixture.orders[0],
      data: { ...fixture.orders[0].data, location: { ...fixture.orders[0].data.location, reviewed: false } },
    },
  ] as Order[];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => ({
      ok: true,
      json: async () => (path.includes('/depot') ? depot : (sourceResponse(path, vehicles, orders) ?? [])),
    })),
  );
  render(
    <Workspace
      depot={depot}
      configuration={{
        mode: 'local',
        company_name: 'Test company',
        data_kind: 'UNVERIFIED',
        examples: false,
        map_style_url: '',
        safety_margin_minutes: 15,
      }}
    />,
  );
  await screen.findByLabelText(orders[0].data.external_id + ' · needs point review');
  fireEvent.click(screen.getByRole('button', { name: 'Deliveries' }));
  fireEvent.click(screen.getByRole('checkbox', { name: `Select ${orders[0].data.external_id}` }));
  fireEvent.click(screen.getByRole('button', { name: 'Fleet' }));
  fireEvent.click(screen.getByRole('checkbox', { name: `Select ${vehicles[0].data.fleet_number}` }));
  fireEvent.click(screen.getByRole('button', { name: 'Planning' }));
  expect(screen.getByText('1 deliveries need attention')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Start new calculation' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Review locations' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Deliveries' }));
  expect(screen.getByRole('checkbox', { name: `Select ${orders[0].data.external_id}` })).toBeChecked();
  // The planning view stays mounted while hidden; its header actions must not follow the user.
  expect(screen.queryByRole('button', { name: 'Review locations' })).not.toBeInTheDocument();
});

it('explains an empty planning date without presenting an existing fleet as a new installation', async () => {
  sessionStorage.clear();
  const depot = fixture.depot as Depot;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => ({
      ok: true,
      json: async () =>
        path.includes('/depot') ? depot : (sourceResponse(path, savedVehicles(fixture.vehicles), []) ?? []),
    })),
  );
  render(
    <Workspace
      depot={depot}
      configuration={{
        mode: 'local',
        company_name: 'Existing company',
        data_kind: 'UNVERIFIED',
        examples: false,
        map_style_url: '',
        safety_margin_minutes: 15,
      }}
    />,
  );
  expect(await screen.findByText(/No deliveries for/)).toBeVisible();
  expect(
    screen.queryByText('Complete these first-use steps to calculate your first plan.'),
  ).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Start new calculation' })).toBeDisabled();
});

it('shows the calculation service warning only in planning and refuses a page selection beyond the fleet limit', async () => {
  sessionStorage.clear();
  const depot = fixture.depot as Depot;
  const fleet = Array.from({ length: 26 }, (_, i) => ({
    ...saved(fixture.vehicles[0]),
    id: `v-${i}`,
    data: { ...saved(fixture.vehicles[0]).data, active: true, fleet_number: `V-${i}` },
  })) as Vehicle[];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => ({
      ok: true,
      json: async () =>
        path.includes('/system-status')
          ? {
              worker: 'NOT_RUNNING',
              routing: 'AVAILABLE',
              dataset: 'Test',
              queued_jobs: 0,
              running_jobs: 0,
              oldest_queued_seconds: null,
            }
          : path.includes('/depot')
            ? depot
            : (sourceResponse(path, fleet, []) ?? []),
    })),
  );
  render(
    <Workspace
      depot={depot}
      configuration={{
        mode: 'local',
        company_name: 'Existing company',
        data_kind: 'UNVERIFIED',
        examples: false,
        map_style_url: '',
        safety_margin_minutes: 15,
      }}
    />,
  );
  expect(await screen.findByText(/calculation worker is not running/)).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Fleet' }));
  expect(screen.queryByText(/calculation worker is not running/)).not.toBeInTheDocument();
  expect(await screen.findByLabelText('Select this page (26)')).toBeDisabled();
  expect(screen.getByText(/would pass the limit of 25/)).toBeVisible();
  fireEvent.click(screen.getByRole('checkbox', { name: 'Select V-0' }));
  expect(screen.getByText('1 vehicle selected across pages')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Select all active vehicles' }));
  expect(await screen.findByText(/26 records are eligible; select at most 25/)).toBeVisible();
  expect(screen.getByText('1 vehicle selected across pages')).toBeInTheDocument();
});

it('shows each vehicle’s size, weights and weekly hours, and names those that do not work on the planning day', async () => {
  sessionStorage.clear();
  const depot = fixture.depot as Depot;
  const base = saved(fixture.vehicles[0]);
  // 23 September 2026 is a Wednesday.
  const weekdays = Object.fromEntries(
    ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'].map((d, i) => [
      d,
      i < 5 ? { start: '07:30', end: '17:00' } : i === 5 ? { start: '08:00', end: '12:00' } : null,
    ]),
  );
  const fleet = [
    {
      ...base,
      id: 'v-week',
      data: { ...base.data, active: true, fleet_number: 'TRK-WEEK', hours: weekdays },
    },
    {
      ...base,
      id: 'v-off',
      data: {
        ...base.data,
        active: true,
        fleet_number: 'TRK-OFF',
        hours: weekdays,
        exceptions: [{ date: '2026-09-23', hours: null }],
      },
    },
  ] as Vehicle[];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => ({
      ok: true,
      json: async () => (path.includes('/depot') ? depot : (sourceResponse(path, fleet, []) ?? [])),
    })),
  );
  window.history.replaceState(null, '', '/#/vehicles?date=2026-09-23');
  render(
    <Workspace
      depot={depot}
      configuration={{
        mode: 'local',
        company_name: 'Test company',
        data_kind: 'UNVERIFIED',
        examples: false,
        map_style_url: '',
        safety_margin_minutes: 15,
      }}
    />,
  );
  expect(await screen.findByText('1 active vehicle does not work that day')).toBeVisible();
  expect(screen.getByText('TRK-OFF · day off')).toBeVisible();
  expect(screen.getAllByText('Mon–Fri 07:30–17:00 · Sat 08:00–12:00')).toHaveLength(2);
  expect(screen.getAllByText(/^Off Wed,? 23 Sept 2026$/)).toHaveLength(1);
  // The truck routing uses, beside the payload it carries.
  expect(screen.getAllByText('H 3.6 · W 2.55 · L 9.6 m')).toHaveLength(2);
  expect(screen.getAllByText('19 t gross · 11.5 t per axle · 2 axles')).toHaveLength(2);
});

it('selects every active reviewed delivery of the day in one step', async () => {
  sessionStorage.clear();
  const depot = fixture.depot as Depot;
  const orders = Array.from({ length: 120 }, (_, i) => ({
    ...fixture.orders[0],
    id: `order-${i}`,
    data: {
      ...fixture.orders[0].data,
      external_id: `ORDER-${i}`,
      active: i !== 7,
      location: { ...fixture.orders[0].data.location, reviewed: i % 10 !== 3 },
    },
  })) as Order[];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => {
      return {
        ok: true,
        json: async () => (path.endsWith('/depot') ? depot : (sourceResponse(path, [], orders) ?? [])),
      };
    }),
  );
  render(
    <Workspace
      depot={depot}
      configuration={{
        mode: 'local_demo',
        company_name: 'Demo',
        data_kind: 'SYNTHETIC',
        examples: false,
        map_style_url: '',
        safety_margin_minutes: 15,
      }}
    />,
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Deliveries' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Select all reviewed deliveries for this day' }));
  // 120 deliveries, minus 12 with unreviewed locations and one inactive one.
  expect(await screen.findByText('107 deliveries selected across pages')).toBeInTheDocument();
});

it('reads and writes only well-formed workspace locations', () => {
  expect(readRoute('#/vehicles?date=2026-09-23')).toEqual({
    area: 'vehicles',
    date: '2026-09-23',
    plan: undefined,
  });
  expect(readRoute('#/planning?date=2026-9-3&plan=../x')).toEqual({
    area: 'planning',
    date: undefined,
    plan: undefined,
  });
  expect(readRoute('#/admin')).toBeNull();
  expect(readRoute('#setup=private-token')).toBeNull();
  const plan = '0123abcd-0000-4000-8000-000000000001';
  expect(readRoute(routeHash({ area: 'planning', date: '2026-09-23', plan }))).toEqual({
    area: 'planning',
    date: '2026-09-23',
    plan,
  });
});

it('opens the section and date in the URL, records navigation and goes back', async () => {
  sessionStorage.clear();
  window.history.replaceState(null, '', '/#/vehicles?date=2026-09-23');
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => ({
      ok: true,
      json: async () =>
        path.includes('/depot')
          ? fixture.depot
          : path.includes('/system-status')
            ? {}
            : (sourceResponse(path, [], []) ?? []),
    })),
  );
  render(
    <Workspace
      depot={fixture.depot as Depot}
      configuration={{
        mode: 'local_demo',
        company_name: 'Demo',
        data_kind: 'SYNTHETIC',
        examples: false,
        map_style_url: '',
        safety_margin_minutes: 15,
      }}
    />,
  );
  expect(await screen.findByRole('heading', { name: 'Fleet', level: 1 })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Deliveries' }));
  await waitFor(() => expect(window.location.hash).toBe('#/orders?date=2026-09-23'));
  expect(screen.getByLabelText('Planning date')).toHaveValue('2026-09-23');
  fireEvent.click(screen.getByRole('button', { name: 'Import CSV' }));
  expect(screen.getByRole('heading', { name: 'Import deliveries', level: 1 })).toBeVisible();
  window.history.back();
  // Back opens the fleet as the menu would: the deliveries import does not follow.
  expect(await screen.findByRole('heading', { name: 'Fleet', level: 1 })).toBeVisible();
  expect(screen.queryByRole('heading', { name: 'Import vehicles' })).not.toBeInTheDocument();
  expect(window.location.hash).toBe('#/vehicles?date=2026-09-23');
});

it('counts and filters deliveries across the whole day, not only the page on screen', async () => {
  sessionStorage.clear();
  const depot = fixture.depot as Depot;
  const orders = Array.from({ length: 120 }, (_, i) => ({
    ...fixture.orders[0],
    id: `order-${i}`,
    data: {
      ...fixture.orders[0].data,
      external_id: `ORDER-${i}`,
      location: i === 5 ? null : { ...fixture.orders[0].data.location, reviewed: i % 10 !== 3 },
    },
  })) as Order[];
  const fetch = vi.fn(async (path: string) => ({
    ok: true,
    json: async () =>
      path.endsWith('/depot') ? depot : (sourceResponse(path, savedVehicles(fixture.vehicles), orders) ?? []),
  }));
  vi.stubGlobal('fetch', fetch);
  window.history.replaceState(null, '', '/#/orders?date=2026-09-23');
  render(
    <Workspace
      depot={depot}
      configuration={{
        mode: 'local_demo',
        company_name: 'Demo',
        data_kind: 'SYNTHETIC',
        examples: false,
        map_style_url: '',
        safety_margin_minutes: 15,
      }}
    />,
  );
  expect(await screen.findByText('All deliveries 120')).toBeVisible();
  expect(screen.getByText('Page 1 of 3')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Needs review 12' }));
  expect(await screen.findByText(/1–12 of 12/)).toBeVisible();
  expect(screen.getByText('ORDER-113', { selector: 'strong' })).toBeVisible();
  expect(fetch.mock.calls.some(([path]) => String(path).endsWith('&status=review'))).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Location missing 1' }));
  expect(await screen.findByText(/1–1 of 1/)).toBeVisible();
  // Each row's entrance button names its delivery, so a screen reader hears more than "Location missing".
  expect(screen.getByRole('button', { name: 'Location missing · ORDER-5' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Fleet' }));
  expect(await screen.findByText('Whole fleet')).toBeVisible();
});

it('asks in its own dialog before an open record loses unsaved edits', async () => {
  sessionStorage.clear();
  window.history.replaceState(null, '', '/#/vehicles?date=2026-09-23');
  const vehicles = savedVehicles(fixture.vehicles);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => ({
      ok: true,
      json: async () =>
        path.includes('/depot')
          ? fixture.depot
          : path.includes('/system-status')
            ? {}
            : (sourceResponse(path, vehicles, []) ?? []),
    })),
  );
  const browser = vi.spyOn(window, 'confirm');
  render(
    <Workspace
      depot={fixture.depot as Depot}
      configuration={{
        mode: 'local_demo',
        company_name: 'Demo',
        data_kind: 'SYNTHETIC',
        examples: false,
        map_style_url: '',
        safety_margin_minutes: 15,
      }}
    />,
  );
  fireEvent.click((await screen.findAllByRole('button', { name: /^Edit/ }))[0]);
  const editor = await screen.findByRole('dialog', { name: 'Record editor' });
  fireEvent.change(within(editor).getByLabelText('Fleet number'), { target: { value: 'VUL-99' } });
  fireEvent.click(screen.getByRole('button', { name: 'Deliveries' }));
  const question = await screen.findByRole('alertdialog', { name: 'Discard unsaved record changes?' });
  fireEvent.click(within(question).getByRole('button', { name: 'Keep editing' }));
  await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
  expect(screen.getByRole('heading', { name: 'Fleet', level: 1 })).toBeInTheDocument();
  expect(within(editor).getByLabelText('Fleet number')).toHaveValue('VUL-99');
  fireEvent.click(within(editor).getByRole('button', { name: 'Close editor' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Discard changes' }));
  await waitFor(() =>
    expect(screen.queryByRole('dialog', { name: 'Record editor' })).not.toBeInTheDocument(),
  );
  expect(browser).not.toHaveBeenCalled();
});

it('cancels, restores and deletes selected deliveries after asking, each at its current version', async () => {
  sessionStorage.clear();
  window.history.replaceState(null, '', '/#/orders?date=2026-09-23');
  let orders = structuredClone(fixture.orders) as unknown as Order[];
  const sent: { path: string; body: unknown }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, options?: RequestInit) => {
      const body = options?.body ? JSON.parse(options.body as string) : undefined;
      let answer: unknown;
      if (path.endsWith('/orders/activation') || path.endsWith('/orders/delete')) {
        sent.push({ path, body });
        const keys = body.orders.map((o: { id: string }) => o.id);
        if (path.endsWith('/delete')) {
          orders = orders.filter((o) => !keys.includes(o.id));
          answer = { deleted: keys.length };
        } else {
          orders = orders.map((o) =>
            keys.includes(o.id)
              ? { ...o, version: o.version + 1, data: { ...o.data, active: body.active } }
              : o,
          );
          answer = orders.filter((o) => keys.includes(o.id));
        }
      } else
        answer = path.includes('/depot')
          ? fixture.depot
          : path.includes('/system-status')
            ? {}
            : (sourceResponse(path, [], orders) ?? []);
      return { ok: true, json: async () => answer };
    }),
  );
  render(
    <Workspace
      depot={fixture.depot as Depot}
      configuration={{
        mode: 'local_demo',
        company_name: 'Demo',
        data_kind: 'SYNTHETIC',
        examples: false,
        map_style_url: '',
        safety_margin_minutes: 15,
      }}
    />,
  );
  fireEvent.click(await screen.findByLabelText('Select DEMO-ORDER-001'));
  fireEvent.click(screen.getByLabelText('Select DEMO-ORDER-002'));
  const bar = screen.getByRole('group', { name: 'Selected deliveries' });
  expect(within(bar).getByText('2 deliveries selected')).toBeVisible();
  // Asking first: keeping them sends nothing.
  fireEvent.click(within(bar).getByRole('button', { name: 'Cancel deliveries' }));
  fireEvent.click(
    within(await screen.findByRole('alertdialog', { name: 'Cancel 2 deliveries?' })).getByRole('button', {
      name: 'Keep them',
    }),
  );
  await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
  expect(sent).toEqual([]);
  fireEvent.click(within(bar).getByRole('button', { name: 'Cancel deliveries' }));
  fireEvent.click(
    within(await screen.findByRole('alertdialog', { name: 'Cancel 2 deliveries?' })).getByRole('button', {
      name: 'Cancel deliveries',
    }),
  );
  expect(await screen.findByText('2 deliveries cancelled.')).toBeVisible();
  expect(sent[0]).toEqual({
    path: '/api/v1/orders/activation',
    body: {
      orders: [
        { id: fixture.orders[0].id, version: 1 },
        { id: fixture.orders[1].id, version: 1 },
      ],
      active: false,
    },
  });
  expect(screen.queryByRole('group', { name: 'Selected deliveries' })).not.toBeInTheDocument();
  fireEvent.click(await screen.findByRole('button', { name: 'Restore DEMO-ORDER-001' }));
  expect(await screen.findByText('Delivery DEMO-ORDER-001 restored.')).toBeVisible();
  expect(sent[1].body).toEqual({ orders: [{ id: fixture.orders[0].id, version: 2 }], active: true });
  fireEvent.click(await screen.findByLabelText('Select DEMO-ORDER-003'));
  fireEvent.click(screen.getByRole('button', { name: 'Delete deliveries' }));
  fireEvent.click(
    within(await screen.findByRole('alertdialog', { name: 'Delete 1 delivery?' })).getByRole('button', {
      name: 'Delete deliveries',
    }),
  );
  expect(await screen.findByText('1 delivery deleted.')).toBeVisible();
  expect(sent[2]).toEqual({
    path: '/api/v1/orders/delete',
    body: { orders: [{ id: fixture.orders[2].id, version: 1 }] },
  });
  await waitFor(() => expect(screen.queryByText('DEMO-ORDER-003', { selector: 'strong' })).toBeNull());
});

it('filters with one row of tabs and says what an empty tab means', async () => {
  sessionStorage.clear();
  window.history.replaceState(null, '', '/');
  const depot = fixture.depot as Depot;
  // Three reviewed deliveries, one of them cancelled (inactive).
  const orders = [0, 1, 2].map((i) => ({
    ...fixture.orders[0],
    id: `order-${i}`,
    data: {
      ...fixture.orders[0].data,
      external_id: `ORDER-${i}`,
      active: i !== 2,
      location: { ...fixture.orders[0].data.location, reviewed: true },
    },
  })) as Order[];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => ({
      ok: true,
      json: async () =>
        path.endsWith('/depot')
          ? depot
          : (sourceResponse(path, savedVehicles(fixture.vehicles), orders) ?? []),
    })),
  );
  render(
    <Workspace
      depot={depot}
      configuration={{
        mode: 'local',
        company_name: 'Test company',
        data_kind: 'UNVERIFIED',
        examples: false,
        map_style_url: '',
        safety_margin_minutes: 15,
      }}
    />,
  );
  fireEvent.click(await screen.findByRole('button', { name: 'Deliveries' }));
  // Inactive deliveries get a tab because there is one; there is no second filter control.
  expect(await screen.findByRole('button', { name: 'Inactive deliveries 1' })).toBeInTheDocument();
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  fireEvent.click(await screen.findByRole('button', { name: 'Needs review 0' }));
  expect(await screen.findByRole('heading', { name: 'Every located delivery is reviewed' })).toBeVisible();
  expect(screen.getByText('Nothing to review for this day before calculating.')).toBeVisible();
  // Away from planning, the footer keeps the planning notice.
  expect(
    screen.getByText(
      'Planning estimate. Verify current road access and driver requirements. Tolls are not included.',
    ),
  ).toBeInTheDocument();
});
