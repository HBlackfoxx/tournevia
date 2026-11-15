import { plural, t, useLanguage } from './i18n';
import { ask } from './confirm';
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type Dispatch,
  type SetStateAction,
} from 'react';
import {
  api,
  ApiError,
  background,
  whenVisible,
  type Depot,
  type Vehicle,
  type Order,
  type Job,
  type Plan,
  type OptimizationRequest,
} from './api';
import { RouteReview } from './RouteReview';
import { Icon } from './Brand';
import { PageActions } from './PageActions';
import { OffDayNotice, availableOn } from './availability';

// What a planner can do about each way a calculation ends without a result.
const failureAdvice: Record<string, string> = {
  CANCELLED: 'Nothing was saved. Start a new calculation when you are ready.',
  JOB_NOT_RUNNING:
    'This calculation was stopped before it finished. Nothing was saved; start a new calculation.',
  JOB_TIMEOUT:
    'The calculation exceeded its time limit. Select fewer deliveries or vehicles, or ask your installation operator to raise JOB_TIMEOUT_SECONDS.',
  WORKER_INTERRUPTED:
    'The calculation worker stopped during this calculation. Start a new calculation; if it happens again, contact your installation operator.',
  ENGINE_FAILED:
    'Correct the records named above (pins, vehicles or assignments) and start a new calculation. If no record is named, check that the routing service is running.',
  DATABASE_ERROR: 'The result could not be saved. Contact your installation operator before retrying.',
};
import { clock, dayMonth, serviceDay } from './format';
import { MapPanel, type MapPoint } from './MapPanel';
import { placePoints } from './Places';
import { SavedPlans } from './SavedPlans';

type Attempt = { key: string; body: OptimizationRequest };
function pending(day: string): Attempt | null {
  try {
    return JSON.parse(sessionStorage.getItem(`vrp-pending:${day}`) ?? 'null') as Attempt | null;
  } catch {
    return null;
  }
}
/** Job list cadence: fast while any calculation for the day is queued or running, slow otherwise. */
export const JOB_POLL_ACTIVE_MS = 2000,
  JOB_POLL_IDLE_MS = 20000;
// The ticking counter lives in its own component so the plan review does not re-render every second,
// and it stays outside live regions so screen readers are not interrupted each second.
function Elapsed({ since }: { since: string }) {
  useLanguage();
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return (
    <span className="elapsed">
      {t('Elapsed time ·')} {Math.max(0, Math.floor((now - Date.parse(since)) / 1000))} s
    </span>
  );
}
export function PlanningPanel({
  day,
  depot,
  places,
  vehicles,
  orders,
  busy,
  mapStyle = 'https://tiles.openfreemap.org/styles/liberty',
  view = 'planning',
  requestedPlanId = null,
  onPlanShown,
  onOpenPlan = () => {},
  onDirtyChange,
  sourcePages,
  onSources,
  onFleet,
  selection,
}: {
  day: string;
  depot: Depot;
  /** Every depot and place of the company, for the map. */
  places?: Depot[];
  vehicles: Vehicle[];
  orders: Order[];
  busy: boolean;
  mapStyle?: string;
  view?: 'planning' | 'saved';
  /** A plan to open (from the URL); reloading or going Back reopens it. */
  requestedPlanId?: string | null;
  /** Reports the plan shown for review, or null while preparing a calculation. */
  onPlanShown?: (id: string | null) => void;
  onOpenPlan?: () => void;
  onDirtyChange?: (dirty: boolean) => void;
  sourcePages?: ReactNode;
  onSources?: () => void;
  onFleet?: () => void;
  selection?: {
    orders: Order[];
    vehicles: Vehicle[];
    setOrders: Dispatch<SetStateAction<Order[]>>;
    setVehicles: Dispatch<SetStateAction<Vehicle[]>>;
  };
}) {
  useLanguage();
  // Keep the selected saved records, including their versions, across source pages.
  // The server rejects stale off-page records rather than silently replacing them.
  const [localVehicles, setLocalVehicles] = useState<Vehicle[]>([]),
    [localOrders, setLocalOrders] = useState<Order[]>([]);
  const selectedVehicles = selection?.vehicles ?? localVehicles,
    setSelectedVehicles = selection?.setVehicles ?? setLocalVehicles,
    selectedOrders = selection?.orders ?? localOrders,
    setSelectedOrders = selection?.setOrders ?? setLocalOrders;
  const [objective, setObjective] = useState<OptimizationRequest['objective']>('TRAVEL_TIME');
  const [policy, setPolicy] = useState<OptimizationRequest['routing_policy']>('TRUCK_STANDARD');
  const [budget, setBudget] = useState(5),
    [sending, setSending] = useState(false),
    [error, setError] = useState('');
  const [attempt, setAttempt] = useState<Attempt | null>(() => pending(day));
  const [jobs, setJobs] = useState<Job[]>([]),
    [chosen, setChosen] = useState(''),
    [pollError, setPollError] = useState('');
  const [plan, setPlan] = useState<Plan | null>(null),
    [planError, setPlanError] = useState(''),
    [reload, setReload] = useState(0);
  const [savedPlanId, setSavedPlanId] = useState<string | null>(requestedPlanId);
  useEffect(() => {
    if (!requestedPlanId) return;
    setSavedPlanId(requestedPlanId);
    setPreparing(false);
  }, [requestedPlanId]);
  const [routeEditing, setRouteEditing] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [preparing, setPreparing] = useState(true);
  const [cancelling, setCancelling] = useState(false),
    [cancelError, setCancelError] = useState('');
  // Screen readers hear when a calculation they are waiting for finishes; the view changes silently otherwise.
  const [announcement, setAnnouncement] = useState<'' | 'SUCCEEDED' | 'FAILED'>('');
  const watched = useRef<{ id: string; status: string } | null>(null);
  const lastPlan = useRef<string | null>(null),
    planRead = useRef('');
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let alive = true,
      busyJobs = false,
      cancel = () => {};
    async function poll() {
      try {
        const rows = await api<Job[]>(`jobs?service_date=${day}&limit=50`, {
          signal: controller.signal,
          headers: background,
        });
        busyJobs = rows.some((j) => j.status === 'QUEUED' || j.status === 'RUNNING');
        if (alive) {
          setJobs((current) => (JSON.stringify(current) === JSON.stringify(rows) ? current : rows));
          setChosen((current) => current || rows[0]?.id || '');
          setPollError('');
        }
      } catch (e) {
        if (alive) setPollError(e instanceof Error ? e.message : t('Could not read jobs.'));
      } finally {
        if (alive) cancel = whenVisible(() => void poll(), busyJobs ? JOB_POLL_ACTIVE_MS : JOB_POLL_IDLE_MS);
      }
    }
    void poll();
    return () => {
      alive = false;
      controller.abort();
      cancel();
    };
  }, [day, reload]);
  const job = savedPlanId
    ? jobs.find((j) => j.plan_id === savedPlanId || j.target_plan_id === savedPlanId)
    : (jobs.find((j) => j.id === chosen) ?? jobs[0]);
  const planId = savedPlanId ?? job?.plan_id ?? job?.target_plan_id;
  useEffect(() => onPlanShown?.(preparing ? null : (planId ?? null)), [planId, preparing, onPlanShown]);
  useEffect(() => {
    const before = watched.current;
    watched.current = job ? { id: job.id, status: job.status } : null;
    // Another calculation shown: what was said about the previous one no longer applies.
    if (before && before.id !== job?.id) setAnnouncement('');
    if (!job || before?.id !== job.id || before.status === job.status) return;
    if (before.status !== 'QUEUED' && before.status !== 'RUNNING') return;
    // Kept as an outcome and worded when shown, so it follows a later change of language.
    if (job.status === 'SUCCEEDED') setAnnouncement('SUCCEEDED');
    else if (job.status === 'FAILED') setAnnouncement('FAILED');
  }, [job]);
  // "Ready for review" is no longer true once the plan is Ready.
  useEffect(() => {
    if (plan?.status === 'READY') setAnnouncement('');
  }, [plan?.status]);
  // Only a different plan clears the review; refreshes keep RouteReview mounted so unsaved moves survive.
  useEffect(() => {
    setPlan(null);
    setPlanError('');
  }, [planId]);
  useEffect(() => {
    if (!planId) return;
    const controller = new AbortController();
    let alive = true,
      cancel = () => {};
    // Opening a plan or an explicit reload is user activity; status-driven and follow-up reads are background polls.
    let quiet = planRead.current === `${planId}:${reload}`;
    planRead.current = `${planId}:${reload}`;
    async function read() {
      try {
        const p = await api<Plan>(`plans/${planId}`, {
          signal: controller.signal,
          ...(quiet ? { headers: background } : {}),
        });
        quiet = true;
        if (alive) {
          setPlan(p);
          if (lastPlan.current !== p.id) {
            lastPlan.current = p.id;
            setPreparing(false);
          }
          setPlanError('');
          if (p.active_job_id) cancel = whenVisible(() => void read(), 1500);
        }
      } catch (e) {
        quiet = true;
        if (alive) {
          setPlanError(e instanceof Error ? e.message : t('Could not read draft.'));
          if (!(e instanceof ApiError && e.status >= 400 && e.status < 500))
            cancel = whenVisible(() => void read(), 2000);
        }
      }
    }
    // An interrupted first read (or StrictMode's effect re-run) is still the user's load.
    void read();
    return () => {
      alive = false;
      if (!quiet) planRead.current = '';
      controller.abort();
      cancel();
    };
  }, [planId, reload, job?.status]);
  useEffect(() => {
    if (job) setPreparing(false);
  }, [job?.id]);
  async function cancelCalculation(id: string) {
    setCancelling(true);
    setCancelError('');
    try {
      const updated = await api<Job>(`jobs/${id}/cancel`, { method: 'POST' });
      if (active.current) setJobs((rows) => rows.map((j) => (j.id === updated.id ? updated : j)));
    } catch (e) {
      if (active.current)
        setCancelError(e instanceof Error ? e.message : t('Could not cancel the calculation.'));
    } finally {
      if (active.current) setCancelling(false);
    }
  }
  function added(created: Job) {
    setPreparing(false);
    setSavedPlanId(created.target_plan_id ?? null);
    setChosen(created.id);
    setJobs((rows) => [created, ...rows.filter((j) => j.id !== created.id)]);
    setReload((n) => n + 1);
  }
  useEffect(() => {
    if (busy) return;
    setSelectedVehicles((rows) => rows.map((row) => vehicles.find((v) => v.id === row.id) ?? row));
    setSelectedOrders((rows) => rows.map((row) => orders.find((o) => o.id === row.id) ?? row));
  }, [vehicles, orders, busy]);
  const mapPoints = useMemo<MapPoint[]>(
    () => [
      ...placePoints(places?.length ? places : [depot]),
      ...orders
        .filter((o) => o.data.location)
        .map((o) => ({
          id: o.id,
          label: o.data.external_id,
          location: o.data.location!,
          color: o.data.location?.reviewed ? '#7297cf' : '#ba821d',
        })),
    ],
    [depot, places, orders],
  );
  const vehicleIds = selectedVehicles.map((v) => v.id),
    orderIds = selectedOrders.map((o) => o.id);
  const eligibleVehicles = vehicles.filter((v) => v.data.active);
  const eligibleOrders = orders.filter((o) => o.data.active && o.data.location?.reviewed);
  const profiles = new Set(selectedVehicles.map((v) => JSON.stringify(v.data.routing))).size;
  // The main depot's point matters only when a selected vehicle starts or ends its day there.
  const main = depot.data.code;
  const usesMain = selectedVehicles.some((v) =>
    [v.data.start ?? main, v.data.end ?? v.data.start ?? main].includes(main),
  );
  const valid =
    !busy &&
    (!usesMain || depot.data.location.reviewed) &&
    selectedVehicles.length > 0 &&
    selectedOrders.length > 0 &&
    selectedVehicles.length <= 25 &&
    selectedOrders.length <= 250 &&
    profiles <= 3 &&
    selectedVehicles.every((v) => v.data.active && availableOn(v, day)) &&
    selectedOrders.every((o) => o.data.active && o.data.location?.reviewed);
  async function submit() {
    if (
      !attempt &&
      routeEditing &&
      !(await ask(t('Discard unsaved route moves?'), {
        detail: t('A new calculation does not keep unsaved route moves.'),
        confirm: t('Discard and calculate'),
      }))
    )
      return;
    const next = attempt ?? {
      key: crypto.randomUUID(),
      body: {
        service_date: day,
        vehicle_ids: selectedVehicles.map((v) => v.id),
        order_ids: selectedOrders.map((o) => o.id),
        routing_policy: policy,
        objective,
        solver_time_limit_seconds: budget,
        source_versions: Object.fromEntries(
          [depot, ...selectedVehicles, ...selectedOrders].map((r) => [r.id, r.version]),
        ),
      },
    };
    setSending(true);
    setError('');
    setAttempt(next);
    // Persist before sending so a lost response or page reload can reuse the exact request.
    try {
      sessionStorage.setItem(`vrp-pending:${day}`, JSON.stringify(next));
    } catch {
      /* In-memory retry still works. */
    }
    try {
      const created = await api<Job>('optimization-jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': next.key },
        body: JSON.stringify(next.body),
      });
      try {
        sessionStorage.removeItem(`vrp-pending:${day}`);
      } catch {
        /* Storage may be disabled. */
      }
      if (active.current) {
        setAttempt(null);
        added(created);
      }
    } catch (e) {
      if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
        try {
          sessionStorage.removeItem(`vrp-pending:${day}`);
        } catch {
          /* optional storage */
        }
        if (active.current) setAttempt(null);
      }
      if (active.current)
        setError(
          e instanceof ApiError && e.identifiers.length
            ? // Name the records the refusal concerns, e.g. the two places too far apart.
              `${e.message} ${t('Concerned: {0}', e.identifiers.map((id) => (id === 'depot' ? t('Depot') : id)).join(', '))}`
            : e instanceof Error
              ? e.message
              : t('Submission failed. Retry the same request.'),
        );
    } finally {
      if (active.current) setSending(false);
    }
  }
  return (
    <section className="planning-panel" aria-label={t('Calculate routes')}>
      <p className="sr-only" role="status">
        {announcement === 'SUCCEEDED'
          ? t('Calculation completed. The draft is ready for review.')
          : announcement === 'FAILED'
            ? t('Calculation could not finish.')
            : ''}
      </p>
      <div hidden={view === 'saved'}>
        <div className="planning-subhead">
          <span className="planning-context">
            <span className="plan-date">{t('Daily plan · {0}', serviceDay(day))}</span>
            <span
              className={`badge ${routeEditing || plan?.result.unassigned.length ? 'attention' : plan?.validation === 'FAIL' ? 'failed' : plan?.status === 'READY' ? 'ready' : ''}`}
            >
              {preparing
                ? valid
                  ? t('Inputs complete')
                  : selectedOrders.some((o) => !o.data.location?.reviewed) ||
                      selectedVehicles.some((v) => !availableOn(v, day))
                    ? t('Inputs need attention')
                    : t('Prepare calculation')
                : routeEditing
                  ? t('Unsaved route edits')
                  : job?.status === 'QUEUED'
                    ? t('Queued')
                    : job?.status === 'RUNNING'
                      ? t('Calculating')
                      : job?.status === 'FAILED'
                        ? t('Calculation failed')
                        : plan?.validation === 'FAIL'
                          ? t('Invalid result')
                          : plan?.status === 'READY'
                            ? t('Ready')
                            : plan?.result.unassigned.length
                              ? t('Draft · partial result')
                              : t('Draft')}
            </span>
            <span className="plan-timezone">{t('Paris time')}</span>
          </span>
          {preparing ? (
            // The calculation is submitted from the form below; the header only offers the
            // separate step that blocks it, reviewing selected delivery locations.
            selectedOrders.some((o) => !o.data.location?.reviewed) &&
            onSources && (
              <PageActions>
                <button onClick={() => onSources()}>{t('Review locations')}</button>
              </PageActions>
            )
          ) : (
            <PageActions>
              <button
                hidden={routeEditing}
                className="secondary"
                disabled={job?.status === 'QUEUED' || job?.status === 'RUNNING'}
                onClick={() => setPreparing(true)}
              >
                {job?.status === 'QUEUED' || job?.status === 'RUNNING'
                  ? t('Calculation in progress')
                  : t('New calculation')}
              </button>
            </PageActions>
          )}
        </div>
        <div hidden={!preparing} className="prepare-layout">
          <div className="preparation">
            <h2>{t('Prepare calculation')}</h2>
            <p>{t('Selections stay with you as you move between pages.')}</p>
            <div className="selection-summary">
              <div>
                <strong>{t('Deliveries')}</strong>
                <p>{plural(selectedOrders.length, '{0} delivery selected', '{0} deliveries selected')}</p>
              </div>
              <button
                className="secondary"
                onClick={() => (onSources ? onSources() : setSelecting((v) => !v))}
              >
                {t('Select deliveries…')}
              </button>
            </div>
            <div className="selection-summary">
              <div>
                <strong>{t('Vehicles')}</strong>
                <p>{plural(selectedVehicles.length, '{0} vehicle selected', '{0} vehicles selected')}</p>
              </div>
              <button className="secondary" onClick={() => (onFleet ? onFleet() : setSelecting((v) => !v))}>
                {t('Select vehicles…')}
              </button>
            </div>
            <OffDayNotice
              vehicles={selectedVehicles.filter((v) => v.data.active && !availableOn(v, day))}
              day={day}
              selected
              onFleet={onFleet}
            />
            {/* Vehicles leaving from different places: a delivery that names no loading place can go
                on any of them, whichever place it leaves from. Said, not blocked. */}
            {new Set(selectedVehicles.map((v) => v.data.start ?? depot.data.code)).size > 1 &&
              selectedOrders.some((o) => !o.data.ships_from) && (
                <p className="info loading-note">
                  {plural(
                    selectedOrders.filter((o) => !o.data.ships_from).length,
                    '{0} selected delivery names no loading place: any selected vehicle may carry it, whichever depot or place it leaves from. Set its loading place in Deliveries if it is only stocked at one depot.',
                    '{0} selected deliveries name no loading place: any selected vehicle may carry them, whichever depot or place it leaves from. Set their loading place in Deliveries if they are only stocked at one depot.',
                  )}
                </p>
              )}
            {selectedOrders.some((o) => !o.data.location?.reviewed) ? (
              <div className="notice">
                <strong>
                  {selectedOrders.filter((o) => !o.data.location?.reviewed).length}{' '}
                  {t('deliveries need attention')}
                </strong>
                <p>
                  {selectedOrders.filter((o) => !o.data.location).length} {t('locations missing ·')}{' '}
                  {selectedOrders.filter((o) => o.data.location && !o.data.location.reviewed).length}{' '}
                  {t('entrance points need review.')}
                </p>
                {onSources && (
                  <button className="text-button" onClick={onSources}>
                    {t('Review delivery inputs and locations')}
                  </button>
                )}
              </div>
            ) : valid ? (
              <div className="success">
                <strong>{t('Inputs checked')}</strong>
                <p>
                  {t(
                    'Selected entrance locations are reviewed. The server checks all inputs again before calculation.',
                  )}
                </p>
              </div>
            ) : (
              <p className="info">
                {t('Select active vehicles and reviewed deliveries to prepare the calculation.')}
              </p>
            )}
            <details
              className={`source-picker ${sourcePages ? 'has-pages' : ''}`}
              open={selecting}
              onToggle={(e) => setSelecting(e.currentTarget.open)}
            >
              <summary>{t('Select records on this page')}</summary>
              <fieldset disabled={sending || !!attempt || busy}>
                <legend>{t('Selected sources')}</legend>
                <div className="planning-columns">
                  <div>
                    <h3>{t('Vehicles')}</h3>
                    <div className="actions">
                      <button
                        className="secondary"
                        disabled={
                          !eligibleVehicles.length ||
                          new Set([...vehicleIds, ...eligibleVehicles.map((v) => v.id)]).size > 25
                        }
                        onClick={() =>
                          setSelectedVehicles((rows) => [
                            ...rows.filter((v) => !eligibleVehicles.some((p) => p.id === v.id)),
                            ...eligibleVehicles,
                          ])
                        }
                      >
                        {t('Select active vehicles on this page')}
                      </button>
                      <button
                        className="secondary"
                        disabled={!selectedVehicles.length}
                        onClick={() => setSelectedVehicles([])}
                      >
                        {t('Clear vehicle selection')}
                      </button>
                    </div>
                    <div className="source-options">
                      {vehicles.map((v) => (
                        <label className="check-row" key={v.id}>
                          <input
                            type="checkbox"
                            checked={vehicleIds.includes(v.id)}
                            disabled={
                              !vehicleIds.includes(v.id) && (!v.data.active || selectedVehicles.length >= 25)
                            }
                            onChange={() =>
                              setSelectedVehicles((rows) =>
                                rows.some((r) => r.id === v.id)
                                  ? rows.filter((r) => r.id !== v.id)
                                  : [...rows, v],
                              )
                            }
                          />
                          {v.data.fleet_number}
                          {!v.data.active ? t(' · inactive') : ''}
                        </label>
                      ))}
                    </div>
                  </div>
                  <div>
                    <h3>{t('Deliveries')}</h3>
                    <div className="actions">
                      <button
                        className="secondary"
                        disabled={
                          !eligibleOrders.length ||
                          new Set([...orderIds, ...eligibleOrders.map((o) => o.id)]).size > 250
                        }
                        onClick={() =>
                          setSelectedOrders((rows) => [
                            ...rows.filter((o) => !eligibleOrders.some((p) => p.id === o.id)),
                            ...eligibleOrders,
                          ])
                        }
                      >
                        {t('Select reviewed deliveries on this page')}
                      </button>
                      <button
                        className="secondary"
                        disabled={!selectedOrders.length}
                        onClick={() => setSelectedOrders([])}
                      >
                        {t('Clear delivery selection')}
                      </button>
                    </div>
                    <div className="source-options">
                      {orders.map((o) => (
                        <label className="check-row" key={o.id}>
                          <input
                            type="checkbox"
                            checked={orderIds.includes(o.id)}
                            disabled={
                              !orderIds.includes(o.id) &&
                              (!o.data.active || !o.data.location?.reviewed || selectedOrders.length >= 250)
                            }
                            onChange={() =>
                              setSelectedOrders((rows) =>
                                rows.some((r) => r.id === o.id)
                                  ? rows.filter((r) => r.id !== o.id)
                                  : [...rows, o],
                              )
                            }
                          />
                          {o.data.external_id}
                          {!o.data.active
                            ? t(' · inactive')
                            : !o.data.location?.reviewed
                              ? t(' · needs point review')
                              : ''}
                        </label>
                      ))}
                    </div>
                  </div>
                </div>
                {sourcePages}
                <p role="status">
                  {selectedVehicles.length} {t('/ 25 vehicles ·')} {selectedOrders.length}{' '}
                  {t('/ 250 deliveries selected across pages ·')} {profiles} {t('/ 3 truck profiles')}
                </p>
                <p className="footnote">
                  {t(
                    'Page changes keep your selection. Changing the planning date clears it. Source changes are checked again when you submit.',
                  )}
                </p>
              </fieldset>
            </details>
            <fieldset disabled={sending || !!attempt || busy}>
              <label>
                {t('Objective')}
                <select
                  value={objective}
                  onChange={(e) => setObjective(e.target.value as OptimizationRequest['objective'])}
                >
                  <option value="TRAVEL_TIME">{t('Travel time')}</option>
                  <option value="MODELED_TRAVEL_COST">{t('Modeled travel cost')}</option>
                </select>
              </label>
              <label>
                {t('Road preference')}
                <select
                  value={policy}
                  onChange={(e) => setPolicy(e.target.value as OptimizationRequest['routing_policy'])}
                >
                  <option value="TRUCK_STANDARD">{t('Balanced · truck standard')}</option>
                  <option value="TRUCK_PREFER_NO_TOLLS">{t('Prefer no tolls')}</option>
                  <option value="TRUCK_PREFER_NO_MOTORWAYS">{t('Prefer no motorways')}</option>
                </select>
              </label>
              <p className="help">{t('A road preference is not a guaranteed ban.')}</p>
              <details className="advanced">
                <summary>{t('Advanced calculation settings')}</summary>
                <label>
                  {t('Solver budget (seconds)')}
                  <input
                    type="number"
                    min={1}
                    max={60}
                    step={1}
                    value={budget}
                    onChange={(e) => setBudget(Number(e.target.value))}
                  />
                </label>
                <p className="help">
                  {t(
                    'Limits: 250 deliveries, 25 vehicles and 3 truck profiles. Travel time minimizes driving after service priorities. Modeled travel cost uses fixed, distance and driving-time rates. Displayed operating costs also include waiting and service; tolls are excluded.',
                  )}
                </p>
              </details>
            </fieldset>
            {profiles > 3 && (
              <p role="alert" className="error">
                {t('Select vehicles with at most three different truck profiles.')}
              </p>
            )}
            {selectedVehicles.some((v) => !v.data.active) || selectedOrders.some((o) => !o.data.active) ? (
              <p role="alert" className="error">
                {t('Some selected records are inactive. Update your delivery and vehicle selections.')}
              </p>
            ) : null}
            {!depot.data.location.reviewed && (usesMain || selectedVehicles.length === 0) && (
              <p className="error">{t('Review the depot point before calculating.')}</p>
            )}
            {attempt && (
              <p role="status">
                {t(
                  'A submission is pending confirmation. Retry sends the same saved selection and key, including after a page reload.',
                )}
              </p>
            )}
            {error && (
              <p role="alert" className="error">
                {error}
              </p>
            )}
            {plan && (
              <p className="footnote">
                {t('A new calculation creates a separate plan; it does not keep manual route edits.')}
              </p>
            )}
            <button
              disabled={
                sending || (!attempt && (!valid || !Number.isInteger(budget) || budget < 1 || budget > 60))
              }
              onClick={() => void submit()}
            >
              {sending ? t('Submitting…') : attempt ? t('Retry submission') : t('Start new calculation')}
            </button>
            <small className="help">{t('A successful calculation creates a draft for your review.')}</small>
          </div>
          <MapPanel
            style={mapStyle}
            points={mapPoints}
            picking={false}
            onPick={() => {}}
            onSelect={() => onSources?.()}
          />
        </div>
        {!preparing && job && (!plan || job.status !== 'SUCCEEDED') && (
          <section className={`calculation-state ${job.status.toLowerCase()}`}>
            <div className="calculation-symbol">
              <Icon
                name={job.status === 'FAILED' ? 'alert' : job.status === 'QUEUED' ? 'clock' : 'route'}
                size={20}
              />
            </div>
            <h2>
              {job.status === 'QUEUED'
                ? t('Waiting for worker')
                : job.status === 'RUNNING'
                  ? t('Calculating routes')
                  : job.status === 'SUCCEEDED'
                    ? t('Calculation completed')
                    : job.error?.code === 'CANCELLED'
                      ? t('Calculation cancelled')
                      : t('Calculation could not finish')}
            </h2>
            <p role="status">
              {(job.error && t(job.error.message)) ??
                (job.status === 'QUEUED'
                  ? t('The calculation is queued. It will start when a worker is available.')
                  : job.status === 'RUNNING'
                    ? t(
                        'The worker is calculating routes for the selected inputs. Review the result before marking it Ready.',
                      )
                    : t('Loading the saved result…'))}
            </p>
            {(job.status === 'QUEUED' || job.status === 'RUNNING') && (
              <>
                <Elapsed key={job.id} since={job.started_at ?? job.created_at} />
                <div className="stage-list">
                  <div>
                    <Icon name="check" /> {t('Calculation submitted')}
                    <span>{t('Complete')}</span>
                  </div>
                  <div className={job.status === 'QUEUED' ? 'current' : ''}>
                    <Icon name={job.status === 'QUEUED' ? 'clock' : 'check'} /> {t('Waiting for worker')}
                    <span>{job.status === 'QUEUED' ? t('Queued') : t('Complete')}</span>
                  </div>
                  <div className={job.status === 'RUNNING' ? 'current' : ''}>
                    <Icon name="clock" />{' '}
                    {
                      {
                        QUEUED: t('Calculating routes'),
                        PRECHECK: t('Checking inputs'),
                        MATRIX: t('Calculating road travel times'),
                        SOLVE: t('Finding an assignment'),
                        ROUTE: t('Checking road routes'),
                        VALIDATE: t('Validating constraints'),
                        SAVE: t('Saving result'),
                        DONE: t('Saved'),
                        FAILED: t('Failed'),
                      }[job.stage]
                    }
                    <span>{job.status === 'RUNNING' ? t('Current stage') : t('Not started')}</span>
                  </div>
                </div>
                <small>{t('No completion estimate is available.')}</small>
                <button
                  className="secondary"
                  disabled={cancelling}
                  onClick={() => void cancelCalculation(job.id)}
                >
                  {cancelling ? t('Cancelling…') : t('Cancel calculation')}
                </button>
                {cancelError && (
                  <p role="alert" className="error">
                    {cancelError}
                  </p>
                )}
              </>
            )}
            {job.error && (
              <>
                <div className="info">
                  <strong>{t('Your inputs are safe')}</strong>
                  <p>
                    {t(
                      'Your saved deliveries and vehicles are unchanged. Any previously saved plan is preserved.',
                    )}
                  </p>
                </div>
                <div className="failure-next">
                  <h3>{t('What to do next')}</h3>
                  <p>
                    {t(
                      failureAdvice[job.error.code] ??
                        'Review the current inputs and retry the calculation. If the service remains unavailable, contact your installation operator.',
                    )}
                  </p>
                  <small>{job.error.code}</small>
                  <button onClick={() => setPreparing(true)}>{t('Review inputs and retry')}</button>
                  {job.error.code === 'ENGINE_FAILED' &&
                    /\bdelivery\b/.test(job.error.message) &&
                    onSources && (
                      <button className="secondary" onClick={onSources}>
                        {t('Review deliveries')}
                      </button>
                    )}
                </div>
                {job.error.details?.length ? (
                  <details>
                    <summary>{t('Calculation diagnostics')}</summary>
                    <pre>{JSON.stringify(job.error.details, null, 2)}</pre>
                  </details>
                ) : null}
              </>
            )}
            <div className="calculation-selection">
              <span>
                <strong>{selectedOrders.length || '—'}</strong>
                {t('Selected deliveries')}
              </span>
              <span>
                <strong>{selectedVehicles.length || '—'}</strong>
                {t('Selected vehicles')}
              </span>
              <span>
                <strong>{serviceDay(day)}</strong>
                {t('Planning date')}
              </span>
            </div>
          </section>
        )}
      </div>
      <div hidden={view !== 'saved'}>
        <SavedPlans
          key={day}
          day={day}
          refresh={`${reload}:${jobs.map((j) => `${j.id}:${j.status}`).join(',')}:${plan?.version ?? ''}`}
          selected={planId ?? undefined}
          onSelect={async (id) => {
            if (
              id !== planId &&
              routeEditing &&
              !(await ask(t('Discard unsaved route moves and open another plan?'), {
                confirm: t('Discard moves'),
              }))
            )
              return;
            setRouteEditing(false);
            onDirtyChange?.(false);
            setSavedPlanId(id);
            setPreparing(false);
            onOpenPlan();
          }}
        />
      </div>
      <div hidden={view === 'saved'}>
        {planError && (
          <p role="alert">
            {planError} <button onClick={() => setReload((n) => n + 1)}>{t('Reload plan')}</button>
          </p>
        )}
        {!preparing && plan && plan.id === planId && (
          <RouteReview
            key={plan.id}
            plan={plan}
            mapStyle={mapStyle}
            onPrepare={() => setPreparing(true)}
            onJob={added}
            onRefresh={() => setReload((n) => n + 1)}
            onDirtyChange={(value) => {
              setRouteEditing(value);
              onDirtyChange?.(value);
            }}
          />
        )}
        {pollError && (
          <p role="alert" className="error">
            {t('Calculation status could not be refreshed:')} {pollError} {t('Retrying automatically.')}
          </p>
        )}
        <details className="recent-jobs support-card">
          <summary>
            <span className="support-icon">
              <Icon name="clock" />
            </span>
            <span>
              <strong>{t('Recent calculations')}</strong>
              <small>
                {serviceDay(day)} · {t('Paris time')} · {jobs.length}{' '}
                {jobs.length === 1 ? t('calculation') : t('calculations')}
              </small>
            </span>
            <span className="disclosure-chevron">
              <Icon name="chevron" />
            </span>
          </summary>
          <div className="support-content">
            {!jobs.length ? (
              <p className="recent-empty">{t('No saved calculations for this day.')}</p>
            ) : (
              <>
                <p className="recent-help">
                  {t(
                    'Open a run to inspect its result. Completed calculations still need review before they are Ready.',
                  )}
                </p>
                <ul className="calculation-history">
                  {jobs.map((j) => {
                    const time = clock(j.created_at, true),
                      status = {
                        QUEUED: t('Queued'),
                        RUNNING: t('Calculating'),
                        SUCCEEDED: t('Completed'),
                        FAILED: t('Failed'),
                      }[j.status];
                    return (
                      <li key={j.id}>
                        <button
                          className="history-run"
                          aria-label={t('Open the calculation of {0} · {1}', time, status)}
                          title={j.id}
                          aria-pressed={!savedPlanId && job?.id === j.id}
                          onClick={async () => {
                            if (
                              routeEditing &&
                              !(await ask(t('Discard unsaved route moves and open another calculation?'), {
                                confirm: t('Discard moves'),
                              }))
                            )
                              return;
                            setRouteEditing(false);
                            onDirtyChange?.(false);
                            setSavedPlanId(null);
                            setChosen(j.id);
                            setPreparing(false);
                          }}
                        >
                          <span className="run-time">
                            <strong>{time}</strong>
                            <small>
                              {dayMonth(j.created_at)} ·{' '}
                              {j.kind === 'OPTIMIZE' ? t('New calculation') : t('Route recalculation')}
                            </small>
                          </span>
                          <span className={`badge run-status ${j.status.toLowerCase()}`}>{status}</span>
                          <span className="run-open">
                            {!savedPlanId && job?.id === j.id ? t('Viewing') : t('Open →')}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </>
            )}
          </div>
        </details>
      </div>
    </section>
  );
}
