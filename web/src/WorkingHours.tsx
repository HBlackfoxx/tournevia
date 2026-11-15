import type { VehicleSource } from './api';
import { DAYS, dayName, type Day, type Hours } from './availability';
import { serviceDay } from './format';
import { t, useLanguage } from './i18n';

type Exception = NonNullable<VehicleSource['exceptions']>[number];
const DEFAULT: Hours = { start: '08:00', end: '17:00' };

/** The next date after `day` (YYYY-MM-DD) that has no exception yet. */
function nextFree(day: string, taken: Exception[]) {
  const date = new Date(`${day}T12:00:00Z`);
  while (taken.some((e) => e.date === date.toISOString().slice(0, 10)))
    date.setUTCDate(date.getUTCDate() + 1);
  return date.toISOString().slice(0, 10);
}

/** A vehicle's weekly working hours and its days off or other hours on given dates. */
export function WorkingHours({
  hours,
  exceptions,
  day,
  errors,
  onHours,
  onExceptions,
}: {
  hours: VehicleSource['hours'];
  exceptions: Exception[];
  /** The planning day: where a new day off starts. */
  day: string;
  errors: Record<string, string>;
  onHours: (hours: VehicleSource['hours']) => void;
  onExceptions: (exceptions: Exception[]) => void;
}) {
  useLanguage();
  const setDay = (name: Day, value: Hours | null) => onHours({ ...hours, [name]: value });
  const setException = (index: number, value: Exception) =>
    onExceptions(exceptions.map((e, i) => (i === index ? value : e)));
  const monday = hours.monday;
  return (
    <div className="working-hours">
      <fieldset className="weekly-hours">
        <legend>{t('Weekly hours')}</legend>
        {DAYS.map((name, i) => {
          const h = hours[name] ?? null,
            label = dayName(name),
            problem = errors[`hours.${name}`];
          return (
            <div className={`hours-row ${h ? '' : 'off'}`} key={name}>
              <label className="check">
                <input
                  type="checkbox"
                  checked={!!h}
                  onChange={(e) =>
                    // A day switched on starts with the hours of the day before, or 08:00–17:00.
                    setDay(name, e.target.checked ? (hours[DAYS[(i + 6) % 7]] ?? DEFAULT) : null)
                  }
                />
                {label}
              </label>
              {h ? (
                <span className="hours-times">
                  <input
                    type="time"
                    step={60}
                    required
                    aria-label={t('{0} start', label)}
                    aria-invalid={!!problem}
                    value={h.start}
                    onChange={(e) => setDay(name, { ...h, start: e.target.value })}
                  />
                  <span aria-hidden="true">–</span>
                  <input
                    type="time"
                    step={60}
                    required
                    aria-label={t('{0} end', label)}
                    aria-invalid={!!problem}
                    value={h.end}
                    onChange={(e) => setDay(name, { ...h, end: e.target.value })}
                  />
                </span>
              ) : (
                <span className="hours-off">{t('Does not work')}</span>
              )}
              {problem && <span className="field-error">{t(problem)}</span>}
            </div>
          );
        })}
        {monday && (
          <button
            type="button"
            className="text-button"
            onClick={() =>
              onHours({ ...hours, tuesday: monday, wednesday: monday, thursday: monday, friday: monday })
            }
          >
            {t('Use Monday’s hours from Tuesday to Friday')}
          </button>
        )}
      </fieldset>
      <fieldset className="days-off">
        <legend>{t('Days off and other hours')}</legend>
        {exceptions.length === 0 && (
          <p className="field-hint">{t('None. Add a day off or different hours for one date.')}</p>
        )}
        {exceptions.map((e, i) => {
          const problem = errors[`exceptions.${i}`] || errors[`exceptions.${i}.hours`];
          return (
            <div className="exception-row" key={i}>
              <input
                type="date"
                required
                aria-label={t('Date')}
                value={e.date}
                onChange={(event) => setException(i, { ...e, date: event.target.value })}
              />
              <select
                aria-label={t('{0}: day off or other hours', e.date ? serviceDay(e.date) : t('Date'))}
                value={e.hours ? 'hours' : 'off'}
                onChange={(event) =>
                  setException(i, { ...e, hours: event.target.value === 'off' ? null : DEFAULT })
                }
              >
                <option value="off">{t('Day off')}</option>
                <option value="hours">{t('Other hours')}</option>
              </select>
              {e.hours && (
                <span className="hours-times">
                  <input
                    type="time"
                    step={60}
                    required
                    aria-label={t('{0} start', e.date)}
                    value={e.hours.start}
                    onChange={(event) =>
                      setException(i, { ...e, hours: { ...e.hours!, start: event.target.value } })
                    }
                  />
                  <span aria-hidden="true">–</span>
                  <input
                    type="time"
                    step={60}
                    required
                    aria-label={t('{0} end', e.date)}
                    value={e.hours.end}
                    onChange={(event) =>
                      setException(i, { ...e, hours: { ...e.hours!, end: event.target.value } })
                    }
                  />
                </span>
              )}
              <button
                type="button"
                className="text-button"
                aria-label={t('Remove {0}', e.date)}
                onClick={() => onExceptions(exceptions.filter((_, j) => j !== i))}
              >
                {t('Remove')}
              </button>
              {problem && <span className="field-error">{t(problem)}</span>}
            </div>
          );
        })}
        {errors.exceptions && <span className="field-error">{t(errors.exceptions)}</span>}
        <button
          type="button"
          className="secondary"
          disabled={exceptions.length >= 100}
          onClick={() => onExceptions([...exceptions, { date: nextFree(day, exceptions), hours: null }])}
        >
          {t('Add a day off')}
        </button>
      </fieldset>
    </div>
  );
}
