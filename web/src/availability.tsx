import type { Vehicle, VehicleSource } from './api';
import { locale, t, useLanguage } from './i18n';

export const DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday'] as const;
export type Day = (typeof DAYS)[number];
export type Hours = { start: string; end: string };

/** The weekday of a YYYY-MM-DD date. */
export function dayOf(date: string): Day {
  return DAYS[(new Date(`${date}T12:00:00Z`).getUTCDay() + 6) % 7];
}

/** A weekday's name in the interface language, short or long. */
export function dayName(day: Day, width: 'short' | 'long' = 'long') {
  // 2 November 2026 is a Monday.
  const date = new Date(Date.UTC(2026, 10, 2 + DAYS.indexOf(day), 12));
  return new Intl.DateTimeFormat(locale(), { weekday: width, timeZone: 'UTC' }).format(date);
}

/** The hours a vehicle works on `date`: that date's exception, or else its weekly hours. */
export function hoursOn(vehicle: { data: VehicleSource }, date: string): Hours | null {
  const exception = vehicle.data.exceptions?.find((e) => e.date === date);
  if (exception) return exception.hours ?? null;
  return vehicle.data.hours[dayOf(date)] ?? null;
}

/** Whether the vehicle works on the planning day. */
export const availableOn = (vehicle: Vehicle, date: string) => !!hoursOn(vehicle, date);

/** Why a vehicle does not work on `date`: a day off, or no weekly hours that weekday. */
export function offReason(vehicle: Vehicle, date: string) {
  return vehicle.data.exceptions?.some((e) => e.date === date && !e.hours)
    ? t('day off')
    : t('no hours on {0}', dayName(dayOf(date)));
}

/** The weekly hours in a line, days with the same hours together: "Mon–Fri 07:30–17:00 · Sat 08:00–12:00". */
export function weekSummary(hours: VehicleSource['hours']) {
  const groups: { days: Day[]; hours: Hours }[] = [];
  DAYS.forEach((day, i) => {
    const h = hours[day];
    if (!h) return;
    const last = groups.at(-1);
    if (
      last &&
      DAYS.indexOf(last.days.at(-1)!) === i - 1 &&
      last.hours.start === h.start &&
      last.hours.end === h.end
    )
      last.days.push(day);
    else groups.push({ days: [day], hours: h });
  });
  if (!groups.length) return t('No weekly hours');
  return groups
    .map(({ days, hours: h }) => {
      const first = dayName(days[0], 'short'),
        end = dayName(days.at(-1)!, 'short');
      return `${days.length > 1 ? `${first}–${end}` : first} ${h.start}–${h.end}`;
    })
    .join(' · ');
}

/** Vehicles that cannot be planned on `day`, and why; their hours are changed in Fleet. */
export function OffDayNotice({
  vehicles,
  day,
  selected,
  onFleet,
}: {
  vehicles: Vehicle[];
  day: string;
  selected: boolean;
  onFleet?: () => void;
}) {
  useLanguage();
  if (!vehicles.length) return null;
  const one = vehicles.length === 1;
  return (
    <div className="notice off-day">
      <strong>
        {selected
          ? one
            ? t('1 selected vehicle does not work that day')
            : t('{0} selected vehicles do not work that day', vehicles.length)
          : one
            ? t('1 active vehicle does not work that day')
            : t('{0} active vehicles do not work that day', vehicles.length)}
      </strong>
      <p>{vehicles.map((v) => `${v.data.fleet_number} · ${offReason(v, day)}`).join(' / ')}</p>
      <p>{t('Change their weekly hours or days off in Fleet; a calculation uses the hours of its day.')}</p>
      {onFleet && (
        <button className="secondary" onClick={onFleet}>
          {t('Open Fleet')}
        </button>
      )}
    </div>
  );
}
