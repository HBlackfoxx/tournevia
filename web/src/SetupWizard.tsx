import { useEffect, useRef, useState } from 'react';
import { api, save, type ExamplesAdded, type Vehicle, type VehicleSource } from './api';
import { addExamples } from './ExampleData';
import { plural, t, useLanguage } from './i18n';
import { formatNumber, kilograms, serviceDay, tonnes } from './format';
import { NumberingSettings } from './Numbering';
import { Icon } from './Brand';

type Preset = {
  id: string;
  name: string;
  prefix: string;
  routing: VehicleSource['routing'];
  capacity: VehicleSource['capacity'];
};
/** Typical French delivery vehicles: starting values that each company checks and edits. */
export const VEHICLE_PRESETS: Preset[] = [
  {
    id: 'van',
    name: 'Van 3.5 t',
    prefix: 'VUL',
    routing: {
      height_mm: 2700,
      width_mm: 2100,
      length_mm: 6200,
      gross_weight_kg: 3500,
      axle_load_kg: 2000,
      axle_count: 2,
    },
    capacity: {
      weight_kg: 1200,
      volume_dm3: 13000,
      pallet_eur_units: 5,
      pallet_ind_units: 2,
      loading_length_mm: 3000,
    },
  },
  {
    id: 'rigid-7',
    name: 'Rigid truck 7.5 t',
    prefix: 'PL',
    routing: {
      height_mm: 3300,
      width_mm: 2550,
      length_mm: 7200,
      gross_weight_kg: 7500,
      axle_load_kg: 5000,
      axle_count: 2,
    },
    capacity: {
      weight_kg: 2800,
      volume_dm3: 30000,
      pallet_eur_units: 12,
      pallet_ind_units: 10,
      loading_length_mm: 5000,
    },
  },
  {
    id: 'rigid-19',
    name: 'Rigid truck 19 t',
    prefix: 'PL',
    routing: {
      height_mm: 3600,
      width_mm: 2550,
      length_mm: 9600,
      gross_weight_kg: 19000,
      axle_load_kg: 11500,
      axle_count: 2,
    },
    capacity: {
      weight_kg: 9500,
      volume_dm3: 42000,
      pallet_eur_units: 18,
      pallet_ind_units: 14,
      loading_length_mm: 7200,
    },
  },
  {
    id: 'articulated',
    name: 'Articulated truck 44 t',
    prefix: 'SEMI',
    routing: {
      height_mm: 4000,
      width_mm: 2550,
      length_mm: 16500,
      gross_weight_kg: 44000,
      axle_load_kg: 11500,
      axle_count: 5,
    },
    capacity: {
      weight_kg: 25000,
      volume_dm3: 90000,
      pallet_eur_units: 33,
      pallet_ind_units: 26,
      loading_length_mm: 13600,
    },
  },
];
const WORKDAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday'] as const;

/** The next free fleet numbers with this prefix: VUL-01, VUL-02… skipping numbers already used. */
export function fleetNumbers(prefix: string, count: number, used: Set<string>): string[] {
  const numbers: string[] = [];
  for (let n = 1; numbers.length < count; n++) {
    const number = `${prefix.trim()}-${String(n).padStart(2, '0')}`;
    if (!used.has(number)) {
      numbers.push(number);
      used.add(number);
    }
  }
  return numbers;
}

/** A saved vehicle from a preset, working Monday to Friday between `from` and `to`. */
export function presetVehicle(preset: Preset, fleet_number: string, from: string, to: string): VehicleSource {
  return {
    fleet_number,
    active: true,
    routing: { ...preset.routing },
    capacity: { ...preset.capacity },
    skills: [],
    hours: {
      ...Object.fromEntries(WORKDAYS.map((day) => [day, { start: from, end: to }])),
      saturday: null,
      sunday: null,
    },
    exceptions: [],
    cost: { currency: 'EUR', fixed_minor: 0, per_km_minor: 0, per_hour_minor: 0 },
    limits: {},
  } as VehicleSource;
}

type Row = { count: number; prefix: string; preset: Preset };
const steps = ['Vehicles', 'Delivery numbers', 'Deliveries'];
const adjustable = [
  ['routing', 'height_mm', 'Height (mm)'],
  ['routing', 'gross_weight_kg', 'Gross routing weight (kg)'],
  ['capacity', 'weight_kg', 'Payload (kg)'],
  ['capacity', 'pallet_eur_units', 'Euro pallets'],
] as const;

/** After first-use setup, a short guide through the vehicles, delivery numbers and first deliveries;
 * each step can be skipped, and the Planning checklist keeps what is left. */
export function SetupWizard({
  day,
  onClose,
  onVehiclesAdded,
  onImport,
  onAddDelivery,
  onExamplesAdded,
}: {
  /** The planning day, for example deliveries. */
  day: string;
  onClose: (finished: boolean) => void;
  onVehiclesAdded: () => void;
  onImport: (kind: 'vehicles' | 'orders') => void;
  onAddDelivery: () => void;
  onExamplesAdded: (added: ExamplesAdded) => void;
}) {
  useLanguage();
  const [step, setStep] = useState(0);
  const [rows, setRows] = useState<Row[]>(() =>
    VEHICLE_PRESETS.map((preset) => ({ count: 0, prefix: preset.prefix, preset })),
  );
  const [from, setFrom] = useState('08:00'),
    [to, setTo] = useState('17:00');
  const [busy, setBusy] = useState(false),
    [progress, setProgress] = useState(''),
    [error, setError] = useState(''),
    [added, setAdded] = useState<string[]>([]);
  const heading = useRef<HTMLHeadingElement>(null);
  const dialog = useRef<HTMLElement>(null);
  useEffect(() => heading.current?.focus(), [step]);
  const total = rows.reduce((n, r) => n + r.count, 0);
  // The numbers each row will receive, continuing across rows that share a prefix.
  const previews = (() => {
    const used = new Set<string>();
    return rows.map((row) => {
      const shown = fleetNumbers(row.prefix, Math.max(row.count, 1), new Set(used));
      fleetNumbers(row.prefix, row.count, used);
      return shown.length > 1 ? `${shown[0]} – ${shown[shown.length - 1]}` : shown[0];
    });
  })();
  const change = (index: number, next: Partial<Row>) =>
    setRows((all) => all.map((row, i) => (i === index ? { ...row, ...next } : row)));
  const adjust = (index: number, group: 'routing' | 'capacity', key: string, value: number) =>
    setRows((all) =>
      all.map((row, i) =>
        i === index
          ? { ...row, preset: { ...row.preset, [group]: { ...row.preset[group], [key]: value } } }
          : row,
      ),
    );
  async function addVehicles() {
    setBusy(true);
    setError('');
    const created: string[] = [];
    const left = rows.map((r) => r.count);
    try {
      const used = new Set((await api<Vehicle[]>('vehicles?limit=250')).map((v) => v.data.fleet_number));
      const planned = rows.map((r) => fleetNumbers(r.prefix, r.count, used));
      for (const [index, numbers] of planned.entries())
        for (const number of numbers) {
          setProgress(t('Adding {0} of {1}…', created.length + 1, total));
          await save<Vehicle>('vehicles', presetVehicle(rows[index].preset, number, from, to));
          created.push(number);
          left[index] -= 1;
        }
      setAdded((all) => [...all, ...created]);
      setRows((all) => all.map((row) => ({ ...row, count: 0 })));
      onVehiclesAdded();
      setStep(1);
    } catch (e) {
      // What was added stays; the counts keep only what is still to add.
      setAdded((all) => [...all, ...created]);
      setRows((all) => all.map((row, i) => ({ ...row, count: left[i] })));
      if (created.length) onVehiclesAdded();
      setError(e instanceof Error ? e.message : t('Request could not be completed'));
    } finally {
      setBusy(false);
      setProgress('');
    }
  }
  return (
    <div className="setup-wizard-overlay">
      <section
        ref={dialog}
        className="setup-wizard"
        role="dialog"
        aria-modal="true"
        aria-labelledby="setup-wizard-title"
        onKeyDown={(e) => {
          if (e.key === 'Escape' && !busy) onClose(false);
          // A modal guide keeps Tab inside it: from its last control back to the first, and back.
          if (e.key !== 'Tab' || !dialog.current) return;
          const fields = Array.from(
            dialog.current.querySelectorAll<HTMLElement>(
              'a[href],button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),summary',
            ),
          ).filter((el) => el.getClientRects().length);
          if (!fields.length) return;
          const first = fields[0],
            last = fields[fields.length - 1],
            inside = fields.includes(document.activeElement as HTMLElement);
          if (e.shiftKey && (document.activeElement === first || !inside)) {
            e.preventDefault();
            last.focus();
          } else if (!e.shiftKey && (document.activeElement === last || !inside)) {
            e.preventDefault();
            first.focus();
          }
        }}
      >
        <header className="setup-wizard-head">
          <ol className="setup-steps" aria-label={t('Setup progress')}>
            {steps.map((title, i) => (
              <li
                key={title}
                className={step > i ? 'complete' : ''}
                aria-current={step === i ? 'step' : undefined}
              >
                <span>{step > i ? <Icon name="check" /> : i + 1}</span>
                {t(title)}
              </li>
            ))}
          </ol>
          <button
            type="button"
            className="text-button setup-wizard-close"
            aria-label={t('Close the guide')}
            disabled={busy}
            onClick={() => onClose(false)}
          >
            <Icon name="close" />
          </button>
        </header>
        {step === 0 && (
          <>
            <h2 id="setup-wizard-title" ref={heading} tabIndex={-1}>
              {t('Add your vehicles')}
            </h2>
            <p className="setup-wizard-intro">
              {t(
                'Choose how many of each type. Typical values are filled in: check heights and weights against each registration document, because they decide which roads a vehicle may use. You can change any vehicle later in Fleet.',
              )}
            </p>
            <div className="preset-list">
              {rows.map((row, index) => {
                const name = t(row.preset.name);
                return (
                  <div className={`preset-row ${row.count ? 'chosen' : ''}`} key={row.preset.id}>
                    <div className="preset-name">
                      <strong>{name}</strong>
                      <small>
                        {t(
                          '{0} m high · {1} · payload {2} · {3} Euro pallets',
                          formatNumber(row.preset.routing.height_mm / 1000, 1),
                          tonnes(row.preset.routing.gross_weight_kg),
                          kilograms(row.preset.capacity.weight_kg),
                          row.preset.capacity.pallet_eur_units,
                        )}
                      </small>
                    </div>
                    <label>
                      {t('How many')}
                      <input
                        type="number"
                        min={0}
                        max={25}
                        inputMode="numeric"
                        aria-label={t('How many: {0}', name)}
                        value={row.count}
                        disabled={busy}
                        onChange={(e) =>
                          change(index, {
                            count: Math.max(0, Math.min(25, Math.floor(Number(e.target.value) || 0))),
                          })
                        }
                      />
                    </label>
                    <label>
                      {t('Fleet numbers')}
                      <input
                        maxLength={30}
                        aria-label={t('Fleet number prefix: {0}', name)}
                        value={row.prefix}
                        disabled={busy}
                        onChange={(e) => change(index, { prefix: e.target.value.toUpperCase() })}
                      />
                      <small>{row.prefix.trim() ? previews[index] : '…'}</small>
                    </label>
                    <details className="preset-adjust">
                      <summary>{t('Adjust the typical values')}</summary>
                      <div className="fields">
                        {adjustable.map(([group, key, label]) => (
                          <label key={key}>
                            {t(label)}
                            <input
                              type="number"
                              min={key === 'pallet_eur_units' ? 0 : 1}
                              step={1}
                              aria-label={`${t(label)} · ${name}`}
                              value={(row.preset[group] as Record<string, number>)[key]}
                              disabled={busy}
                              onChange={(e) =>
                                adjust(index, group, key, Math.floor(Number(e.target.value) || 0))
                              }
                            />
                          </label>
                        ))}
                      </div>
                    </details>
                  </div>
                );
              })}
            </div>
            <fieldset className="preset-hours">
              <legend>{t('Working hours, Monday to Friday')}</legend>
              <label>
                <span className="sr-only">{t('From')}</span>
                <input type="time" value={from} disabled={busy} onChange={(e) => setFrom(e.target.value)} />
              </label>
              <span aria-hidden="true">–</span>
              <label>
                <span className="sr-only">{t('To')}</span>
                <input type="time" value={to} disabled={busy} onChange={(e) => setTo(e.target.value)} />
              </label>
              <small>{t('Days off and other hours are set per vehicle in Fleet.')}</small>
            </fieldset>
            {error && (
              <p role="alert" className="error">
                {added.length > 0 && t('Added {0}.', added.join(', '))} {error}
              </p>
            )}
            <div className="setup-wizard-actions">
              <button
                type="button"
                className="text-button"
                disabled={busy}
                onClick={() => onImport('vehicles')}
              >
                {t('Import a vehicle file instead')}
              </button>
              <span>
                <button type="button" className="secondary" disabled={busy} onClick={() => setStep(1)}>
                  {t('Skip for now')}
                </button>
                <button
                  type="button"
                  disabled={
                    busy ||
                    !total ||
                    !from ||
                    !to ||
                    from >= to ||
                    rows.some((r) => r.count && !r.prefix.trim())
                  }
                  onClick={() => void addVehicles()}
                >
                  {busy ? progress : plural(total, 'Add {0} vehicle', 'Add {0} vehicles')}
                </button>
              </span>
            </div>
          </>
        )}
        {step === 1 && (
          <>
            <h2 id="setup-wizard-title" ref={heading} tabIndex={-1}>
              {t('Delivery numbers')}
            </h2>
            {added.length > 0 && (
              <p role="status" className="success">
                <Icon name="check" /> {t('Added {0}.', added.join(', '))}
              </p>
            )}
            <NumberingSettings titled={false} saveLabel="Save and continue" onSaved={() => setStep(2)} />
            <div className="setup-wizard-actions">
              <button type="button" className="secondary" onClick={() => setStep(0)}>
                {t('Back')}
              </button>
              <button type="button" className="secondary" onClick={() => setStep(2)}>
                {t('Skip for now')}
              </button>
            </div>
          </>
        )}
        {step === 2 && (
          <>
            <h2 id="setup-wizard-title" ref={heading} tabIndex={-1}>
              {t('Add your first deliveries')}
            </h2>
            <p className="setup-wizard-intro">
              {t(
                'Deliveries are planned by day. Import a file from your order system or spreadsheet, or add one by hand.',
              )}
            </p>
            <div className="setup-wizard-choices">
              <button type="button" className="secondary" onClick={() => onImport('orders')}>
                <strong>{t('Import a delivery file')}</strong>
                <span>{t('CSV, with a preview before anything is saved.')}</span>
              </button>
              <button type="button" className="secondary" onClick={onAddDelivery}>
                <strong>{t('Add a delivery')}</strong>
                <span>{t('Find its address and check the vehicle entrance on the map.')}</span>
              </button>
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() => {
                  setBusy(true);
                  setError('');
                  addExamples(day)
                    .then(onExamplesAdded)
                    .catch((e: unknown) =>
                      setError(e instanceof Error ? e.message : t('Request could not be completed')),
                    )
                    .finally(() => setBusy(false));
                }}
              >
                <strong>{t('Try with example deliveries')}</strong>
                <span>
                  {t(
                    'Ten fictional deliveries in Paris for {0}, with two example vehicles. Marked Example and removable in Settings.',
                    serviceDay(day),
                  )}
                </span>
              </button>
            </div>
            {error && (
              <p role="alert" className="error">
                {error}
              </p>
            )}
            <div className="setup-wizard-actions">
              <button type="button" className="secondary" onClick={() => setStep(1)}>
                {t('Back')}
              </button>
              <button type="button" className="secondary" onClick={() => onClose(true)}>
                {t('Skip for now')}
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
