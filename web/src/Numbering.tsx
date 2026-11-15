import { useEffect, useState } from 'react';
import { api, ApiError } from './api';
import { t, useLanguage } from './i18n';

type Numbering = { format: string | null; start: number; next: string | null; suggestion: string | null };
type DatePart = '' | '{YYYY}' | '{YYYY}{MM}' | '{YYYY}{MM}{DD}';
type Parts = { prefix: string; date: DatePart; separator: string; digits: number };

const DATES: [DatePart, string][] = [
  ['', 'No date'],
  ['{YYYY}', 'Year (2026)'],
  ['{YYYY}{MM}', 'Year and month (202611)'],
  ['{YYYY}{MM}{DD}', 'Delivery date (20261103)'],
];
// Text, then optionally a date part and its separator, then the number.
const SHAPE = /^([A-Za-z0-9 ._/-]*?)(?:(\{YYYY\}(?:\{MM\}(?:\{DD\})?)?)([-/._]?))?\{N(?::([1-9]))?\}$/;

/** A format the simple fields can show, or null for one only the API could have written. */
export function partsOf(format: string): Parts | null {
  const m = SHAPE.exec(format);
  if (!m) return null;
  return {
    prefix: m[1],
    date: (m[2] ?? '') as DatePart,
    separator: m[2] ? m[3] : '',
    digits: Number(m[4] ?? 1),
  };
}

export function formatOf(p: Parts): string {
  return `${p.prefix}${p.date}${p.date ? p.separator : ''}{N:${p.digits}}`;
}

/** What a format gives for a number on a day, as the server writes it. */
export function example(format: string, number: number, day = new Date()): string {
  const y = String(day.getFullYear()),
    m = String(day.getMonth() + 1).padStart(2, '0'),
    d = String(day.getDate()).padStart(2, '0');
  return format
    .replace('{YYYY}', y)
    .replace('{YY}', y.slice(2))
    .replace('{MM}', m)
    .replace('{DD}', d)
    .replace(/\{N(?::([1-9]))?\}/, (_, digits) => String(number).padStart(Number(digits ?? 1), '0'));
}

/** The administrator's format for new delivery identifiers. Existing identifiers never change. */
/** In the setup guide: the guide gives the heading, and saving moves on (`onSaved`). */
export function NumberingSettings({
  titled = true,
  onSaved,
  saveLabel = 'Save delivery numbers',
}: {
  titled?: boolean;
  onSaved?: () => void;
  saveLabel?: string;
}) {
  useLanguage();
  const [saved, setSaved] = useState<Numbering | null>(null),
    [on, setOn] = useState(false),
    [parts, setParts] = useState<Parts>({ prefix: 'LIV-', date: '{YYYY}', separator: '-', digits: 4 }),
    [start, setStart] = useState(1),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [notice, setNotice] = useState('');
  const show = (current: Numbering) => {
    setSaved(current);
    setOn(!!current.format);
    setStart(current.start);
    const known = current.format
      ? partsOf(current.format)
      : current.suggestion
        ? partsOf(current.suggestion)
        : null;
    if (known) setParts(known);
  };
  useEffect(() => {
    api<Numbering>('workspace/numbering')
      .then(show)
      .catch((e) => setError(e instanceof Error ? e.message : t('Request could not be completed')));
  }, []);
  const format = formatOf(parts);
  // The number this format would give now, from the existing identifiers; null while unknown.
  const [preview, setPreview] = useState<string | null>(null);
  useEffect(() => {
    if (!on) return;
    let active = true;
    const timer = window.setTimeout(() => {
      api<{ identifier: string | null }>(
        `workspace/numbering/preview?format=${encodeURIComponent(format)}&start=${start}`,
      )
        .then((next) => active && setPreview(next.identifier))
        .catch(() => active && setPreview(null));
    }, 250);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [on, format, start]);
  const custom = saved?.format && !partsOf(saved.format);
  const changed = !!saved && (on ? saved.format !== format || saved.start !== start : saved.format !== null);
  async function store(next: { format: string | null; start: number }) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await api<Numbering>('workspace/numbering', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(next),
      });
      show(result);
      onSaved?.();
      setNotice(
        result.format
          ? t('Saved. The next delivery today receives {0}.', result.next ?? '')
          : t('Saved. New deliveries need an identifier typed in.'),
      );
    } catch (e) {
      setError(
        e instanceof ApiError && Object.values(e.fields).length
          ? t(Object.values(e.fields)[0])
          : e instanceof Error
            ? e.message
            : t('Request could not be completed'),
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="workspace numbering-settings">
      {titled && <h2>{t('Delivery numbers')}</h2>}
      <p>
        {t(
          'New deliveries can receive the next number automatically, in the add form and for import rows with an empty identifier. Existing identifiers never change.',
        )}
      </p>
      {saved && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void store({ format: on ? format : null, start });
          }}
        >
          <label className="check">
            <input
              type="checkbox"
              checked={on}
              disabled={busy}
              onChange={(e) => {
                setOn(e.target.checked);
                setNotice('');
              }}
            />
            {t('Number new deliveries automatically')}
          </label>
          {on && (
            <>
              {custom && (
                <p className="notice">
                  {t(
                    'The current format {0} was set through the API. Saving here replaces it.',
                    saved.format ?? '',
                  )}
                </p>
              )}
              {!saved.format && saved.suggestion && (
                <p className="field-hint">
                  {t(
                    'Proposed from the shape of your existing identifiers; numbering continues after the highest one.',
                  )}
                </p>
              )}
              <div className="fields numbering-fields">
                <label>
                  {t('Text before the number')}
                  <input
                    value={parts.prefix}
                    maxLength={20}
                    // Only what an identifier format accepts: letters, digits, spaces and . _ / -
                    onChange={(e) =>
                      setParts({ ...parts, prefix: e.target.value.replace(/[^A-Za-z0-9 ._/-]/g, '') })
                    }
                  />
                </label>
                <label>
                  {t('Date part')}
                  <select
                    value={parts.date}
                    onChange={(e) => setParts({ ...parts, date: e.target.value as DatePart })}
                  >
                    {DATES.map(([value, label]) => (
                      <option key={value} value={value}>
                        {t(label)}
                      </option>
                    ))}
                  </select>
                </label>
                {parts.date && (
                  <label>
                    {t('Between date and number')}
                    <select
                      value={parts.separator}
                      onChange={(e) => setParts({ ...parts, separator: e.target.value })}
                    >
                      {['-', '/', '.', '_', ''].map((s) => (
                        <option key={s} value={s}>
                          {s || t('Nothing')}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                <label>
                  {t('Digits')}
                  <select
                    value={parts.digits}
                    onChange={(e) => setParts({ ...parts, digits: Number(e.target.value) })}
                  >
                    {[1, 2, 3, 4, 5, 6, 7, 8].map((n) => (
                      <option key={n} value={n}>
                        {n === 1 ? t('As needed') : n}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  {t('Start from')}
                  <input
                    type="number"
                    min={1}
                    max={999999999}
                    value={start}
                    onChange={(e) => setStart(Math.max(1, Math.floor(Number(e.target.value) || 1)))}
                  />
                </label>
              </div>
              <p className="numbering-example">
                {preview ? (
                  <>
                    {t('Next delivery today')}: <strong>{preview}</strong>
                  </>
                ) : (
                  <>
                    {t('Example')}: <strong>{example(format, start)}</strong>
                  </>
                )}
                <small>
                  {parts.date
                    ? t(
                        'Numbering starts again with each {0}.',
                        t(parts.date === '{YYYY}' ? 'year' : parts.date === '{YYYY}{MM}' ? 'month' : 'day'),
                      )
                    : t('One sequence for all days.')}{' '}
                  {t(
                    'The next number follows the highest existing identifier in this format, and is at least the starting number.',
                  )}
                </small>
              </p>
            </>
          )}
          <button disabled={busy || !changed}>{busy ? t('Saving…') : t(saveLabel)}</button>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          {notice && <p role="status">{notice}</p>}
        </form>
      )}
    </section>
  );
}
