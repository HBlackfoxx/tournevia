import { useState } from 'react';
import { api, type AddressCandidate, type Depot, type Plan } from './api';
import { AddressField } from './addressLookup';
import { ask } from './confirm';
import { t, useLanguage } from './i18n';
import { MapPanel, type MapPoint } from './MapPanel';

/** How a depot or place is named in lists and choices. */
export const placeLabel = (place: Depot) => `${place.data.code} · ${place.data.name}`;

/** Map pins for depots (a warehouse) and other places (a flag), the main depot first. */
export const placePoints = (places: Depot[]): MapPoint[] =>
  places.map((p) => ({
    id: p.id,
    label: placeLabel(p),
    location: p.data.location,
    depot: true,
    place: p.data.kind === 'PLACE',
    code: p.data.code,
  }));

/** In a calculation: where a vehicle starts and ends its day (no code: the main depot; no end: its start). */
export function routeEnds(
  input: Plan['input_data'],
  vehicle: Plan['input_data']['vehicles'][number]['data'],
) {
  const all = [input.depot, ...(input.depots ?? [])];
  const find = (code?: string | null) => (code ? all.find((p) => p.data.code === code) : undefined);
  const start = find(vehicle.start) ?? input.depot;
  return { start, end: find(vehicle.end) ?? start };
}

/** The company's depots and other places, for administrators: add, edit, make main and delete. */
export function PlacesSettings({
  places,
  style,
  onEdit,
  onChanged,
}: {
  places: Depot[];
  style: string;
  /** Opens the editor for a depot or place; without a row, a new one of that kind. */
  onEdit: (row: Depot | null, kind: 'DEPOT' | 'PLACE') => void;
  onChanged: (notice: string) => void;
}) {
  useLanguage();
  const [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  async function act(row: Depot, action: 'main' | 'delete') {
    if (action === 'delete') {
      const confirmed = await ask(t('Delete {0}?', placeLabel(row)), {
        detail: t(
          'Vehicles and deliveries that name it must use another depot or place first. Saved plans keep their own copy.',
        ),
        confirm: t('Delete'),
        cancel: t('Keep it'),
      });
      if (!confirmed) return;
    }
    setBusy(true);
    setError('');
    try {
      await api(action === 'main' ? `depots/${row.id}/main` : `depots/${row.id}`, {
        method: action === 'main' ? 'POST' : 'DELETE',
        headers: { 'If-Match': `"${row.version}"` },
      });
      onChanged(
        action === 'main'
          ? t('{0} is now the main depot.', placeLabel(row))
          : t('{0} deleted.', placeLabel(row)),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : t('Request could not be completed'));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="workspace places">
      <div className="section-heading">
        <div>
          <h2>{t('Depots and places')}</h2>
          <p>
            {t(
              'Where vehicles start and end their day. A vehicle leaves loaded from where it starts: the main depot unless you choose another depot or a place, such as a driver’s home.',
            )}
          </p>
        </div>
        <span className="actions">
          <button className="secondary" onClick={() => onEdit(null, 'PLACE')}>
            {t('Add place')}
          </button>
          <button onClick={() => onEdit(null, 'DEPOT')}>{t('Add depot')}</button>
        </span>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      <div className="table-scroll source-table">
        <table>
          <thead>
            <tr>
              <th>{t('Code')}</th>
              <th>{t('Name')}</th>
              <th>{t('Kind')}</th>
              <th>{t('Address')}</th>
              <th>{t('Entrance')}</th>
              <th>
                <span className="sr-only">{t('Actions')}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {places.map((row) => {
              const d = row.data;
              return (
                <tr key={row.id}>
                  <td className="record-id">
                    <strong>{d.code}</strong>
                  </td>
                  <td>
                    {d.name}
                    {row.main && <small className="badge ready main-badge">{t('Main depot')}</small>}
                    {row.example && <small className="badge example-badge">{t('Example')}</small>}
                  </td>
                  <td>{d.kind === 'PLACE' ? t('Other place') : t('Depot')}</td>
                  <td>{d.address || '–'}</td>
                  <td>
                    <span className={`badge location-badge ${d.location.reviewed ? 'ready' : 'attention'}`}>
                      {d.location.reviewed ? t('Reviewed') : t('Needs review')}
                    </span>
                  </td>
                  <td className="place-actions">
                    <button className="text-button" disabled={busy} onClick={() => onEdit(row, d.kind)}>
                      {t('Edit')} <span className="sr-only">{d.code}</span>
                    </button>
                    {!row.main && !row.example && d.kind !== 'PLACE' && (
                      <button className="text-button" disabled={busy} onClick={() => void act(row, 'main')}>
                        {t('Make main')} <span className="sr-only">{d.code}</span>
                      </button>
                    )}
                    {!row.main && (
                      <button
                        className="text-button delete-record"
                        disabled={busy}
                        onClick={() => void act(row, 'delete')}
                      >
                        {t('Delete')} <span className="sr-only">{d.code}</span>
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="help">
        {t(
          'Deliveries can ship from a depot: only vehicles that start there carry them. A calculation needs the reviewed entrance of every depot and place its vehicles use.',
        )}
      </p>
      {style && places.length > 0 && (
        <div className="places-map">
          <MapPanel
            style={style}
            points={placePoints(places)}
            picking={false}
            onPick={() => {}}
            onSelect={(id) => {
              const row = places.find((p) => p.id === id);
              if (row) onEdit(row, row.data.kind);
            }}
          />
        </div>
      )}
    </section>
  );
}

/** Inside the vehicle editor: add another place, such as a driver's home, by its address. Its
 * entrance is reviewed later in Settings, before calculating. */
export function AddPlace({ attribution, onAdded }: { attribution: string; onAdded: (place: Depot) => void }) {
  useLanguage();
  const [name, setName] = useState(''),
    [code, setCode] = useState(''),
    [address, setAddress] = useState(''),
    [found, setFound] = useState<AddressCandidate | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [open, setOpen] = useState(false);
  async function save() {
    if (!code.trim() || !name.trim() || !found) {
      setError(t('Give the place a code and a name, and choose its address from the suggestions.'));
      return;
    }
    setBusy(true);
    setError('');
    try {
      const place = await api<Depot>('depots', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          code: code.trim(),
          name: name.trim(),
          kind: 'PLACE',
          address: found.label,
          location: { latitude: found.latitude, longitude: found.longitude, reviewed: false },
          timezone: 'Europe/Paris',
        }),
      });
      onAdded(place);
      setOpen(false);
      setName('');
      setCode('');
      setAddress('');
      setFound(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : t('Request could not be completed'));
    } finally {
      setBusy(false);
    }
  }
  // Inside the vehicle form: these fields have no name, so the vehicle's own save ignores them.
  return (
    <details className="add-place" open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>{t('Add another place…')}</summary>
      <div className="fields">
        <label>
          {t('Place name')}
          <input value={name} maxLength={120} disabled={busy} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          {t('Place code')}
          <input value={code} maxLength={40} disabled={busy} onChange={(e) => setCode(e.target.value)} />
        </label>
      </div>
      <AddressField
        label={t('Place address')}
        name=""
        value={address}
        attribution={attribution}
        maxLength={500}
        onChange={(text) => {
          setAddress(text);
          setFound(null);
        }}
        onPick={(candidate) => {
          setAddress(candidate.label);
          setFound(candidate);
        }}
      />
      <p className="field-hint">
        {t('Review its entrance in Settings › Depots and places before calculating.')}
      </p>
      <button type="button" className="secondary" disabled={busy} onClick={() => void save()}>
        {busy ? t('Saving…') : t('Add place')}
      </button>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </details>
  );
}
