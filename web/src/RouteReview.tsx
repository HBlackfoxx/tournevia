import { getLanguage, plural, t, useLanguage } from './i18n';
import {
  cubicMetres,
  dateTime,
  duration,
  euros,
  formatNumber,
  grossWeight,
  isTight,
  kilograms,
  kilometres,
  margin,
  metres,
  tonnes,
  truckSize,
} from './format';
import { equipmentList } from './equipment';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api, ApiError, sessionExpired, type EditRequest, type Job, type Plan } from './api';
import { MapPanel, type MapPoint, type MapRoute } from './MapPanel';
import { PageActions } from './PageActions';
import { Icon } from './Brand';
import { ask } from './confirm';
import { placePoints, routeEnds } from './Places';
import { sheetHash } from './RouteSheets';

// One distinct color per vehicle (up to the 25-vehicle limit) for map lines, pins and list chips.
// Chosen greedily for CIEDE2000 separation; the 19 added colors keep at least 4.5:1 contrast with white pin numbers.
export const routeColors = [
  '#1766e8',
  '#008473',
  '#8551c9',
  '#b45e07',
  '#ad3670',
  '#267d9b',
  '#445500',
  '#881111',
  '#ee0022',
  '#664411',
  '#008811',
  '#0011dd',
  '#005533',
  '#770088',
  '#aa5555',
  '#777700',
  '#554477',
  '#cc00bb',
  '#773344',
  '#005588',
  '#aa4422',
  '#886699',
  '#886622',
  '#557744',
  '#dd0055',
];
const color = (index: number) => routeColors[index % routeColors.length];
/** A letter per vehicle, shown on its pins and in the legend, so routes are told apart without colour. */
export const routeTag = (index: number) => String.fromCharCode(65 + (index % 26));
const reasons: Record<string, string> = {
  NO_COMPATIBLE_VEHICLE: 'No compatible vehicle',
  NO_ROAD_NEAR_PIN:
    'No road usable by the compatible trucks within 50 m of the pin. Move the pin to the vehicle entrance and review it.',
  NO_ROUTE_IN_DATASET: 'No route in the routing dataset',
  SOLVER_UNASSIGNED: 'Not assigned within the calculation budget',
  MANUALLY_UNASSIGNED: 'Manually unassigned',
};
/** True when `id` appears as a whole identifier, so "A1" does not match "A10" or "A1-B". */
export function mentions(text: string, id: string) {
  const part = /[\p{L}\p{N}_-]/u;
  for (let at = id ? text.indexOf(id) : -1; at >= 0; at = text.indexOf(id, at + 1))
    if (!part.test(text[at - 1] ?? '') && !part.test(text[at + id.length] ?? '')) return true;
  return false;
}
const dimensions = [
  'weight_kg',
  'volume_dm3',
  'pallet_eur_units',
  'pallet_ind_units',
  'loading_length_mm',
] as const;
/** One load figure in the units a dispatcher reads: "9 500 kg", "42 m³", "18 Euro pallets", "7,2 m loading length". */
function amount(index: number, value: number) {
  switch (dimensions[index]) {
    case 'weight_kg':
      return kilograms(value);
    case 'volume_dm3':
      return cubicMetres(value);
    case 'pallet_eur_units':
      return plural(value, '{0} Euro pallet', '{0} Euro pallets');
    case 'pallet_ind_units':
      return plural(value, '{0} industrial pallet', '{0} industrial pallets');
    default:
      return t('{0} loading length', metres(value));
  }
}
/** A translated title for a validator issue; the issue text keeps the technical detail and identifiers. */
const issueTitles: Record<string, string> = {
  CAPACITY: 'Load exceeds the vehicle capacity',
  WINDOW: 'Service starts after its time window',
  RETURN: 'Vehicle returns after its availability',
  SKILLS: 'Vehicle lacks required equipment',
  SHIPS_FROM: 'Vehicle does not start at the depot the delivery ships from',
  LIMIT: 'Route exceeds a vehicle limit',
  SCHEDULE: 'Schedule disagrees with the road travel times',
  LOAD: 'Remaining load is inconsistent',
  FORBIDDEN_ARC: 'Route uses a road connection unavailable to this truck',
  SNAP: 'Road path does not reach the reviewed pin',
  GEOMETRY: 'Road path is invalid',
  COVERAGE: 'Every delivery must appear exactly once',
  VEHICLE: 'Vehicle is unknown or used twice',
  PROFILE: 'Truck profile does not match the vehicle',
  IDENTITY: 'Stop sequence does not match the road legs',
  LEGS: 'Road legs do not match the stops',
  STOPS: 'Route has no stops or an unknown delivery',
  TOTALS: 'Route totals disagree with the road legs',
  COST: 'Cost estimate does not match the route',
  METRICS: 'Road duration or distance is invalid',
  MATRIX: 'Reachability data is missing',
  OWNERSHIP: 'Result belongs to another company',
  CONTEXT: 'Objective or road preference changed',
  PROVENANCE: 'Result origin does not match the inputs',
};
export function issueTitle(issue: string) {
  const title = issueTitles[issue.split(':', 1)[0]];
  return title ? t(title) : t('Blocking issue');
}
type Dimension = (typeof dimensions)[number];
/** The capacity dimension a route uses most, as a share of the vehicle's capacity (all five are enforced). */
export function tightestLoad(demands: Record<Dimension, number>[], capacity: Record<Dimension, number>) {
  let tightest = { index: 0, used: 0, capacity: capacity[dimensions[0]], share: -1 };
  dimensions.forEach((dimension, index) => {
    const used = demands.reduce((sum, demand) => sum + demand[dimension], 0);
    const share = capacity[dimension] > 0 ? used / capacity[dimension] : used > 0 ? Infinity : 0;
    if (share > tightest.share) tightest = { index, used, capacity: capacity[dimension], share };
  });
  return tightest;
}
const dimensionNames = ['weight', 'volume', 'Euro pallets', 'industrial pallets', 'loading length'];
type Assignment = { vehicle_id: string; order_ids: string[] };
/** Problems a manual assignment visibly has before recalculation: capacity, stop limit and equipment.
 * Time windows need road travel times, so they are left to the recalculation's validation. */
export function assignmentWarnings(routes: Assignment[], input: Plan['input_data']): string[] {
  const warnings: string[] = [];
  for (const route of routes) {
    const vehicle = input.vehicles.find((v) => v.id === route.vehicle_id);
    if (!vehicle) continue;
    const orders = route.order_ids
      .map((id) => input.orders.find((o) => o.id === id))
      .filter((o): o is (typeof input.orders)[number] => !!o);
    const load = tightestLoad(
      orders.map((o) => o.data.demand),
      vehicle.data.capacity,
    );
    if (load.share > 1)
      warnings.push(
        t('{0} would exceed its {1} capacity.', vehicle.data.fleet_number, t(dimensionNames[load.index])),
      );
    const limit = vehicle.data.limits?.max_stops;
    if (limit != null && orders.length > limit)
      warnings.push(t('{0} would exceed its limit of {1} stops.', vehicle.data.fleet_number, limit));
    const { start } = routeEnds(input, vehicle.data);
    for (const order of orders) {
      if (!order.data.required_skills.every((skill) => vehicle.data.skills.includes(skill)))
        warnings.push(
          t('{0} lacks equipment required by {1}.', vehicle.data.fleet_number, order.data.external_id),
        );
      if (order.data.ships_from && order.data.ships_from !== start.data.code)
        warnings.push(
          t(
            '{0} ships from {1}; {2} starts at {3}.',
            order.data.external_id,
            order.data.ships_from,
            vehicle.data.fleet_number,
            start.data.code,
          ),
        );
    }
  }
  return warnings;
}
function loadLabel(index: number, used: number, capacity: number) {
  switch (dimensions[index]) {
    case 'weight_kg':
      return `${tonnes(used)} / ${tonnes(capacity)}`;
    case 'volume_dm3':
      return `${formatNumber(used / 1000, 1)} / ${formatNumber(capacity / 1000, 1)}\u00a0m³`;
    case 'pallet_eur_units':
      return t('{0} / {1} Euro pallets', used, capacity);
    case 'pallet_ind_units':
      return t('{0} / {1} industrial pallets', used, capacity);
    default:
      return t('{0} / {1} m loading length', formatNumber(used / 1000, 1), formatNumber(capacity / 1000, 1));
  }
}
/** A stop's margin. Ten minutes or less is marked in words and with an icon, not by colour alone. */
function StopMargin({ seconds }: { seconds: number | undefined }) {
  if (seconds === undefined) return <>—</>;
  if (Math.round(seconds / 60) < 0)
    return (
      <span className="stop-margin late">
        <Icon name="alert" /> {margin(seconds)}
      </span>
    );
  if (isTight(seconds))
    return (
      <span className="stop-margin tight">
        <Icon name="clock" /> {margin(seconds)}
        <span className="sr-only"> · {t('tight')}</span>
      </span>
    );
  return <>{margin(seconds)}</>;
}
type Attempt = { key: string; version: number; body: EditRequest };
function assignment(plan: Plan): EditRequest {
  return {
    routes: plan.result.routes.map((r) => ({
      vehicle_id: r.vehicle_id,
      order_ids: r.stops.map((s) => s.order_id),
    })),
    unassigned_order_ids: plan.result.unassigned.map((u) => u.order_id),
  };
}
function pending(id: string): Attempt | null {
  try {
    return JSON.parse(sessionStorage.getItem(`vrp-edit:${id}`) ?? 'null') as Attempt | null;
  } catch {
    return null;
  }
}
// Creating an Intl formatter costs far more than formatting; a 250-stop plan formats every stop
// time on each render, so the two fixed Paris formatters are built once.
const PARIS_HOUR = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Europe/Paris',
  hour: '2-digit',
  hourCycle: 'h23',
});
const PARIS_CLOCK = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Europe/Paris',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
});
export function parisTime(day: string, seconds: number) {
  const midnightUTC = Date.parse(`${day}T00:00:00Z`);
  // Paris changes clocks after midnight: determine the offset at day start,
  // then add elapsed seconds. Reusing the noon offset breaks transition days.
  // An unreadable day shows no time rather than stopping the whole screen.
  if (Number.isNaN(midnightUTC) || !Number.isFinite(seconds)) return '';
  const offset = Number(PARIS_HOUR.format(new Date(midnightUTC))) * 3600;
  return PARIS_CLOCK.format(new Date(midnightUTC + (seconds - offset) * 1000));
}
/** A timestamp as seconds after the day's Paris midnight, the clock of the plan's stop times
 * (the server's planning epoch), so a window end compares directly with an arrival. */
export function parisSeconds(day: string, iso: string) {
  const midnightUTC = Date.parse(`${day}T00:00:00Z`);
  if (Number.isNaN(midnightUTC)) return NaN;
  const offset = Number(PARIS_HOUR.format(new Date(midnightUTC))) * 3600;
  return (Date.parse(iso) - midnightUTC) / 1000 + offset;
}
export function RouteReview({
  plan,
  mapStyle,
  onJob,
  onRefresh,
  onDirtyChange,
  onPrepare,
}: {
  plan: Plan;
  mapStyle: string;
  onJob: (job: Job) => void;
  onRefresh: () => void;
  onDirtyChange?: (dirty: boolean) => void;
  onPrepare?: () => void;
}) {
  const language = useLanguage();
  const [edit, setEdit] = useState<EditRequest>(() => assignment(plan)),
    [dirty, setDirty] = useState(false);
  const [editVersion, setEditVersion] = useState(plan.version);
  const [attempt, setAttempt] = useState<Attempt | null>(() => pending(plan.id)),
    [sending, setSending] = useState(false),
    [error, setError] = useState(''),
    [readyConflict, setReadyConflict] = useState(false);
  const [selected, setSelected] = useState(''),
    [vehicle, setVehicle] = useState(''),
    [hidden, setHidden] = useState<string[]>([]),
    [accept, setAccept] = useState(false);
  const [mobileView, setMobileView] = useState<'list' | 'map'>('list'),
    [destination, setDestination] = useState(''),
    [before, setBefore] = useState('');
  const [moving, setMoving] = useState(false),
    [expanded, setExpanded] = useState<string[]>(
      plan.result.unassigned.length ? [plan.input_data.vehicles[0]?.id ?? ''] : [],
    );
  const [allStops, setAllStops] = useState<string[]>([]);
  const [mobileActions, setMobileActions] = useState(false);
  // The phone "•••" menu closes like any other menu: Escape (focus back on its button) or a press outside it.
  const actionsMenu = useRef<HTMLDivElement>(null);
  // The route legend floats over the map; the map fits its points clear of it.
  const legend = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!mobileActions) return;
    const close = (event: Event) => {
      if (event instanceof KeyboardEvent) {
        if (event.key !== 'Escape') return;
        actionsMenu.current?.querySelector<HTMLElement>('.mobile-actions-toggle')?.focus();
      } else if (actionsMenu.current?.contains(event.target as Node)) return;
      setMobileActions(false);
    };
    document.addEventListener('keydown', close);
    document.addEventListener('pointerdown', close);
    return () => {
      document.removeEventListener('keydown', close);
      document.removeEventListener('pointerdown', close);
    };
  }, [mobileActions]);
  const dragging = useRef<string | null>(null);
  useEffect(() => {
    onDirtyChange?.(dirty);
    const guard = (event: BeforeUnloadEvent) => {
      if (dirty || attempt) {
        event.preventDefault();
        event.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [dirty, attempt, onDirtyChange]);
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  useEffect(() => {
    if (!dirty && !attempt) {
      setEdit(assignment(plan));
      setEditVersion(plan.version);
    }
  }, [plan, dirty, attempt]);
  const input = plan.input_data,
    result = plan.result,
    busy = !!plan.active_job_id || plan.validation === 'PENDING';
  useEffect(() => setAccept(false), [plan.id, plan.version]);
  const served = result.routes.reduce((n, r) => n + r.stops.length, 0),
    disabled = busy || sending || !!attempt;
  const routes = useMemo<MapRoute[]>(
    () =>
      result.routes
        .filter((r) => !hidden.includes(r.vehicle_id))
        .flatMap((r) =>
          r.legs.map((leg) => ({
            id: r.vehicle_id,
            color: color(input.vehicles.findIndex((v) => v.id === r.vehicle_id)),
            geometry: leg.geometry,
            selected: vehicle === r.vehicle_id,
          })),
        ),
    [result, input, hidden, vehicle],
  );
  const points = useMemo<MapPoint[]>(
    () => [
      ...placePoints([input.depot, ...(input.depots ?? [])]),
      ...input.orders
        .filter((o) => {
          const r = result.routes.find((r) => r.stops.some((s) => s.order_id === o.id));
          return !r || !hidden.includes(r.vehicle_id);
        })
        .map((o) => {
          const r = result.routes.find((r) => r.stops.some((s) => s.order_id === o.id));
          return {
            id: o.id,
            label: o.data.external_id,
            location: o.data.location!,
            address: o.data.address,
            detail: r
              ? `${parisTime(input.service_date, r.stops.find((s) => s.order_id === o.id)!.service_start)} · ${input.vehicles.find((v) => v.id === r.vehicle_id)?.data.fleet_number} · ${t('stop')} ${r.stops.findIndex((s) => s.order_id === o.id) + 1}`
              : t('Unassigned'),
            number: r ? r.stops.findIndex((s) => s.order_id === o.id) + 1 : undefined,
            tag: r ? routeTag(input.vehicles.findIndex((v) => v.id === r.vehicle_id)) : undefined,
            color: r ? color(input.vehicles.findIndex((v) => v.id === r.vehicle_id)) : '#6b6e70',
          };
        }),
    ],
    [input, result, hidden, language],
  );
  function choose(id: string) {
    setSelected(id);
    const target = result.routes.find((r) => r.stops.some((s) => s.order_id === id));
    if (target && target.stops.findIndex((s) => s.order_id === id) >= 4)
      setAllStops((ids) => (ids.includes(target.vehicle_id) ? ids : [...ids, target.vehicle_id]));
    const r = result.routes.find((r) => r.stops.some((s) => s.order_id === id));
    if (r) {
      setVehicle(r.vehicle_id);
      setDestination(r.vehicle_id);
      setExpanded((ids) => (ids.includes(r.vehicle_id) ? ids : [...ids, r.vehicle_id]));
    } else setDestination('');
    setBefore('');
  }
  function move(id: string, target: string, beforeId = '') {
    const next: EditRequest = {
      routes: edit.routes
        .map((r) => ({ ...r, order_ids: r.order_ids.filter((k) => k !== id) }))
        .filter((r) => r.order_ids.length),
      unassigned_order_ids: edit.unassigned_order_ids.filter((k) => k !== id),
    };
    if (target) {
      const r = next.routes.find((r) => r.vehicle_id === target);
      if (r) {
        const index = beforeId ? r.order_ids.indexOf(beforeId) : -1;
        r.order_ids.splice(index < 0 ? r.order_ids.length : index, 0, id);
      } else next.routes.push({ vehicle_id: target, order_ids: [id] });
    } else next.unassigned_order_ids.push(id);
    setEdit(next);
    setDirty(true);
    setSelected(id);
    setError('');
    if (target) {
      setVehicle(target);
      setExpanded([target]);
    }
  }
  function reorder(vehicleId: string, index: number, direction: number) {
    setEdit({
      ...edit,
      routes: edit.routes.map((r) => {
        if (r.vehicle_id !== vehicleId) return r;
        const ids = [...r.order_ids];
        [ids[index], ids[index + direction]] = [ids[index + direction], ids[index]];
        return { ...r, order_ids: ids };
      }),
    });
    setDirty(true);
    setError('');
  }
  async function submit() {
    const next = attempt ?? { key: crypto.randomUUID(), version: editVersion, body: edit };
    setAttempt(next);
    setSending(true);
    setError('');
    try {
      sessionStorage.setItem(`vrp-edit:${plan.id}`, JSON.stringify(next));
    } catch {
      /* Same-page retry still works. */
    }
    try {
      const job = await api<Job>(`plans/${plan.id}/edits`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'If-Match': `"${next.version}"`,
          'Idempotency-Key': next.key,
        },
        body: JSON.stringify(next.body),
      });
      try {
        sessionStorage.removeItem(`vrp-edit:${plan.id}`);
      } catch {
        /* optional storage */
      }
      if (live.current) {
        setAttempt(null);
        setDirty(false);
        onJob(job);
      }
    } catch (e) {
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
        try {
          sessionStorage.removeItem(`vrp-edit:${plan.id}`);
        } catch {
          /* optional */
        }
        if (live.current) setAttempt(null);
      }
      if (live.current)
        setError(e instanceof Error ? e.message : t('Edit failed. Retry the same submission.'));
    } finally {
      if (live.current) setSending(false);
    }
  }
  async function markReady(replaceConflictingReady = false) {
    setSending(true);
    setError('');
    setReadyConflict(false);
    try {
      await api<Plan>(`plans/${plan.id}/finalize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'If-Match': `"${plan.version}"` },
        body: JSON.stringify({
          accept_unassigned: accept,
          replace_conflicting_ready: replaceConflictingReady,
        }),
      });
      if (live.current) onRefresh();
    } catch (e) {
      if (!live.current) return;
      if (e instanceof ApiError && e.code === 'READY_CONFLICT') setReadyConflict(true);
      else setError(e instanceof Error ? e.message : t('Finalization failed; reload the current plan.'));
    } finally {
      if (live.current) setSending(false);
    }
  }
  async function download() {
    setSending(true);
    setError('');
    try {
      // French spreadsheet programs expect semicolons and a byte-order mark.
      const layout = getLanguage() === 'fr' ? 'spreadsheet' : 'standard';
      const response = await fetch(`/api/v1/plans/${plan.id}/export.csv?layout=${layout}`, {
        credentials: 'same-origin',
        headers: { 'If-Match': `"${plan.version}"` },
      });
      if (!response.ok) {
        if (response.status === 401) sessionExpired();
        const problem = await response.json().catch(() => ({}));
        throw new Error(
          typeof problem.detail === 'string' ? t(problem.detail) : t('Export rejected. Reload the plan.'),
        );
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = `plan-${plan.id}-v${plan.version}.csv`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      if (live.current) setError(e instanceof Error ? e.message : t('Export failed.'));
    } finally {
      if (live.current) setSending(false);
    }
  }
  // Route sheets are dispatch output too: the same conditions as the CSV file, and the server checks them again.
  const dispatchable =
    plan.status === 'READY' &&
    plan.validation === 'PASS' &&
    !plan.result_stale &&
    plan.sources_current &&
    served > 0 &&
    !dirty &&
    !disabled;
  function openSheets(vehicleId?: string) {
    window.open(
      sheetHash({ plan: plan.id, version: plan.version, date: input.service_date, vehicle: vehicleId }),
      '_blank',
      'noopener',
    );
  }
  const routeVehicles =
    dirty || moving
      ? [...input.vehicles].sort((a, b) => Number(b.id === vehicle) - Number(a.id === vehicle))
      : input.vehicles;
  const order = (id: string) => input.orders.find((o) => o.id === id)!;
  const clock = (seconds: number) => parisTime(input.service_date, seconds);
  // One day axis for every route, in whole hours from the first departure to the last return,
  // so the vehicles' days can be compared at a glance.
  const used = result.routes.filter((r) => r.stops.length);
  const dayFrom = used.length ? Math.floor(Math.min(...used.map((r) => r.departure)) / 3600) * 3600 : 0,
    dayTo = used.length
      ? Math.max(dayFrom + 7200, Math.ceil(Math.max(...used.map((r) => r.end)) / 3600) * 3600)
      : 0;
  const dayAt = (seconds: number) => ((seconds - dayFrom) / (dayTo - dayFrom || 1)) * 100;
  const dayHours = used.length
    ? Array.from(
        { length: Math.floor((dayTo - dayFrom) / (dayTo - dayFrom > 8 * 3600 ? 7200 : 3600)) + 1 },
        (_, i) => dayFrom + i * (dayTo - dayFrom > 8 * 3600 ? 7200 : 3600),
      )
    : [];
  const metricsCurrent = !dirty && !plan.result_stale && !busy;
  // Each stop's margin: its window end minus its arrival, on the saved result's estimated times.
  // A stop whose date or window cannot be read has no margin rather than a wrong one.
  const margins = useMemo(() => {
    const ends = new Map(input.orders.map((o) => [o.id, o.data.time_window.end]));
    const entries: [string, number][] = [];
    for (const route of result.routes)
      for (const stop of route.stops) {
        const end = ends.get(stop.order_id);
        const seconds = end ? parisSeconds(input.service_date, end) - stop.arrival : NaN;
        if (Number.isFinite(seconds)) entries.push([stop.order_id, seconds]);
      }
    return new Map(entries);
  }, [input, result]);
  // The day's smallest margin, and every stop at ten minutes or less, in route order.
  const smallest = [...margins].reduce<[string, number] | null>(
    (least, entry) => (!least || entry[1] < least[1] ? entry : least),
    null,
  );
  const tight = [...margins]
    .filter(([, seconds]) => isTight(seconds))
    .map(([id]) => order(id).data.external_id);
  const warnings = useMemo(() => (dirty ? assignmentWarnings(edit.routes, input) : []), [dirty, edit, input]);
  // Version 1 is the untouched optimization result; any edit or Ready change increments it.
  const candidate = plan.version === 1;
  // Not approvable as it stands: validation failed or is pending, or the result needs recalculating.
  const blocked = plan.validation !== 'PASS' || plan.result_stale;
  const known = result.routes.reduce((n, r) => n + r.known_cost_minor, 0);
  const selectedOrder = selected && selected !== input.depot.id ? order(selected) : undefined;
  const selectedRoute = result.routes.find((r) => r.stops.some((s) => s.order_id === selected)),
    selectedStop = selectedRoute?.stops.find((s) => s.order_id === selected);
  const selectedDetails = selectedOrder && (
    <div className="selected-stop-detail">
      <div className="section-heading">
        <strong>{selectedOrder.data.external_id}</strong>
        <span className="badge">{t('Selected stop')}</span>
      </div>
      <p>{selectedOrder.data.address}</p>
      <div className="stop-detail-columns">
        <div>
          <small>
            {t('Arrival / service start')}{' '}
            <b>
              {metricsCurrent && selectedStop
                ? `${clock(selectedStop.arrival)} / ${clock(selectedStop.service_start)}`
                : t('Recalculate to update')}
            </b>
          </small>
          <small>
            {t('Departure / duration')}{' '}
            <b>
              {metricsCurrent && selectedStop
                ? `${clock(selectedStop.departure)} / ${selectedOrder.data.service_seconds / 60} min`
                : t('Recalculate to update')}
            </b>
          </small>
          <small>
            {t('Service window')}{' '}
            <b>
              {selectedOrder.data.time_window.start.slice(11, 16)}–
              {selectedOrder.data.time_window.end.slice(11, 16)}
            </b>
          </small>
          {metricsCurrent && selectedStop && (
            <small>
              {t('Margin before the window ends')}{' '}
              <b>
                <StopMargin seconds={margins.get(selectedStop.order_id)} />
              </b>
            </small>
          )}
        </div>
        <div>
          <small>
            {plural(selectedOrder.data.demand.pallet_eur_units, '{0} Euro pallet', '{0} Euro pallets')} ·{' '}
            {kilograms(selectedOrder.data.demand.weight_kg)}
          </small>
          <small>
            {cubicMetres(selectedOrder.data.demand.volume_dm3)} ·{' '}
            {plural(
              selectedOrder.data.demand.pallet_ind_units,
              '{0} industrial pallet',
              '{0} industrial pallets',
            )}{' '}
            · {metres(selectedOrder.data.demand.loading_length_mm)}
          </small>
          <small>{selectedOrder.data.instructions || t('No delivery instructions.')}</small>
        </div>
      </div>
      <details>
        <summary>{t('All load and equipment details')}</summary>
        <p>{dimensions.map((d, i) => amount(i, selectedOrder.data.demand[d])).join(' · ')}</p>
        <small>
          {t('Required equipment:')} {equipmentList(selectedOrder.data.required_skills) || t('None')}{' '}
          {t('· priority')} {selectedOrder.data.priority ?? 0}
        </small>
        {selectedStop && metricsCurrent && (
          <p>
            {t('Remaining / free load:')}{' '}
            {dimensions
              .map(
                (d, i) =>
                  `${amount(i, selectedStop.load_after[i])} / ${amount(i, (input.vehicles.find((v) => v.id === selectedRoute?.vehicle_id)?.data.capacity[d] ?? 0) - selectedStop.load_after[i])}`,
              )
              .join(' · ')}
          </p>
        )}
      </details>
      <button className="text-button" onClick={() => setMoving((v) => !v)}>
        {moving ? t('Close stop move') : t('Move this stop')}
      </button>
    </div>
  );
  // Deliveries left unassigned may simply not fit a vehicle's own route limits.
  const limitsReached = result.routes.flatMap((route) => {
    const vehicle = input.vehicles.find((v) => v.id === route.vehicle_id);
    const limits = vehicle?.data.limits ?? {},
      name = vehicle?.data.fleet_number ?? '';
    return [
      limits.max_stops != null && route.stops.length >= limits.max_stops
        ? t('{0} {1}/{2} stops', name, route.stops.length, limits.max_stops)
        : null,
      limits.max_distance_m != null && route.distance_m >= limits.max_distance_m * 0.95
        ? t('{0} distance limit', name)
        : null,
      limits.max_driving_seconds != null && route.driving_seconds >= limits.max_driving_seconds * 0.95
        ? t('{0} driving-time limit', name)
        : null,
    ].filter((note): note is string => note !== null);
  });
  return (
    <section
      className={`route-review ${plan.validation === 'FAIL' ? 'is-invalid' : ''}`}
      aria-label={t('Draft route review')}
    >
      <div className="review-heading">
        <div className="sr-only">
          <h2 className="plan-heading">
            {input.data_kind === 'SYNTHETIC'
              ? plan.status === 'READY'
                ? t('Saved synthetic ready plan · version {0}', plan.version)
                : t('Saved synthetic draft · version {0}', plan.version)
              : plan.status === 'READY'
                ? t('Saved ready plan · version {0}', plan.version)
                : t('Saved draft · version {0}', plan.version)}
          </h2>
          <p>
            {t('Independent validation')} {t(plan.validation ?? result.validation)}
          </p>
        </div>
        <PageActions>
          <div ref={actionsMenu} className={`plan-actions ${mobileActions ? 'is-open' : ''}`}>
            <button
              className="mobile-actions-toggle secondary"
              aria-label={t('Plan actions')}
              aria-expanded={mobileActions}
              onClick={() => setMobileActions((open) => !open)}
            >
              •••
            </button>
            <div className="actions">
              {onPrepare && (
                <button className="mobile-new-calculation secondary" onClick={onPrepare}>
                  {t('New calculation')}
                </button>
              )}
              <button
                className="secondary"
                disabled={sending || !!attempt}
                onClick={async () => {
                  if (dirty) {
                    if (
                      !(await ask(t('Discard unsaved stop moves and reload the saved plan?'), {
                        confirm: t('Discard moves and reload'),
                      }))
                    )
                      return;
                    setDirty(false);
                    setEdit(assignment(plan));
                  }
                  onRefresh();
                }}
              >
                {dirty ? t('Discard changes') : t('Reload plan')}
              </button>
              {(dirty || attempt) && (
                <button
                  disabled={sending || (!attempt && (busy || !dirty || editVersion !== plan.version))}
                  onClick={() => void submit()}
                >
                  {sending
                    ? t('Saving…')
                    : attempt
                      ? t('Retry edit submission')
                      : t('Recalculate these routes')}
                </button>
              )}
            </div>
          </div>
        </PageActions>
      </div>
      <div className="plan-metrics">
        {/* Assignment counts follow pending edits; distance, time and cost need a recalculation. */}
        <div>
          <strong>{dirty ? edit.routes.reduce((n, r) => n + r.order_ids.length, 0) : served}</strong>
          <small>{t('Assigned deliveries')}</small>
        </div>
        <div
          className={(dirty ? edit.unassigned_order_ids.length : result.unassigned.length) ? 'attention' : ''}
        >
          <strong>{dirty ? edit.unassigned_order_ids.length : result.unassigned.length}</strong>
          <small>{t('Unassigned deliveries')}</small>
        </div>
        <div
          className={`smallest-margin ${smallest && isTight(smallest[1]) ? 'attention' : ''}`}
          title={t(
            'How much later the truck could arrive at this stop and still start within its window, on estimated travel times without live traffic.',
          )}
        >
          <strong>
            {smallest ? margin(smallest[1]) : '—'}
            {smallest && <span className="figure-stop"> {order(smallest[0]).data.external_id}</span>}
          </strong>
          <small>{metricsCurrent ? t('Smallest margin') : t('Previous smallest margin')}</small>
        </div>
        <div>
          <strong>
            {dirty
              ? edit.routes.filter((r) => r.order_ids.length).length
              : result.routes.filter((r) => r.stops.length).length}{' '}
            / {input.vehicles.length}
          </strong>
          <small>{t('Vehicles used')}</small>
        </div>
        <div>
          <strong>{kilometres(result.routes.reduce((n, r) => n + r.distance_m, 0))}</strong>
          <small>{metricsCurrent ? t('Total distance') : t('Previous distance')}</small>
        </div>
        <div>
          <strong>{duration(result.routes.reduce((n, r) => n + r.driving_seconds, 0))}</strong>
          <small>{metricsCurrent ? t('Driving time') : t('Previous driving time')}</small>
        </div>
        <div>
          <strong>{euros(known, 0)}</strong>
          <small>
            {/* If the label wraps, the separator stays with the first part. */}
            {metricsCurrent
              ? t('Modeled cost · tolls excluded').replace(' · ', ' · ')
              : t('Previous modeled cost')}
          </small>
        </div>
      </div>
      {dirty ? (
        <div role="status" className="notice">
          <p>
            <strong>{t('Manual route changes are pending.')}</strong>{' '}
            {t(
              'Unsaved assignment. The map and totals show the last saved routes; stop times below require recalculation.',
            )}
          </p>
          {plan.status === 'READY' && (
            <p>
              {t(
                'This plan is Ready: recalculating returns it to Draft. Mark it Ready again and redistribute the updated routes.',
              )}
            </p>
          )}
          {warnings.length > 0 && (
            <ul className="edit-warnings">
              {warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        // The approval band: the plan's state, and the one thing to do next (disabled while blocked).
        <section
          className={`approval ${
            plan.validation === 'FAIL'
              ? 'approval-failed'
              : plan.status === 'READY'
                ? 'approval-ready'
                : blocked
                  ? ''
                  : result.unassigned.length
                    ? 'approval-partial'
                    : ''
          }`}
          aria-labelledby="approval-state"
        >
          <div className="approval-state">
            <Icon
              name={
                plan.validation === 'FAIL'
                  ? 'alert'
                  : plan.status === 'READY'
                    ? 'check'
                    : !blocked && result.unassigned.length
                      ? 'alert'
                      : 'info'
              }
            />
            <div>
              <h3 id="approval-state">
                {plan.validation === 'FAIL'
                  ? t('Invalid result')
                  : plan.status === 'READY'
                    ? t('Ready · version {0}', plan.version)
                    : blocked
                      ? t('Draft · needs recalculation')
                      : result.unassigned.length
                        ? plural(
                            result.unassigned.length,
                            'Draft · {0} unassigned delivery',
                            'Draft · {0} unassigned deliveries',
                          )
                        : t('Draft · validation passed')}
              </h3>
              <p>
                {plan.validation === 'FAIL' ? (
                  <>
                    {plural(
                      result.blocking_issues.length,
                      '{0} blocking validation issue.',
                      '{0} blocking validation issues.',
                    )}{' '}
                    {t(
                      'This result cannot be marked Ready or exported. Review the affected vehicles and deliveries.',
                    )}
                  </>
                ) : plan.status === 'READY' ? (
                  <>
                    {plan.finalized_at && t('Marked ready {0}.', dateTime(plan.finalized_at))}{' '}
                    {result.unassigned.length > 0 &&
                      t('{0} of {1} deliveries assigned.', served, input.orders.length)}{' '}
                    {t('Editing this plan clears Ready.')}
                  </>
                ) : blocked ? (
                  t('This result needs recalculation and validation before approval.')
                ) : result.unassigned.length ? (
                  <>
                    <span className="desktop-copy">
                      {candidate
                        ? t(
                            'Candidate found within the search budget: {0} of {1} deliveries assigned. Review the unassigned deliveries before marking this plan Ready.',
                            served,
                            input.orders.length,
                          )
                        : t(
                            '{0} of {1} deliveries assigned. Review the unassigned deliveries before marking this plan Ready.',
                            served,
                            input.orders.length,
                          )}
                    </span>
                    <span className="mobile-copy">
                      {t('Review the unassigned deliveries before marking this plan Ready.')}
                    </span>
                  </>
                ) : candidate ? (
                  t(
                    'Candidate found within the search budget: all {0} deliveries are assigned and validation passed. This result remains a Draft until you approve it.',
                    served,
                  )
                ) : (
                  t(
                    'All {0} deliveries are assigned and validation passed. This result remains a Draft until you approve it.',
                    served,
                  )
                )}
              </p>
              {/* A tight plan is valid; the dispatcher still decides with the stops that a delay would make late. */}
              {plan.validation !== 'FAIL' && !blocked && tight.length > 0 && (
                <p className="approval-margin">
                  <Icon name="clock" />{' '}
                  {plural(
                    tight.length,
                    '{0} stop is reached 10 min or less before its window ends:',
                    '{0} stops are reached 10 min or less before their windows end:',
                  )}{' '}
                  {tight.join(', ')}.
                </p>
              )}
            </div>
          </div>
          {input.data_kind !== 'SYNTHETIC' && !attempt && (
            <div className="approval-actions">
              {plan.status === 'READY' ? (
                <>
                  <button disabled={!dispatchable} onClick={() => openSheets()}>
                    <Icon name="print" /> {t('Print the route sheets')}
                  </button>
                  <button className="secondary" disabled={!dispatchable} onClick={() => void download()}>
                    {t('Export CSV')}
                  </button>
                </>
              ) : !blocked && result.unassigned.length > 0 && !accept ? (
                <button
                  className="secondary"
                  onClick={() => {
                    choose(result.unassigned[0].order_id);
                    setMoving(true);
                    setMobileView('list');
                  }}
                >
                  {t('Review unassigned')}
                </button>
              ) : (
                <button
                  disabled={
                    disabled ||
                    blocked ||
                    !plan.sources_current ||
                    served === 0 ||
                    (result.unassigned.length > 0 && !accept)
                  }
                  onClick={() => void markReady()}
                >
                  {t('Mark ready')}
                </button>
              )}
            </div>
          )}
        </section>
      )}
      <p className="limitation">
        {t('Planning estimate. Verify current road access and driver requirements. Tolls are not included.')}
      </p>
      {input.vehicles.some((v) => v.example) && (
        <p className="notice example-plan">
          <span className="badge example-badge">{t('Example')}</span>{' '}
          {t(
            'A plan of example deliveries and vehicles, to try Tournevia. Its route sheets and CSV file say it is not for driving.',
          )}
        </p>
      )}
      {!plan.sources_current && (
        <p role="status" className="notice">
          {t(
            'Deliveries, vehicles or the depot changed after this calculation. Start a new calculation before marking it Ready or exporting.',
          )}
        </p>
      )}
      {readyConflict && (
        <div role="alert" className="notice">
          <p>
            <strong>
              {t(
                'Another Ready plan for this day already dispatches some of the same vehicles or deliveries.',
              )}
            </strong>{' '}
            {t('Replacing it returns that plan to Draft. Redistribute routes if its file was already sent.')}
          </p>
          <div className="actions">
            <button className="secondary" onClick={() => setReadyConflict(false)}>
              {t('Keep the other plan')}
            </button>
            <button disabled={sending} onClick={() => void markReady(true)}>
              {t('Replace the other Ready plan')}
            </button>
          </div>
        </div>
      )}
      {input.data_kind === 'SYNTHETIC' && (
        <p className="limitation">
          {t('Synthetic plans cannot be marked ready or exported as operational routes.')}
        </p>
      )}
      {served === 0 && (
        <p className="error">{t('No work served. This calculation is not a dispatch success.')}</p>
      )}
      {(busy || plan.result_stale) && (
        <p role="status" className="error">
          {busy ? t('Recalculation pending.') : t('Recalculation failed; the edit was not applied.')}{' '}
          {t('Existing routes, times and geometry are stale. Finalization and export are disabled.')}
        </p>
      )}
      {plan.edit_error && (
        <p role="alert" className="error">
          {t(plan.edit_error.message)} <small>({plan.edit_error.code})</small>
        </p>
      )}
      {dirty && editVersion !== plan.version && (
        <div role="alert" className="error">
          <p>
            {t(
              'The saved plan changed after you began editing. Reload the draft before submitting another edit.',
            )}
          </p>
          <button className="secondary" disabled={busy} onClick={() => setEditVersion(plan.version)}>
            {t('Keep my moves on the latest version')}
          </button>{' '}
          <small>{t('Recalculating then replaces the other change with your assignment.')}</small>
        </div>
      )}
      {attempt && (
        <p role="status">
          {t('Edit submission awaiting confirmation. Retry uses the original version, sequence and key.')}
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="mobile-view tabs" aria-label={t('Planning view')}>
        <button aria-pressed={mobileView === 'list'} onClick={() => setMobileView('list')}>
          <Icon name="list" /> {t('List')}
        </button>
        <button aria-pressed={mobileView === 'map'} onClick={() => setMobileView('map')}>
          <Icon name="map" /> {t('Map')}
        </button>
      </div>
      <div className={`planning-split show-${mobileView}`}>
        <div className="planning-list">
          <div className="route-scroll">
            {result.blocking_issues.map((issue, i) => (
              <article className="blocking-issue" key={i}>
                <h3>
                  <Icon name="alert" /> {issueTitle(issue)}
                </h3>
                <p>
                  <small>{issue}</small>
                </p>
                <button
                  className="secondary"
                  onClick={() => {
                    const match = input.orders.find((o) => mentions(issue, o.data.external_id));
                    if (match) choose(match.id);
                    else {
                      const v = input.vehicles.find((v) => mentions(issue, v.data.fleet_number));
                      if (v) {
                        setVehicle(v.id);
                        setExpanded((ids) => (ids.includes(v.id) ? ids : [...ids, v.id]));
                      }
                    }
                  }}
                >
                  {t('Inspect vehicle and stops')}
                </button>
              </article>
            ))}
            {selectedOrder && (moving || dirty) && (
              <section className="stop-move">
                <div className="section-heading">
                  <h3>
                    {t('Move stop ·')} {selectedOrder.data.external_id}
                  </h3>
                  <span className="badge attention">{dirty ? t('Pending') : t('Edit sequence')}</span>
                </div>
                <p>{selectedOrder.data.address}</p>
                <div className="fields">
                  <label>
                    {t('Destination vehicle')}
                    <select
                      value={destination}
                      disabled={disabled}
                      onChange={(e) => {
                        setDestination(e.target.value);
                        setBefore('');
                      }}
                    >
                      <option value="">{t('Unassigned')}</option>
                      {input.vehicles.map((v) => (
                        <option key={v.id} value={v.id}>
                          {v.data.fleet_number}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    {t('Insert position')}
                    <select
                      value={before}
                      disabled={disabled || !destination}
                      onChange={(e) => setBefore(e.target.value)}
                    >
                      <option value="">{t('At end')}</option>
                      {edit.routes
                        .find((r) => r.vehicle_id === destination)
                        ?.order_ids.filter((id) => id !== selected)
                        .map((id) => (
                          <option key={id} value={id}>
                            {t('Before')} {order(id).data.external_id}
                          </option>
                        ))}
                    </select>
                  </label>
                </div>
                <div className="actions">
                  <button
                    className="secondary"
                    disabled={disabled || edit.unassigned_order_ids.includes(selected)}
                    onClick={() => move(selected, '')}
                  >
                    {t('Move to Unassigned')}
                  </button>
                  {/* Unassigning has its own button; applying needs a vehicle. */}
                  <button
                    disabled={disabled || !destination}
                    onClick={() => move(selected, destination, before)}
                  >
                    {t('Apply stop move')}
                  </button>
                </div>
              </section>
            )}
            {/* The bar stays at the top while the routes scroll; the axis inside keeps the strips' measure. */}
            {dayHours.length > 0 && (
              <div className="day-axis-bar" aria-hidden="true">
                <div className="day-axis">
                  {dayHours.map((h) => (
                    <span key={h} style={{ left: `${dayAt(h)}%` }}>
                      {clock(h)}
                    </span>
                  ))}
                </div>
              </div>
            )}
            {routeVehicles.map((v) => {
              const vi = input.vehicles.findIndex((item) => item.id === v.id),
                route = edit.routes.find((r) => r.vehicle_id === v.id),
                saved = result.routes.find((r) => r.vehicle_id === v.id),
                ids = route?.order_ids ?? [];
              const load = tightestLoad(
                ids.map((id) => order(id).data.demand),
                v.data.capacity,
              );
              return (
                <section
                  key={v.id}
                  onDragOver={(e) => {
                    if (!disabled) e.preventDefault();
                  }}
                  onDrop={(e) => {
                    e.preventDefault();
                    if (!disabled && dragging.current) {
                      move(dragging.current, v.id);
                      dragging.current = null;
                    }
                  }}
                  className={`vehicle-route ${vehicle === v.id ? 'selected-route' : ''}`}
                  aria-label={t('Route {0}', v.data.fleet_number)}
                >
                  <div className="route-card-heading">
                    <button
                      className="route-title"
                      onClick={() => {
                        setVehicle(v.id);
                        setExpanded((open) =>
                          open.includes(v.id) ? open.filter((id) => id !== v.id) : [...open, v.id],
                        );
                      }}
                    >
                      <span className="route-dot" style={{ background: color(vi) }} />
                      {v.data.fleet_number}
                    </button>
                    {/* A vehicle without deliveries has nothing to validate: it is simply not used. */}
                    {!ids.length ? (
                      <span className="badge">{t('Not used')}</span>
                    ) : (
                      <span
                        className={`badge ${!metricsCurrent ? 'attention' : plan.validation === 'PASS' ? 'ready' : 'failed'}`}
                      >
                        {!metricsCurrent
                          ? t('Pending validation')
                          : plan.validation === 'PASS'
                            ? t('No blocking issues')
                            : t('Review issues')}
                      </span>
                    )}
                  </div>
                  {/* The truck the route was calculated for: its size and weight chose these roads. */}
                  <p className="route-truck">
                    {truckSize(v.data.routing)} · {grossWeight(v.data.routing)}
                  </p>
                  {(() => {
                    // With several places in the plan, every route says where its truck loads (its start):
                    // the deliveries it carries are loaded there.
                    const { start, end } = routeEnds(input, v.data);
                    return !input.depots?.length ? null : (
                      <p className="route-ends">
                        {start.id === end.id
                          ? t('Loaded at {0} · returns there', start.data.name)
                          : t('Loaded at {0} · ends at {1}', start.data.name, end.data.name)}
                      </p>
                    );
                  })()}
                  <div className="route-card-metrics">
                    <span>{plural(ids.length, '{0} stop', '{0} stops')}</span>
                    <span>{saved ? `${clock(saved.departure)}–${clock(saved.end)}` : t('No route')}</span>
                    <span>{saved ? kilometres(saved.distance_m) : ''}</span>
                    {saved && Math.round(saved.waiting_seconds / 60) > 0 && (
                      <span className="route-waiting">
                        {t('{0} waiting', duration(saved.waiting_seconds))}
                      </span>
                    )}
                  </div>
                  {saved && saved.stops.length > 0 && dayHours.length > 0 && (
                    <div className="day-strip" aria-hidden="true">
                      <span
                        className="day-span"
                        style={{
                          left: `${dayAt(saved.departure)}%`,
                          width: `${dayAt(saved.end) - dayAt(saved.departure)}%`,
                          background: color(vi),
                        }}
                      />
                      {/* Waiting for a window to open: the same route, drawn hollow. */}
                      {saved.stops
                        .filter((s) => s.service_start > s.arrival)
                        .map((s) => (
                          <b
                            key={`wait-${s.order_id}`}
                            className="day-wait"
                            title={t(
                              'Waits {0} for the window of {1}',
                              duration(s.service_start - s.arrival),
                              order(s.order_id).data.external_id,
                            )}
                            style={{
                              left: `${dayAt(s.arrival)}%`,
                              width: `${dayAt(s.service_start) - dayAt(s.arrival)}%`,
                              color: color(vi),
                            }}
                          />
                        ))}
                      {saved.stops.map((s) => (
                        <i key={s.order_id} style={{ left: `${dayAt(s.service_start)}%` }} />
                      ))}
                    </div>
                  )}
                  <div className="route-load">
                    <small>{saved ? duration(saved.driving_seconds) : '—'}</small>
                    <meter
                      min={0}
                      max={1}
                      high={0.9}
                      optimum={0}
                      value={Math.min(1, load.share)}
                      aria-label={t('{0} tightest load', v.data.fleet_number)}
                    />
                    <small title={t('The capacity dimension this route uses most; all five are enforced.')}>
                      {loadLabel(load.index, load.used, load.capacity)}
                    </small>
                    {saved && saved.stops.length > 0 && (
                      <button
                        className="text-button"
                        onClick={() => {
                          setVehicle(v.id);
                          choose(saved.stops[0].order_id);
                          setMobileView('map');
                        }}
                      >
                        {t('On map')}
                      </button>
                    )}
                  </div>
                  <details
                    open={expanded.includes(v.id)}
                    onToggle={(e) => {
                      const open = e.currentTarget.open;
                      setExpanded((ids) =>
                        open ? (ids.includes(v.id) ? ids : [...ids, v.id]) : ids.filter((id) => id !== v.id),
                      );
                    }}
                  >
                    <summary>{t('View stops')}</summary>
                    {!ids.length ? (
                      <p>{t('No deliveries assigned.')}</p>
                    ) : (
                      <div className="table-scroll">
                        <table>
                          <thead>
                            <tr>
                              <th className="num">#</th>
                              <th>{t('Delivery / address')}</th>
                              <th>{t('Start')}</th>
                              <th>{t('Window')}</th>
                              <th
                                className="num"
                                title={t(
                                  'How much later the truck could arrive at this stop and still start within its window, on estimated travel times without live traffic.',
                                )}
                              >
                                {t('Margin')}
                              </th>
                            </tr>
                          </thead>
                          <tbody>
                            {ids.map((id, i) => {
                              if (!dirty && !allStops.includes(v.id) && i >= 4) return null;
                              const o = order(id),
                                stop = saved?.stops.find((s) => s.order_id === id);
                              return (
                                <tr
                                  key={id}
                                  draggable={!disabled}
                                  onDragStart={(e) => {
                                    dragging.current = id;
                                    e.dataTransfer.setData('text/plain', id);
                                  }}
                                  onDragEnd={() => {
                                    dragging.current = null;
                                  }}
                                  onDragOver={(e) => {
                                    if (!disabled) e.preventDefault();
                                  }}
                                  onDrop={(e) => {
                                    e.preventDefault();
                                    e.stopPropagation();
                                    if (!disabled && dragging.current && dragging.current !== id) {
                                      move(dragging.current, v.id, id);
                                      dragging.current = null;
                                    }
                                  }}
                                  className={selected === id ? 'selected-stop' : ''}
                                >
                                  <td className="num">{i + 1}</td>
                                  <td>
                                    <button
                                      className="stop-name"
                                      aria-label={`${i + 1}. ${o.data.external_id}`}
                                      onClick={() => choose(id)}
                                    >
                                      {o.data.external_id}
                                    </button>
                                    <small>{o.data.address}</small>
                                    {selected === id && (
                                      <div className="row-sequence">
                                        <button
                                          className="text-button"
                                          aria-label={t('Move {0} up', o.data.external_id)}
                                          disabled={disabled || i === 0}
                                          onClick={() => reorder(v.id, i, -1)}
                                        >
                                          {t('↑ Move up')}
                                        </button>
                                        <button
                                          className="text-button"
                                          aria-label={t('Move {0} down', o.data.external_id)}
                                          disabled={disabled || i === ids.length - 1}
                                          onClick={() => reorder(v.id, i, 1)}
                                        >
                                          {t('↓ Move down')}
                                        </button>
                                      </div>
                                    )}
                                  </td>
                                  <td>
                                    {metricsCurrent && stop ? clock(stop.service_start) : t('Pending')}
                                    {metricsCurrent &&
                                      stop &&
                                      Math.round((stop.service_start - stop.arrival) / 60) > 0 && (
                                        <small className="stop-wait">
                                          {t('waits {0}', duration(stop.service_start - stop.arrival))}
                                        </small>
                                      )}
                                  </td>
                                  <td className="num window-cell">
                                    {o.data.time_window.start.slice(11, 16)}–
                                    {o.data.time_window.end.slice(11, 16)}
                                    {/* On a phone the margin sits under the window, in place of its own column. */}
                                    {metricsCurrent && stop && (
                                      <small className="margin-under">
                                        {t('Margin')} <StopMargin seconds={margins.get(id)} />
                                      </small>
                                    )}
                                  </td>
                                  <td className="num margin-cell">
                                    {metricsCurrent && stop ? <StopMargin seconds={margins.get(id)} /> : '—'}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    )}
                    {ids.length > 4 && !dirty && (
                      <div className="stop-pagination">
                        <small>
                          {t(
                            'Showing {0} of {1} stops',
                            allStops.includes(v.id) ? ids.length : 4,
                            ids.length,
                          )}
                        </small>
                        <button
                          className="text-button"
                          onClick={() =>
                            setAllStops((open) =>
                              open.includes(v.id) ? open.filter((id) => id !== v.id) : [...open, v.id],
                            )
                          }
                        >
                          {allStops.includes(v.id) ? t('Show fewer stops') : t('View all stops')}
                        </button>
                      </div>
                    )}
                    {ids.includes(selected) && selectedDetails}
                    {dispatchable && saved && saved.stops.length > 0 && (
                      <button className="text-button route-sheet-link" onClick={() => openSheets(v.id)}>
                        <Icon name="print" /> {t('Route sheet for {0}', v.data.fleet_number)}
                      </button>
                    )}
                    <details className="route-facts">
                      <summary>{t('Capacity, timing and cost details')}</summary>
                      <p>
                        {t('Capacity:')} {dimensions.map((d, i) => amount(i, v.data.capacity[d])).join(' · ')}
                      </p>
                      {saved && (
                        <>
                          <p>
                            {!metricsCurrent ? t('Previous result: ') : ''}
                            {t(
                              '{0} · driving {1} · service {2} · waiting {3} · depart {4} · return {5}',
                              kilometres(saved.distance_m, 1),
                              duration(saved.driving_seconds),
                              duration(saved.service_seconds),
                              duration(saved.waiting_seconds),
                              clock(saved.departure),
                              clock(saved.end),
                            )}
                          </p>
                          <p>
                            {metricsCurrent
                              ? t(
                                  'Modeled operating cost: {0} · fixed {1} · distance {2} · time {3} (driving, waiting and service). Tolls excluded.',
                                  euros(saved.known_cost_minor),
                                  euros(saved.fixed_minor),
                                  euros(saved.distance_minor),
                                  euros(saved.time_minor),
                                )
                              : t(
                                  'Previous saved cost: {0} · fixed {1} · distance {2} · time {3} (driving, waiting and service). Tolls excluded.',
                                  euros(saved.known_cost_minor),
                                  euros(saved.fixed_minor),
                                  euros(saved.distance_minor),
                                  euros(saved.time_minor),
                                )}
                          </p>
                        </>
                      )}
                    </details>
                  </details>
                </section>
              );
            })}
          </div>
          <section
            className={`unassigned-panel ${edit.unassigned_order_ids.length ? '' : 'all-assigned'}`}
            aria-label={t('Unassigned deliveries')}
          >
            <h3>
              {edit.unassigned_order_ids.length ? (
                <>
                  <Icon name="alert" /> {t('Unassigned deliveries ({0})', edit.unassigned_order_ids.length)}
                </>
              ) : (
                <>
                  <Icon name="check" /> {t('Validation and approval')}
                </>
              )}
            </h3>
            {!dirty &&
              limitsReached.length > 0 &&
              result.unassigned.some((u) => u.reason === 'SOLVER_UNASSIGNED') && (
                <p className="info limit-note">
                  {t(
                    'Route limits were reached: {0}. Raise or remove them in Fleet if these deliveries should be served, then recalculate.',
                    limitsReached.join(' · '),
                  )}
                </p>
              )}
            {/* The safety margin narrows every window, so it can be why a delivery found no place. */}
            {!dirty &&
              !!input.safety_margin_seconds &&
              result.unassigned.some((u) => u.reason === 'SOLVER_UNASSIGNED') && (
                <p className="info limit-note">
                  {t(
                    'Calculated with a {0} min safety margin before each window ends. A delivery that can only be reached later than that stays unassigned; lower the margin in Settings › Calculation to plan it.',
                    Math.round(input.safety_margin_seconds / 60),
                  )}
                </p>
              )}
            {!edit.unassigned_order_ids.length ? (
              <p>
                {plan.validation === 'PASS'
                  ? t('All selected deliveries are assigned.')
                  : t('Review the blocking issues above.')}{' '}
                {plan.status === 'READY' && plan.validation === 'PASS' && !plan.result_stale && !dirty
                  ? t('The current Ready result is eligible for export.')
                  : t('A successful calculation does not automatically approve the plan.')}
              </p>
            ) : (
              edit.unassigned_order_ids.map((id) => (
                <div className="unassigned-row" key={id}>
                  <div>
                    <strong>{order(id).data.external_id}</strong> · {order(id).data.address}
                    <small>
                      {dirty
                        ? t('Assignment changed')
                        : t(
                            reasons[result.unassigned.find((u) => u.order_id === id)?.reason ?? ''] ??
                              'Unassigned',
                          )}
                    </small>
                  </div>
                  <button
                    className="secondary"
                    onClick={() => {
                      choose(id);
                      setMoving(true);
                    }}
                  >
                    {t('Inspect')} <span className="sr-only">{order(id).data.external_id}</span>
                  </button>
                </div>
              ))
            )}
            {input.data_kind !== 'SYNTHETIC' && result.unassigned.length > 0 && plan.status !== 'READY' && (
              <label className="check partial-accept">
                <input
                  type="checkbox"
                  checked={accept}
                  onChange={(e) => setAccept(e.target.checked)}
                  disabled={disabled || dirty}
                />
                {t('I accept the unassigned deliveries in this partial plan')}
              </label>
            )}
            {selectedOrder && edit.unassigned_order_ids.includes(selected) && selectedDetails}
          </section>
        </div>
        <div className="planning-map">
          <MapPanel
            style={mapStyle}
            points={points}
            routes={routes}
            selected={selected}
            stale={plan.result_stale || dirty}
            picking={false}
            onPick={() => {}}
            onSelect={choose}
            onRouteSelect={(id) => {
              setVehicle(id);
              setExpanded((ids) => (ids.includes(id) ? ids : [...ids, id]));
            }}
            overlays={() => [legend.current]}
          />
          <div ref={legend} className="route-controls" role="group" aria-label={t('Routes shown on the map')}>
            <div className="route-controls-actions">
              <button className="text-button" disabled={!hidden.length} onClick={() => setHidden([])}>
                {t('Show all')}
              </button>
              <button
                className="text-button"
                disabled={hidden.length === input.vehicles.length}
                onClick={() => setHidden(input.vehicles.map((v) => v.id))}
              >
                {t('Hide all')}
              </button>
            </div>
            {input.vehicles.map((v, i) => (
              <label key={v.id}>
                <input
                  type="checkbox"
                  checked={!hidden.includes(v.id)}
                  onChange={() =>
                    setHidden((ids) => (ids.includes(v.id) ? ids.filter((k) => k !== v.id) : [...ids, v.id]))
                  }
                />
                <span className="route-tag" style={{ background: color(i) }} aria-hidden="true">
                  {routeTag(i)}
                </span>{' '}
                {v.data.fleet_number}
              </label>
            ))}
          </div>
          {selectedOrder && (
            <div className="mobile-stop-card">
              {selectedDetails}
              <button className="secondary" onClick={() => setMobileView('list')}>
                {t('Show selected stop in list')}
              </button>
            </div>
          )}
        </div>
      </div>
      <details className="plan-context support-card">
        <summary>
          <span className="support-icon">
            <Icon name="info" />
          </span>
          <span>
            <strong>{t('Calculation details and routing dataset')}</strong>
            <small>{t('How this result was calculated and what changes affect it')}</small>
          </span>
          <span className="disclosure-chevron">
            <Icon name="chevron" />
          </span>
        </summary>
        <div className="support-content">
          <dl className="calculation-facts">
            <div>
              <dt>{t('Objective')}</dt>
              <dd>{result.objective === 'TRAVEL_TIME' ? t('Travel time') : t('Modeled travel cost')}</dd>
            </div>
            <div>
              <dt>{t('Road preference')}</dt>
              <dd>
                {
                  {
                    TRUCK_STANDARD: t('Balanced · truck standard'),
                    TRUCK_PREFER_NO_TOLLS: t('Prefer no tolls'),
                    TRUCK_PREFER_NO_MOTORWAYS: t('Prefer no motorways'),
                  }[result.routing_policy]
                }
              </dd>
            </div>
            <div>
              <dt>{t('Safety margin')}</dt>
              <dd>
                {input.safety_margin_seconds
                  ? t('{0} min before each window ends', Math.round(input.safety_margin_seconds / 60))
                  : t('None: planned up to the last minute')}
              </dd>
            </div>
            <div>
              <dt>{t('Saved plan')}</dt>
              <dd>
                {t('Version')} {plan.version}
              </dd>
            </div>
            <div>
              <dt>{t('Tolls and all-in total')}</dt>
              <dd>{t('Not calculated')}</dd>
            </div>
          </dl>
          <div className="dataset-note">
            <div>
              <strong>{t('Routing dataset')}</strong>
              <p>{result.dataset}</p>
            </div>
          </div>
          <div className="plan-change-notes">
            <div>
              <strong>{t('After editing')}</strong>
              <p>
                {t(
                  'Changes clear Ready status. Review the recalculated plan, mark it Ready and redistribute the updated routes.',
                )}
              </p>
            </div>
            <div>
              <strong>{t('Starting a new calculation')}</strong>
              <p>{t('Creates a separate plan. Manual edits from this plan are not carried over.')}</p>
            </div>
          </div>
        </div>
      </details>
    </section>
  );
}
