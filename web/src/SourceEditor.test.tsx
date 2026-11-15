import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { saved } from './test-server';
import { describe, it, expect, vi } from 'vitest';
import { SourceEditor } from './SourceEditor';
import type { Depot, Order } from './api';
import fixture from '../../backend/tests/fixtures/paris-ten.json';
// The save button is named by its visible label, which depends on the record and tab.
const SAVE = /^(Save delivery|Save depot|Save location|Save vehicle|Confirm reviewed entrance)$/;

vi.mock('./MapPanel', () => ({
  MapPanel: ({ onPick }: { onPick: (p: unknown) => void }) => (
    <button onClick={() => onPick({ latitude: 48.84, longitude: 2.39, reviewed: false })}>
      Test move pin
    </button>
  ),
}));
const row: Order = {
  id: 'order',
  organization_id: 'company',
  example: false,
  version: 4,
  data: {
    external_id: 'DEMO-1',
    service_date: '2026-09-23',
    active: true,
    address: 'Synthetic entrance',
    location: { latitude: 48.835, longitude: 2.375, reviewed: true },
    time_window: { start: '2026-09-23T08:00:00+02:00', end: '2026-09-23T17:00:00+02:00' },
    service_seconds: 300,
    demand: {
      weight_kg: 100,
      volume_dm3: 20,
      pallet_eur_units: 0,
      pallet_ind_units: 0,
      loading_length_mm: 0,
    },
    required_skills: [],
    instructions: 'Synthetic',
    priority: 10,
  },
};
function editor(onSaved = vi.fn()) {
  render(
    <SourceEditor
      selection={{ kind: 'orders', row }}
      day="2026-09-23"
      style="https://maps.example/style"
      points={[]}
      vehicles={[]}
      onSaved={onSaved}
      onSelect={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  return onSaved;
}
describe('source editing', () => {
  it('resets review when a pin moves and sends the original edit version', async () => {
    const fetch = vi.fn(async (_url: string, _options: RequestInit) => ({
      ok: true,
      json: async () => ({ ...row, version: 5 }),
    }));
    vi.stubGlobal('fetch', fetch);
    const saved = editor();
    fireEvent.click(screen.getByRole('button', { name: 'Location' }));
    fireEvent.click(screen.getByText('Test move pin'));
    expect(screen.getByLabelText('I reviewed this vehicle entrance on the map')).not.toBeChecked();
    expect(screen.getByLabelText('I reviewed this vehicle entrance on the map')).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: SAVE }));
    await waitFor(() => expect(saved).toHaveBeenCalled());
    // Saved from the Location tab: the editor reopens there, where the saved pin is reviewed next.
    expect(saved.mock.calls[0][2]).toBe('location');
    const options = fetch.mock.calls[0][1] as RequestInit;
    expect(options.headers).toMatchObject({ 'If-Match': '"4"', 'X-VRP-Request': '1' });
    expect(JSON.parse(options.body as string).location).toEqual({
      latitude: 48.84,
      longitude: 2.39,
      reviewed: false,
    });
  });
  it('keeps edits and shows a stale-version failure', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: false,
        status: 412,
        json: async () => ({ detail: 'Record changed. Reload before editing.' }),
      })),
    );
    const saved = editor();
    fireEvent.change(screen.getByLabelText('Priority (0–100)'), { target: { value: '25' } });
    fireEvent.click(screen.getByRole('button', { name: SAVE }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Record changed');
    expect(saved).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Priority (0–100)')).toHaveValue(25);
  });
});

it('sends converted delivery quantities and minutes while keeping all five dimensions', async () => {
  const fetch = vi.fn(async () => ({ ok: true, json: async () => ({ ...row, version: 5 }) }));
  vi.stubGlobal('fetch', fetch);
  editor();
  fireEvent.change(screen.getByLabelText('Volume (m³)'), { target: { value: '1.25' } });
  fireEvent.change(screen.getByLabelText('Loading length (m)'), { target: { value: '2.4' } });
  fireEvent.change(screen.getByLabelText('Service duration (minutes)'), { target: { value: '0.1' } });
  fireEvent.click(screen.getByRole('button', { name: SAVE }));
  await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  const body = JSON.parse((fetch.mock.calls[0] as unknown as [string, RequestInit])[1].body as string);
  expect(body.demand).toEqual({
    weight_kg: 100,
    volume_dm3: 1250,
    pallet_eur_units: 0,
    pallet_ind_units: 0,
    loading_length_mm: 2400,
  });
  expect(body.service_seconds).toBe(6);
});
it('keeps focus in the editor when switching to the entrance review, so Escape still closes it', () => {
  const onClose = vi.fn();
  render(
    <SourceEditor
      selection={{ kind: 'orders', row }}
      day="2026-09-23"
      style="https://maps.example/style"
      points={[]}
      vehicles={[]}
      onSaved={vi.fn()}
      onSelect={vi.fn()}
      onClose={onClose}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Show on map' }));
  const dialog = screen.getByRole('dialog', { name: 'Record editor' });
  expect(dialog).toHaveFocus();
  fireEvent.keyDown(document.activeElement!, { key: 'Escape' });
  expect(onClose).toHaveBeenCalled();
});

it('blocks reversed times locally and keeps the entered values for correction', () => {
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  editor();
  fireEvent.change(screen.getByLabelText('Service until'), { target: { value: '2026-09-23T07:00' } });
  fireEvent.click(screen.getByRole('button', { name: SAVE }));
  expect(fetch).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent('highlighted');
  expect(screen.getByText('End must be after start.')).toBeInTheDocument();
});
it('shows inline API errors and retains the complete record after a rejected save', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: false,
      status: 422,
      json: async () => ({ detail: [{ loc: ['body', 'demand', 'weight_kg'], msg: 'Test capacity issue' }] }),
    })),
  );
  editor();
  fireEvent.click(screen.getByRole('button', { name: SAVE }));
  await screen.findByText('Test capacity issue');
  expect(screen.getByLabelText(/Weight \(kg\)/)).toHaveAttribute('aria-invalid', 'true');
  expect(screen.getByDisplayValue('DEMO-1')).toBeInTheDocument();
});

it('preserves edits on service failure without blaming an invalid field', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: false,
      status: 503,
      json: async () => ({ detail: 'Service temporarily unavailable. Retry later.' }),
    })),
  );
  const saved = editor();
  fireEvent.change(screen.getByLabelText('Delivery instructions'), {
    target: { value: 'Keep this edited instruction' },
  });
  fireEvent.click(screen.getByRole('button', { name: SAVE }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Request could not be completed');
  expect(screen.getByRole('alert')).not.toHaveTextContent(/fields? needs? attention/);
  // Focus moves to the notice in an effect right after it appears.
  await waitFor(() => expect(screen.getByRole('alert')).toHaveFocus());
  expect(screen.getByLabelText('Delivery instructions')).toHaveValue('Keep this edited instruction');
  expect(screen.getByRole('button', { name: SAVE })).toBeEnabled();
  expect(saved).not.toHaveBeenCalled();
});

it('converts vehicle units and preserves independent payload, road and route constraints', async () => {
  const vehicle = saved(fixture.vehicles[0]);
  const fetch = vi.fn(async () => ({ ok: true, json: async () => vehicle }));
  vi.stubGlobal('fetch', fetch);
  render(
    <SourceEditor
      selection={{ kind: 'vehicles', row: vehicle }}
      day={fixture.service_date}
      style=""
      points={[]}
      vehicles={[]}
      onSaved={vi.fn()}
      onSelect={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  const enter = (label: string, value: string) =>
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  enter('Height (m)', '3.85');
  enter('Volume (m³)', '42.125');
  enter('Loading length (m)', '7.2');
  enter('Fixed cost (€)', '12.34');
  enter('Distance rate (€ / km)', '0.85');
  enter('Time rate (€ / hour)', '28.5');
  enter('Maximum stops', '20');
  enter('Maximum distance (km)', '125.5');
  enter('Maximum driving (minutes)', '90');
  fireEvent.click(screen.getByRole('button', { name: SAVE }));
  await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  const body = JSON.parse((fetch.mock.calls[0] as unknown as [string, RequestInit])[1].body as string);
  expect(body.routing).toEqual({ ...vehicle.data.routing, height_mm: 3850 });
  expect(body.capacity).toEqual({ ...vehicle.data.capacity, volume_dm3: 42125, loading_length_mm: 7200 });
  expect(body.cost).toEqual({ currency: 'EUR', fixed_minor: 1234, per_km_minor: 85, per_hour_minor: 2850 });
  expect(body.limits).toEqual({ max_stops: 20, max_distance_m: 125500, max_driving_seconds: 5400 });
});

function vehicleEditor() {
  const vehicle = saved(fixture.vehicles[0]);
  render(
    <SourceEditor
      selection={{ kind: 'vehicles', row: vehicle }}
      day={fixture.service_date}
      style=""
      points={[]}
      vehicles={[]}
      onSaved={vi.fn()}
      onSelect={vi.fn()}
      onClose={vi.fn()}
    />,
  );
}

it('explains out-of-range values in its own words and limits text to the API lengths', () => {
  const fetch = vi.fn();
  vi.stubGlobal('fetch', fetch);
  vehicleEditor();
  expect(screen.getByLabelText('Fleet number')).toHaveAttribute('maxLength', '40');
  expect(screen.getByLabelText('Height (m)')).toHaveAttribute('max', '10');
  fireEvent.change(screen.getByLabelText('Height (m)'), { target: { value: '12' } });
  fireEvent.change(screen.getByLabelText('Weight (kg)'), { target: { value: '-5' } });
  fireEvent.click(screen.getByRole('button', { name: SAVE }));
  expect(fetch).not.toHaveBeenCalled();
  expect(screen.getByRole('alert')).toHaveTextContent('2 fields need attention');
  expect(screen.getByText('Use a value of at most 10.')).toBeInTheDocument();
  expect(screen.getByText('Use a value of at least 0.')).toBeInTheDocument();
  expect(screen.getByLabelText(/^Height \(m\)/)).toHaveAttribute('aria-invalid', 'true');
});

it('writes equipment codes the way the API accepts them and names a code it would refuse', async () => {
  const fetch = vi.fn(async (_url: string, _options: RequestInit) => ({
    ok: true,
    json: async () => ({ ...row, version: 5 }),
  }));
  vi.stubGlobal('fetch', fetch);
  editor();
  // Named equipment is ticked; a typed code that names it ticks it too, other codes stay typed.
  const tailLift = screen.getByRole('checkbox', { name: 'Tail lift' });
  const codes = screen.getByLabelText('Other equipment codes (separate with |)');
  fireEvent.change(codes, { target: { value: 'tail lift|2X' } });
  expect(tailLift).toBeChecked();
  expect(codes).toHaveValue('2X');
  fireEvent.click(screen.getByRole('button', { name: SAVE }));
  expect(fetch).not.toHaveBeenCalled();
  expect(
    await screen.findByText('Equipment codes start with a capital letter and use only A–Z, 0–9 and _: 2X'),
  ).toBeVisible();
  fireEvent.change(codes, { target: { value: 'crane|' } });
  fireEvent.click(screen.getByRole('checkbox', { name: 'Refrigerated body' }));
  fireEvent.click(screen.getByRole('button', { name: SAVE }));
  await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  expect(JSON.parse(fetch.mock.calls[0][1].body as string).required_skills).toEqual([
    'TAIL_LIFT',
    'CRANE',
    'REFRIGERATED',
  ]);
});

it('suggests addresses while typing; a choice places the pin but leaves the entrance to review', async () => {
  const fetch = vi.fn(async (url: string, _options?: RequestInit) => ({
    ok: true,
    json: async () =>
      url.includes('/geocode')
        ? [
            {
              label: '12 Rue de Rivoli 75004 Paris',
              latitude: 48.8553,
              longitude: 2.3601,
              score: 0.97,
              kind: 'housenumber',
            },
            {
              label: 'Rue de Rivoli 75001 Paris',
              latitude: 48.8606,
              longitude: 2.3388,
              score: 0.71,
              kind: 'street',
            },
          ]
        : row,
  }));
  vi.stubGlobal('fetch', fetch);
  const onClose = vi.fn();
  render(
    <SourceEditor
      selection={{ kind: 'orders', row }}
      day="2026-09-23"
      style=""
      points={[]}
      vehicles={[]}
      onSaved={vi.fn()}
      onSelect={vi.fn()}
      onClose={onClose}
      addressLookup="Base Adresse Nationale (Licence Ouverte 2.0)"
    />,
  );
  const address = screen.getByRole('combobox', { name: 'Delivery address' });
  expect(address).toHaveValue('Synthetic entrance');
  // The saved address is not looked up; only what is typed is.
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.change(address, { target: { value: '12 rue de rivoli paris' } });
  const options = await screen.findAllByRole('option');
  expect(options.map((o) => o.textContent)).toEqual([
    '12 Rue de Rivoli 75004 ParisAddress · match 97 %',
    'Rue de Rivoli 75001 ParisStreet · match 71 %',
  ]);
  expect(options[0]).toHaveAttribute('aria-selected', 'true');
  // Escape closes the suggestions, not the editor.
  fireEvent.keyDown(address, { key: 'Escape' });
  expect(screen.queryByRole('listbox')).toBeNull();
  expect(onClose).not.toHaveBeenCalled();
  fireEvent.keyDown(address, { key: 'ArrowDown' });
  fireEvent.keyDown(address, { key: 'ArrowUp' });
  fireEvent.keyDown(address, { key: 'Enter' });
  expect(address).toHaveValue('12 Rue de Rivoli 75004 Paris');
  expect(screen.getByLabelText('Latitude')).toHaveValue(48.8553);
  expect(screen.getByLabelText('Longitude')).toHaveValue(2.3601);
  expect(screen.getByText(/Pin placed\. Review the vehicle entrance/)).toBeInTheDocument();
  expect(screen.getByLabelText('I reviewed this vehicle entrance on the map')).not.toBeChecked();
  expect(fetch.mock.calls.map(([url]) => url)).toEqual(['/api/v1/geocode?q=12%20rue%20de%20rivoli%20paris']);
  expect(
    screen.getAllByText('Addresses: Base Adresse Nationale (Licence Ouverte 2.0)')[0],
  ).toBeInTheDocument();
});

it('keeps an ordinary address field and says how to turn search on when lookup is off', () => {
  vi.stubGlobal('fetch', vi.fn());
  editor();
  expect(screen.queryByRole('combobox', { name: 'Delivery address' })).toBeNull();
  expect(screen.getByRole('textbox', { name: 'Delivery address' })).toHaveValue('Synthetic entrance');
  expect(
    screen.getByText(
      'Address search is off for this workspace. An administrator can turn it on in Settings.',
    ),
  ).toBeInTheDocument();
});

it('proposes the next delivery number and lets the server give the final one on saving', async () => {
  const calls: [string, RequestInit | undefined][] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      calls.push([url, options]);
      return {
        ok: true,
        json: async () => (url.includes('next-identifier') ? { identifier: 'LIV-2026-0007' } : row),
      };
    }),
  );
  const newDelivery = () =>
    render(
      <SourceEditor
        selection={{ kind: 'orders' }}
        day="2026-09-23"
        style=""
        points={[]}
        vehicles={[]}
        onSaved={vi.fn()}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
  const view = newDelivery();
  const id = screen.getByLabelText(/^Delivery identifier/);
  await waitFor(() => expect(id).toHaveValue('LIV-2026-0007'));
  expect(calls[0][0]).toBe('/api/v1/orders/next-identifier?service_date=2026-09-23');
  expect(
    screen.getByText('Next delivery number. The final number is given when you save.'),
  ).toBeInTheDocument();
  fireEvent.change(screen.getByRole('textbox', { name: 'Delivery address' }), {
    target: { value: '1 Rue Test, Paris' },
  });
  fireEvent.click(screen.getByRole('button', { name: SAVE }));
  await waitFor(() => expect(calls).toHaveLength(2));
  expect(calls[1][0]).toBe('/api/v1/orders?assign_identifier=true');
  expect(JSON.parse(calls[1][1]!.body as string).external_id).toBe('LIV-2026-0007');
  view.unmount();
  // A typed identifier is kept as typed.
  calls.length = 0;
  newDelivery();
  const typed = screen.getByLabelText(/^Delivery identifier/);
  await waitFor(() => expect(typed).toHaveValue('LIV-2026-0007'));
  fireEvent.change(typed, { target: { value: 'SPECIAL-1' } });
  expect(screen.queryByText('Next delivery number. The final number is given when you save.')).toBeNull();
  fireEvent.change(screen.getByRole('textbox', { name: 'Delivery address' }), {
    target: { value: '1 Rue Test, Paris' },
  });
  fireEvent.click(screen.getByRole('button', { name: SAVE }));
  await waitFor(() => expect(calls).toHaveLength(2));
  expect(calls[1][0]).toBe('/api/v1/orders');
  expect(JSON.parse(calls[1][1]!.body as string).external_id).toBe('SPECIAL-1');
});

it('saves the depot address chosen from the suggestions with its unreviewed point', async () => {
  const depot = { ...(fixture.depot as unknown as Depot), version: 3 };
  const calls: [string, RequestInit | undefined][] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      calls.push([url, options]);
      return {
        ok: true,
        json: async () =>
          url.includes('/geocode')
            ? [
                {
                  label: '5 Rue Fictive 94200 Ivry-sur-Seine',
                  latitude: 48.81,
                  longitude: 2.39,
                  score: 0.95,
                  kind: 'housenumber',
                },
              ]
            : depot,
      };
    }),
  );
  render(
    <SourceEditor
      selection={{ kind: 'depot', row: depot }}
      day="2026-09-23"
      style=""
      points={[]}
      vehicles={[]}
      onSaved={vi.fn()}
      onSelect={vi.fn()}
      onClose={vi.fn()}
      addressLookup="IGN"
    />,
  );
  fireEvent.change(screen.getByRole('combobox', { name: 'Depot address' }), {
    target: { value: '5 rue fictive ivry' },
  });
  fireEvent.click(await screen.findByRole('option', { name: /5 Rue Fictive/ }));
  fireEvent.click(screen.getByRole('button', { name: SAVE }));
  await waitFor(() => expect(calls).toHaveLength(2));
  const body = JSON.parse(calls[1][1]!.body as string);
  expect(calls[1][0]).toBe(`/api/v1/depots/${depot.id}`);
  expect(body.address).toBe('5 Rue Fictive 94200 Ivry-sur-Seine');
  expect(body.location).toEqual({ latitude: 48.81, longitude: 2.39, reviewed: false });
});

it('edits weekly hours and days off, and refuses hours that end before they start', async () => {
  const vehicle = saved(fixture.vehicles[0]);
  const calls: [string, RequestInit | undefined][] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      calls.push([url, options]);
      return { ok: true, json: async () => vehicle };
    }),
  );
  render(
    <SourceEditor
      selection={{ kind: 'vehicles', row: vehicle }}
      day="2026-11-02"
      style=""
      points={[]}
      vehicles={[]}
      onSaved={vi.fn()}
      onSelect={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  // Sunday off, Saturday mornings, and Monday's hours copied to the rest of the week.
  fireEvent.click(screen.getByRole('checkbox', { name: 'Sunday' }));
  fireEvent.change(screen.getByLabelText('Saturday end'), { target: { value: '12:00' } });
  fireEvent.change(screen.getByLabelText('Monday start'), { target: { value: '06:30' } });
  fireEvent.click(screen.getByRole('button', { name: 'Use Monday’s hours from Tuesday to Friday' }));
  fireEvent.click(screen.getByRole('button', { name: 'Add a day off' }));
  expect(screen.getByLabelText('Date')).toHaveValue('2026-11-02');
  // An end before the start is named before anything is sent.
  fireEvent.change(screen.getByLabelText('Saturday end'), { target: { value: '05:00' } });
  fireEvent.click(screen.getByRole('button', { name: SAVE }));
  expect(await screen.findByText('End must be after start.')).toBeInTheDocument();
  expect(calls).toHaveLength(0);
  fireEvent.change(screen.getByLabelText('Saturday end'), { target: { value: '12:00' } });
  fireEvent.click(screen.getByRole('button', { name: SAVE }));
  await waitFor(() => expect(calls).toHaveLength(1));
  const body = JSON.parse(calls[0][1]!.body as string);
  expect(body.hours.sunday).toBeNull();
  expect(body.hours.saturday).toEqual({ start: '07:30', end: '12:00' });
  expect(body.hours.friday).toEqual({ start: '06:30', end: '17:00' });
  expect(body.exceptions).toEqual([{ date: '2026-11-02', hours: null }]);
  expect(body).not.toHaveProperty('available_window');
});

it('links a delivery to a saved customer picked from the search and sends its code', async () => {
  const saved = {
    id: 'c-1',
    organization_id: 'company',
    version: 1,
    data: {
      code: 'CLI-1',
      name: 'Boulangerie Fictive',
      active: true,
      address: '5 Rue Fictive, Ivry',
      location: { latitude: 48.8156, longitude: 2.3889, reviewed: true },
      usual_hours: null,
      service_seconds: null,
      required_skills: [],
      instructions: '',
    },
  };
  const calls: [string, RequestInit | undefined][] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      calls.push([url, options]);
      return { ok: true, json: async () => (url.includes('/customers') ? [saved] : row) };
    }),
  );
  editor();
  fireEvent.change(screen.getByRole('combobox', { name: 'Customer (optional)' }), {
    target: { value: 'boul' },
  });
  fireEvent.click(await screen.findByRole('option', { name: /CLI-1 · Boulangerie Fictive/ }));
  expect(screen.getByText('CLI-1')).toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: 'Delivery address' })).toHaveValue('5 Rue Fictive, Ivry');
  expect(screen.getByText('Reviewed entrance of customer CLI-1.')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: SAVE }));
  await waitFor(() =>
    expect(calls.some(([url, o]) => url === '/api/v1/orders/order' && o?.method === 'PUT')).toBe(true),
  );
  const body = JSON.parse(calls.find(([url]) => url === '/api/v1/orders/order')![1]!.body as string);
  expect(body.customer).toBe('CLI-1');
  expect(body.location).toEqual({ latitude: 48.8156, longitude: 2.3889, reviewed: true });
});

it('deletes a delivery only after asking, at the version it shows', async () => {
  const fetch = vi.fn(async (_url: string, _options: RequestInit) => ({
    ok: true,
    json: async () => ({ deleted: 1 }),
  }));
  vi.stubGlobal('fetch', fetch);
  const answer = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
  const onDeleted = vi.fn();
  render(
    <SourceEditor
      selection={{ kind: 'orders', row }}
      day="2026-09-23"
      style="https://maps.example/style"
      points={[]}
      vehicles={[]}
      onSaved={vi.fn()}
      onDeleted={onDeleted}
      onSelect={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'Delete delivery' }));
  await waitFor(() => expect(answer).toHaveBeenCalledTimes(1));
  expect(answer.mock.calls[0][0]).toMatch(/^Delete delivery DEMO-1\?\n\nIt is removed for good/);
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'Delete delivery' }));
  await waitFor(() => expect(onDeleted).toHaveBeenCalledWith(row));
  const [url, options] = fetch.mock.calls[0];
  expect(url).toBe('/api/v1/orders/delete');
  expect(JSON.parse(options.body as string)).toEqual({ orders: [{ id: 'order', version: 4 }] });
  answer.mockRestore();
});

it('offers no delete when the editor has no place to report it', () => {
  render(
    <SourceEditor
      selection={{ kind: 'orders', row }}
      day="2026-09-23"
      style="https://maps.example/style"
      points={[]}
      vehicles={[]}
      onSaved={vi.fn()}
      onSelect={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  expect(screen.queryByRole('button', { name: 'Delete delivery' })).toBeNull();
});

it('names the point a delivery would lose, and offers no removal without a point', () => {
  const editor = (location: Order['data']['location']) => (
    <SourceEditor
      selection={{ kind: 'orders', row: { ...row, data: { ...row.data, location } } }}
      day="2026-09-23"
      style="https://maps.example/style"
      points={[]}
      vehicles={[]}
      onSaved={vi.fn()}
      onSelect={vi.fn()}
      onClose={vi.fn()}
    />
  );
  const view = render(editor(row.data.location));
  fireEvent.click(screen.getByRole('button', { name: 'Show on map' }));
  fireEvent.click(screen.getByRole('button', { name: 'Remove the reviewed point' }));
  // Removed: nothing left to remove.
  expect(screen.queryByRole('button', { name: /^Remove the/ })).not.toBeInTheDocument();
  view.unmount();
  render(editor({ latitude: 48.835, longitude: 2.375, reviewed: false }));
  fireEvent.click(screen.getByRole('button', { name: 'Review on map' }));
  expect(screen.getByRole('button', { name: 'Remove the point' })).toBeInTheDocument();
});

it('treats a new record nobody has typed into as not yet saved, not as unsaved changes', () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, json: async () => ({ identifier: null }) })),
  );
  render(
    <SourceEditor
      selection={{ kind: 'vehicles' }}
      day="2026-09-23"
      style=""
      points={[]}
      vehicles={[]}
      onSaved={vi.fn()}
      onSelect={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  expect(screen.getByRole('heading', { name: 'Add vehicle' })).toBeVisible();
  expect(screen.getByText('Not saved yet')).toBeVisible();
  expect(screen.queryByText('Unsaved changes')).toBeNull();
  fireEvent.change(screen.getByLabelText('Fleet number'), { target: { value: 'VUL-09' } });
  expect(screen.getByText('Unsaved changes')).toBeVisible();
});

it('names a place as a place, in whole French phrases', async () => {
  const { setLanguage } = await import('./i18n');
  const { act } = await import('react');
  act(() => setLanguage('fr'));
  try {
    render(
      <SourceEditor
        selection={{ kind: 'depot', placeKind: 'PLACE' }}
        day="2026-09-23"
        style=""
        points={[]}
        vehicles={[]}
        onSaved={vi.fn()}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(screen.getByRole('heading', { name: 'Ajouter un lieu' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'Enregistrer le lieu' })).toBeVisible();
    expect(screen.queryByText(/dépôt/)).toBeNull();
  } finally {
    act(() => setLanguage('en'));
  }
});
