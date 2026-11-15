import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import fixture from '../../backend/tests/fixtures/paris-ten.json';
import { ExampleSettings } from './ExampleData';
import { ConfirmHost } from './confirm';
import { SetupWizard } from './SetupWizard';
import { Workspace } from './Workspace';
import { RouteSheetsPage } from './RouteSheets';
import { savedVehicles, sourceResponse } from './test-server';
import type { Depot, Order, RouteSheets } from './api';

vi.mock('./MapPanel', () => ({ MapPanel: () => null }));
afterEach(() => vi.unstubAllGlobals());

const added = {
  service_date: '2026-11-03',
  depot: 'EXAMPLE',
  vehicles: ['EX-TRUCK-01', 'EX-VAN-01'],
  deliveries: Array.from({ length: 10 }, (_, i) => `EX-20261103-${String(i + 1).padStart(2, '0')}`),
};
function server() {
  const calls: [string, RequestInit | undefined][] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      calls.push([url, options]);
      return {
        ok: true,
        json: async () => (url.endsWith('/examples/remove') ? { removed: 13 } : added),
      };
    }),
  );
  return calls;
}

it('adds example deliveries for the planning day and removes every example only after asking', async () => {
  const calls = server();
  const onChanged = vi.fn();
  const view = render(
    <>
      <ExampleSettings present={false} day="2026-11-03" onChanged={onChanged} />
      <ConfirmHost />
    </>,
  );
  expect(screen.getByText('This workspace has no example data.')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Remove example data' })).toBeNull();
  // The planning day by default; another day can be chosen here.
  expect(screen.getByLabelText('Day of the example deliveries')).toHaveValue('2026-11-03');
  fireEvent.change(screen.getByLabelText('Day of the example deliveries'), {
    target: { value: '2026-11-05' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Add example deliveries' }));
  await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
  expect(calls[0][0]).toBe('/api/v1/examples');
  expect(JSON.parse(calls[0][1]!.body as string)).toEqual({ service_date: '2026-11-05' });
  expect(onChanged.mock.calls[0][0]).toContain('10 example deliveries added for');
  expect(onChanged.mock.calls[0][0]).toContain('Example vehicles added: EX-TRUCK-01, EX-VAN-01.');
  view.rerender(
    <>
      <ExampleSettings present day="2026-11-03" onChanged={onChanged} />
      <ConfirmHost />
    </>,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Remove example data' }));
  const dialog = await screen.findByRole('alertdialog');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Keep them' }));
  await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull());
  expect(calls).toHaveLength(1); // Kept.
  fireEvent.click(screen.getByRole('button', { name: 'Remove example data' }));
  fireEvent.click(
    within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Remove example data' }),
  );
  await waitFor(() => expect(onChanged).toHaveBeenLastCalledWith('13 example records removed.'));
  expect(calls[1][0]).toBe('/api/v1/examples/remove');
});

it('offers example deliveries in the setup guide', async () => {
  const calls = server();
  const onExamplesAdded = vi.fn();
  render(
    <SetupWizard
      day="2026-11-03"
      onClose={vi.fn()}
      onVehiclesAdded={vi.fn()}
      onImport={vi.fn()}
      onAddDelivery={vi.fn()}
      onExamplesAdded={onExamplesAdded}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Skip for now' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Skip for now' }));
  fireEvent.click(screen.getByRole('button', { name: /Try with example deliveries/ }));
  await waitFor(() => expect(onExamplesAdded).toHaveBeenCalledWith(added));
  expect(calls.at(-1)![0]).toBe('/api/v1/examples');
});

it('marks example records, warns about them and never mixes them in one selection', async () => {
  const depot = fixture.depot as Depot;
  const vehicles = savedVehicles(fixture.vehicles).map((v, i) => ({ ...v, example: i === 0 }));
  const orders = fixture.orders.map((o) => ({ ...o, example: true })) as unknown as Order[];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => ({
      ok: true,
      json: async () => (path.endsWith('/depot') ? depot : (sourceResponse(path, vehicles, orders) ?? [])),
    })),
  );
  render(
    <Workspace
      depot={depot}
      role="admin"
      configuration={{
        mode: 'shared',
        company_name: 'Transports Fictifs',
        data_kind: 'UNVERIFIED',
        examples: true,
        map_style_url: '',
        safety_margin_minutes: 15,
      }}
    />,
  );
  expect(await screen.findByText('Example data is in this workspace.')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Manage example data' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Fleet' }));
  const truck = (
    await screen.findByText(fixture.vehicles[0].data.fleet_number, { selector: 'strong' })
  ).closest('td')!;
  expect(within(truck).getByText('Example')).toBeVisible();
  const van = screen.getByText(fixture.vehicles[1].data.fleet_number, { selector: 'strong' }).closest('td')!;
  expect(within(van).queryByText('Example')).toBeNull();
  // The deliveries of the day are examples: selecting every vehicle then keeps to the example ones.
  fireEvent.click(screen.getByRole('button', { name: 'Deliveries' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Select all reviewed deliveries for this day' }));
  expect(await screen.findByText('10 deliveries selected across pages')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Fleet' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Select all active vehicles' }));
  expect(
    await screen.findByText('Only the examples are selected: they are planned only with other examples.'),
  ).toBeVisible();
  expect(screen.getByText('1 vehicle selected across pages')).toBeVisible();
});

it('prints that an example plan is not for driving', async () => {
  const sheets: RouteSheets = {
    plan_id: '7f1c2b9e-1d2a-4c3b-9e8f-0a1b2c3d4e5f',
    plan_version: 2,
    service_date: '2026-11-03',
    company: 'Transports Fictifs',
    generated_at: '2026-11-02T17:00:00+01:00',
    example: true,
    sheets: [
      {
        vehicle_id: '11111111-2222-4333-8444-555555555555',
        vehicle: 'EX-VAN-01',
        start: {
          code: 'EXAMPLE',
          name: 'Example depot (Paris)',
          address: '',
          latitude: 48.835,
          longitude: 2.375,
        },
        end: {
          code: 'EXAMPLE',
          name: 'Example depot (Paris)',
          address: '',
          latitude: 48.835,
          longitude: 2.375,
        },
        departure: '2026-11-03T07:30:00+01:00',
        arrival: '2026-11-03T09:00:00+01:00',
        distance_m: 1000,
        driving_seconds: 600,
        service_seconds: 600,
        waiting_seconds: 0,
        load: { weight_kg: 0, volume_dm3: 0, pallet_eur_units: 0, pallet_ind_units: 0, loading_length_mm: 0 },
        stops: [],
      },
    ],
  };
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, json: async () => sheets })),
  );
  render(<RouteSheetsPage route={{ plan: sheets.plan_id, version: 2 }} />);
  const sheet = await screen.findByRole('article', { name: 'Route sheet EX-VAN-01' });
  expect(within(sheet).getByRole('note')).toHaveTextContent('Example data, not for driving.');
});
