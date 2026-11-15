import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { PlacesSettings, routeEnds } from './Places';
import { SourceEditor } from './SourceEditor';
import { assignmentWarnings } from './RouteReview';
import { saved } from './test-server';
import type { Depot, Order, Plan } from './api';
import fixture from '../../backend/tests/fixtures/paris-ten.json';

vi.mock('./MapPanel', () => ({ MapPanel: () => null }));

const main = { ...fixture.depot, main: true } as unknown as Depot;
const place = (code: string, kind: 'DEPOT' | 'PLACE', reviewed = true): Depot =>
  ({
    id: `id-${code}`,
    organization_id: main.organization_id,
    version: 3,
    main: false,
    data: {
      code,
      name: code.toLowerCase(),
      kind,
      address: '',
      location: { latitude: 48.9, longitude: 2.36, reviewed },
      timezone: 'Europe/Paris',
    },
  }) as Depot;
const nord = place('NORD', 'DEPOT'),
  home = place('HOME', 'PLACE', false);

it('lists depots and places, makes a depot main and deletes a place only after asking', async () => {
  // The server answers a deletion with 204 and no body, which must not read as a failure.
  const fetch = vi.fn(async (_url: string, options?: RequestInit) =>
    options?.method === 'DELETE' ? new Response(null, { status: 204 }) : Response.json({}),
  );
  vi.stubGlobal('fetch', fetch);
  const answer = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
  const onEdit = vi.fn(),
    onChanged = vi.fn();
  render(<PlacesSettings places={[main, nord, home]} style="" onEdit={onEdit} onChanged={onChanged} />);
  const rows = screen.getAllByRole('row').slice(1);
  expect(within(rows[0]).getByText('Main depot')).toBeVisible();
  expect(within(rows[0]).queryByRole('button', { name: /Delete/ })).toBeNull(); // Never the main depot.
  expect(within(rows[2]).getByText('Other place')).toBeVisible();
  expect(within(rows[2]).getByText('Needs review')).toBeVisible();
  expect(within(rows[2]).queryByRole('button', { name: /Make main/ })).toBeNull(); // Only a depot.
  fireEvent.click(within(rows[1]).getByRole('button', { name: 'Make main NORD' }));
  await waitFor(() => expect(onChanged).toHaveBeenCalledWith('NORD · nord is now the main depot.'));
  expect(fetch.mock.calls[0][0]).toBe('/api/v1/depots/id-NORD/main');
  expect((fetch.mock.calls[0][1]!.headers as Record<string, string>)['If-Match']).toBe('"3"');
  fireEvent.click(within(rows[2]).getByRole('button', { name: 'Delete HOME' }));
  expect(answer).toHaveBeenCalledTimes(1);
  expect(fetch).toHaveBeenCalledTimes(1); // Kept.
  fireEvent.click(within(rows[2]).getByRole('button', { name: 'Delete HOME' }));
  await waitFor(() => expect(onChanged).toHaveBeenCalledWith('HOME · home deleted.'));
  expect(fetch.mock.calls[1][1]!.method).toBe('DELETE');
  fireEvent.click(screen.getByRole('button', { name: 'Add place' }));
  expect(onEdit).toHaveBeenCalledWith(null, 'PLACE');
  answer.mockRestore();
});

it('saves where a vehicle starts and ends its day, and adds a place from the vehicle editor', async () => {
  const vehicle = saved(fixture.vehicles[0]);
  const added = place('KARIM', 'PLACE', false);
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
                  label: '3 Rue Fictive 93200 Saint-Denis',
                  latitude: 48.93,
                  longitude: 2.35,
                  score: 0.97,
                  kind: 'housenumber',
                },
              ]
            : url.endsWith('/depots')
              ? added
              : { ...vehicle, version: 2 },
      };
    }),
  );
  const onPlaceAdded = vi.fn();
  render(
    <SourceEditor
      selection={{ kind: 'vehicles', row: vehicle }}
      day={fixture.service_date}
      style=""
      points={[]}
      vehicles={[]}
      places={[main, nord, home]}
      onPlaceAdded={onPlaceAdded}
      addressLookup="IGN"
      onSaved={vi.fn()}
      onSelect={vi.fn()}
      onClose={vi.fn()}
    />,
  );
  const start = screen.getByLabelText('Starts at'),
    end = screen.getByLabelText('Ends at');
  expect(start).toHaveValue('');
  expect(
    within(start)
      .getAllByRole('option')
      .map((o) => o.textContent),
  ).toEqual([`Main depot · ${main.data.code} · ${main.data.name}`, 'NORD · nord', 'HOME · home']);
  expect(end).toHaveValue('');
  expect(screen.getByRole('option', { name: 'Where it started' })).toBeInTheDocument();
  fireEvent.change(start, { target: { value: 'HOME' } });
  fireEvent.change(end, { target: { value: 'NORD' } });
  // Another place, from its address; it starts unreviewed and becomes the vehicle's start.
  fireEvent.click(screen.getByText('Add another place…'));
  fireEvent.change(screen.getByLabelText('Place name'), { target: { value: 'Karim' } });
  fireEvent.change(screen.getByLabelText('Place code'), { target: { value: 'KARIM' } });
  fireEvent.change(screen.getByRole('combobox', { name: 'Place address' }), {
    target: { value: '3 rue fictive' },
  });
  fireEvent.click(await screen.findByRole('option', { name: /3 Rue Fictive/ }));
  fireEvent.click(screen.getByRole('button', { name: 'Add place' }));
  await waitFor(() => expect(onPlaceAdded).toHaveBeenCalledWith(added));
  const created = calls.find(([url]) => url.endsWith('/depots'))!;
  expect(JSON.parse(created[1]!.body as string)).toMatchObject({
    code: 'KARIM',
    kind: 'PLACE',
    address: '3 Rue Fictive 93200 Saint-Denis',
    location: { latitude: 48.93, longitude: 2.35, reviewed: false },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save vehicle' }));
  await waitFor(() => expect(calls.some(([url]) => url.endsWith(`/vehicles/${vehicle.id}`))).toBe(true));
  const body = JSON.parse(calls.find(([url]) => url.endsWith(`/vehicles/${vehicle.id}`))![1]!.body as string);
  expect([body.start, body.end]).toEqual(['KARIM', 'NORD']);
});

it('offers ships from only when the company has more than one depot', () => {
  const order = { ...fixture.orders[0], data: { ...fixture.orders[0].data } } as unknown as Order;
  const editor = (places: Depot[]) =>
    render(
      <SourceEditor
        selection={{ kind: 'orders', row: order }}
        day={fixture.service_date}
        style=""
        points={[]}
        vehicles={[]}
        places={places}
        onSaved={vi.fn()}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
  editor([main, home]).unmount();
  expect(screen.queryByLabelText('Ships from')).toBeNull();
  editor([main, nord, home]);
  const select = screen.getByLabelText('Ships from');
  expect(
    within(select)
      .getAllByRole('option')
      .map((o) => o.textContent),
  ).toEqual(['Any depot', `${main.data.code} · ${main.data.name}`, 'NORD · nord']);
});

it('names where routes start and warns when a moved delivery ships from another depot', () => {
  const input = structuredClone(fixture) as unknown as Plan['input_data'];
  input.depots = [nord];
  input.vehicles[0].data.start = 'NORD';
  input.orders[0].data.ships_from = 'NORD';
  expect(routeEnds(input, input.vehicles[0].data)).toEqual({ start: nord, end: nord });
  expect(routeEnds(input, input.vehicles[1].data).start).toEqual(input.depot);
  const order = input.orders[0];
  expect(
    assignmentWarnings([{ vehicle_id: input.vehicles[0].id, order_ids: [order.id] }], input).join(' '),
  ).not.toContain('ships from');
  expect(assignmentWarnings([{ vehicle_id: input.vehicles[1].id, order_ids: [order.id] }], input)).toContain(
    `${order.data.external_id} ships from NORD; ${input.vehicles[1].data.fleet_number} starts at ${input.depot.data.code}.`,
  );
});

it('offers the example depot only to example vehicles, and never makes it main', () => {
  const example = { ...place('EXAMPLE', 'DEPOT'), example: true };
  render(<PlacesSettings places={[main, example]} style="" onEdit={vi.fn()} onChanged={vi.fn()} />);
  const row = screen.getAllByRole('row')[2];
  expect(within(row).getByText('Example')).toBeVisible();
  expect(within(row).queryByRole('button', { name: /Make main/ })).toBeNull();
  const editor = (vehicle: ReturnType<typeof saved>) =>
    render(
      <SourceEditor
        selection={{ kind: 'vehicles', row: vehicle }}
        day={fixture.service_date}
        style=""
        points={[]}
        vehicles={[]}
        places={[main, example]}
        onSaved={vi.fn()}
        onSelect={vi.fn()}
        onClose={vi.fn()}
      />,
    );
  const codes = () =>
    within(screen.getByLabelText('Starts at'))
      .getAllByRole('option')
      .map((o) => o.textContent);
  const own = editor({ ...saved(fixture.vehicles[0]), example: false });
  expect(codes()).toEqual([`Main depot · ${main.data.code} · ${main.data.name}`]);
  own.unmount();
  editor({ ...saved(fixture.vehicles[0]), example: true });
  expect(codes()).toEqual([`Main depot · ${main.data.code} · ${main.data.name}`, 'EXAMPLE · example']);
});
