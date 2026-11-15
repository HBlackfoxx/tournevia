import { locale, plural, t } from './i18n';

/** Numbers, money, units and dates in the interface language (not the browser's), e.g. "1,200 kg" / "1 200 kg". */
export function formatNumber(value: number, digits = 0): string {
  return new Intl.NumberFormat(locale(), {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

/** Integer minor units (cents) as euros: "€13.68" in English, "13,68 €" in French. */
export function euros(minor: number, digits = 2): string {
  return new Intl.NumberFormat(locale(), {
    style: 'currency',
    currency: 'EUR',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(minor / 100);
}

const unit = (value: string, symbol: string) => `${value}\u00a0${symbol}`;

export function kilometres(metres: number, digits = 0): string {
  return unit(formatNumber(metres / 1000, digits), 'km');
}

export function kilograms(kilograms: number): string {
  return unit(formatNumber(kilograms), 'kg');
}

/** Up to two decimals without trailing zeros, in the interface language: "42", "1,05", "7,2". */
function decimal(value: number): string {
  return new Intl.NumberFormat(locale(), { maximumFractionDigits: 2 }).format(value);
}

/** Volumes are stored in dm³ and read in m³: 42000 → "42 m³", 1050 → "1,05 m³". */
export function cubicMetres(dm3: number): string {
  return unit(decimal(dm3 / 1000), 'm³');
}

/** Lengths are stored in mm and read in metres: 7200 → "7,2 m". */
export function metres(mm: number): string {
  return unit(decimal(mm / 1000), 'm');
}

export function tonnes(kilograms: number, digits = 1): string {
  // A real zero prints as 0 t, not 0.0 t.
  return unit(formatNumber(kilograms / 1000, kilograms === 0 ? 0 : digits), 't');
}

/** The truck profile routing uses: its size and weights, in mm and kg. */
type Truck = {
  height_mm: number;
  width_mm: number;
  length_mm: number;
  gross_weight_kg: number;
  axle_load_kg: number;
  axle_count: number;
};

/** Lines break only between the parts, never inside "2 axles" or "L 9,6 m". */
const keepParts = (text: string) =>
  text
    .split(' · ')
    .map((part) => part.replace(/ /g, ' '))
    .join(' · ');

/** A truck's size as a dispatcher reads it, height first: "H 3,6 · l 2,55 · L 9,6 m". */
export function truckSize(truck: Truck): string {
  return keepParts(
    t(
      'H {0} · W {1} · L {2} m',
      decimal(truck.height_mm / 1000),
      decimal(truck.width_mm / 1000),
      decimal(truck.length_mm / 1000),
    ),
  );
}

/** Its gross weight alone, as on a route card: "19 t gross". */
export function grossWeight(truck: Truck): string {
  return keepParts(t('{0} gross', unit(decimal(truck.gross_weight_kg / 1000), 't')));
}

/** Its weights: "19 t gross · 11,5 t per axle · 2 axles". */
export function truckWeights(truck: Truck): string {
  return keepParts(
    `${grossWeight(truck)} · ${t('{0} per axle', unit(decimal(truck.axle_load_kg / 1000), 't'))} · ${plural(truck.axle_count, '{0} axle', '{0} axles')}`,
  );
}

/** A stop's margin: how much later the truck could arrive and still start within the window.
 * Ten minutes or less, rounded as shown, is tight; below zero the stop is late. */
export const TIGHT_MINUTES = 10;
export const isTight = (seconds: number) => Math.round(seconds / 60) <= TIGHT_MINUTES;
export function margin(seconds: number): string {
  return Math.round(seconds / 60) < 0 ? t('{0} late', duration(-seconds)) : duration(seconds);
}

/** Rounded to whole minutes before splitting, so a duration never shows "60 min": "1 h 05 min", "45 min". */
export function duration(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  const hours = Math.floor(minutes / 60);
  return hours
    ? `${hours}\u00a0h\u00a0${String(minutes % 60).padStart(2, '0')}\u00a0min`
    : `${minutes}\u00a0min`;
}

/** A missing or unreadable timestamp is shown as given rather than breaking the screen. */
const moment = (iso: string) => {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
};

/** A moment in Paris time, the one date-and-time format of the interface: "3 Nov 2026, 17:29" / "3 nov. 2026, 17:29". */
export function dateTime(iso: string): string {
  const date = moment(iso);
  if (!date) return iso ?? '';
  return new Intl.DateTimeFormat(locale(), {
    timeZone: 'Europe/Paris',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(date);
}

/** The time of a moment in Paris time: "17:29", or "17:29:05" with `seconds`. */
export function clock(iso: string, seconds = false): string {
  const date = moment(iso);
  if (!date) return iso ?? '';
  return new Intl.DateTimeFormat(locale(), {
    timeZone: 'Europe/Paris',
    hour: '2-digit',
    minute: '2-digit',
    ...(seconds ? { second: '2-digit' } : {}),
    hourCycle: 'h23',
  }).format(date);
}

/** The day and month of a moment in Paris time: "03 Nov" / "03 nov.". */
export function dayMonth(iso: string): string {
  const date = moment(iso);
  if (!date) return iso ?? '';
  return new Intl.DateTimeFormat(locale(), {
    timeZone: 'Europe/Paris',
    day: '2-digit',
    month: 'short',
  }).format(date);
}

/** A planning day (ISO date, no time zone) as a readable date: "Wed 23 Sept 2026" / "mer. 23 sept. 2026". */
export function serviceDay(isoDate: string): string {
  const [year, month, day] = isoDate.split('-').map(Number);
  if (!year || !month || !day) return isoDate;
  return new Intl.DateTimeFormat(locale(), {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(Date.UTC(year, month - 1, day));
}
