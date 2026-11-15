import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import fixture from '../../backend/tests/fixtures/paris-ten.json';
import { fleetNumbers, presetVehicle, SetupWizard, VEHICLE_PRESETS } from './SetupWizard';
import { Workspace } from './Workspace';
import { savedVehicles, sourceResponse } from './test-server';
import type { Depot, VehicleSource } from './api';

vi.mock('./MapPanel', () => ({ MapPanel: () => null }));
afterEach(() => vi.unstubAllGlobals());

const numbering = { format: null, start: 1, next: null, suggestion: 'LIV-{YYYY}-{0000}' };
function server(fail?: (body: VehicleSource) => boolean) {
  const posted: VehicleSource[] = [];
  const fetch = vi.fn(async (url: string, options?: RequestInit) => {
    if (options?.method === 'POST') {
      const body = JSON.parse(options.body as string) as VehicleSource;
      if (fail?.(body))
        return { ok: false, status: 422, json: async () => ({ detail: 'Request could not be completed' }) };
      posted.push(body);
      return { ok: true, json: async () => ({ id: body.fleet_number, version: 1, data: body }) };
    }
    if (url.includes('/workspace/numbering')) return { ok: true, json: async () => numbering };
    // One vehicle already exists: its number is not given again.
    return { ok: true, json: async () => [{ id: 'old', version: 1, data: { fleet_number: 'VUL-01' } }] };
  });
  vi.stubGlobal('fetch', fetch);
  return posted;
}
const wizard = (props: Partial<Parameters<typeof SetupWizard>[0]> = {}) => {
  const handlers = {
    day: '2026-11-03',
    onExamplesAdded: vi.fn(),
    onClose: vi.fn(),
    onVehiclesAdded: vi.fn(),
    onImport: vi.fn(),
    onAddDelivery: vi.fn(),
    ...props,
  };
  render(<SetupWizard {...handlers} />);
  return handlers;
};

it('numbers new vehicles after those already used, and works them Monday to Friday', () => {
  expect(fleetNumbers('VUL', 3, new Set(['VUL-01', 'VUL-03']))).toEqual(['VUL-02', 'VUL-04', 'VUL-05']);
  expect(fleetNumbers(' PL ', 1, new Set())).toEqual(['PL-01']);
  const van = presetVehicle(VEHICLE_PRESETS[0], 'VUL-02', '07:30', '16:00');
  expect(van).toMatchObject({ fleet_number: 'VUL-02', active: true, skills: [], exceptions: [], limits: {} });
  expect(van.hours.monday).toEqual({ start: '07:30', end: '16:00' });
  expect(van.hours.friday).toEqual({ start: '07:30', end: '16:00' });
  expect([van.hours.saturday, van.hours.sunday]).toEqual([null, null]);
  expect(van.cost).toEqual({ currency: 'EUR', fixed_minor: 0, per_km_minor: 0, per_hour_minor: 0 });
  // Every preset stays within what the routing engine accepts.
  for (const preset of VEHICLE_PRESETS) {
    expect(preset.routing.height_mm).toBeLessThanOrEqual(10000);
    expect(preset.routing.length_mm).toBeLessThanOrEqual(50000);
    expect(preset.routing.axle_count).toBeGreaterThanOrEqual(2);
  }
});

it('adds vehicles from the presets with their own fleet numbers, adjusted values and hours', async () => {
  const posted = server();
  const handlers = wizard();
  expect(screen.getByRole('dialog', { name: 'Add your vehicles' })).toBeVisible();
  expect(screen.getByRole('button', { name: /^Add/ })).toBeDisabled(); // Nothing chosen yet.
  fireEvent.change(screen.getByLabelText('How many: Van 3.5 t'), { target: { value: '2' } });
  fireEvent.change(screen.getByLabelText('How many: Rigid truck 19 t'), { target: { value: '1' } });
  fireEvent.change(screen.getByLabelText('Height (mm) · Rigid truck 19 t'), { target: { value: '3800' } });
  const [from, to] = screen.getAllByDisplayValue(/^\d\d:\d\d$/);
  fireEvent.change(from, { target: { value: '07:00' } });
  fireEvent.change(to, { target: { value: '16:00' } });
  // Rows sharing a prefix continue each other's numbers.
  fireEvent.change(screen.getByLabelText('How many: Rigid truck 7.5 t'), { target: { value: '1' } });
  expect(screen.getByText('PL-02')).toBeVisible();
  fireEvent.change(screen.getByLabelText('How many: Rigid truck 7.5 t'), { target: { value: '0' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add 3 vehicles' }));
  expect(await screen.findByText('Added VUL-02, VUL-03, PL-01.')).toBeVisible();
  expect(posted.map((v) => v.fleet_number)).toEqual(['VUL-02', 'VUL-03', 'PL-01']);
  expect(posted[2].routing.height_mm).toBe(3800);
  expect(posted[2].capacity.pallet_eur_units).toBe(18);
  expect(posted[0].hours.wednesday).toEqual({ start: '07:00', end: '16:00' });
  expect(handlers.onVehiclesAdded).toHaveBeenCalledOnce();
  // The next step: how new deliveries are numbered.
  expect(await screen.findByRole('heading', { name: 'Delivery numbers', level: 2 })).toBeInTheDocument();
});

it('keeps what was added when one vehicle is refused, and leaves only the rest to add', async () => {
  const posted = server((body) => body.fleet_number === 'VUL-03');
  const handlers = wizard();
  fireEvent.change(screen.getByLabelText('How many: Van 3.5 t'), { target: { value: '3' } });
  fireEvent.click(screen.getByRole('button', { name: 'Add 3 vehicles' }));
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('Added VUL-02.');
  expect(posted.map((v) => v.fleet_number)).toEqual(['VUL-02']);
  expect(screen.getByLabelText('How many: Van 3.5 t')).toHaveValue(2);
  expect(handlers.onVehiclesAdded).toHaveBeenCalledOnce();
  expect(screen.getByRole('dialog', { name: 'Add your vehicles' })).toBeVisible(); // Still on this step.
});

it('can skip each step, and leads to importing or adding the first deliveries', async () => {
  server();
  const handlers = wizard();
  fireEvent.click(screen.getByRole('button', { name: 'Skip for now' }));
  await screen.findByRole('heading', { name: 'Delivery numbers', level: 2 });
  fireEvent.click(screen.getByRole('button', { name: 'Skip for now' }));
  const dialog = screen.getByRole('dialog', { name: 'Add your first deliveries' });
  fireEvent.click(within(dialog).getByRole('button', { name: /Import a delivery file/ }));
  expect(handlers.onImport).toHaveBeenCalledWith('orders');
  fireEvent.click(within(dialog).getByRole('button', { name: /Add a delivery/ }));
  expect(handlers.onAddDelivery).toHaveBeenCalledOnce();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Skip for now' }));
  expect(handlers.onClose).toHaveBeenCalledWith(true);
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(handlers.onClose).toHaveBeenLastCalledWith(false);
});

it('opens the guide for the administrator who just set up a company, not for planners or samples', async () => {
  const depot = fixture.depot as Depot,
    vehicles = savedVehicles(fixture.vehicles);
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) => ({
      ok: true,
      json: async () => (path.endsWith('/depot') ? depot : (sourceResponse(path, vehicles, []) ?? [])),
    })),
  );
  const company = {
    mode: 'shared' as const,
    company_name: 'Transports Fictifs',
    data_kind: 'UNVERIFIED' as const,
    examples: false,
    map_style_url: '',
    safety_margin_minutes: 15,
  };
  const view = render(<Workspace depot={depot} configuration={company} role="admin" welcome />);
  expect(await screen.findByRole('dialog', { name: 'Add your vehicles' })).toBeVisible();
  // The page behind the guide cannot be reached, by Tab or by a screen reader, until it closes.
  const page = [view.container.querySelector('.sidebar')!, view.container.querySelector('.app-body')!];
  expect(page.every((part) => part.hasAttribute('inert'))).toBe(true);
  fireEvent.click(screen.getByRole('button', { name: 'Close the guide' }));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(page.some((part) => part.hasAttribute('inert'))).toBe(false);
  expect(screen.getByText(/Guide closed/)).toBeVisible();
  view.unmount();
  render(<Workspace depot={depot} configuration={company} role="planner" welcome />).unmount();
  expect(screen.queryByRole('dialog')).toBeNull();
  render(
    <Workspace depot={depot} configuration={{ ...company, data_kind: 'SYNTHETIC' }} role="admin" welcome />,
  );
  expect(screen.queryByRole('dialog')).toBeNull();
});

it('saves the delivery numbers and moves on', async () => {
  server();
  wizard();
  fireEvent.click(screen.getByRole('button', { name: 'Skip for now' }));
  fireEvent.click(await screen.findByLabelText('Number new deliveries automatically'));
  fireEvent.click(screen.getByRole('button', { name: 'Save and continue' }));
  expect(await screen.findByRole('dialog', { name: 'Add your first deliveries' })).toBeVisible();
  const put = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.find(([, o]) => o?.method === 'PUT')!;
  expect(put[0]).toBe('/api/v1/workspace/numbering');
  expect(JSON.parse(put[1].body as string).format).toBeTruthy();
});

it('keeps Tab inside the guide while it is open', () => {
  server();
  // The test page has no layout; every control counts as shown.
  const rects = vi
    .spyOn(HTMLElement.prototype, 'getClientRects')
    .mockReturnValue([{}] as unknown as DOMRectList);
  wizard();
  const dialog = screen.getByRole('dialog', { name: 'Add your vehicles' });
  const controls = Array.from(
    dialog.querySelectorAll<HTMLElement>(
      'a[href],button:not(:disabled),input:not(:disabled),select:not(:disabled),summary',
    ),
  );
  const first = controls[0],
    last = controls[controls.length - 1];
  last.focus();
  fireEvent.keyDown(last, { key: 'Tab' });
  expect(first).toHaveFocus();
  fireEvent.keyDown(first, { key: 'Tab', shiftKey: true });
  expect(last).toHaveFocus();
  rects.mockRestore();
});
