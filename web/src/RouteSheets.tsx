import { useEffect, useState } from 'react';
import { api, type RouteSheets } from './api';
import { plural, t, useLanguage } from './i18n';
import { clock, dateTime, duration, formatNumber, kilograms, kilometres, serviceDay } from './format';
import { Brand, Icon } from './Brand';
import { equipmentList } from './equipment';

type Sheet = RouteSheets['sheets'][number];
type Load = Sheet['load'];
/** Where the route sheets page is: one READY plan at the version the dispatcher saw, optionally one vehicle. */
export type SheetRoute = { plan: string; version: number; date?: string; vehicle?: string };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export function readSheetRoute(hash: string): SheetRoute | null {
  const match = /^#\/sheets\?(.*)$/.exec(hash);
  if (!match) return null;
  const params = new URLSearchParams(match[1]);
  const plan = params.get('plan') ?? '',
    version = Number(params.get('version')),
    date = params.get('date') ?? '',
    vehicle = params.get('vehicle') ?? '';
  if (!UUID.test(plan) || !Number.isInteger(version) || version < 1) return null;
  return {
    plan,
    version,
    date: /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : undefined,
    vehicle: UUID.test(vehicle) ? vehicle : undefined,
  };
}
export function sheetHash({ plan, version, date, vehicle }: SheetRoute): string {
  const params = new URLSearchParams({ plan, version: String(version) });
  if (date) params.set('date', date);
  if (vehicle) params.set('vehicle', vehicle);
  return `#/sheets?${params}`;
}

/** Google Maps and Waze directions to a reviewed vehicle entrance, from wherever the driver is. */
export function navigationLinks(latitude: number, longitude: number) {
  return {
    google: `https://www.google.com/maps/dir/?api=1&destination=${latitude}%2C${longitude}&travelmode=driving`,
    waze: `https://waze.com/ul?ll=${latitude},${longitude}&navigate=yes`,
  };
}

/** What a vehicle or a stop carries, leaving out the dimensions it does not use. */
export function loadText(load: Load): string {
  const parts = [
    load.pallet_eur_units ? plural(load.pallet_eur_units, '{0} Euro pallet', '{0} Euro pallets') : '',
    load.pallet_ind_units
      ? plural(load.pallet_ind_units, '{0} industrial pallet', '{0} industrial pallets')
      : '',
    load.weight_kg ? kilograms(load.weight_kg) : '',
    load.volume_dm3 ? `${formatNumber(load.volume_dm3 / 1000, 1)}\u00a0m³` : '',
  ].filter(Boolean);
  return parts.join(' · ') || '—';
}

function Navigate({ latitude, longitude, name }: { latitude: number; longitude: number; name: string }) {
  const links = navigationLinks(latitude, longitude);
  return (
    <div className="sheet-nav">
      <a href={links.google} target="_blank" rel="noopener noreferrer" aria-label={`Google Maps · ${name}`}>
        Google Maps
      </a>
      <a href={links.waze} target="_blank" rel="noopener noreferrer" aria-label={`Waze · ${name}`}>
        Waze
      </a>
      <small className="sheet-point">
        {latitude.toFixed(5)}, {longitude.toFixed(5)}
      </small>
    </div>
  );
}

function SheetPage({ sheet, data }: { sheet: Sheet; data: RouteSheets }) {
  const printed = dateTime(data.generated_at);
  const same = sheet.start.code === sheet.end.code;
  return (
    <article className="route-sheet" aria-label={t('Route sheet {0}', sheet.vehicle)}>
      {data.example && (
        <p className="sheet-example" role="note">
          {t('Example data, not for driving.')}{' '}
          <span>{t('These deliveries and vehicles are fictional, added to try Tournevia.')}</span>
        </p>
      )}
      <header className="sheet-head">
        <div>
          <p className="sheet-company">{data.company}</p>
          <h2>
            {t('Route sheet')} · <span className="sheet-vehicle">{sheet.vehicle}</span>
          </h2>
          <p className="sheet-day">{serviceDay(data.service_date)}</p>
        </div>
        <p className="sheet-meta">
          {t('Ready plan · version {0}', data.plan_version)}
          <br />
          {t('Printed {0}', printed)}
        </p>
      </header>
      <dl className="sheet-summary">
        <div>
          <dt>{t('Loads and leaves')}</dt>
          <dd>
            <strong>{clock(sheet.departure)}</strong> {sheet.start.name}
            <small>{sheet.start.address}</small>
          </dd>
        </div>
        <div>
          <dt>{same ? t('Back') : t('Ends')}</dt>
          <dd>
            <strong>{clock(sheet.arrival)}</strong> {sheet.end.name}
            <small>{sheet.end.address}</small>
          </dd>
        </div>
        <div>
          <dt>{t('Stops')}</dt>
          <dd>
            <strong>{sheet.stops.length}</strong>
            <small>
              {kilometres(sheet.distance_m)} · {t('driving {0}', duration(sheet.driving_seconds))}
            </small>
          </dd>
        </div>
        <div>
          <dt>{t('Load when leaving')}</dt>
          <dd>{loadText(sheet.load)}</dd>
        </div>
      </dl>
      {/* Read before driving, and never left alone on a page of its own. */}
      <div className="sheet-notes">
        <p>
          {t(
            'Google Maps and Waze lead to the reviewed vehicle entrance but choose their own path, which may not suit this truck. Follow road signs and the order on this sheet.',
          )}
        </p>
        <p>
          {t(
            'Planning estimate. Verify current road access and driver requirements. Tolls are not included.',
          )}
        </p>
      </div>
      <table className="sheet-stops">
        <thead>
          <tr>
            <th className="num sheet-seq">#</th>
            <th className="sheet-time">{t('Time')}</th>
            <th>{t('Delivery and address')}</th>
            <th className="sheet-window">{t('Window')}</th>
            <th className="sheet-load">{t('Load')}</th>
            <th className="sheet-go">{t('Navigate')}</th>
            <th className="sheet-check">{t('Done')}</th>
          </tr>
        </thead>
        <tbody>
          {sheet.stops.map((stop) => {
            const waits = stop.service_start !== stop.arrival;
            return (
              <tr key={stop.order_id}>
                <td className="num sheet-seq">{stop.sequence}</td>
                <td className="sheet-time">
                  <strong>{clock(waits ? stop.service_start : stop.arrival)}</strong>
                  {waits && <small>{t('arrives {0}', clock(stop.arrival))}</small>}
                  <small>{t('{0} min on site', Math.round(stop.service_seconds / 60))}</small>
                </td>
                <td className="sheet-where">
                  <strong>
                    {stop.delivery}
                    {stop.customer && <span className="sheet-customer"> · {stop.customer}</span>}
                  </strong>
                  <span>{stop.address}</span>
                  {stop.instructions && <p className="sheet-instructions">{stop.instructions}</p>}
                  {stop.required_skills.length > 0 && (
                    <small>
                      {t('Required equipment:')} {equipmentList(stop.required_skills)}
                    </small>
                  )}
                </td>
                <td className="sheet-window">
                  <span className="sheet-label">{t('Window')} </span>
                  {clock(stop.time_window.start)}–{clock(stop.time_window.end)}
                </td>
                <td className="sheet-load">{loadText(stop.demand)}</td>
                <td className="sheet-go">
                  <Navigate latitude={stop.latitude} longitude={stop.longitude} name={stop.delivery} />
                </td>
                <td className="sheet-check" aria-hidden="true">
                  <span />
                </td>
              </tr>
            );
          })}
          <tr className="sheet-return">
            <td className="num sheet-seq">
              <Icon name="route" />
            </td>
            <td className="sheet-time">
              <strong>{clock(sheet.arrival)}</strong>
            </td>
            <td className="sheet-where" colSpan={3}>
              <strong>{same ? t('Back to {0}', sheet.end.name) : t('End at {0}', sheet.end.name)}</strong>
              <span>{sheet.end.address}</span>
            </td>
            <td className="sheet-go">
              <Navigate latitude={sheet.end.latitude} longitude={sheet.end.longitude} name={sheet.end.name} />
            </td>
            <td className="sheet-check" aria-hidden="true" />
          </tr>
        </tbody>
      </table>
    </article>
  );
}

/** The drivers' route sheets of one READY plan: printable, one page per vehicle, with navigation links. */
export function RouteSheetsPage({ route }: { route: SheetRoute }) {
  useLanguage();
  const [data, setData] = useState<RouteSheets | null>(null),
    [error, setError] = useState('');
  const [vehicle, setVehicle] = useState(route.vehicle ?? '');
  useEffect(() => {
    let live = true;
    setData(null);
    setError('');
    api<RouteSheets>(`plans/${route.plan}/sheets`, { headers: { 'If-Match': `"${route.version}"` } })
      .then((result) => {
        if (live) setData(result);
      })
      .catch((e: unknown) => {
        if (live) setError(e instanceof Error ? e.message : t('Route sheets are unavailable.'));
      });
    return () => {
      live = false;
    };
  }, [route.plan, route.version]);
  useEffect(() => setVehicle(route.vehicle ?? ''), [route.vehicle]);
  const date = data?.service_date ?? route.date;
  const back = `#/planning?${new URLSearchParams({ ...(date ? { date } : {}), plan: route.plan })}`;
  const shown = data?.sheets.filter((s) => !vehicle || s.vehicle_id === vehicle) ?? [];
  // The browser offers the title as the PDF file name.
  const title = data
    ? `${t('Route sheets')} · ${data.service_date}${shown.length === 1 ? ` · ${shown[0].vehicle}` : ''}`
    : t('Route sheets');
  useEffect(() => {
    const previous = document.title;
    document.title = title;
    return () => {
      document.title = previous;
    };
  }, [title]);
  return (
    <div className="sheets-page">
      <header className="sheets-toolbar">
        <Brand />
        <h1>{t('Route sheets')}</h1>
        {data && data.sheets.length > 1 && (
          <label>
            <span className="sr-only">{t('Vehicle')}</span>
            <select
              value={vehicle}
              onChange={(e) => {
                setVehicle(e.target.value);
                window.history.replaceState(
                  null,
                  '',
                  sheetHash({ ...route, date, vehicle: e.target.value || undefined }),
                );
              }}
            >
              <option value="">{t('All vehicles ({0})', data.sheets.length)}</option>
              {data.sheets.map((s) => (
                <option key={s.vehicle_id} value={s.vehicle_id}>
                  {s.vehicle}
                </option>
              ))}
            </select>
          </label>
        )}
        <span className="sheets-actions">
          <a className="sheets-back" href={back}>
            {t('Back to the plan')}
          </a>
          <button disabled={!shown.length} onClick={() => window.print()}>
            <Icon name="print" /> {t('Print')}
          </button>
        </span>
      </header>
      <main className="sheets-body">
        {error ? (
          <div className="connection-card" role="alert">
            <p>{error}</p>
            <a href={back}>{t('Back to the plan')}</a>
          </div>
        ) : !data ? (
          <p role="status" className="sheets-loading">
            {t('Loading route sheets…')}
          </p>
        ) : (
          <>
            <p className="sheets-hint">
              {t(
                'One page per vehicle. Print them, or save them as PDF to send to drivers: the navigation links stay clickable in the PDF.',
              )}
            </p>
            {shown.map((sheet) => (
              <SheetPage key={sheet.vehicle_id} sheet={sheet} data={data} />
            ))}
          </>
        )}
      </main>
    </div>
  );
}
