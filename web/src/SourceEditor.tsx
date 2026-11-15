import { plural, t, useLanguage } from './i18n';
import { useEffect, useRef, useState } from 'react';
import type { components } from './api-types';
import {
  api,
  ApiError,
  deleteOrders,
  save,
  type AccessCheck,
  type Depot,
  type Location,
  type Order,
  type OrderInput,
  type Vehicle,
  type VehicleSource,
  type Customer,
  type CustomerInput,
} from './api';
import {
  badEquipmentCode,
  equipmentCodes,
  invalidMessage,
  limits,
  parisCandidates,
  scaledValue,
} from './formValues';
import { serviceDay } from './format';
import { equipment, isNamedEquipment } from './equipment';
import { MapPanel, type MapPoint } from './MapPanel';
import { AddressField } from './addressLookup';
import { Icon } from './Brand';
import { DAYS } from './availability';
import { WorkingHours } from './WorkingHours';
import { CustomerField, SaveAsCustomer, fromCustomer } from './Customers';
import { ask } from './confirm';
import { AddPlace, placeLabel } from './Places';

type DepotInput = components['schemas']['DepotInput'];
type Data = DepotInput | OrderInput | VehicleSource | CustomerInput;
type Row = Depot | Order | Vehicle | Customer;
export type Selection = {
  kind: 'depot' | 'orders' | 'vehicles' | 'customers';
  row?: Row;
  view?: 'location';
  /** A new depot (DEPOT) or other place (PLACE). */
  placeKind?: 'DEPOT' | 'PLACE';
};
const amounts = [
  ['weight_kg', 'Weight (kg)'],
  ['volume_dm3', 'Volume (dm³)'],
  ['pallet_eur_units', 'Euro pallets'],
  ['pallet_ind_units', 'Industrial pallets'],
  ['loading_length_mm', 'Loading length (mm)'],
];
const truck = [
  ['height_mm', 'Height (mm)'],
  ['width_mm', 'Width (mm)'],
  ['length_mm', 'Length (mm)'],
  ['gross_weight_kg', 'Gross routing weight (kg)'],
  ['axle_load_kg', 'Axle load (kg)'],
  ['axle_count', 'Axles'],
];
const zero = { weight_kg: 0, volume_dm3: 0, pallet_eur_units: 0, pallet_ind_units: 0, loading_length_mm: 0 };
function blank(kind: Selection['kind'], day: string): Data {
  if (kind === 'depot')
    return {
      code: '',
      name: '',
      kind: 'DEPOT',
      address: '',
      location: null,
      timezone: 'Europe/Paris',
    } as unknown as DepotInput;
  if (kind === 'customers')
    return {
      code: '',
      name: '',
      active: true,
      address: '',
      location: null,
      usual_hours: null,
      service_seconds: null,
      required_skills: [],
      instructions: '',
    };
  if (kind === 'vehicles')
    return {
      fleet_number: '',
      active: true,
      routing: {
        height_mm: NaN,
        width_mm: NaN,
        length_mm: NaN,
        gross_weight_kg: NaN,
        axle_load_kg: NaN,
        axle_count: NaN,
      },
      capacity: { ...zero },
      skills: [],
      // A starting week the person adjusts: Monday to Friday, 08:00–17:00.
      hours: Object.fromEntries(
        DAYS.map((d) => [d, d === 'saturday' || d === 'sunday' ? null : { start: '08:00', end: '17:00' }]),
      ),
      exceptions: [],
      cost: { currency: 'EUR', fixed_minor: 0, per_km_minor: 0, per_hour_minor: 0 },
      limits: {},
    };
  return {
    external_id: '',
    service_date: day,
    active: true,
    address: '',
    location: null,
    time_window: { start: `${day}T08:00:00+02:00`, end: `${day}T17:00:00+02:00` },
    service_seconds: 0,
    demand: { ...zero },
    required_skills: [],
    instructions: '',
    priority: 0,
  };
}
// Pick the actual Paris offset for initial windows, including winter dates.
function initial(kind: Selection['kind'], day: string): Data {
  const data = blank(kind, day);
  const noon = new Date(`${day}T12:00:00Z`);
  // An unreadable day keeps winter time rather than stopping the editor.
  const offset = Number.isNaN(noon.getTime())
    ? '+01:00'
    : (new Intl.DateTimeFormat('en', { timeZone: 'Europe/Paris', timeZoneName: 'longOffset' })
        .formatToParts(noon)
        .find((p) => p.type === 'timeZoneName')
        ?.value.replace('GMT', '') ?? '+01:00');
  if ('time_window' in data) {
    data.time_window.start = `${day}T08:00:00${offset}`;
    data.time_window.end = `${day}T17:00:00${offset}`;
  }
  return data;
}
/** The delivery editor tab that holds a field. */
function panelOf(name: string) {
  return name.startsWith('location')
    ? 'location'
    : name.startsWith('demand')
      ? 'quantities'
      : name === 'required_skills' || name === 'priority'
        ? 'requirements'
        : name === 'instructions'
          ? 'instructions'
          : 'details';
}

export function SourceEditor({
  selection,
  day,
  style,
  points,
  vehicles,
  onSaved,
  onDeleted,
  onSelect,
  onClose,
  onDirtyChange,
  addressLookup,
  places: allPlaces = [],
  onPlaceAdded,
}: {
  selection: Selection;
  day: string;
  style: string;
  points: MapPoint[];
  vehicles: Vehicle[];
  /** `view`: the editor reopens where the record was saved from, so a saved pin can be reviewed next. */
  onSaved: (kind: Selection['kind'], row: Row, view?: Selection['view']) => void;
  /** A delivery was deleted from this editor. */
  onDeleted?: (row: Order) => void;
  onSelect: (id: string) => void;
  onClose: () => void;
  onDirtyChange?: (dirty: boolean) => void;
  /** Attribution of the workspace's address lookup, when it is on. */
  addressLookup?: string | null;
  /** The company's depots and places, the main depot first: where vehicles start and end. */
  places?: Depot[];
  /** Lets an administrator add another place from the vehicle editor. */
  onPlaceAdded?: (place: Depot) => void;
}) {
  useLanguage();
  const { kind, row } = selection;
  // An example depot is only for example vehicles and deliveries.
  const places = allPlaces.filter(
    (p) => !p.example || !!(selection.row && 'example' in selection.row && selection.row.example),
  );
  const [panel, setPanel] = useState(selection.view === 'location' ? 'location' : 'details');
  const [data, setData] = useState<Data>(() =>
    row
      ? structuredClone(row.data)
      : kind === 'depot'
        ? ({ ...initial(kind, day), kind: selection.placeKind ?? 'DEPOT' } as Data)
        : initial(kind, day),
  );
  // A new record as it opened, before anything was typed: closing it then loses nothing.
  const blankData = useRef(data);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const rules = limits[kind];
  // A depot or place; data saved before places existed has no kind and is a depot.
  const placeKind = 'kind' in data && data.kind === 'PLACE' ? 'PLACE' : 'DEPOT';
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [picking, setPicking] = useState(false);
  const [vehicleId, setVehicleId] = useState(vehicles[0]?.id ?? '');
  const [access, setAccess] = useState<AccessCheck | null>(null);
  // A new delivery proposes the company's next number; the server gives the final one on saving,
  // so two people adding deliveries at once never receive the same number.
  const [proposed, setProposed] = useState<string | null>(null);
  const proposal = useRef<string | null>(null);
  const serviceDate = kind === 'orders' && !row && 'service_date' in data ? data.service_date : '';
  useEffect(() => {
    if (!serviceDate) return;
    let active = true;
    api<{ identifier: string | null }>(
      `orders/next-identifier?service_date=${encodeURIComponent(serviceDate)}`,
    )
      .then((next) => {
        if (!active || !next?.identifier) return;
        const identifier = next.identifier;
        setData((current) => {
          const typed = 'external_id' in current ? current.external_id : '';
          // Never replace an identifier the person typed.
          if (typed && typed !== proposal.current) return current;
          proposal.current = identifier;
          return { ...current, external_id: identifier } as Data;
        });
        setProposed(identifier);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [serviceDate]);
  const dialog = useRef<HTMLElement>(null);
  const errorNotice = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (error) {
      errorNotice.current?.focus({ preventScroll: true });
      errorNotice.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
    }
  }, [error]);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const scroll = window.scrollY;
    if (scroll) window.scrollTo(0, 0);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    dialog.current?.focus({ preventScroll: true });
    return () => {
      document.body.style.overflow = overflow;
      if (scroll) window.scrollTo(0, scroll);
      previous?.focus({ preventScroll: true });
    };
  }, []);
  // Switching between the details and the entrance review removes the button that was pressed;
  // focus returns to the dialog so Escape and the Tab loop keep working.
  const firstPanel = useRef(true);
  useEffect(() => {
    if (firstPanel.current) {
      firstPanel.current = false;
      return;
    }
    dialog.current?.focus({ preventScroll: true });
  }, [panel]);
  const revision = useRef(0);
  const mapArea = useRef<HTMLElement>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      revision.current++;
    };
  }, []);
  const location = 'location' in data ? data.location : undefined;
  const originalLocation = row && 'location' in row.data ? row.data.location : undefined;
  const moved =
    !row ||
    location?.latitude !== originalLocation?.latitude ||
    location?.longitude !== originalLocation?.longitude;
  const numbered = !row && !!proposed && 'external_id' in data && data.external_id === proposed;
  // The proposed delivery number arrives after opening; it is not a change the person made.
  const dirty = row
    ? JSON.stringify(data) !== JSON.stringify(row.data)
    : JSON.stringify(
        numbered
          ? { ...data, external_id: (blankData.current as { external_id?: string }).external_id }
          : data,
      ) !== JSON.stringify(blankData.current);
  useEffect(() => {
    onDirtyChange?.(dirty);
    const guard = (event: BeforeUnloadEvent) => {
      if (dirty) {
        event.preventDefault();
        event.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', guard);
    return () => window.removeEventListener('beforeunload', guard);
  }, [dirty, onDirtyChange]);
  function change(path: string, value: unknown) {
    changeAll([[path, value]]);
  }
  /** Several fields in one edit, such as a chosen address and its point. */
  function changeAll(edits: [string, unknown][]) {
    setFieldErrors((errors) => ({ ...errors, ...Object.fromEntries(edits.map(([path]) => [path, ''])) }));
    setError('');
    const copy = structuredClone(data) as unknown as Record<string, unknown>;
    for (const [path, value] of edits) {
      const parts = path.split('.');
      let target = copy;
      for (const part of parts.slice(0, -1)) {
        if (!target[part]) target[part] = {};
        target = target[part] as Record<string, unknown>;
      }
      if (value === undefined) delete target[parts.at(-1)!];
      else target[parts.at(-1)!] = value;
      if (path.startsWith('location.') && path !== 'location.reviewed')
        (copy.location as Location).reviewed = false;
    }
    revision.current++;
    setAccess(null);
    setData(copy as unknown as Data);
  }
  /** The delivery or depot address, with suggestions when address lookup is on; a choice places the pin. */
  function addressInput(label: string) {
    if (!('address' in data) && kind !== 'depot') return null;
    return (
      <AddressField
        label={label}
        name="address"
        value={('address' in data && data.address) || ''}
        attribution={addressLookup}
        required={kind === 'orders'}
        maxLength={rules.address?.maxLength ?? 500}
        error={fieldErrors.address}
        onChange={(text) => change('address', text)}
        onPick={(c) => {
          changeAll([
            ['address', c.label],
            ['location', { latitude: c.latitude, longitude: c.longitude, reviewed: false }],
          ]);
          setPicking(false);
        }}
      />
    );
  }
  function get(path: string): unknown {
    return path.split('.').reduce<unknown>((v, k) => (v as Record<string, unknown> | undefined)?.[k], data);
  }
  function field(path: string, label: string, numeric = false, optional = false, hint?: string) {
    const rule = rules[path] ?? {},
      raw = get(path),
      stamp = path.endsWith('.start') || path.endsWith('.end');
    const scale = path.includes('cost.')
      ? 100
      : path.endsWith('volume_dm3') ||
          path.endsWith('length_mm') ||
          path.endsWith('height_mm') ||
          path.endsWith('width_mm') ||
          path.endsWith('max_distance_m')
        ? 1000
        : path === 'service_seconds' || path.endsWith('max_driving_seconds')
          ? 60
          : 1;
    const displayLabel = label
      .replace('Volume (dm³)', 'Volume (m³)')
      .replace('(mm)', '(m)')
      .replace('Fixed cents', 'Fixed cost (€)')
      .replace('Cents / km', 'Distance rate (€ / km)')
      .replace('Cents / hour', 'Time rate (€ / hour)')
      .replace('Service duration (seconds)', 'Service duration (minutes)')
      .replace('Maximum distance (m)', 'Maximum distance (km)')
      .replace('Maximum driving (seconds)', 'Maximum driving (minutes)')
      .replace(' (with UTC offset)', '')
      .replace(' (YYYY-MM-DD)', '');
    const value =
      raw == null || (typeof raw === 'number' && !Number.isFinite(raw))
        ? ''
        : stamp
          ? String(raw).slice(0, 19)
          : numeric
            ? String(Number(raw) / scale)
            : String(raw);
    const candidates = stamp ? parisCandidates(value) : [];
    return (
      <label key={path}>
        {t(displayLabel)}
        <input
          name={path}
          type={stamp ? 'datetime-local' : path === 'service_date' ? 'date' : numeric ? 'number' : 'text'}
          value={value}
          required={!optional}
          min={numeric && rule.minimum !== undefined ? rule.minimum / scale : undefined}
          max={numeric && rule.maximum !== undefined ? rule.maximum / scale : undefined}
          maxLength={numeric || stamp ? undefined : rule.maxLength}
          step={stamp ? 60 : numeric ? (scale === 60 ? 'any' : 1 / scale) : undefined}
          aria-invalid={!!fieldErrors[path]}
          aria-describedby={fieldErrors[path] ? `error-${path}` : undefined}
          onChange={(e) => {
            let next: unknown = e.target.value;
            if (stamp) {
              const possible = parisCandidates(e.target.value);
              next = possible.find((p) => p.endsWith(String(raw).slice(-6))) ?? possible[0] ?? e.target.value;
            } else if (numeric)
              next =
                e.target.value === ''
                  ? optional
                    ? undefined
                    : NaN
                  : scaledValue(Number(e.target.value), scale);
            change(path, next);
          }}
        />
        {stamp && value && candidates.length === 0 && (
          <span className="field-error">{t('This local time does not exist in Europe/Paris.')}</span>
        )}
        {stamp && candidates.length === 2 && (
          <>
            <span>{t('Repeated clock-change hour: choose the offset.')}</span>
            <select
              aria-label={t('{0} UTC offset', displayLabel)}
              value={String(raw).slice(-6)}
              onChange={(e) => change(path, value + e.target.value)}
            >
              <option value="+02:00">{t('Summer time (+02:00)')}</option>
              <option value="+01:00">{t('Winter time (+01:00)')}</option>
            </select>
          </>
        )}
        {hint && !fieldErrors[path] && <span className="field-hint">{hint}</span>}
        {fieldErrors[path] && (
          // The field's description (aria-describedby), kept out of its accessible name.
          <span id={`error-${path}`} className="field-error" aria-hidden="true">
            {t(fieldErrors[path])}
          </span>
        )}
      </label>
    );
  }
  function skills(path: string) {
    const values = get(path) as string[];
    // Server errors name one code (skills.2); show them at the field.
    const problem = Object.entries(fieldErrors).find(
      ([key, text]) => text && (key === path || key.startsWith(path + '.')),
    );
    // Named equipment is ticked; any other code a file or the API brings stays editable as written.
    const named = values.filter(isNamedEquipment),
      others = values.filter((code) => !isNamedEquipment(code));
    return (
      <fieldset className="equipment-field">
        <legend>{t('Equipment')}</legend>
        <div className="equipment-choices">
          {equipment.map((item) => (
            <label className="check" key={item.code}>
              <input
                type="checkbox"
                checked={values.includes(item.code)}
                onChange={(e) =>
                  change(
                    path,
                    e.target.checked ? [...values, item.code] : values.filter((code) => code !== item.code),
                  )
                }
              />
              {t(item.label)}
            </label>
          ))}
        </div>
        <label>
          {t('Other equipment codes (separate with |)')}
          <input
            name={path}
            value={others.join('|')}
            onChange={(e) => change(path, [...named, ...equipmentCodes(e.target.value)])}
            aria-invalid={!!problem}
            aria-describedby={problem ? `error-${path}` : undefined}
          />
        </label>
        {problem && (
          <span id={`error-${path}`} className="field-error">
            {t(problem[1])}
          </span>
        )}
      </fieldset>
    );
  }
  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError('');
    setFieldErrors({});
    const errors: Record<string, string> = {};
    for (const element of Array.from(e.currentTarget.elements))
      if (element instanceof HTMLInputElement && element.name && !element.validity.valid)
        errors[element.name] = invalidMessage(element);
    const window = 'time_window' in data ? data.time_window : null;
    let times = false;
    if ('usual_hours' in data && data.usual_hours && data.usual_hours.end <= data.usual_hours.start)
      errors.usual_hours = t('End must be after start.');
    if ('hours' in data) {
      for (const name of DAYS) {
        const h = data.hours[name];
        if (h && h.end <= h.start) errors[`hours.${name}`] = t('End must be after start.');
      }
      (data.exceptions ?? []).forEach((e, i) => {
        if (e.hours && e.hours.end <= e.hours.start)
          errors[`exceptions.${i}`] = t('End must be after start.');
        else if ((data.exceptions ?? []).findIndex((other) => other.date === e.date) !== i)
          errors[`exceptions.${i}`] = t('Each date can have only one exception.');
      });
    }
    if (window) {
      const prefix = 'time_window';
      for (const key of ['start', 'end'] as const)
        if (!errors[`${prefix}.${key}`] && !parisCandidates(window[key].slice(0, 19)).includes(window[key])) {
          errors[`${prefix}.${key}`] = t('Choose a valid Europe/Paris time and offset.');
          times = true;
        }
      if (!errors[`${prefix}.end`] && Date.parse(window.end) <= Date.parse(window.start)) {
        errors[`${prefix}.end`] = t('End must be after start.');
        times = true;
      }
    }
    const skillPath = 'skills' in data ? 'skills' : 'required_skills' in data ? 'required_skills' : null;
    // Empty and repeated codes are dropped; anything else the API would refuse is named.
    const codes = skillPath ? [...new Set((get(skillPath) as string[]).filter(Boolean))] : [];
    if (skillPath) {
      const bad = badEquipmentCode(codes),
        most = rules[skillPath]?.maxItems ?? 30;
      if (bad !== undefined)
        errors[skillPath] = t(
          'Equipment codes start with a capital letter and use only A–Z, 0–9 and _: {0}',
          bad,
        );
      else if (codes.length > most) errors[skillPath] = t('Use at most {0} items.', most);
    }
    const names = Object.keys(errors);
    if (names.length) {
      setFieldErrors(errors);
      if (kind === 'orders') setPanel(panelOf(names[0]));
      setError(
        times && names.every((name) => name.includes('window'))
          ? t('Check the highlighted service or availability times. Your edits remain here.')
          : t('Correct the highlighted fields. Your edits remain here.'),
      );
      return;
    }
    setBusy(true);
    try {
      const result = await save<Row>(
        kind === 'depot'
          ? `depots${row ? '/' + row.id : ''}`
          : `${kind}${row ? '/' + row.id : numbered ? '?assign_identifier=true' : ''}`,
        skillPath ? ({ ...data, [skillPath]: codes } as Data) : data,
        row?.version,
      );
      if (mounted.current) onSaved(kind, result, panel === 'location' ? 'location' : undefined);
    } catch (e) {
      if (mounted.current) {
        setError(e instanceof Error ? e.message : t('Save failed. Your edits remain here.'));
        if (e instanceof ApiError) setFieldErrors(e.fields);
      }
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  async function remove() {
    if (kind !== 'orders' || !row || !onDeleted) return;
    const order = row as Order;
    const confirmed = await ask(t('Delete delivery {0}?', order.data.external_id), {
      detail: t(
        'It is removed for good. Saved plans keep their own copy and will ask for a fresh calculation. To keep the delivery for later, untick Active instead.',
      ),
      confirm: t('Delete delivery'),
      cancel: t('Keep delivery'),
    });
    if (!confirmed || !mounted.current) return;
    setBusy(true);
    setError('');
    try {
      await deleteOrders([order]);
      if (mounted.current) onDeleted(order);
    } catch (e) {
      if (mounted.current) setError(e instanceof Error ? e.message : t('Could not delete the delivery.'));
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  async function check() {
    if (!row || !vehicleId || dirty) return;
    setBusy(true);
    setError('');
    const version = revision.current;
    try {
      const result = await api<AccessCheck>(`orders/${row.id}/access-check?vehicle_id=${vehicleId}`, {
        signal: AbortSignal.timeout(12000),
      });
      if (version === revision.current) {
        if (result.order_version !== row.version)
          throw new Error(t('Order changed. Reload before reviewing access.'));
        setAccess(result);
      }
    } catch (e) {
      if (version === revision.current) setError(e instanceof Error ? e.message : t('Road check failed.'));
    } finally {
      setBusy(false);
    }
  }
  const snapped = access
    ? [access.outbound?.geometry.at(-1), access.inbound?.geometry[0]].filter(
        (p): p is [number, number] => !!p,
      )
    : undefined;
  return (
    <section
      ref={dialog}
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      className={`editor editor-${kind} ${panel === 'location' ? 'location-editor' : ''}`}
      aria-label={t('Record editor')}
      onKeyDown={(e) => {
        if (e.key === 'Escape' && !busy) {
          e.stopPropagation();
          onClose();
        }
        if (e.key === 'Tab') {
          const fields = Array.from(
            dialog.current?.querySelectorAll<HTMLElement>(
              'button:not(:disabled),input:not(:disabled),select:not(:disabled),summary',
            ) ?? [],
          ).filter((el) => el.getClientRects().length);
          if (!fields.length) return;
          const first = fields[0],
            last = fields[fields.length - 1];
          if (e.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) {
            e.preventDefault();
            last.focus();
          } else if (!e.shiftKey && document.activeElement === last) {
            e.preventDefault();
            first.focus();
          }
        }
      }}
    >
      <div className="section-heading">
        <div>
          <h2>
            {panel === 'location'
              ? t('Review delivery entrance')
              : // Whole phrases: languages put the article and the verb differently ("Ajouter un lieu").
                {
                  orders: row ? t('Edit delivery') : t('Add delivery'),
                  vehicles: row ? t('Edit vehicle') : t('Add vehicle'),
                  customers: row ? t('Edit customer') : t('Add customer'),
                  depot:
                    placeKind === 'PLACE'
                      ? row
                        ? t('Edit place')
                        : t('Add place')
                      : row
                        ? t('Edit depot')
                        : t('Add depot'),
                }[kind]}
            {row && ('external_id' in row.data || 'fleet_number' in row.data || 'code' in row.data) && (
              <>
                {' · '}
                <span className="record-id">
                  {'external_id' in row.data
                    ? row.data.external_id
                    : 'fleet_number' in row.data
                      ? row.data.fleet_number
                      : 'code' in row.data
                        ? row.data.code
                        : ''}
                </span>
              </>
            )}
          </h2>
          <p className="help">
            {kind === 'vehicles'
              ? t('Saved vehicle specifications · Paris time and euros')
              : kind === 'customers'
                ? t('Saved customer · its deliveries reuse this reviewed entrance')
                : `${serviceDay(day)} · ${t('Paris time')}`}
          </p>
        </div>
        <div className="editor-header-actions">
          {kind === 'orders' && panel === 'location' && (
            <button className="secondary back-to-details" disabled={busy} onClick={() => setPanel('details')}>
              {t('Back to details')}
            </button>
          )}
          <button
            className="editor-close"
            disabled={busy}
            onClick={onClose}
            aria-label={t('Close editor')}
            title={t('Close editor')}
          >
            <Icon name="close" size={20} />
          </button>
        </div>
      </div>
      {kind === 'orders' && (
        <nav className="tabs editor-tabs" aria-label={t('Delivery fields')}>
          {['details', 'location', 'quantities', 'requirements', 'instructions'].map((tab) => (
            <button key={tab} aria-pressed={panel === tab} onClick={() => setPanel(tab)}>
              {t(tab[0].toUpperCase() + tab.slice(1))}
            </button>
          ))}
        </nav>
      )}
      {error && (
        <div ref={errorNotice} tabIndex={-1} className="error" role="alert">
          <strong>
            {Object.values(fieldErrors).filter(Boolean).length
              ? plural(
                  Object.values(fieldErrors).filter(Boolean).length,
                  '{0} field needs attention',
                  '{0} fields need attention',
                )
              : t('Request could not be completed')}
          </strong>
          <p>{error}</p>
        </div>
      )}
      <div
        className={
          kind === 'depot' || kind === 'customers' || panel === 'location' ? 'editor-grid entrance-grid' : ''
        }
      >
        <form id="source-form" noValidate onSubmit={(e) => void submit(e)}>
          <fieldset disabled={busy}>
            {kind === 'vehicles' && (
              <>
                <div className="vehicle-identity fields">
                  {field('fleet_number', 'Fleet number')}
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={'active' in data && data.active}
                      onChange={(e) => change('active', e.target.checked)}
                    />
                    {t('Active')}
                  </label>
                </div>
                <div className="vehicle-form-grid">
                  <section className="form-card">
                    <h3>
                      {t('Payload capacities')} <small>{t('All five dimensions')}</small>
                    </h3>
                    <div className="fields">
                      {amounts.map(([key, label]) => field(`capacity.${key}`, label, true))}
                    </div>
                    <p className="help">
                      {t('Payload is the load carried. It is not the vehicle’s gross routing weight.')}
                    </p>
                  </section>
                  <section className="form-card">
                    <h3>{t('Road-routing constraints')}</h3>
                    <div className="fields three-fields">
                      {truck.map(([key, label]) => field(`routing.${key}`, label, true))}
                    </div>
                    <p className="info">
                      {t('Truck profile · all dimensions and axle constraints are required.')}
                    </p>
                  </section>
                  <section className="form-card">
                    <h3>{t('Working hours and equipment')}</h3>
                    {'hours' in data && (
                      <WorkingHours
                        hours={data.hours}
                        exceptions={data.exceptions ?? []}
                        day={day}
                        errors={fieldErrors}
                        onHours={(hours) => change('hours', hours)}
                        onExceptions={(exceptions) => change('exceptions', exceptions)}
                      />
                    )}
                    {skills('skills')}
                    <p className="help">
                      {t(
                        'Paris local times. A day off or other hours on one date replace the weekly hours that day. Overnight work is unsupported.',
                      )}
                    </p>
                  </section>
                  <section className="form-card vehicle-places">
                    <h3>{t('Start and end of the day')}</h3>
                    <div className="fields">
                      <label>
                        {t('Starts at')}
                        <select
                          value={('start' in data && data.start) || ''}
                          onChange={(e) => change('start', e.target.value || null)}
                        >
                          {places
                            .filter((p, i) => i === 0 || !p.main)
                            .map((p, i) => (
                              <option key={p.id} value={i === 0 ? '' : p.data.code}>
                                {i === 0 ? t('Main depot · {0}', placeLabel(p)) : placeLabel(p)}
                              </option>
                            ))}
                          {'start' in data &&
                            data.start &&
                            !places.some((p) => p.data.code === data.start) && (
                              <option value={data.start}>{data.start}</option>
                            )}
                        </select>
                      </label>
                      <label>
                        {t('Ends at')}
                        <select
                          value={('end' in data && data.end) || ''}
                          onChange={(e) => change('end', e.target.value || null)}
                        >
                          <option value="">{t('Where it started')}</option>
                          {places.map((p) => (
                            <option key={p.id} value={p.data.code}>
                              {placeLabel(p)}
                            </option>
                          ))}
                          {'end' in data && data.end && !places.some((p) => p.data.code === data.end) && (
                            <option value={data.end}>{data.end}</option>
                          )}
                        </select>
                      </label>
                    </div>
                    <p className="help">
                      {t(
                        'The vehicle leaves loaded from where it starts. Deliveries that ship from a depot go only on vehicles that start there.',
                      )}
                    </p>
                    {onPlaceAdded && addressLookup && (
                      <AddPlace
                        attribution={addressLookup}
                        onAdded={(place) => {
                          onPlaceAdded(place);
                          change('start', place.data.code);
                        }}
                      />
                    )}
                  </section>
                  <section className="form-card">
                    <h3>{t('Operating rates and route limits')}</h3>
                    <div className="fields three-fields">
                      {field('cost.per_km_minor', 'Cents / km', true)}
                      {field('cost.per_hour_minor', 'Cents / hour', true)}
                      {field('cost.fixed_minor', 'Fixed cents', true)}
                    </div>
                    <div className="fields three-fields">
                      {field('limits.max_distance_m', 'Maximum distance (m)', true, true)}
                      {field('limits.max_driving_seconds', 'Maximum driving (seconds)', true, true)}
                      {field('limits.max_stops', 'Maximum stops', true, true)}
                    </div>
                    <p className="help">
                      {t('Optional blank limits mean no additional limit. Modeled cost excludes tolls.')}
                    </p>
                  </section>
                </div>
              </>
            )}
            {kind === 'orders' && (
              <>
                <div hidden={panel !== 'details'}>
                  <div className="fields">
                    {field(
                      'external_id',
                      'Delivery identifier',
                      false,
                      false,
                      numbered
                        ? t('Next delivery number. The final number is given when you save.')
                        : undefined,
                    )}
                    {field('service_date', 'Service date (YYYY-MM-DD)')}
                  </div>
                  <CustomerField
                    value={'customer' in data ? (data.customer ?? null) : null}
                    disabled={busy}
                    onClear={() => change('customer', null)}
                    onPick={(c) => changeAll(fromCustomer(c, data as OrderInput))}
                  />
                  {addressInput('Delivery address')}
                  <p className="location-line">
                    {!location
                      ? t('No pin yet. Choose a suggested address or place the pin on the map.')
                      : location.reviewed && 'customer' in data && data.customer
                        ? t('Reviewed entrance of customer {0}.', data.customer)
                        : location.reviewed
                          ? t('Vehicle entrance reviewed on the map.')
                          : t('Pin placed. Review the vehicle entrance on the map before planning.')}{' '}
                    <button type="button" className="text-button" onClick={() => setPanel('location')}>
                      {location?.reviewed ? t('Show on map') : t('Review on map')}
                    </button>
                  </p>
                  {!addressLookup && (
                    <p className="field-hint">
                      {t(
                        'Address search is off for this workspace. An administrator can turn it on in Settings.',
                      )}
                    </p>
                  )}
                  <div className="fields service-fields">
                    {field('time_window.start', 'Service from (with UTC offset)')}
                    {field('time_window.end', 'Service until (with UTC offset)')}
                    {field('service_seconds', 'Service duration (seconds)', true)}
                  </div>
                </div>
                <section
                  hidden={panel !== 'details' && panel !== 'quantities'}
                  className="form-card delivery-load"
                >
                  <h3>
                    {t('Quantities')} <small>{t('All five dimensions')}</small>
                  </h3>
                  <div className="fields quantity-fields">
                    {amounts.map(([key, label]) => field(`demand.${key}`, label, true))}
                  </div>
                </section>
                <div hidden={panel !== 'details' && panel !== 'requirements'}>
                  <div className="fields">
                    {field('priority', 'Priority (0–100)', true)}
                    {skills('required_skills')}
                  </div>
                  {(places.filter((p) => p.data.kind !== 'PLACE').length > 1 ||
                    ('ships_from' in data && data.ships_from)) && (
                    <div className="ships-from">
                      <label>
                        {t('Ships from')}
                        <select
                          value={('ships_from' in data && data.ships_from) || ''}
                          onChange={(e) => change('ships_from', e.target.value || null)}
                        >
                          <option value="">{t('Any depot')}</option>
                          {places
                            .filter((p) => p.data.kind !== 'PLACE')
                            .map((p) => (
                              <option key={p.id} value={p.data.code}>
                                {placeLabel(p)}
                              </option>
                            ))}
                        </select>
                      </label>
                      <small className="field-hint">
                        {t('Only vehicles that start at this depot carry the delivery.')}
                      </small>
                    </div>
                  )}
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={'active' in data && data.active}
                      onChange={(e) => change('active', e.target.checked)}
                    />
                    {t('Active')}
                  </label>
                </div>
                <div hidden={panel !== 'details' && panel !== 'instructions'}>
                  {field('instructions', 'Delivery instructions', false, true)}
                </div>
              </>
            )}
            {kind === 'depot' && (
              <>
                {field('code', placeKind === 'PLACE' ? 'Place code' : 'Depot code')}
                {field('name', placeKind === 'PLACE' ? 'Place name' : 'Depot name')}
                <label>
                  {t('Kind')}
                  <select
                    value={placeKind}
                    disabled={!!row && 'main' in row && row.main}
                    onChange={(e) => change('kind', e.target.value)}
                  >
                    <option value="DEPOT">{t('Depot · goods are loaded here')}</option>
                    <option value="PLACE">{t('Other place · such as a driver’s home')}</option>
                  </select>
                </label>
                <p className="field-hint">
                  {row && 'main' in row && row.main
                    ? t('The main depot: vehicles without another start begin and end their day here.')
                    : placeKind === 'PLACE'
                      ? t(
                          'A vehicle starting here leaves already loaded. Deliveries cannot ship from a place.',
                        )
                      : t('Vehicles can start here, and deliveries can ship from here.')}
                </p>
                <p>{t('Timezone')}: Europe/Paris</p>
              </>
            )}
            {kind === 'customers' && 'usual_hours' in data && (
              <>
                <div className="fields">
                  {field('code', 'Customer code')}
                  {field('name', 'Customer name')}
                </div>
                <fieldset className="usual-hours">
                  <legend>{t('Usual delivery hours')}</legend>
                  <label className="check">
                    <input
                      type="checkbox"
                      checked={!!data.usual_hours}
                      onChange={(e) =>
                        change('usual_hours', e.target.checked ? { start: '08:00', end: '12:00' } : null)
                      }
                    />
                    {t('Fill new deliveries with usual hours')}
                  </label>
                  {data.usual_hours && (
                    <span className="hours-times">
                      <input
                        type="time"
                        step={60}
                        required
                        aria-label={t('Usual hours start')}
                        value={data.usual_hours.start}
                        onChange={(e) =>
                          change('usual_hours', { ...data.usual_hours!, start: e.target.value })
                        }
                      />
                      <span aria-hidden="true">–</span>
                      <input
                        type="time"
                        step={60}
                        required
                        aria-label={t('Usual hours end')}
                        value={data.usual_hours.end}
                        onChange={(e) => change('usual_hours', { ...data.usual_hours!, end: e.target.value })}
                      />
                    </span>
                  )}
                  {fieldErrors.usual_hours && (
                    <span className="field-error">{t(fieldErrors.usual_hours)}</span>
                  )}
                </fieldset>
                {field('service_seconds', 'Service duration (seconds)', true, true)}
                {skills('required_skills')}
                {field('instructions', 'Delivery instructions', false, true)}
                <label className="check">
                  <input
                    type="checkbox"
                    checked={data.active}
                    onChange={(e) => change('active', e.target.checked)}
                  />
                  {t('Active')}
                </label>
              </>
            )}
            {kind !== 'vehicles' && (
              <div hidden={kind === 'orders' && panel !== 'location'}>
                <h3>{t('Vehicle entrance')}</h3>
                {kind === 'orders' ? (
                  addressLookup ? (
                    addressInput('Delivery address')
                  ) : (
                    <p className="help">{'address' in data && data.address}</p>
                  )
                ) : (
                  <>
                    {addressInput(
                      kind === 'customers'
                        ? 'Customer address'
                        : placeKind === 'PLACE'
                          ? 'Place address'
                          : 'Depot address',
                    )}
                    {!addressLookup && (
                      <p className="field-hint">
                        {t(
                          'Address search is off for this workspace. An administrator can turn it on in Settings.',
                        )}
                      </p>
                    )}
                  </>
                )}
                <p className="help">
                  {t(
                    'A found address points at the building, not always at the vehicle entrance: move the pin to the entrance, then review it.',
                  )}
                </p>
                <details
                  className="coordinates"
                  open={Object.entries(fieldErrors).some(
                    ([key, text]) => text && key.startsWith('location.'),
                  )}
                >
                  <summary>{t('Enter coordinates')}</summary>
                  <div className="fields">
                    {(['latitude', 'longitude'] as const).map((key) => (
                      <label key={key}>
                        {key === 'latitude' ? t('Latitude') : t('Longitude')}
                        <input
                          name={`location.${key}`}
                          type="number"
                          step="any"
                          min={rules[`location.${key}`]?.minimum}
                          max={rules[`location.${key}`]?.maximum}
                          value={location && Number.isFinite(location[key]) ? location[key] : ''}
                          required={kind === 'depot' || !!location}
                          onChange={(e) =>
                            change(`location.${key}`, e.target.value === '' ? NaN : Number(e.target.value))
                          }
                        />
                      </label>
                    ))}
                  </div>
                </details>
                <div className="actions">
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => {
                      setPicking(!picking);
                      if (!picking)
                        mapArea.current?.scrollIntoView?.({ behavior: 'smooth', block: 'center' });
                    }}
                  >
                    {picking ? t('Stop placing pin') : t('Pick on map')}
                  </button>
                  {/* Only a point that exists can be removed; a reviewed one loses its review with it. */}
                  {(kind === 'orders' || kind === 'customers') && location && (
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => {
                        change('location', null);
                        setPicking(false);
                      }}
                    >
                      {location.reviewed ? t('Remove the reviewed point') : t('Remove the point')}
                    </button>
                  )}
                </div>
                <div className={`entrance-status ${location?.reviewed ? 'success' : 'notice'}`}>
                  <Icon name={location?.reviewed ? 'check' : 'alert'} />
                  <div>
                    <strong>{location?.reviewed ? t('Entrance reviewed') : t('Needs review')}</strong>
                    <p>{t('Inspect the saved vehicle entrance. Moving the pin resets its review.')}</p>
                  </div>
                </div>
                <label className="check">
                  <input
                    type="checkbox"
                    disabled={!location || moved}
                    checked={!!location?.reviewed}
                    onChange={(e) => change('location.reviewed', e.target.checked)}
                  />
                  {t('I reviewed this vehicle entrance on the map')}
                </label>
                {moved && location && <p className="help">{t('This pin needs saving before review.')}</p>}
                {kind === 'orders' &&
                  row &&
                  !dirty &&
                  location?.reviewed &&
                  !('customer' in row.data && row.data.customer) && (
                    <SaveAsCustomer order={row as Order} onSaved={(order) => onSaved('orders', order)} />
                  )}
              </div>
            )}
            {kind === 'orders' && panel === 'location' && (
              <section className="access-check">
                <h3>{t('Check saved point against roads')}</h3>
                <label>
                  {t('Truck for access check')}
                  <select
                    value={vehicleId}
                    disabled={busy}
                    onChange={(e) => {
                      setVehicleId(e.target.value);
                      setAccess(null);
                      revision.current++;
                    }}
                  >
                    <option value="">{t('Choose a vehicle')}</option>
                    {vehicles.map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.data.fleet_number}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  type="button"
                  disabled={busy || dirty || !row || !location || !vehicleId}
                  onClick={() => void check()}
                >
                  {t('Check road access')}
                </button>
                <p className="help">
                  {t(
                    "Uses this truck's exact dimensions, weight and axles. Save changes before checking. The search radius stays at 50 m.",
                  )}
                </p>
                {access && (
                  <div
                    role="status"
                    className={`access-result ${access.status === 'REACHABLE' ? 'success' : 'notice'}`}
                  >
                    <Icon name={access.status === 'REACHABLE' ? 'check' : 'alert'} />
                    <div>
                      <strong>
                        {access.status === 'REACHABLE'
                          ? t('Road connection found')
                          : t('No round trip found')}
                      </strong>
                      <p>{access.message}</p>
                      {snapped?.map((p, i) => (
                        <small key={i}>
                          {t('Road endpoint')} {i + 1}: {p[1].toFixed(6)}, {p[0].toFixed(6)}
                        </small>
                      ))}
                    </div>
                  </div>
                )}
              </section>
            )}
            <div className="editor-save">
              <span>{dirty ? t('Unsaved changes') : row ? t('Saved record') : t('Not saved yet')}</span>
              {kind === 'orders' && row && onDeleted && panel !== 'location' && (
                <button
                  type="button"
                  className="text-button delete-record"
                  disabled={busy}
                  onClick={() => void remove()}
                >
                  {t('Delete delivery')}
                </button>
              )}
              <button disabled={busy || (!dirty && panel === 'location')} type="submit">
                {busy
                  ? t('Working…')
                  : panel === 'location'
                    ? location?.reviewed && !originalLocation?.reviewed
                      ? t('Confirm reviewed entrance')
                      : t('Save location')
                    : kind === 'vehicles'
                      ? t('Save vehicle')
                      : kind === 'orders'
                        ? t('Save delivery')
                        : kind === 'customers'
                          ? t('Save customer')
                          : placeKind === 'PLACE'
                            ? t('Save place')
                            : t('Save depot')}
              </button>
            </div>
          </fieldset>
        </form>
        {(kind === 'depot' || kind === 'customers' || panel === 'location') && (
          <aside ref={mapArea}>
            <MapPanel
              style={style}
              points={points.filter((p) => p.id === row?.id)}
              selected={row?.id}
              draft={location}
              snapped={snapped}
              picking={picking && !busy}
              onPick={(point) => {
                change('location', point);
                setPicking(false);
              }}
              onSelect={(id) => {
                if (!busy) onSelect(id);
              }}
            />
          </aside>
        )}
      </div>
    </section>
  );
}
