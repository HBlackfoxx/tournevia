import { plural, t, useLanguage } from './i18n';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  api,
  deleteOrders,
  setOrdersActive,
  type Configuration,
  type Depot,
  type Order,
  type OrderSummary,
  type Vehicle,
  type VehicleSummary,
  type SessionRecord,
} from './api';
import { ImportPanel } from './ImportPanel';
import { MapPanel, type MapPoint } from './MapPanel';
import { SourceEditor, type Selection } from './SourceEditor';
import { PlanningPanel } from './PlanningPanel';
import { AccountPanel, Users } from './AccountPanel';
import { LanguageSettings, ThemeToggle } from './LanguageSettings';
import { Brand, Icon, NavIcon } from './Brand';
import { SystemStatusBanner } from './SystemStatus';
import { cubicMetres, euros, kilograms, metres, serviceDay, tonnes, truckSize, truckWeights } from './format';
import { equipmentList, equipmentName } from './equipment';
import { PageActionsSlot } from './PageActions';
import { OffDayNotice, availableOn, weekSummary } from './availability';
import { addedMessage, ExampleSettings } from './ExampleData';
import { SetupWizard } from './SetupWizard';
import { AddressLookupSettings } from './addressLookup';
import { CalculationSettings } from './CalculationSettings';
import { NumberingSettings } from './Numbering';
import { Customers } from './Customers';
import { ask, ConfirmHost } from './confirm';
import { PlacesSettings, placeLabel, placePoints } from './Places';

/** A summary count for a list filter; the summaries name their counts after the filters. */
function countOf(summary: OrderSummary | VehicleSummary | null, filter: string): number | null {
  if (!summary) return null;
  const value = (summary as Record<string, unknown>)[filter === 'all' ? 'total' : filter];
  return typeof value === 'number' ? value : null;
}

type Area = 'planning' | 'orders' | 'customers' | 'vehicles' | 'saved' | 'settings';
type Route = { area: Area; date?: string; plan?: string };
const AREAS: Area[] = ['planning', 'orders', 'customers', 'vehicles', 'saved', 'settings'];
/** The workspace location kept in the URL fragment, e.g. #/planning?date=2026-09-23&plan=<id>. */
export function readRoute(hash: string): Route | null {
  const match = /^#\/([a-z]+)(?:\?(.*))?$/.exec(hash);
  if (!match || !AREAS.includes(match[1] as Area)) return null;
  const params = new URLSearchParams(match[2] ?? '');
  const date = params.get('date') ?? '',
    plan = params.get('plan') ?? '';
  return {
    area: match[1] as Area,
    date: /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : undefined,
    plan: /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(plan) ? plan : undefined,
  };
}
export function routeHash({ area, date, plan }: Route): string {
  const params = new URLSearchParams(date ? { date } : {});
  if (plan) params.set('plan', plan);
  return `#/${area}${params.size ? `?${params}` : ''}`;
}
const areas: { id: Area; name: string }[] = [
  { id: 'planning', name: 'Planning' },
  { id: 'orders', name: 'Deliveries' },
  { id: 'customers', name: 'Customers' },
  { id: 'vehicles', name: 'Fleet' },
  { id: 'saved', name: 'Saved plans' },
  { id: 'settings', name: 'Settings' },
];
export function Workspace({
  depot: initialDepot,
  configuration,
  role = 'admin',
  session,
  connection = 'ready',
  onSignedOut = () => {},
  onReconnect = () => {},
  welcome = false,
}: {
  depot: Depot;
  configuration: Configuration;
  role?: 'admin' | 'planner';
  session?: SessionRecord;
  connection?: 'ready' | 'checking' | 'failed';
  onSignedOut?: () => void;
  onReconnect?: () => void;
  /** Just set up: the administrator is guided through vehicles, delivery numbers and deliveries. */
  welcome?: boolean;
}) {
  useLanguage();
  const [initialRoute] = useState(() => readRoute(window.location.hash));
  const [day, setDay] = useState(
    () =>
      initialRoute?.date ?? new Intl.DateTimeFormat('en-CA', { timeZone: 'Europe/Paris' }).format(new Date()),
  );
  // The plan the URL asks the planning view to open, and the plan it reports showing.
  const [requestedPlan, setRequestedPlan] = useState(initialRoute?.plan ?? null),
    [shownPlan, setShownPlan] = useState<string | null>(null);
  const [actionsSlot, setActionsSlot] = useState<HTMLDivElement | null>(null);
  const [area, setArea] = useState<Area>(initialRoute?.area ?? 'planning'),
    [mobile, setMobile] = useState(false),
    [settings, setSettings] = useState<
      'depot' | 'numbers' | 'addresses' | 'calculation' | 'examples' | 'users' | 'personal'
    >(role === 'admin' ? 'depot' : 'personal');
  const [depot, setDepot] = useState(initialDepot),
    [places, setPlaces] = useState<Depot[]>([initialDepot]),
    [vehicles, setVehicles] = useState<Vehicle[]>([]),
    [orders, setOrders] = useState<Order[]>([]);
  const [selection, setSelection] = useState<Selection | null>(null),
    [editorDirty, setEditorDirty] = useState(false),
    [routeDirty, setRouteDirty] = useState(false),
    [reload, setReload] = useState(0);
  const [offset, setOffset] = useState(0),
    [vehicleOffset, setVehicleOffset] = useState(0),
    [busy, setBusy] = useState(true),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [working, setWorking] = useState(false);
  const guided = role === 'admin' && configuration.data_kind !== 'SYNTHETIC';
  const [wizard, setWizard] = useState(welcome && guided);
  const [selectedOrders, setSelectedOrders] = useState<Order[]>([]),
    [selectedVehicles, setSelectedVehicles] = useState<Vehicle[]>([]),
    [showChecklist, setShowChecklist] = useState(true);
  const [query, setQuery] = useState(''),
    [search, setSearch] = useState(''),
    [filter, setFilter] = useState('all'),
    [importing, setImporting] = useState(false);
  const [orderSummary, setOrderSummary] = useState<OrderSummary | null>(null),
    [vehicleSummary, setVehicleSummary] = useState<VehicleSummary | null>(null),
    [offDay, setOffDay] = useState<Vehicle[]>([]);
  // Search and filters run on the server across the whole day (or fleet), one page at a time.
  useEffect(() => {
    const timer = setTimeout(() => {
      setSearch(query.trim());
      setOffset(0);
      setVehicleOffset(0);
    }, 250);
    return () => clearTimeout(timer);
  }, [query]);
  const searchParam = search ? `&q=${encodeURIComponent(search)}` : '',
    filterParam = filter === 'all' ? '' : `&status=${filter}`,
    orderQuery = area === 'orders' ? searchParam : '',
    vehicleQuery = area === 'vehicles' ? searchParam : '',
    orderListing = area === 'orders' ? filterParam + searchParam : '',
    vehicleListing = area === 'vehicles' ? filterParam + searchParam : '',
    fleetShown = area === 'vehicles';
  useEffect(() => {
    const controller = new AbortController(),
      signal = controller.signal;
    setBusy(true);
    setError('');
    void Promise.all([
      api<Depot>('depot', { signal }),
      api<Depot[]>('depots', { signal }),
      api<Vehicle[]>(`vehicles?limit=50&offset=${vehicleOffset}${vehicleListing}`, { signal }),
      api<Order[]>(`orders?service_date=${day}&limit=50&offset=${offset}${orderListing}`, { signal }),
      api<OrderSummary>(`orders/summary?service_date=${day}${orderQuery}`, { signal }),
      api<VehicleSummary>(`vehicles/summary?service_date=${day}${vehicleQuery}`, { signal }),
      fleetShown
        ? api<Vehicle[]>(`vehicles?status=off_day&service_date=${day}&limit=250`, { signal })
        : Promise.resolve([]),
    ])
      .then(([d, all, v, o, os, vs, off]) => {
        if (!controller.signal.aborted) {
          setDepot(d);
          // The main depot alone until the list arrives (or when a server predates depots and places).
          setPlaces(Array.isArray(all) && all.length ? all : [d]);
          setVehicles(v);
          setOrders(o);
          // Selected records shown again are kept at their current version.
          const fresher = <T extends { id: string }>(chosen: T[], shown: T[]) =>
            chosen.some((c) => shown.some((r) => r.id === c.id))
              ? chosen.map((c) => shown.find((r) => r.id === c.id) ?? c)
              : chosen;
          setSelectedOrders((chosen) => fresher(chosen, o));
          setSelectedVehicles((chosen) => fresher(chosen, v));
          setOrderSummary(os);
          setVehicleSummary(vs);
          setOffDay(off);
        }
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => controller.abort();
  }, [
    day,
    offset,
    vehicleOffset,
    reload,
    orderListing,
    vehicleListing,
    orderQuery,
    vehicleQuery,
    fleetShown,
  ]);
  const points = useMemo<MapPoint[]>(
    () => [
      ...placePoints(places.some((p) => p.id === depot.id) ? places : [depot, ...places]),
      ...orders
        .filter((o) => o.data.location)
        .map((o) => ({ id: o.id, label: o.data.external_id, location: o.data.location! })),
    ],
    [depot, places, orders],
  );
  /** Runs `go` at once when nothing unsaved would be lost; otherwise only once the dispatcher
   * agrees to discard the open record's edits and, when `moves` asks about them, the plan's
   * unsaved route moves. Resolves whether `go` ran. */
  function leave(go: () => void, moves?: string): Promise<boolean> {
    const record = editorDirty,
      route = !!moves && routeDirty;
    if (!record && !route) {
      go();
      return Promise.resolve(true);
    }
    return (async () => {
      if (record && !(await ask(t('Discard unsaved record changes?')))) return false;
      if (route && !(await ask(moves!, { confirm: t('Discard moves') }))) return false;
      go();
      return true;
    })();
  }
  // Keep the location in the URL: a section or date change is a new history entry (Back
  // returns to it); the open plan alone replaces the entry, so polling never adds steps.
  const written = useRef('');
  useEffect(() => {
    const hash = routeHash({
      area,
      date: day,
      plan: area === 'planning' ? (shownPlan ?? undefined) : undefined,
    });
    if (window.location.hash === hash) {
      written.current = hash;
      return;
    }
    const previous = readRoute(written.current);
    if (previous && (previous.area !== area || previous.date !== day))
      window.history.pushState(null, '', hash);
    else window.history.replaceState(null, '', hash);
    written.current = hash;
  }, [area, day, shownPlan]);
  const guards = useRef({ leave, enter, area, day });
  guards.current = { leave, enter, area, day };
  useEffect(() => {
    // Back and Forward go through the same unsaved-work checks as the in-app controls.
    const onBack = () => {
      const hash = window.location.hash,
        route = readRoute(hash);
      if (!route) return;
      const { leave: leaving, enter: entering, area: shown, day: current } = guards.current;
      const otherDay = !!route.date && route.date !== current;
      void leaving(
        () => {
          written.current = hash;
          // Another section opens as the menu opens it: no import, search or filter carried over.
          if (route.area !== shown) entering(route.area);
          setSelection(null);
          setEditorDirty(false);
          if (otherDay) {
            setDay(route.date!);
            setSelectedOrders([]);
            setSelectedVehicles([]);
            setOffset(0);
            setRouteDirty(false);
          }
          setRequestedPlan(route.plan ?? null);
        },
        otherDay || route.area !== 'planning'
          ? t('Discard unsaved route moves and leave this plan?')
          : undefined,
      ).then((left) => {
        if (!left) window.history.pushState(null, '', written.current);
      });
    };
    window.addEventListener('popstate', onBack);
    return () => window.removeEventListener('popstate', onBack);
  }, []);
  /** Opens a section fresh, without the previous section's editor, import, search or filter. */
  function enter(next: Area) {
    setSelection(null);
    setEditorDirty(false);
    setArea(next);
    setMobile(false);
    setQuery('');
    setSearch('');
    setFilter('all');
    setImporting(false);
    setNotice('');
  }
  function navigate(next: Area) {
    void leave(() => enter(next));
  }
  function select(next: Selection | null) {
    void leave(() => {
      setSelection(next);
      setEditorDirty(false);
      setNotice('');
    });
  }
  /** Where a vehicle starts or ends, by the place's name (its code until the list of places arrives). */
  const placeName = (code: string) => [depot, ...places].find((p) => p.data.code === code)?.data.name ?? code;
  // With more than one depot or place, a delivery's loading place decides which vehicles can carry it.
  const loadingPlaces = new Set([depot.id, ...places.map((p) => p.id)]).size > 1;
  function choose(id: string) {
    const place = id === depot.id ? depot : places.find((p) => p.id === id);
    if (place) {
      if (role === 'admin') select({ kind: 'depot', row: place });
    } else {
      const row = orders.find((o) => o.id === id);
      if (row) select({ kind: 'orders', row });
    }
  }
  const rows = area === 'vehicles' ? vehicles : orders,
    pageOffset = area === 'vehicles' ? vehicleOffset : offset,
    summary = area === 'vehicles' ? vehicleSummary : orderSummary,
    // Records matching the filter and search across all pages; the page length until counts arrive.
    total = countOf(summary, filter) ?? rows.length,
    narrowed = filter !== 'all' || !!search;
  function changeFilter(next: string) {
    setFilter(next);
    setOffset(0);
    setVehicleOffset(0);
  }
  const sourcesVisible = area === 'orders' || area === 'vehicles';
  const firstUse =
    area === 'planning' &&
    showChecklist &&
    !busy &&
    !error &&
    offset === 0 &&
    vehicleOffset === 0 &&
    !orders.length &&
    !vehicles.length;
  const chosen = area === 'orders' ? selectedOrders : selectedVehicles;
  const setChosen = (next: (Order | Vehicle)[]) =>
    area === 'orders' ? setSelectedOrders(next as Order[]) : setSelectedVehicles(next as Vehicle[]);
  function toggle(row: Order | Vehicle) {
    setChosen(chosen.some((r) => r.id === row.id) ? chosen.filter((r) => r.id !== row.id) : [...chosen, row]);
  }
  const activePage = rows.filter((r) => r.data.active),
    allSelected = activePage.length > 0 && activePage.every((r) => chosen.some((c) => c.id === r.id));
  // Like the planning picker, refuse a page selection that would pass the limit instead of silently truncating it.
  const limit = area === 'orders' ? 250 : 25,
    overLimit = !allSelected && new Set([...chosen, ...activePage].map((r) => r.id)).size > limit;
  /** One step instead of paging: every active, reviewed delivery of the day (or every active vehicle). */
  async function selectAllEligible() {
    setError('');
    try {
      // The day's counts tell whether everything eligible fits one calculation.
      const [counted, eligible] =
        area === 'orders'
          ? await Promise.all([
              api<OrderSummary>(`orders/summary?service_date=${day}`).then((s) => s.ready),
              api<Order[]>(`orders?service_date=${day}&status=ready&limit=250`),
            ])
          : await Promise.all([
              api<VehicleSummary>(`vehicles/summary?service_date=${day}`).then((s) => s.active),
              api<Vehicle[]>('vehicles?status=active&limit=250'),
            ]);
      // Examples are planned only with other examples: follow the other selection's kind, or keep
      // to the company's own records when the day or the fleet has both.
      const other = area === 'orders' ? selectedVehicles : selectedOrders;
      const kinds = new Set(other.map((r) => !!r.example));
      const example =
        kinds.size === 1 ? kinds.has(true) : eligible.length > 0 && eligible.every((r) => !!r.example);
      const picked = (eligible as (Order | Vehicle)[]).filter((r) => !!r.example === example);
      if (counted > limit)
        setError(t('{0} records are eligible; select at most {1} for one calculation.', counted, limit));
      else {
        setChosen(picked);
        if (picked.length < eligible.length)
          setNotice(
            example
              ? t('Only the examples are selected: they are planned only with other examples.')
              : t('Example records are left out: they are planned only with other examples.'),
          );
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : t('Could not read records.'));
    }
  }
  /** Cancels the selected deliveries (kept, inactive, not planned) or deletes them for good. */
  async function changeSelected(action: 'cancel' | 'delete') {
    const picked = selectedOrders,
      count = picked.length;
    const confirmed =
      action === 'cancel'
        ? await ask(plural(count, 'Cancel {0} delivery?', 'Cancel {0} deliveries?'), {
            detail: t('They stay in the list as inactive and are not planned. You can restore them later.'),
            confirm: t('Cancel deliveries'),
            cancel: t('Keep them'),
            danger: false,
          })
        : await ask(plural(count, 'Delete {0} delivery?', 'Delete {0} deliveries?'), {
            detail: t(
              'They are removed for good. Saved plans keep their own copy and will ask for a fresh calculation. To keep deliveries for later, cancel them instead.',
            ),
            confirm: t('Delete deliveries'),
            cancel: t('Keep them'),
          });
    if (!confirmed) return;
    setWorking(true);
    setError('');
    setNotice('');
    try {
      if (action === 'cancel') await setOrdersActive(picked, false);
      else await deleteOrders(picked);
      setSelectedOrders((chosen) => chosen.filter((c) => !picked.some((p) => p.id === c.id)));
      setNotice(
        action === 'cancel'
          ? plural(count, '{0} delivery cancelled.', '{0} deliveries cancelled.')
          : plural(count, '{0} delivery deleted.', '{0} deliveries deleted.'),
      );
      setReload((n) => n + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : t('Could not change the selected deliveries.'));
    } finally {
      setWorking(false);
    }
  }
  async function restore(order: Order) {
    setWorking(true);
    setError('');
    setNotice('');
    try {
      await setOrdersActive([order], true);
      setNotice(t('Delivery {0} restored.', order.data.external_id));
      setReload((n) => n + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : t('Could not change the selected deliveries.'));
    } finally {
      setWorking(false);
    }
  }
  function selectPage() {
    if (overLimit) return;
    setChosen(
      allSelected
        ? chosen.filter((r) => !activePage.some((a) => a.id === r.id))
        : [...chosen.filter((r) => !activePage.some((a) => a.id === r.id)), ...activePage],
    );
  }
  const completed = [
    depot.data.location.reviewed,
    (vehicleSummary?.total ?? vehicles.length) > 0,
    (orderSummary?.total ?? orders.length) > 0,
    orderSummary
      ? orderSummary.total > 0 && orderSummary.review + orderSummary.missing === 0
      : orders.length > 0 && orders.every((o) => o.data.location?.reviewed),
    false,
  ];
  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobile ? 'is-open' : ''}`} inert={wizard || undefined}>
        <Brand />
        <nav aria-label={t('Main navigation')}>
          {areas.map((a) => (
            <button
              key={a.id}
              aria-current={area === a.id ? 'page' : undefined}
              title={t(a.name)}
              onClick={() => navigate(a.id)}
            >
              <NavIcon name={a.id} />
              <span className="nav-label">{t(a.name)}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-account">
          {session ? (
            <AccountPanel
              compact
              session={session}
              onSignedOut={onSignedOut}
              canSignOut={() => leave(() => {}, t('Discard unsaved route moves and sign out?'))}
            />
          ) : (
            <small>{t('Community workspace')}</small>
          )}
        </div>
      </aside>
      <div className="app-body" inert={wizard || undefined}>
        <header className="topbar">
          <button
            className="secondary menu-button"
            aria-expanded={mobile}
            aria-label={t('Toggle navigation')}
            onClick={() => setMobile((v) => !v)}
          >
            <Icon name="menu" size={20} />
          </button>
          <span className="mobile-brand">
            <Brand />
          </span>
          <strong>{configuration.company_name ?? t('Workspace')}</strong>
          {area !== 'vehicles' && area !== 'customers' && area !== 'settings' && (
            <label className="date-control">
              {t('Planning date')}
              <input
                type="date"
                value={day}
                required
                onChange={(e) => {
                  const next = e.target.value;
                  if (next)
                    void leave(() => {
                      setSelection(null);
                      setEditorDirty(false);
                      setRouteDirty(false);
                      setDay(next);
                      setOffset(0);
                      setSelectedOrders([]);
                      setSelectedVehicles([]);
                    }, t('Discard unsaved route moves and change the planning date?'));
                }}
              />
            </label>
          )}
          <span
            className={`connection-status ${error || connection === 'failed' ? 'connection-failed' : ''}`}
            role="status"
          >
            {connection === 'checking' ? (
              t('Checking connection…')
            ) : error || connection === 'failed' ? (
              t('Connection needs attention')
            ) : (
              <>
                <span className="status-dot" aria-hidden="true" />
                {t('Connected')}
              </>
            )}
          </span>
          <button
            className="text-button connection-refresh"
            disabled={connection === 'checking'}
            title={t('Check connection without changing your planning date or edits')}
            onClick={onReconnect}
          >
            <Icon name="refresh" /> <span className="connection-label">{t('Check connection')}</span>
          </button>
          <ThemeToggle />
        </header>
        <PageActionsSlot.Provider value={actionsSlot}>
          <main className={`workspace-main area-${area}`}>
            <div className="page-title">
              <div>
                <h1>
                  {firstUse
                    ? t('Your workspace is ready')
                    : importing
                      ? area === 'orders'
                        ? t('Import deliveries')
                        : t('Import vehicles')
                      : t(areas.find((a) => a.id === area)?.name ?? '')}
                </h1>
                <p>
                  {firstUse
                    ? t('Complete these first-use steps to calculate your first plan.')
                    : importing
                      ? t('Preview the file first. Nothing is saved until you import it.')
                      : area === 'planning'
                        ? t('Prepare deliveries, review routes and resolve remaining work.')
                        : area === 'orders'
                          ? t('Manage deliveries and review their vehicle entrances.')
                          : area === 'customers'
                            ? t(
                                'Each customer’s reviewed entrance and usual needs, reused by its deliveries.',
                              )
                            : area === 'vehicles'
                              ? t('Your shared fleet, capacities and road constraints.')
                              : area === 'saved'
                                ? t('Reopen the saved plans for your selected planning date.')
                                : role === 'admin'
                                  ? t('Manage your depots and places, delivery numbers and company accounts.')
                                  : t('Your personal preferences for this workspace.')}
                </p>
              </div>
              {area === 'customers' && (
                <div className="actions">
                  <button onClick={() => select({ kind: 'customers' })}>{t('Add customer')}</button>
                </div>
              )}
              {sourcesVisible && (
                <div className="actions">
                  <button className="secondary" onClick={() => setImporting((v) => !v)}>
                    {importing ? t('Close import') : t('Import CSV')}
                  </button>
                  {!importing && (
                    <button onClick={() => select({ kind: area === 'vehicles' ? 'vehicles' : 'orders' })}>
                      {area === 'vehicles' ? t('Add vehicle') : t('Add delivery')}
                    </button>
                  )}
                </div>
              )}
              <div className="page-actions" ref={setActionsSlot} />
            </div>
            {configuration.data_kind === 'SYNTHETIC' && (
              <p className="notice sample-notice">
                <span>
                  <strong>{t('Sample workspace.')}</strong>{' '}
                  {t('Fictional data. Operational export is unavailable.')}
                </span>
              </p>
            )}
            {configuration.examples && area !== 'settings' && (
              <p className="notice sample-notice example-notice">
                <span>
                  <strong>{t('Example data is in this workspace.')}</strong>{' '}
                  <span className="example-notice-detail">
                    {t(
                      'Records marked Example are fictional: their plans are for trying Tournevia, not for driving.',
                    )}
                  </span>
                </span>
                {role === 'admin' && (
                  <button
                    className="secondary"
                    onClick={() =>
                      void leave(() => {
                        enter('settings');
                        setSettings('examples');
                      })
                    }
                  >
                    {t('Manage example data')}
                  </button>
                )}
              </p>
            )}
            {error && (
              <p role="alert" className="error">
                {error} <button onClick={() => setReload((n) => n + 1)}>{t('Retry data')}</button>
              </p>
            )}
            {connection === 'failed' && (
              <div role="alert" className="error">
                <strong>{t('Connection check failed')}</strong>
                <p>
                  {t(
                    'Your current date, selections and edits are still here. The displayed records may be out of date. Check the connection again before continuing.',
                  )}
                </p>
              </div>
            )}
            {area === 'planning' && <SystemStatusBanner />}
            {notice && (
              <p role="status" className="success">
                {notice}
              </p>
            )}
            {!firstUse && !busy && !error && !orders.length && offset === 0 && area === 'planning' && (
              <div className="info date-empty">
                <strong>
                  {t('No deliveries for')} {serviceDay(day)}
                </strong>
                <p>
                  {t(
                    'Deliveries are shown for the selected date. Change the planning date above to find another day’s work, or add deliveries for this day.',
                  )}
                </p>
              </div>
            )}
            {firstUse && (
              <>
                <p className="onboarding-count">
                  <span className="badge">{t('Company workspace')}</span> {completed.filter(Boolean).length}{' '}
                  {t('of 5 steps complete')}
                  {guided && (
                    <button className="secondary continue-setup" onClick={() => setWizard(true)}>
                      {t('Open the setup guide')}
                    </button>
                  )}
                </p>
                <div className="first-use-grid">
                  <section className="first-use-steps">
                    {[
                      [
                        t('Depot configured'),
                        `${depot.data.name} · ${completed[0] ? t('entrance reviewed') : t('entrance needs review')}`,
                        t('Edit depot'),
                        () => select({ kind: 'depot', row: depot }),
                      ],
                      [
                        t('Add or import vehicles'),
                        t('Set capacities, equipment, availability and operating rates.'),
                        t('Add vehicle'),
                        () => {
                          navigate('vehicles');
                          select({ kind: 'vehicles' });
                        },
                      ],
                      [
                        t('Add or import deliveries'),
                        t('Choose a planning date and add the work for that day.'),
                        t('Go to deliveries'),
                        () => navigate('orders'),
                      ],
                      [
                        t('Review delivery locations'),
                        t('Confirm saved vehicle-entrance points.'),
                        t('Review locations'),
                        () => {
                          navigate('orders');
                          setFilter('review');
                        },
                      ],
                      [
                        t('Calculate the first plan'),
                        t('Select deliveries and vehicles once the inputs are ready.'),
                        t('Open planning'),
                        () => setShowChecklist(false),
                      ],
                    ].map(([title, description, label, action], i) => (
                      <div className={`first-use-row ${completed[i] ? 'complete' : ''}`} key={i}>
                        <span className="step-circle">{completed[i] ? <Icon name="check" /> : i + 1}</span>
                        <div>
                          <strong>{String(title)}</strong>
                          <p>{String(description)}</p>
                        </div>
                        {completed[i] ? (
                          <span className="badge ready">{t('Complete')}</span>
                        ) : (
                          <button
                            disabled={i === 0 && role !== 'admin'}
                            className={i === 1 ? '' : 'secondary'}
                            onClick={action as () => void}
                          >
                            {String(label)}
                          </button>
                        )}
                      </div>
                    ))}
                    <p className="checklist-footer">
                      {t('Your progress follows your saved records.')}
                      {role === 'admin' && (
                        <button
                          className="text-button"
                          onClick={() => {
                            navigate('settings');
                            setSettings('users');
                          }}
                        >
                          {t('Add colleagues later')}
                        </button>
                      )}
                    </p>
                  </section>
                  <section className="first-use-empty" aria-label={t('Depot on the map')}>
                    {/* The company's own depot, where every route will start and end. */}
                    {configuration.map_style_url && (
                      <MapPanel
                        style={configuration.map_style_url}
                        points={[
                          {
                            id: depot.id,
                            label: depot.data.name,
                            location: depot.data.location,
                            depot: true,
                          },
                        ]}
                        picking={false}
                        onPick={() => {}}
                        onSelect={() => {}}
                      />
                    )}
                    <div>
                      <h2>{t('No plan for this day yet')}</h2>
                      <p>
                        {t(
                          'No routes have been calculated for this day. Add your fleet and deliveries, then review the result before marking it Ready.',
                        )}
                      </p>
                    </div>
                  </section>
                </div>
              </>
            )}
            {sourcesVisible && !importing && (
              <>
                {area === 'vehicles' && (
                  <div className="fleet-metrics">
                    {[
                      [
                        t('Fleet vehicles'),
                        vehicleSummary?.total ?? '–',
                        search ? t('Matching the search') : t('Whole fleet'),
                      ],
                      [t('Active vehicles'), vehicleSummary?.active ?? '–', t('Available for selection')],
                      [
                        t('Payload capacity'),
                        vehicleSummary ? tonnes(vehicleSummary.active_payload_kg) : '–',
                        t('Across active vehicles'),
                      ],
                      [
                        t('Equipment'),
                        vehicleSummary
                          ? plural(vehicleSummary.equipment.length, '{0} type', '{0} types')
                          : '–',
                        equipmentList(vehicleSummary?.equipment ?? []) || t('No equipment recorded'),
                      ],
                    ].map(([label, value, note]) => (
                      <div key={label}>
                        <small>{label}</small>
                        <strong>{value}</strong>
                        <p>{note}</p>
                      </div>
                    ))}
                  </div>
                )}
                <nav className="tabs source-tabs" aria-label={t('Record filters')}>
                  {/* The tabs are the one filter control; inactive deliveries get a tab only when there are some. */}
                  {(area === 'orders'
                    ? [
                        ['all', t('All deliveries {0}', countOf(orderSummary, 'all') ?? '–')],
                        ['review', t('Needs review {0}', countOf(orderSummary, 'review') ?? '–')],
                        ['missing', t('Location missing {0}', countOf(orderSummary, 'missing') ?? '–')],
                        ...(countOf(orderSummary, 'inactive') || filter === 'inactive'
                          ? [
                              [
                                'inactive',
                                t('Inactive deliveries {0}', countOf(orderSummary, 'inactive') ?? '–'),
                              ],
                            ]
                          : []),
                      ]
                    : [
                        ['all', t('All vehicles {0}', countOf(vehicleSummary, 'all') ?? '–')],
                        ['active', t('Active {0}', countOf(vehicleSummary, 'active') ?? '–')],
                        ['inactive', t('Inactive {0}', countOf(vehicleSummary, 'inactive') ?? '–')],
                      ]
                  ).map(([id, label]) => (
                    <button key={id} aria-pressed={filter === id} onClick={() => changeFilter(id)}>
                      {label}
                    </button>
                  ))}
                </nav>
                <div className="source-toolbar">
                  <label>
                    <span className="sr-only">
                      {area === 'orders' ? t('Search deliveries of this day') : t('Search the fleet')}
                    </span>
                    <input
                      type="search"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder={
                        area === 'orders' ? t('Search ID or address') : t('Search fleet identifier')
                      }
                    />
                  </label>
                  {rows.length > 0 && (
                    <>
                      <label className="check page-selection">
                        <input
                          type="checkbox"
                          checked={allSelected}
                          disabled={!activePage.length || overLimit}
                          onChange={selectPage}
                        />
                        {t('Select this page ({0})', activePage.length)}
                      </label>
                      <button className="text-button" onClick={() => void selectAllEligible()}>
                        {area === 'orders'
                          ? t('Select all reviewed deliveries for this day')
                          : t('Select all active vehicles')}
                      </button>
                      <span>
                        {area === 'orders'
                          ? plural(
                              chosen.length,
                              '{0} delivery selected across pages',
                              '{0} deliveries selected across pages',
                            )
                          : plural(
                              chosen.length,
                              '{0} vehicle selected across pages',
                              '{0} vehicles selected across pages',
                            )}
                      </span>
                    </>
                  )}
                  {overLimit && (
                    <small role="status" className="attention">
                      {t(
                        'Selecting this page would pass the limit of {0}. Select records individually or clear some selections.',
                        limit,
                      )}
                    </small>
                  )}
                </div>
                {area === 'orders' && selectedOrders.length > 0 && (
                  <div className="selection-actions" role="group" aria-label={t('Selected deliveries')}>
                    <strong>
                      {plural(selectedOrders.length, '{0} delivery selected', '{0} deliveries selected')}
                    </strong>
                    <button
                      className="secondary"
                      disabled={working}
                      onClick={() => void changeSelected('cancel')}
                    >
                      {t('Cancel deliveries')}
                    </button>
                    <button
                      className="secondary delete-record"
                      disabled={working}
                      onClick={() => void changeSelected('delete')}
                    >
                      {t('Delete deliveries')}
                    </button>
                    <button className="text-button" disabled={working} onClick={() => setSelectedOrders([])}>
                      {t('Clear selection')}
                    </button>
                  </div>
                )}
                {area === 'vehicles' && <OffDayNotice vehicles={offDay} day={day} selected={false} />}
                <div className="table-scroll source-table">
                  <table>
                    <thead>
                      <tr>
                        <th>
                          <span className="sr-only">{t('Select')}</span>
                        </th>
                        <th>{area === 'orders' ? t('Delivery ID') : t('Vehicle')}</th>
                        <th>{area === 'orders' ? t('Address / entrance') : t('Status')}</th>
                        {area === 'orders' && loadingPlaces && <th>{t('Ships from')}</th>}
                        {area === 'vehicles' && <th>{t('Size and weight')}</th>}
                        <th className="num">
                          {area === 'orders' ? t('Service window') : t('Payload capacity')}
                        </th>
                        <th className={area === 'orders' ? 'num' : undefined}>
                          {area === 'orders' ? t('Weight') : t('Other capacities')}
                        </th>
                        <th>{area === 'orders' ? t('Location') : t('Equipment')}</th>
                        <th>{area === 'orders' ? t('Status') : t('Availability / rates')}</th>
                        <th>
                          <span className="sr-only">{t('Edit')}</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((row) => {
                        const d = row.data;
                        return (
                          <tr
                            key={row.id}
                            className={chosen.some((c) => c.id === row.id) ? 'selected-source' : ''}
                          >
                            <td>
                              <input
                                type="checkbox"
                                aria-label={t(
                                  'Select {0}',
                                  'external_id' in d ? d.external_id : d.fleet_number,
                                )}
                                checked={chosen.some((c) => c.id === row.id)}
                                disabled={
                                  (!d.active && !chosen.some((c) => c.id === row.id)) ||
                                  (chosen.length >= limit && !chosen.some((c) => c.id === row.id))
                                }
                                onChange={() => toggle(row)}
                              />
                            </td>
                            <td className="record-id">
                              <strong>{'external_id' in d ? d.external_id : d.fleet_number}</strong>
                              {row.example && <span className="badge example-badge">{t('Example')}</span>}
                            </td>
                            {'external_id' in d ? (
                              <>
                                <td>{d.address}</td>
                                {loadingPlaces && (
                                  <td>
                                    {d.ships_from ? (
                                      placeName(d.ships_from)
                                    ) : (
                                      <span className="muted-value">{t('Any vehicle')}</span>
                                    )}
                                  </td>
                                )}
                                <td className="num">
                                  {d.time_window.start.slice(11, 16)}–{d.time_window.end.slice(11, 16)}
                                </td>
                                <td className="num">{kilograms(d.demand.weight_kg)}</td>
                                <td>
                                  <button
                                    className={`badge location-badge ${d.location?.reviewed ? 'ready' : 'attention'}`}
                                    onClick={() => select({ kind: 'orders', row, view: 'location' })}
                                  >
                                    {!d.location
                                      ? t('Location missing')
                                      : d.location.reviewed
                                        ? t('Reviewed')
                                        : t('Needs review')}{' '}
                                    <span className="sr-only">· {d.external_id}</span>
                                  </button>
                                </td>
                                <td>
                                  <span className="badge">{d.active ? t('Active') : t('Inactive')}</span>
                                  {!d.active && (
                                    <button
                                      className="text-button restore-record"
                                      disabled={working}
                                      onClick={() => void restore(row as Order)}
                                    >
                                      {t('Restore')} <span className="sr-only">{d.external_id}</span>
                                    </button>
                                  )}
                                </td>
                              </>
                            ) : (
                              <>
                                <td>
                                  <span className={`badge ${d.active ? 'ready' : ''}`}>
                                    {d.active ? t('Active') : t('Inactive')}
                                  </span>
                                </td>
                                <td className="truck-cell">
                                  <strong>{truckSize(d.routing)}</strong>
                                  <small>{truckWeights(d.routing)}</small>
                                </td>
                                <td className="num">
                                  <strong>{kilograms(d.capacity.weight_kg)}</strong>
                                </td>
                                <td>
                                  {cubicMetres(d.capacity.volume_dm3)} ·{' '}
                                  {plural(d.capacity.pallet_eur_units, '{0} Euro pallet', '{0} Euro pallets')}
                                  <small>
                                    {plural(
                                      d.capacity.pallet_ind_units,
                                      '{0} industrial pallet',
                                      '{0} industrial pallets',
                                    )}{' '}
                                    · {t('{0} loading length', metres(d.capacity.loading_length_mm))}
                                  </small>
                                </td>
                                <td>
                                  <div className="tags">
                                    {d.skills.map((skill) => (
                                      <span className="tag" key={skill}>
                                        {equipmentName(skill)}
                                      </span>
                                    ))}
                                  </div>
                                </td>
                                <td>
                                  {d.active && !availableOn(row as Vehicle, day) && (
                                    <span className="badge attention">{t('Off {0}', serviceDay(day))}</span>
                                  )}
                                  {weekSummary(d.hours)}
                                  {(d.start || d.end) && (
                                    <small className="vehicle-start">
                                      {t(
                                        'Starts at {0} · ends at {1}',
                                        placeName(d.start ?? depot.data.code),
                                        placeName(d.end ?? d.start ?? depot.data.code),
                                      )}
                                    </small>
                                  )}
                                  <small>
                                    {t(
                                      '{0} / km · {1} / hour',
                                      euros(d.cost.per_km_minor),
                                      euros(d.cost.per_hour_minor),
                                    )}
                                  </small>
                                </td>
                              </>
                            )}
                            <td>
                              <button
                                className="secondary"
                                onClick={() =>
                                  select({ kind: area === 'orders' ? 'orders' : 'vehicles', row })
                                }
                              >
                                {t('Edit')}{' '}
                                <span className="sr-only">
                                  {'external_id' in d ? d.external_id : d.fleet_number}
                                </span>
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                {!busy && !rows.length && (
                  <div className="empty">
                    {/* An empty tab says what being empty means; a search says what to change. */}
                    <h2>
                      {search
                        ? t('No matching records')
                        : filter === 'review'
                          ? t('Every located delivery is reviewed')
                          : filter === 'missing'
                            ? t('Every delivery has a location')
                            : filter === 'inactive'
                              ? area === 'orders'
                                ? t('No inactive deliveries')
                                : t('No inactive vehicles')
                              : filter === 'active'
                                ? t('No active vehicles')
                                : area === 'orders'
                                  ? t('No deliveries on {0}', serviceDay(day))
                                  : t('Build your fleet')}
                    </h2>
                    <p>
                      {search
                        ? t('Change the filter or search.')
                        : narrowed
                          ? filter === 'review' || filter === 'missing'
                            ? t('Nothing to review for this day before calculating.')
                            : filter === 'active'
                              ? t('Restore a vehicle or add one to plan with it.')
                              : area === 'orders'
                                ? t('Cancelled deliveries stay here as inactive and can be restored.')
                                : t(
                                    'Vehicles taken out of service stay here as inactive and can be restored.',
                                  )
                          : area === 'orders'
                            ? t('Change the planning date above, add a delivery or import a CSV.')
                            : t('Add a vehicle or import a CSV.')}
                    </p>
                  </div>
                )}
                <div className="actions pagination" hidden={!busy && !rows.length}>
                  <small>
                    {busy
                      ? t('Loading…')
                      : t('{0}–{1} of {2}', pageOffset + 1, pageOffset + rows.length, total)}{' '}
                    {t('· selections retained across pages')}
                  </small>
                  <button
                    className="secondary"
                    hidden={total <= 50}
                    disabled={busy || pageOffset === 0}
                    onClick={() =>
                      area === 'orders'
                        ? setOffset((n) => Math.max(0, n - 50))
                        : setVehicleOffset((n) => Math.max(0, n - 50))
                    }
                  >
                    {t('Previous 50')}
                  </button>
                  <span hidden={total <= 50}>
                    {t('Page {0} of {1}', pageOffset / 50 + 1, Math.max(1, Math.ceil(total / 50)))}
                  </span>
                  <button
                    className="secondary"
                    hidden={total <= 50}
                    disabled={busy || rows.length < 50 || pageOffset + rows.length >= total}
                    onClick={() =>
                      area === 'orders' ? setOffset((n) => n + 50) : setVehicleOffset((n) => n + 50)
                    }
                  >
                    {t('Next 50')}
                  </button>
                  <button
                    className="text-button"
                    onClick={() =>
                      void leave(() => {
                        setSelection(null);
                        setEditorDirty(false);
                        setReload((n) => n + 1);
                      })
                    }
                  >
                    {t('Reload records')}
                  </button>
                </div>
                {area === 'vehicles' && (
                  <>
                    <div className="info">
                      <strong>{t('Shared workspace data')}</strong>
                      <p>
                        {t(
                          'Your fleet is independent of the planning date. Selections stay with you as you move between pages.',
                        )}
                      </p>
                    </div>
                    <section className="workspace fleet-note">
                      <h2>{t('Keep payload and road constraints separate')}</h2>
                      <p>
                        {t(
                          'Payload quantities control what a vehicle carries. Gross weight, axle load and dimensions constrain the roads it can use.',
                        )}
                      </p>
                    </section>
                  </>
                )}
              </>
            )}
            {area === 'customers' && (
              <Customers reload={reload} onEdit={(row) => select({ kind: 'customers', row })} />
            )}
            {sourcesVisible && importing && (
              <ImportPanel
                key={area}
                kind={area === 'orders' ? 'orders' : 'vehicles'}
                onCommit={() => setReload((n) => n + 1)}
                onReview={(next) => {
                  setImporting(false);
                  setFilter(next);
                }}
              />
            )}
            <div hidden={firstUse || (area !== 'planning' && area !== 'saved')}>
              {/* Hidden panels stay mounted; only the visible planning view puts actions in the header. */}
              <PageActionsSlot.Provider value={!firstUse && area === 'planning' ? actionsSlot : null}>
                <PlanningPanel
                  key={day}
                  selection={{
                    orders: selectedOrders,
                    vehicles: selectedVehicles,
                    setOrders: setSelectedOrders,
                    setVehicles: setSelectedVehicles,
                  }}
                  day={day}
                  depot={depot}
                  places={places}
                  vehicles={vehicles}
                  orders={orders}
                  busy={busy || !!error || connection !== 'ready'}
                  mapStyle={configuration.map_style_url}
                  view={area === 'saved' ? 'saved' : 'planning'}
                  requestedPlanId={requestedPlan}
                  onPlanShown={setShownPlan}
                  onOpenPlan={() => setArea('planning')}
                  onDirtyChange={setRouteDirty}
                  onSources={() => navigate('orders')}
                  onFleet={() => navigate('vehicles')}
                  sourcePages={
                    <div className="planning-columns">
                      {(['orders', 'vehicles'] as const).map((kind) => (
                        <div key={kind}>
                          <small>
                            {kind === 'orders' ? t('Delivery') : t('Fleet')} {t('page')}{' '}
                            {(kind === 'orders' ? offset : vehicleOffset) / 50 + 1}
                          </small>
                          <button
                            className="secondary"
                            aria-label={t('Previous {0} page', kind)}
                            disabled={busy || (kind === 'orders' ? offset : vehicleOffset) === 0}
                            onClick={() =>
                              kind === 'orders'
                                ? setOffset((n) => Math.max(0, n - 50))
                                : setVehicleOffset((n) => Math.max(0, n - 50))
                            }
                          >
                            {t('Previous')}
                          </button>{' '}
                          <button
                            className="secondary"
                            aria-label={t('Next {0} page', kind)}
                            disabled={busy || (kind === 'orders' ? orders : vehicles).length < 50}
                            onClick={() =>
                              kind === 'orders' ? setOffset((n) => n + 50) : setVehicleOffset((n) => n + 50)
                            }
                          >
                            {t('Next')}
                          </button>
                        </div>
                      ))}
                    </div>
                  }
                />
              </PageActionsSlot.Provider>
            </div>
            {area === 'settings' && (
              <div className="settings-layout">
                <nav className="settings-nav" aria-label={t('Settings')}>
                  <button aria-pressed={settings === 'personal'} onClick={() => setSettings('personal')}>
                    <NavIcon name="person" />
                    {t('Personal preferences')}
                  </button>
                  {role === 'admin' && (
                    <>
                      <button aria-pressed={settings === 'depot'} onClick={() => setSettings('depot')}>
                        <NavIcon name="settings" />
                        {t('Depots and places')}
                      </button>
                      <button aria-pressed={settings === 'numbers'} onClick={() => setSettings('numbers')}>
                        <NavIcon name="numbers" />
                        {t('Delivery numbers')}
                      </button>
                      <button
                        aria-pressed={settings === 'addresses'}
                        onClick={() => setSettings('addresses')}
                      >
                        <NavIcon name="addresses" />
                        {t('Address search')}
                      </button>
                      <button
                        aria-pressed={settings === 'calculation'}
                        onClick={() => setSettings('calculation')}
                      >
                        <NavIcon name="calculation" />
                        {t('Calculation')}
                      </button>
                      <button aria-pressed={settings === 'examples'} onClick={() => setSettings('examples')}>
                        <NavIcon name="examples" />
                        {t('Example data')}
                      </button>
                      <button aria-pressed={settings === 'users'} onClick={() => setSettings('users')}>
                        <NavIcon name="users" />
                        {t('Users')}
                      </button>
                      <small>
                        {t('Administrator settings')}
                        <br />
                        {configuration.company_name}
                      </small>
                    </>
                  )}
                </nav>
                <div>
                  {settings === 'personal' ? (
                    <LanguageSettings named={!!session?.identity.user_id} />
                  ) : (
                    role === 'admin' && (
                      <>
                        {settings === 'numbers' ? (
                          <NumberingSettings />
                        ) : settings === 'addresses' ? (
                          <AddressLookupSettings
                            source={configuration.address_lookup_source}
                            attribution={configuration.address_lookup}
                            onChanged={onReconnect}
                          />
                        ) : settings === 'calculation' ? (
                          <CalculationSettings
                            minutes={configuration.safety_margin_minutes ?? 15}
                            onChanged={onReconnect}
                          />
                        ) : settings === 'examples' ? (
                          <ExampleSettings
                            present={!!configuration.examples}
                            day={day}
                            onChanged={(message) => {
                              setNotice(message);
                              setReload((n) => n + 1);
                              onReconnect();
                            }}
                          />
                        ) : settings === 'users' && session?.identity.user_id ? (
                          <Users currentId={session.identity.user_id} />
                        ) : (
                          <PlacesSettings
                            places={places}
                            style={configuration.map_style_url}
                            onEdit={(row, kind) =>
                              select({ kind: 'depot', row: row ?? undefined, placeKind: kind })
                            }
                            onChanged={(message) => {
                              setNotice(message);
                              setReload((n) => n + 1);
                            }}
                          />
                        )}
                      </>
                    )
                  )}
                </div>
              </div>
            )}
            {selection && (
              <div
                className={`editor-overlay ${selection.kind !== 'orders' || selection.view === 'location' ? 'full-editor' : ''}`}
              >
                <SourceEditor
                  key={`${selection.kind}:${selection.row?.id ?? 'new'}:${selection.row?.version ?? 0}`}
                  selection={selection}
                  day={day}
                  style={configuration.map_style_url}
                  points={points}
                  vehicles={vehicles}
                  onDirtyChange={setEditorDirty}
                  addressLookup={configuration.address_lookup}
                  onSaved={(kind, row, view) => {
                    setEditorDirty(false);
                    setSelection({ kind, row, view });
                    const data = row.data as {
                      external_id?: string;
                      fleet_number?: string;
                      name?: string;
                      code?: string;
                    };
                    setNotice(
                      kind === 'orders'
                        ? t('Delivery {0} saved.', data.external_id)
                        : kind === 'vehicles'
                          ? t('Vehicle {0} saved.', data.fleet_number)
                          : kind === 'customers'
                            ? t('Customer {0} saved.', data.code)
                            : t('{0} saved.', placeLabel(row as Depot)),
                    );
                    setReload((n) => n + 1);
                  }}
                  onDeleted={(order) => {
                    setEditorDirty(false);
                    setSelection(null);
                    setSelectedOrders((chosen) => chosen.filter((c) => c.id !== order.id));
                    setNotice(t('Delivery {0} deleted.', order.data.external_id));
                    setReload((n) => n + 1);
                  }}
                  places={places}
                  onPlaceAdded={
                    role === 'admin'
                      ? (place) => {
                          setPlaces((all) => [...all, place]);
                          setNotice(t('{0} saved.', placeLabel(place)));
                        }
                      : undefined
                  }
                  onSelect={choose}
                  onClose={() => select(null)}
                />
              </div>
            )}
          </main>
        </PageActionsSlot.Provider>
        <footer className="workspace-footer">
          {/* A plan states this notice under its approval; the planning page does not repeat it here. */}
          <div>
            {area !== 'planning' && (
              <strong>
                {t(
                  'Planning estimate. Verify current road access and driver requirements. Tolls are not included.',
                )}
              </strong>
            )}
          </div>
          <a href="/assets/THIRD_PARTY_NOTICES.txt">
            {t('Third-party notices')} <span aria-hidden="true">↗</span>
          </a>
        </footer>
      </div>
      {/* Beside the page rather than inside it, so the page can be inert while the guide is open. */}
      {wizard && (
        <SetupWizard
          day={day}
          onExamplesAdded={(added) => {
            setWizard(false);
            void leave(() => {
              enter('planning');
              setNotice(addedMessage(added));
              setReload((n) => n + 1);
            });
            onReconnect(); // The configuration now reports the examples.
          }}
          onVehiclesAdded={() => setReload((n) => n + 1)}
          onImport={(kind) => {
            setWizard(false);
            void leave(() => {
              enter(kind);
              setImporting(true);
            });
          }}
          onAddDelivery={() => {
            setWizard(false);
            void leave(() => {
              enter('orders');
              setSelection({ kind: 'orders' });
            });
          }}
          onClose={(finished) => {
            setWizard(false);
            setNotice(
              finished
                ? t('Setup finished. Add deliveries whenever you are ready, from Deliveries.')
                : t(
                    'Guide closed. Vehicles are in Fleet, delivery numbers in Settings and deliveries in Deliveries.',
                  ),
            );
          }}
        />
      )}
      <ConfirmHost />
    </div>
  );
}
