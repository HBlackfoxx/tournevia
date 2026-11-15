import { useEffect, useId, useRef, useState } from 'react';
import { api, type Customer, type CustomerSummary, type Order, type OrderInput } from './api';
import { parisCandidates } from './formValues';
import { t, useLanguage } from './i18n';

type Filter = 'all' | 'review' | 'missing';
const PAGE = 50;

/** Saved customers: each one's reviewed entrance and the usual needs its deliveries reuse. */
export function Customers({ reload, onEdit }: { reload: number; onEdit: (row: Customer) => void }) {
  useLanguage();
  const [rows, setRows] = useState<Customer[]>([]),
    [summary, setSummary] = useState<CustomerSummary | null>(null),
    [filter, setFilter] = useState<Filter>('all'),
    [query, setQuery] = useState(''),
    [search, setSearch] = useState(''),
    [offset, setOffset] = useState(0),
    [busy, setBusy] = useState(true),
    [error, setError] = useState('');
  useEffect(() => {
    // The search runs once typing pauses.
    const timer = window.setTimeout(() => {
      setSearch(query.trim());
      setOffset(0);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [query]);
  useEffect(() => {
    let active = true;
    const q = search ? `&q=${encodeURIComponent(search)}` : '';
    setBusy(true);
    Promise.all([
      api<Customer[]>(`customers?limit=${PAGE}&offset=${offset}&status=${filter}${q}`),
      api<CustomerSummary>(`customers/summary${q ? `?${q.slice(1)}` : ''}`),
    ])
      .then(([list, counts]) => {
        if (!active) return;
        setRows(list);
        setSummary(counts);
        setError('');
      })
      .catch((e) => active && setError(e instanceof Error ? e.message : t('Request could not be completed')))
      .finally(() => active && setBusy(false));
    return () => {
      active = false;
    };
  }, [filter, search, offset, reload]);
  const total = summary ? (filter === 'all' ? summary.total : summary[filter]) : 0;
  return (
    <section className="customers">
      <nav className="tabs source-tabs" aria-label={t('Customer filters')}>
        {(
          [
            ['all', t('All customers {0}', summary?.total ?? '–')],
            ['review', t('Needs review {0}', summary?.review ?? '–')],
            ['missing', t('No entrance {0}', summary?.missing ?? '–')],
          ] as [Filter, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            aria-pressed={filter === key}
            onClick={() => {
              setFilter(key);
              setOffset(0);
            }}
          >
            {label}
          </button>
        ))}
      </nav>
      <div className="source-toolbar">
        <label>
          <span className="sr-only">{t('Search customers')}</span>
          <input
            type="search"
            placeholder={t('Search code, name or address')}
            value={query}
            maxLength={100}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {!busy && !error && rows.length === 0 ? (
        <div className="info">
          <strong>
            {search || filter !== 'all' ? t('No customer matches.') : t('No saved customers yet.')}
          </strong>
          <p>
            {t(
              'Save a customer once with its reviewed vehicle entrance; every later delivery for it reuses that entrance without a new review. From a delivery whose entrance you reviewed, use Save as customer.',
            )}
          </p>
        </div>
      ) : (
        <div className="table-scroll source-table">
          <table>
            <thead>
              <tr>
                <th>{t('Code')}</th>
                <th>{t('Name')}</th>
                <th>{t('Address')}</th>
                <th>{t('Entrance')}</th>
                <th>{t('Usual hours')}</th>
                <th>
                  <span className="sr-only">{t('Actions')}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const d = row.data;
                return (
                  <tr key={row.id} className={d.active ? '' : 'inactive'}>
                    <td className="record-id">
                      <strong>{d.code}</strong>
                    </td>
                    <td>
                      {d.name}
                      {!d.active && <small>{t('Inactive')}</small>}
                    </td>
                    <td>{d.address}</td>
                    <td>
                      <span
                        className={`badge location-badge ${d.location?.reviewed ? 'ready' : 'attention'}`}
                      >
                        {!d.location
                          ? t('No entrance')
                          : d.location.reviewed
                            ? t('Reviewed')
                            : t('Needs review')}
                      </span>
                    </td>
                    <td>{d.usual_hours ? `${d.usual_hours.start}–${d.usual_hours.end}` : '–'}</td>
                    <td>
                      <button className="text-button" onClick={() => onEdit(row)}>
                        {t('Edit')}
                        <span className="sr-only"> {d.code}</span>
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {total > PAGE && (
        <div className="pagination">
          <button
            className="secondary"
            disabled={busy || offset === 0}
            onClick={() => setOffset((n) => n - PAGE)}
          >
            {t('Previous 50')}
          </button>
          <span>{t('Page {0} of {1}', offset / PAGE + 1, Math.ceil(total / PAGE))}</span>
          <button
            className="secondary"
            disabled={busy || offset + PAGE >= total}
            onClick={() => setOffset((n) => n + PAGE)}
          >
            {t('Next 50')}
          </button>
        </div>
      )}
    </section>
  );
}

/** Edits that fill a delivery from a saved customer: its address, reviewed entrance and usual needs. */
export function fromCustomer(customer: Customer, order: OrderInput): [string, unknown][] {
  const d = customer.data;
  const edits: [string, unknown][] = [
    ['customer', d.code],
    ['address', d.address],
  ];
  if (d.location) edits.push(['location', { ...d.location }]);
  if (d.usual_hours) {
    // The later offset of a repeated clock-change hour, as the editor proposes.
    const at = (clock: string) => parisCandidates(`${order.service_date}T${clock}`).at(-1);
    const start = at(d.usual_hours.start),
      end = at(d.usual_hours.end);
    if (start && end) edits.push(['time_window', { start, end }]);
  }
  if (d.service_seconds !== null && d.service_seconds !== undefined)
    edits.push(['service_seconds', d.service_seconds]);
  if (d.required_skills?.length) edits.push(['required_skills', d.required_skills]);
  if (d.instructions) edits.push(['instructions', d.instructions]);
  return edits;
}

/** The delivery's customer: search saved customers, or show the linked one with a way to unlink it. */
export function CustomerField({
  value,
  disabled,
  onPick,
  onClear,
}: {
  value: string | null;
  disabled?: boolean;
  onPick: (customer: Customer) => void;
  onClear: () => void;
}) {
  useLanguage();
  const list = useId();
  const [query, setQuery] = useState(''),
    [found, setFound] = useState<Customer[]>([]),
    [active, setActive] = useState(0),
    [open, setOpen] = useState(false);
  const request = useRef(0);
  useEffect(() => {
    const text = query.trim();
    if (value || !text) {
      request.current++;
      setFound([]);
      return;
    }
    const mine = ++request.current;
    const timer = window.setTimeout(() => {
      api<Customer[]>(`customers?status=active&limit=8&q=${encodeURIComponent(text)}`)
        .then((rows) => {
          if (request.current !== mine) return;
          setFound(rows);
          setActive(0);
          setOpen(true);
        })
        .catch(() => {
          if (request.current === mine) setFound([]);
        });
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query, value]);
  if (value)
    return (
      <p className="customer-link">
        {t('Customer')}: <strong>{value}</strong>{' '}
        <button type="button" className="text-button" disabled={disabled} onClick={onClear}>
          {t('Unlink customer')}
        </button>
      </p>
    );
  const pick = (customer: Customer) => {
    setQuery('');
    setOpen(false);
    setFound([]);
    onPick(customer);
  };
  const showing = open && found.length > 0;
  return (
    <div className="address-field customer-field">
      <label>
        {t('Customer (optional)')}
        <input
          type="search"
          autoComplete="off"
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={showing}
          aria-controls={list}
          aria-activedescendant={showing ? `${list}-${active}` : undefined}
          placeholder={t('Search a saved customer')}
          value={query}
          disabled={disabled}
          onChange={(e) => setQuery(e.target.value)}
          onBlur={() => setOpen(false)}
          onKeyDown={(e) => {
            if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && found.length) {
              e.preventDefault();
              setOpen(true);
              setActive((i) => (i + (e.key === 'ArrowDown' ? 1 : -1) + found.length) % found.length);
            } else if (e.key === 'Enter' && showing) {
              e.preventDefault();
              pick(found[active]);
            } else if (e.key === 'Escape' && showing) {
              e.stopPropagation();
              setOpen(false);
            }
          }}
        />
      </label>
      {showing && (
        <ul id={list} role="listbox" className="address-suggestions" aria-label={t('Customers found')}>
          {found.map((c, i) => (
            <li
              key={c.id}
              id={`${list}-${i}`}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => e.preventDefault()}
              onMouseEnter={() => setActive(i)}
              onClick={() => pick(c)}
            >
              <strong>
                {c.data.code} · {c.data.name}
              </strong>
              <small>
                {c.data.address} ·{' '}
                {!c.data.location
                  ? t('No entrance')
                  : c.data.location.reviewed
                    ? t('Reviewed entrance')
                    : t('Entrance needs review')}
              </small>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Saves a delivery's reviewed entrance and needs as a new customer, linked to this delivery. */
export function SaveAsCustomer({ order, onSaved }: { order: Order; onSaved: (order: Order) => void }) {
  useLanguage();
  const [code, setCode] = useState(''),
    [name, setName] = useState(''),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  async function save() {
    if (!code.trim() || !name.trim()) {
      setError(t('Give the customer a code and a name.'));
      return;
    }
    setBusy(true);
    setError('');
    try {
      const saved = await api<{ customer: Customer; order: Order }>(`orders/${order.id}/customer`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'If-Match': `"${order.version}"` },
        body: JSON.stringify({ code: code.trim(), name: name.trim() }),
      });
      onSaved(saved.order);
    } catch (e) {
      setError(e instanceof Error ? e.message : t('Request could not be completed'));
    } finally {
      setBusy(false);
    }
  }
  // Inside the record form: these fields have no name, so the record's own save ignores them.
  const enter = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      void save();
    }
  };
  return (
    <details className="save-customer">
      <summary>{t('Save as customer…')}</summary>
      <p className="field-hint">
        {t(
          'Later deliveries for this customer reuse this reviewed entrance, address and needs without a new review.',
        )}
      </p>
      <div className="fields">
        <label>
          {t('Customer code')}
          <input
            value={code}
            maxLength={40}
            disabled={busy}
            onKeyDown={enter}
            onChange={(e) => setCode(e.target.value)}
          />
        </label>
        <label>
          {t('Customer name')}
          <input
            value={name}
            maxLength={120}
            disabled={busy}
            onKeyDown={enter}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
      </div>
      <button type="button" className="secondary" disabled={busy} onClick={() => void save()}>
        {busy ? t('Saving…') : t('Save customer')}
      </button>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </details>
  );
}
