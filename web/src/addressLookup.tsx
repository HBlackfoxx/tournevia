import { useEffect, useId, useRef, useState } from 'react';
import { api, ApiError, type AddressCandidate } from './api';
import { t, useLanguage } from './i18n';

const kinds: Record<AddressCandidate['kind'], string> = {
  housenumber: 'Address',
  street: 'Street',
  locality: 'Locality',
  municipality: 'Town',
};

/** An address input that suggests matching French addresses while typing, when the workspace has
 * address lookup. A choice only proposes a point: the vehicle entrance is still reviewed on the map.
 * Without address lookup it is an ordinary text field. */
export function AddressField({
  label,
  name,
  value,
  attribution,
  onChange,
  onPick,
  required = false,
  maxLength,
  error,
  search,
}: {
  label: string;
  name: string;
  value: string;
  /** Attribution of the address lookup; absent when the workspace has none. */
  attribution?: string | null;
  onChange: (text: string) => void;
  onPick: (candidate: AddressCandidate) => void;
  required?: boolean;
  maxLength?: number;
  error?: string;
  /** Another way to look an address up, e.g. during setup before anyone is signed in. */
  search?: (query: string) => Promise<AddressCandidate[]>;
}) {
  useLanguage();
  const lookup = useRef(search);
  lookup.current = search;
  const list = useId(),
    errorId = useId();
  const [results, setResults] = useState<AddressCandidate[]>([]),
    [active, setActive] = useState(-1),
    [open, setOpen] = useState(false),
    [problem, setProblem] = useState(''),
    [typed, setTyped] = useState(false);
  const request = useRef(0);
  const query = value.trim();
  useEffect(() => {
    // Only what the person types is looked up, never a saved or chosen address.
    if (!attribution || !typed || query.length < 3) {
      request.current++;
      setResults([]);
      return;
    }
    const mine = ++request.current;
    const timer = window.setTimeout(() => {
      (lookup.current
        ? lookup.current(query)
        : api<AddressCandidate[]>(`geocode?q=${encodeURIComponent(query)}`)
      )
        .then((found) => {
          if (request.current !== mine) return;
          setResults(found);
          setActive(found.length ? 0 : -1);
          setProblem(found.length ? '' : t('No address found. Place the pin on the map instead.'));
          setOpen(true);
        })
        .catch((e) => {
          if (request.current !== mine) return;
          setResults([]);
          setProblem(
            e instanceof ApiError && e.status === 503
              ? t('Address search is unavailable right now. Place the pin on the map instead.')
              : e instanceof Error
                ? e.message
                : t('Request could not be completed'),
          );
        });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [attribution, typed, query]);
  const pick = (candidate: AddressCandidate) => {
    request.current++;
    setTyped(false);
    setOpen(false);
    setResults([]);
    setProblem('');
    onPick(candidate);
  };
  const showing = open && results.length > 0;
  return (
    <div className="address-field">
      <label>
        {t(label)}
        <input
          name={name}
          type="text"
          autoComplete="off"
          value={value}
          required={required}
          maxLength={maxLength}
          role={attribution ? 'combobox' : undefined}
          aria-autocomplete={attribution ? 'list' : undefined}
          aria-expanded={attribution ? showing : undefined}
          aria-controls={attribution ? list : undefined}
          aria-activedescendant={showing && active >= 0 ? `${list}-${active}` : undefined}
          aria-invalid={!!error}
          aria-describedby={error ? errorId : undefined}
          onChange={(e) => {
            setTyped(true);
            setOpen(true);
            setProblem('');
            onChange(e.target.value);
          }}
          onBlur={() => setOpen(false)}
          onKeyDown={(e) => {
            if (!attribution) return;
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
              e.preventDefault();
              setOpen(true);
              if (results.length)
                setActive((i) => (i + (e.key === 'ArrowDown' ? 1 : -1) + results.length) % results.length);
            } else if (e.key === 'Enter' && showing) {
              // Inside a record form: Enter takes the suggestion instead of saving the record.
              e.preventDefault();
              pick(results[Math.max(active, 0)]);
            } else if (e.key === 'Escape' && showing) {
              e.stopPropagation();
              setOpen(false);
            }
          }}
        />
        {error && (
          <span id={errorId} className="field-error" aria-hidden="true">
            {t(error)}
          </span>
        )}
      </label>
      {showing && (
        <ul id={list} role="listbox" className="address-suggestions" aria-label={t('Addresses found')}>
          {results.map((c, i) => (
            <li
              key={`${c.label}-${i}`}
              id={`${list}-${i}`}
              role="option"
              aria-selected={i === active}
              // Keep the focus in the field so the choice is not lost to its blur.
              onMouseDown={(e) => e.preventDefault()}
              onMouseEnter={() => setActive(i)}
              onClick={() => pick(c)}
            >
              <strong>{c.label}</strong>
              <small>
                {t(kinds[c.kind])} · {t('match {0} %', Math.round(c.score * 100))}
              </small>
            </li>
          ))}
        </ul>
      )}
      {problem && (
        <p role="status" className="field-hint">
          {problem}
        </p>
      )}
      {attribution && <small className="address-attribution">{t('Addresses: {0}', attribution)}</small>}
    </div>
  );
}

/** The administrator's choice to look addresses up with IGN's public service. When the server sets
 * an address service for the whole installation, that one is always used: there is nothing to choose. */
export function AddressLookupSettings({
  source,
  attribution,
  onChanged,
}: {
  source?: 'installation' | 'public' | null;
  attribution?: string | null;
  onChanged: () => void;
}) {
  useLanguage();
  const [chosen, setChosen] = useState(source === 'public'),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [saved, setSaved] = useState('');
  if (source === 'installation')
    return (
      <section className="workspace address-settings">
        <h2>{t('Address search')}</h2>
        <p>
          {t(
            'Address search is set on the server for every company on this installation: {0}.',
            attribution ?? '',
          )}
        </p>
        <p className="field-hint">{t('To change it, ask whoever runs the server.')}</p>
      </section>
    );
  return (
    <section className="workspace address-settings">
      <h2>{t('Address search')}</h2>
      <p>
        {t(
          'Find delivery and depot addresses as you type, and place imported deliveries that have an address but no coordinates.',
        )}
      </p>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setError('');
          setSaved('');
          try {
            await api('workspace/address-lookup', {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ public_service: chosen }),
            });
            setSaved(chosen ? t('Address search is on.') : t('Address search is off.'));
            onChanged();
          } catch (e) {
            setError(e instanceof Error ? e.message : t('Request could not be completed'));
          } finally {
            setBusy(false);
          }
        }}
      >
        <label className="check">
          <input
            type="checkbox"
            checked={chosen}
            disabled={busy}
            onChange={(e) => {
              setChosen(e.target.checked);
              setSaved('');
            }}
          />
          {t('Use the IGN address service (France)')}
        </label>
        <p className="field-hint">
          {t(
            'Free and without an account. Addresses typed into an address field, and imported addresses without coordinates, are sent to the IGN Géoplateforme (data.geopf.fr) to find their position, with the depot’s position so that nearby addresses come first. Nothing else is sent.',
          )}
        </p>
        <button disabled={busy || chosen === (source === 'public')}>
          {busy ? t('Saving…') : t('Save address search')}
        </button>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        {saved && <p role="status">{saved}</p>}
      </form>
    </section>
  );
}
