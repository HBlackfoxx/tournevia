import { plural, t, useLanguage } from './i18n';
import { Fragment, useState } from 'react';
import { api, ApiError, type ImportResult, type OrderInput, type VehicleSource } from './api';
import { weekSummary } from './availability';
import { Icon } from './Brand';
import { equipmentList } from './equipment';
import columns from './csv-columns.json';
import { formatNumber } from './format';

function PreviewValues({ data }: { data: OrderInput | VehicleSource }) {
  useLanguage();
  const vehicle = 'fleet_number' in data;
  const identifier = vehicle ? data.fleet_number : data.external_id;
  const amounts = vehicle ? data.capacity : data.demand;
  const fields: [string, string | number][] = [
    [t('Active'), data.active ? t('Yes') : t('No')],
    ...((vehicle
      ? [[t('Weekly hours'), weekSummary(data.hours)]]
      : [
          [t('Service window start'), data.time_window.start],
          [t('Service window end'), data.time_window.end],
        ]) as [string, string][]),
    [t('Equipment'), equipmentList(vehicle ? data.skills : data.required_skills) || t('None')],
    [vehicle ? t('Capacity — weight (kg)') : t('Load — weight (kg)'), amounts.weight_kg],
    [vehicle ? t('Capacity — volume (dm³)') : t('Load — volume (dm³)'), amounts.volume_dm3],
    [t('Euro pallets'), amounts.pallet_eur_units],
    [t('Industrial pallets'), amounts.pallet_ind_units],
    [t('Loading length (mm)'), amounts.loading_length_mm],
  ];
  if (vehicle)
    fields.push(
      [t('Truck height (mm)'), data.routing.height_mm],
      [t('Truck width (mm)'), data.routing.width_mm],
      [t('Truck length (mm)'), data.routing.length_mm],
      [t('Gross routing weight (kg)'), data.routing.gross_weight_kg],
      [t('Axle load (kg)'), data.routing.axle_load_kg],
      [t('Axles'), data.routing.axle_count],
      [t('Fixed cost (EUR cents)'), data.cost.fixed_minor],
      [t('Distance rate (EUR cents/km)'), data.cost.per_km_minor],
      [t('Time rate (EUR cents/hour)'), data.cost.per_hour_minor],
      [t('Maximum stops'), data.limits?.max_stops ?? t('No additional limit')],
      [t('Maximum distance (m)'), data.limits?.max_distance_m ?? t('No additional limit')],
      [t('Maximum driving (seconds)'), data.limits?.max_driving_seconds ?? t('No additional limit')],
    );
  else
    fields.push(
      [t('Service date'), data.service_date],
      [t('Address / entrance'), data.address],
      [t('Latitude'), data.location?.latitude ?? t('Unresolved')],
      [t('Longitude'), data.location?.longitude ?? t('Unresolved')],
      [t('Point reviewed'), data.location?.reviewed ? t('Yes') : t('No — review after import')],
      [t('Service duration (seconds)'), data.service_seconds],
      [t('Priority'), data.priority ?? 0],
      [t('Instructions'), data.instructions || t('None')],
    );
  return (
    <details>
      <summary>
        {t('Review values for')} {identifier}
      </summary>
      <dl className="preview-values">
        {fields.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

type Row = NonNullable<ImportResult['rows']>[number];
type Warning = NonNullable<Row['warnings']>[number];

function warningText(w: Warning) {
  return w.template ? t(w.template, ...(w.values ?? [])) : t(w.message);
}

/** The record fields an import row changes, as people know them. */
const FIELDS: Record<string, string> = {
  service_date: 'Service date',
  active: 'Active',
  address: 'Address / entrance',
  location: 'Point',
  time_window: 'Service window',
  service_seconds: 'Service duration',
  demand: 'Load',
  required_skills: 'Equipment codes',
  instructions: 'Instructions',
  priority: 'Priority',
  customer: 'Customer',
  routing: 'Truck dimensions and weight',
  capacity: 'Payload capacity',
  skills: 'Equipment codes',
  hours: 'Weekly hours',
  cost: 'Rates',
  limits: 'Route limits',
};
const fieldName = (name: string) => (FIELDS[name] ? t(FIELDS[name]) : name);

/** Where a delivery's point comes from: the file, the address, or nowhere yet. */
function LocationSource({ row }: { row: Row }) {
  useLanguage();
  if (!row.location_source) return null;
  return (
    <small className={`location-source source-${row.location_source.toLowerCase()}`}>
      {row.location_source === 'CUSTOMER'
        ? row.customer_match === 'ADDRESS'
          ? t(
              'Entrance of customer {0} · same address',
              row.data && 'customer' in row.data ? (row.data.customer ?? '') : '',
            )
          : t('Entrance of customer {0}', row.data && 'customer' in row.data ? (row.data.customer ?? '') : '')
        : row.location_source === 'SAVED'
          ? t('Saved point kept')
          : row.location_source === 'FILE'
            ? t('Point from the file')
            : row.location_source === 'ADDRESS'
              ? t('Found from the address · match {0} %', Math.round((row.address_match?.score ?? 0) * 100))
              : t('No point')}
    </small>
  );
}

/**
 * The file's exact columns, generated from the server's own list (scripts/export_contracts.py): the
 * header row to copy, and a template holding it to fill in a spreadsheet.
 */
function FileColumns({ kind }: { kind: 'vehicles' | 'orders' }) {
  useLanguage();
  const { required, optional } = columns[kind];
  const row = [...required, ...optional].join(',');
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(row);
      setCopied(true);
    } catch {
      // Without clipboard access the row stays selected for the browser's own copy.
      const code = document.getElementById(`header-row-${kind}`);
      if (code) {
        const range = document.createRange();
        range.selectNodeContents(code);
        window.getSelection()?.removeAllRanges();
        window.getSelection()?.addRange(range);
      }
    }
  }
  function download() {
    // A byte-order mark lets spreadsheets open the file as UTF-8, so accents survive; the import accepts it.
    const url = URL.createObjectURL(new Blob(['\ufeff' + row + '\r\n'], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = kind === 'orders' ? 'tournevia-deliveries.csv' : 'tournevia-vehicles.csv';
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return (
    <section className="import-columns" aria-labelledby={`import-columns-${kind}`}>
      <h2 id={`import-columns-${kind}`}>{t('Columns of the file')}</h2>
      <p>
        {t(
          'The first line names the columns, in this order and spelled exactly so. The columns from {0} on are optional: leave their cells empty when you do not use them.',
          optional[0],
        )}
      </p>
      <pre className="import-header-row">
        {/* Lines may break after a comma, never inside a column name. */}
        <code id={`header-row-${kind}`}>
          {required.map((name) => (
            <Fragment key={name}>
              {name},<wbr />
            </Fragment>
          ))}
          <span className="optional-columns">
            {optional.map((name, i) => (
              <Fragment key={name}>
                {name}
                {i < optional.length - 1 && (
                  <>
                    ,<wbr />
                  </>
                )}
              </Fragment>
            ))}
          </span>
        </code>
      </pre>
      <div className="import-columns-actions">
        <button type="button" className="secondary" onClick={() => void copy()}>
          {t('Copy the header row')}
        </button>
        <button type="button" className="secondary" onClick={download}>
          {t('Download the template')}
        </button>
        <span role="status" className="field-hint">
          {copied ? t('Header row copied.') : ''}
        </span>
      </div>
    </section>
  );
}

export function ImportPanel({
  kind,
  onCommit,
  onReview,
}: {
  kind: 'vehicles' | 'orders';
  onCommit: () => void;
  /** Opens the deliveries with this filter: unreviewed points, or no point at all. */
  onReview?: (filter: 'review' | 'missing') => void;
}) {
  useLanguage();
  const [file, setFile] = useState<File | null>(null);
  const [onlyToCheck, setOnlyToCheck] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [dragging, setDragging] = useState(false);
  const toCheck = result?.rows?.filter((r) => r.warnings?.length) ?? [];
  function choose(selected: File | null) {
    setResult(null);
    setError(selected && selected.size > 1048576 ? t('CSV limit is 1 MiB.') : '');
    setFile(selected && selected.size <= 1048576 ? selected : null);
  }
  // The step the import is at: choose, preview, import, then (deliveries) review their points.
  const step = !result ? 0 : !result.committed ? 1 : kind === 'orders' ? 3 : 2;
  const steps = [
    t('1 Choose file'),
    t('2 Preview'),
    t('3 Import'),
    ...(kind === 'orders' ? [t('4 Review locations')] : []),
  ];
  const count = (source: Row['location_source']) =>
    result?.rows?.filter((r) => r.location_source === source).length ?? 0;
  async function run(dry: boolean) {
    if (!file) return;
    setBusy(true);
    setError('');
    try {
      setOnlyToCheck(false);
      // Existing records may change: the preview lists what each row changes before anything is saved.
      const response = await api<ImportResult>(`imports/${kind}?dry_run=${dry}&update_existing=true`, {
        method: 'POST',
        headers: { 'Content-Type': 'text/csv' },
        body: file,
      });
      setResult(response);
      if (response.committed) onCommit();
    } catch (e) {
      setResult(e instanceof ApiError ? (e.result ?? null) : null);
      setError(e instanceof Error ? e.message : t('Import failed.'));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className={`import-flow ${result ? 'has-preview' : ''}`}>
      <ol className="import-steps">
        {steps.map((label, i) => (
          <li
            key={label}
            className={i === step ? 'current' : i < step ? 'done' : ''}
            aria-current={i === step ? 'step' : undefined}
          >
            {label}
          </li>
        ))}
      </ol>
      <div className="import-file">
        <label
          className={`import-drop ${dragging ? 'is-dragging' : ''} ${file ? 'has-file' : ''}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            if (!busy) choose(e.dataTransfer.files?.[0] ?? null);
          }}
        >
          <span className="drop-title">
            {file
              ? file.name
              : kind === 'orders'
                ? t('Drop the deliveries CSV file here')
                : t('Drop the vehicles CSV file here')}
          </span>
          <span className="drop-hint">
            {file
              ? `${t('{0} KB', formatNumber(file.size / 1024, 1))} · ${t('Click to choose another file')}`
              : t('or click to choose it · UTF-8, up to 250 rows and 1 MiB')}
          </span>
          {/* After a preview, the browser's own file button would show its language and font. */}
          <span className="choose-another" aria-hidden="true">
            {t('Choose another file')}
          </span>
          <input
            aria-label={kind === 'orders' ? t('Deliveries CSV file') : t('Vehicles CSV file')}
            type="file"
            accept=".csv,text/csv"
            disabled={busy}
            onChange={(e) => choose(e.target.files?.[0] ?? null)}
          />
        </label>
        <button disabled={!file || busy} onClick={() => void run(true)}>
          {t('Preview CSV')}
        </button>
      </div>
      <div className={result?.committed ? 'success' : result?.errors?.length ? 'notice' : 'info'}>
        <strong>{result?.committed ? t('Import complete') : t('Preview saves nothing')}</strong>
        <p>
          {result?.committed
            ? kind === 'orders'
              ? t('Review the imported entrance coordinates before calculating.')
              : t('The imported vehicles are in your fleet.')
            : result?.errors?.length
              ? t('No records have been saved. Resolve all errors before importing this file.')
              : t(
                  'Preview the parsed records, then confirm the import. A row for an existing record shows what it changes, and nothing changes until you confirm.',
                )}
        </p>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {result && (
        <div aria-live="polite">
          {kind === 'orders' && !!result.rows?.length && (
            <div className="import-location-summary">
              <strong>{t('Delivery points')}</strong>
              <span>
                {t(
                  '{0} from saved customers · {1} from the file · {2} found from the address · {3} without a point',
                  count('CUSTOMER'),
                  count('FILE'),
                  count('ADDRESS'),
                  count('NONE'),
                )}
              </span>
              {count('SAVED') > 0 && (
                <span>
                  {plural(
                    count('SAVED'),
                    '{0} existing delivery keeps its saved point',
                    '{0} existing deliveries keep their saved points',
                  )}
                </span>
              )}
              <small>
                {t(
                  'Only a saved customer’s reviewed entrance arrives reviewed. Check every other vehicle entrance on the map before planning: an address points at the building, not always at the entrance.',
                )}
              </small>
            </div>
          )}
          {result.address_lookup && (
            <p className="notice address-lookup-note">
              {result.address_lookup === 'UNAVAILABLE'
                ? t('Address lookup was unavailable, so rows without coordinates have no location.')
                : t('Address lookup ran out of time, so some rows without coordinates have no location.')}
            </p>
          )}
          <div className="import-metrics">
            {[
              // Counted from the rows: with any error the server imports nothing and reports zero.
              [t('New'), result.rows?.filter((r) => r.action === 'INSERT').length ?? 0],
              [t('Changed'), result.rows?.filter((r) => r.action === 'UPDATE').length ?? 0],
              [t('Unchanged'), result.rows?.filter((r) => r.action === 'UNCHANGED').length ?? 0],
              ...(kind === 'orders' ? [[t('Rows to check'), toCheck.length]] : []),
              [t('Rows with errors'), new Set(result.errors?.map((e) => e.row)).size],
            ].map(([title, count]) => (
              <div key={title}>
                <small>{title}</small>
                <strong>{count}</strong>
              </div>
            ))}
          </div>
          <section className="import-panel">
            <div className="section-heading">
              <h3>{t('Parsed rows')}</h3>
              {toCheck.length > 0 ? (
                <label className="check only-to-check">
                  <input
                    type="checkbox"
                    checked={onlyToCheck}
                    onChange={(e) => setOnlyToCheck(e.target.checked)}
                  />
                  {t('Only rows to check ({0})', toCheck.length)}
                </label>
              ) : (
                <small>{t('Fixed CSV format · maximum 250 rows / 1 MiB')}</small>
              )}
            </div>
            <div className="preview-scroll table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>{t('Row')}</th>
                    <th>{t('Identifier')}</th>
                    <th>{kind === 'orders' ? t('Address') : t('Fleet profile')}</th>
                    <th>{t('Service / availability window')}</th>
                    <th>{t('Weight')}</th>
                    <th>{t('Preview result')}</th>
                  </tr>
                </thead>
                <tbody>
                  {result.rows
                    ?.filter((row) => !onlyToCheck || row.warnings?.length)
                    .map((row) => {
                      const d = row.data;
                      return (
                        <Fragment key={row.row}>
                          <tr>
                            <td>{row.row}</td>
                            <td className="record-id">
                              <strong>{'fleet_number' in d ? d.fleet_number : d.external_id}</strong>
                              {row.numbered && <small className="import-tag">{t('New number')}</small>}
                            </td>
                            <td className="import-address">
                              {'address' in d ? (
                                <>
                                  {d.address}
                                  <LocationSource row={row} />
                                  {!!row.warnings?.length && (
                                    <ul className="row-warnings">
                                      {row.warnings.map((w, i) => (
                                        <li key={i}>
                                          <Icon name="alert" /> {warningText(w)}
                                        </li>
                                      ))}
                                    </ul>
                                  )}
                                </>
                              ) : (
                                t(
                                  '{0} m · {1} kg gross',
                                  d.routing.height_mm / 1000,
                                  d.routing.gross_weight_kg,
                                )
                              )}
                            </td>
                            <td>
                              {'time_window' in d
                                ? `${d.time_window.start.slice(11, 16)}–${d.time_window.end.slice(11, 16)}`
                                : weekSummary(d.hours)}
                            </td>
                            <td>{('demand' in d ? d.demand : d.capacity).weight_kg} kg</td>
                            <td>
                              <span
                                className={`badge ${row.action === 'INSERT' ? 'ready' : row.action === 'UPDATE' ? 'attention' : ''}`}
                              >
                                {row.action === 'INSERT'
                                  ? t('New')
                                  : row.action === 'UNCHANGED'
                                    ? t('Unchanged')
                                    : t('Changed')}
                              </span>
                              {row.action === 'UPDATE' && !!row.changes?.length && (
                                <small className="import-changes">
                                  {row.changes.map(fieldName).join(' · ')}
                                </small>
                              )}
                            </td>
                          </tr>
                          <tr className="preview-detail-row">
                            <td colSpan={6}>
                              <PreviewValues data={d} />
                            </td>
                          </tr>
                        </Fragment>
                      );
                    })}
                  {result.errors?.map((e, i) => (
                    <tr className="import-error-row" key={`error-${i}`}>
                      <td>{e.row}</td>
                      <td colSpan={5}>
                        <strong>{e.field}</strong>:{' '}
                        {e.template ? t(e.template, ...(e.values ?? [])) : t(e.message)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="import-finish">
              <small>
                {kind === 'orders'
                  ? t(
                      'Imported coordinates still need review. A changed delivery without coordinates keeps its saved point.',
                    )
                  : t('Changed vehicles keep their days off.')}
              </small>
              {!result.committed && (
                <button disabled={busy || !!result.errors?.length || !file} onClick={() => void run(false)}>
                  {kind === 'orders' ? t('Import deliveries') : t('Import vehicles')}
                </button>
              )}
              {result.committed && kind === 'orders' && onReview && (
                <span className="actions">
                  {count('NONE') > 0 && (
                    <button className="secondary" onClick={() => onReview('missing')}>
                      {t('Place the missing points')}
                    </button>
                  )}
                  <button onClick={() => onReview('review')}>{t('Review the imported points')}</button>
                </span>
              )}
            </div>
          </section>
        </div>
      )}
      {!result && (
        <>
          <FileColumns kind={kind} />
          <ul className="import-notes">
            {kind === 'orders' && (
              <li>
                {t(
                  'Leave external_id empty to give a row the next delivery number (Settings › Delivery numbers). An optional customer column after priority names a saved customer, whose reviewed entrance the row reuses; a row with exactly a customer’s address reuses it too. Other rows without coordinates are placed from their address when address search is on.',
                )}
              </li>
            )}
            <li>
              {kind === 'orders'
                ? t(
                    'ships_from names the code of the depot a delivery is loaded at; empty means any vehicle.',
                  )
                : t(
                    'start and end name the code of a depot or place; empty means the main depot, and an empty end means where the vehicle started.',
                  )}
            </li>
          </ul>
        </>
      )}
    </section>
  );
}
