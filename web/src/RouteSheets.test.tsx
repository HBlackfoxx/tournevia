import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { act } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { navigationLinks, readSheetRoute, RouteSheetsPage, sheetHash } from './RouteSheets';
import { setLanguage } from './i18n';
import type { RouteSheets } from './api';

const PLAN = '7f1c2b9e-1d2a-4c3b-9e8f-0a1b2c3d4e5f',
  VAN = '11111111-2222-4333-8444-555555555555',
  TRUCK = '66666666-7777-4888-8999-000000000000';
const depot = {
  code: 'MAIN',
  name: 'Dépôt Ivry-sur-Seine',
  address: '5 Rue Fictive 94200 Ivry-sur-Seine',
  latitude: 48.8156,
  longitude: 2.3849,
};
const none = { weight_kg: 0, volume_dm3: 0, pallet_eur_units: 0, pallet_ind_units: 0, loading_length_mm: 0 };
const at = (time: string) => `2026-10-31T${time}:00+01:00`;
const stop = (sequence: number, delivery: string, arrival: string, start: string, extra = {}) => ({
  sequence,
  order_id: `order-${sequence}-${delivery}`,
  delivery,
  customer: null,
  address: `${sequence} Rue de Test 75011 Paris`,
  latitude: 48.85 + sequence / 100,
  longitude: 2.37,
  time_window: { start: at('08:00'), end: at('10:00') },
  arrival: at(arrival),
  service_start: at(start),
  departure: at(start),
  service_seconds: 900,
  demand: { ...none, weight_kg: 300, pallet_eur_units: 1 },
  required_skills: [],
  instructions: '',
  ...extra,
});
const data: RouteSheets = {
  plan_id: PLAN,
  plan_version: 4,
  service_date: '2026-10-31',
  company: 'Transports Fictifs',
  generated_at: '2026-10-30T17:05:00+01:00',
  example: false,
  sheets: [
    {
      vehicle_id: VAN,
      vehicle: 'VUL-07',
      start: {
        code: 'DOM-01',
        name: 'Domicile du chauffeur',
        address: '20 Rue de Paris 93100 Montreuil',
        latitude: 48.859,
        longitude: 2.438,
      },
      end: depot,
      departure: at('07:30'),
      arrival: at('12:10'),
      distance_m: 42500,
      driving_seconds: 5400,
      service_seconds: 1800,
      waiting_seconds: 600,
      load: { ...none, weight_kg: 1200, volume_dm3: 2400, pallet_eur_units: 4 },
      stops: [
        stop(1, 'LIV-2026-0001', '07:50', '08:00', {
          customer: 'LEM',
          instructions: 'Quai 2, sonner à l’accueil',
          required_skills: ['TAIL_LIFT'],
        }),
        stop(2, 'LIV-2026-0002', '08:40', '08:40'),
      ],
    },
    {
      vehicle_id: TRUCK,
      vehicle: 'VUL-01',
      start: depot,
      end: depot,
      departure: at('08:00'),
      arrival: at('11:00'),
      distance_m: 30000,
      driving_seconds: 3600,
      service_seconds: 900,
      waiting_seconds: 0,
      load: none,
      stops: [stop(1, 'LIV-2026-0003', '09:00', '09:00')],
    },
  ],
};

afterEach(() => {
  act(() => setLanguage('en'));
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.history.replaceState(null, '', '#/');
});

it('keeps the plan, its version and an optional vehicle in the sheets location', () => {
  const route = { plan: PLAN, version: 4, date: '2026-10-31', vehicle: VAN };
  expect(readSheetRoute(sheetHash(route))).toEqual(route);
  expect(readSheetRoute(sheetHash({ plan: PLAN, version: 2 }))).toEqual({
    plan: PLAN,
    version: 2,
    date: undefined,
    vehicle: undefined,
  });
  expect(readSheetRoute(`#/sheets?plan=${PLAN}&version=0`)).toBeNull();
  expect(readSheetRoute('#/sheets?plan=plan&version=1')).toBeNull();
  expect(readSheetRoute(`#/planning?plan=${PLAN}&version=1`)).toBeNull();
});

it('leads to the reviewed vehicle entrance in Google Maps and Waze', () => {
  expect(navigationLinks(48.8566, 2.3522)).toEqual({
    google: 'https://www.google.com/maps/dir/?api=1&destination=48.8566%2C2.3522&travelmode=driving',
    waze: 'https://waze.com/ul?ll=48.8566,2.3522&navigate=yes',
  });
});

it("prints each vehicle's day: its places, stops in order, times, load and navigation links", async () => {
  const fetch = vi.fn(async (_url: string, _options?: RequestInit) => ({ ok: true, json: async () => data }));
  vi.stubGlobal('fetch', fetch);
  const print = vi.spyOn(window, 'print').mockImplementation(() => undefined);
  render(<RouteSheetsPage route={{ plan: PLAN, version: 4, date: '2026-10-31' }} />);
  const van = await screen.findByRole('article', { name: 'Route sheet VUL-07' });
  expect(fetch.mock.calls[0][0]).toBe(`/api/v1/plans/${PLAN}/sheets`);
  expect((fetch.mock.calls[0][1]!.headers as Record<string, string>)['If-Match']).toBe('"4"');
  expect(within(van).getByText('Transports Fictifs')).toBeVisible();
  expect(within(van).getByText('Ready plan · version 4', { exact: false })).toBeVisible();
  // It leaves from the driver's home and ends at the depot.
  expect(within(van).getByText('Loads and leaves')).toBeVisible();
  expect(within(van).getByText('Ends')).toBeVisible();
  expect(within(van).getByText('20 Rue de Paris 93100 Montreuil')).toBeVisible();
  expect(within(van).getByText('4 Euro pallets · 1,200 kg · 2.4 m³')).toBeVisible();
  const rows = within(van).getAllByRole('row').slice(1);
  expect(rows.map((row) => row.querySelector('.sheet-seq')!.textContent)).toEqual(['1', '2', '']);
  // A stop that waits for its window shows when service starts and when the van arrives.
  expect(within(rows[0]).getByText('08:00')).toBeVisible();
  expect(within(rows[0]).getByText('arrives 07:50')).toBeVisible();
  expect(within(rows[0]).getByText('15 min on site')).toBeVisible();
  expect(within(rows[0]).getByText('· LEM')).toBeVisible();
  expect(within(rows[0]).getByText('Quai 2, sonner à l’accueil')).toBeVisible();
  expect(within(rows[0]).getByText('Required equipment: Tail lift')).toBeVisible();
  expect(rows[0]).toHaveTextContent('08:00–10:00');
  expect(within(rows[1]).queryByText(/arrives/)).toBeNull();
  const google = within(rows[0]).getByRole('link', { name: 'Google Maps · LIV-2026-0001' });
  expect(google).toHaveAttribute('href', navigationLinks(48.86, 2.37).google);
  expect(google).toHaveAttribute('target', '_blank');
  expect(google).toHaveAttribute('rel', 'noopener noreferrer');
  expect(within(rows[0]).getByRole('link', { name: 'Waze · LIV-2026-0001' })).toHaveAttribute(
    'href',
    navigationLinks(48.86, 2.37).waze,
  );
  expect(within(rows[2]).getByText('End at Dépôt Ivry-sur-Seine')).toBeVisible();
  expect(within(rows[2]).getByRole('link', { name: 'Waze · Dépôt Ivry-sur-Seine' })).toHaveAttribute(
    'href',
    navigationLinks(depot.latitude, depot.longitude).waze,
  );
  expect(within(van).getByText(/choose their own path, which may not suit this truck/)).toBeVisible();
  // A vehicle back where it started says so.
  const truck = screen.getByRole('article', { name: 'Route sheet VUL-01' });
  expect(within(truck).getByText('Back')).toBeVisible();
  expect(within(truck).getByText('Back to Dépôt Ivry-sur-Seine')).toBeVisible();
  expect(within(truck).getByText('—')).toBeVisible(); // Nothing to carry.
  // One vehicle's sheet; the location keeps the choice.
  fireEvent.change(screen.getByLabelText('Vehicle'), { target: { value: TRUCK } });
  expect(screen.getAllByRole('article').map((a) => a.getAttribute('aria-label'))).toEqual([
    'Route sheet VUL-01',
  ]);
  expect(readSheetRoute(window.location.hash)?.vehicle).toBe(TRUCK);
  expect(document.title).toBe('Route sheets · 2026-10-31 · VUL-01');
  fireEvent.click(screen.getByRole('button', { name: 'Print' }));
  expect(print).toHaveBeenCalledOnce();
  expect(screen.getByRole('link', { name: 'Back to the plan' })).toHaveAttribute(
    'href',
    `#/planning?date=2026-10-31&plan=${PLAN}`,
  );
});

it('opens on one vehicle and speaks French', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, json: async () => data })),
  );
  act(() => setLanguage('fr'));
  render(<RouteSheetsPage route={{ plan: PLAN, version: 4, vehicle: VAN }} />);
  expect(await screen.findByRole('article', { name: 'Feuille de route VUL-07' })).toBeVisible();
  expect(screen.getAllByRole('article')).toHaveLength(1);
  expect(screen.getByRole('heading', { level: 1, name: 'Feuilles de route' })).toBeVisible();
  expect(screen.getByText('arrivée 07:50')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Imprimer' })).toBeEnabled();
});

it('says why the sheets are unavailable and leads back to the plan', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: false,
      status: 409,
      json: async () => ({ code: 'PLAN_NOT_READY', detail: 'Mark this current plan ready before export.' }),
    })),
  );
  render(<RouteSheetsPage route={{ plan: PLAN, version: 2 }} />);
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('Mark this current plan ready before export.');
  expect(within(alert).getByRole('link', { name: 'Back to the plan' })).toHaveAttribute(
    'href',
    `#/planning?plan=${PLAN}`,
  );
  await waitFor(() => expect(screen.getByRole('button', { name: 'Print' })).toBeDisabled());
});
