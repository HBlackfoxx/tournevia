import type { Order, OrderSummary, Vehicle, VehicleSummary } from './api';
import { availableOn, DAYS } from './availability';

type CalculationVehicle = {
  id: string;
  organization_id?: string;
  version: number;
  data: { available_window: { start: string; end: string } } & Record<string, unknown>;
};

/** For tests: a calculation's vehicle as a saved one that works the same local hours every day. */
export function saved(vehicle: CalculationVehicle): Vehicle {
  const { available_window: window, ...rest } = vehicle.data;
  const hours = { start: window.start.slice(11, 16), end: window.end.slice(11, 16) };
  return {
    ...vehicle,
    organization_id: vehicle.organization_id ?? 'company',
    data: { ...rest, hours: Object.fromEntries(DAYS.map((d) => [d, hours])), exceptions: [] },
  } as unknown as Vehicle;
}

export const savedVehicles = (vehicles: CalculationVehicle[]) => vehicles.map(saved);

/** For tests: answers record list and summary requests from in-memory records, as the API does
 * (status filter, case-insensitive search, offset and limit). Other paths return undefined. */
export function sourceResponse(path: string, vehicles: Vehicle[], orders: Order[]): unknown {
  const url = new URL(path, 'http://localhost');
  const match = /\/(vehicles|orders)(\/summary)?$/.exec(url.pathname);
  if (!match) return undefined;
  const params = url.searchParams,
    day = params.get('service_date') ?? '',
    q = params.get('q')?.toLowerCase();
  const rows: (Order | Vehicle)[] = (match[1] === 'orders' ? orders : vehicles).filter((row) => {
    const data = row.data;
    const text = 'external_id' in data ? `${data.external_id} ${data.address}` : data.fleet_number;
    return !q || text.toLowerCase().includes(q);
  });
  const reviewed = (row: Order | Vehicle) =>
    'location' in row.data ? row.data.location?.reviewed : undefined;
  const rules: Record<string, (row: Order | Vehicle) => boolean> = {
    active: (row) => row.data.active,
    inactive: (row) => !row.data.active,
    review: (row) => reviewed(row) === false,
    missing: (row) => 'location' in row.data && !row.data.location,
    ready: (row) => row.data.active && reviewed(row) === true,
    off_day: (row) => row.data.active && !availableOn(row as Vehicle, day),
  };
  const count = (name: string) => rows.filter(rules[name]).length;
  if (match[2] && match[1] === 'orders')
    return {
      total: rows.length,
      active: count('active'),
      inactive: count('inactive'),
      review: count('review'),
      missing: count('missing'),
      ready: count('ready'),
    } satisfies OrderSummary;
  if (match[2]) {
    const fleet = rows as Vehicle[];
    return {
      total: rows.length,
      active: count('active'),
      inactive: count('inactive'),
      off_day: count('off_day'),
      active_payload_kg: fleet
        .filter((v) => v.data.active)
        .reduce((n, v) => n + v.data.capacity.weight_kg, 0),
      equipment: [...new Set(fleet.flatMap((v) => v.data.skills))].sort(),
    } satisfies VehicleSummary;
  }
  const status = params.get('status') ?? 'all',
    offset = Number(params.get('offset') ?? 0),
    limit = Number(params.get('limit') ?? 50);
  return (status === 'all' ? rows : rows.filter(rules[status])).slice(offset, offset + limit);
}
