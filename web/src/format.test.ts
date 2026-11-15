import { act } from 'react';
import { afterEach, expect, it } from 'vitest';
import {
  clock,
  cubicMetres,
  dateTime,
  duration,
  euros,
  formatNumber,
  isTight,
  kilograms,
  kilometres,
  margin,
  metres,
  serviceDay,
  tonnes,
  truckSize,
  truckWeights,
} from './format';
import { setLanguage } from './i18n';

const plain = (text: string) => text.replace(/[\u00a0\u202f]/g, ' ');
afterEach(() => act(() => setLanguage('en')));

it('formats numbers, money and units in the interface language, not the browser locale', () => {
  act(() => setLanguage('en'));
  expect(plain(euros(1368))).toBe('€13.68');
  expect(plain(euros(20100, 0))).toBe('€201');
  expect(plain(kilograms(1200))).toBe('1,200 kg');
  expect(plain(tonnes(400))).toBe('0.4 t');
  expect(plain(kilometres(28_600))).toBe('29 km');
  expect(plain(formatNumber(2.5, 1))).toBe('2.5');
  act(() => setLanguage('fr'));
  expect(plain(euros(1368))).toBe('13,68 €');
  expect(plain(euros(20100, 0))).toBe('201 €');
  expect(plain(kilograms(1200))).toBe('1 200 kg');
  expect(plain(tonnes(400))).toBe('0,4 t');
  expect(plain(kilometres(12_345, 1))).toBe('12,3 km');
});

it('rounds durations to whole minutes before splitting hours', () => {
  expect(plain(duration(3599))).toBe('1 h 00 min');
  expect(plain(duration(7170))).toBe('2 h 00 min');
  expect(plain(duration(5400))).toBe('1 h 30 min');
  expect(plain(duration(29))).toBe('0 min');
  expect(plain(duration(89))).toBe('1 min');
});

it('shows a planning day as a readable date without shifting it across time zones', () => {
  act(() => setLanguage('en'));
  expect(serviceDay('2026-09-23')).toMatch(/Wed.*23.*Sep.*2026/);
  act(() => setLanguage('fr'));
  expect(serviceDay('2026-09-23')).toMatch(/mer\..*23.*sept\..*2026/);
  expect(serviceDay('not-a-date')).toBe('not-a-date');
});

it('reads stored dm³ and mm as m³ and metres, with French decimals and no trailing zeros', () => {
  act(() => setLanguage('fr'));
  expect(plain(cubicMetres(42000))).toBe('42 m³');
  expect(plain(cubicMetres(1050))).toBe('1,05 m³');
  expect(plain(metres(7200))).toBe('7,2 m');
  act(() => setLanguage('en'));
  expect(plain(cubicMetres(1050))).toBe('1.05 m³');
  expect(plain(metres(13600))).toBe('13.6 m');
});

it('shows moments in Paris time in one format, and a missing timestamp without breaking', () => {
  act(() => setLanguage('fr'));
  expect(plain(dateTime('2026-11-03T16:29:00Z'))).toBe('3 nov. 2026, 17:29');
  expect(clock('2026-11-03T16:29:00Z')).toBe('17:29');
  expect(dateTime(undefined as unknown as string)).toBe('');
  expect(clock('not a date')).toBe('not a date');
});

it('reads a truck as height, width and length in metres, then its weights', () => {
  const truck = {
    height_mm: 3600,
    width_mm: 2550,
    length_mm: 9600,
    gross_weight_kg: 19000,
    axle_load_kg: 11500,
    axle_count: 2,
  };
  act(() => setLanguage('en'));
  expect(plain(truckSize(truck))).toBe('H 3.6 · W 2.55 · L 9.6 m');
  expect(plain(truckWeights(truck))).toBe('19 t gross · 11.5 t per axle · 2 axles');
  act(() => setLanguage('fr'));
  expect(plain(truckSize(truck))).toBe('H 3,6 · l 2,55 · L 9,6 m');
  expect(plain(truckWeights(truck))).toBe('poids total 19 t · 11,5 t par essieu · 2 essieux');
});

it('marks a margin of ten minutes or less as tight, as it is shown, and a negative one as late', () => {
  expect(isTight(0)).toBe(true);
  expect(isTight(629)).toBe(true); // shown as 10 min
  expect(isTight(630)).toBe(false); // shown as 11 min
  expect(plain(margin(4 * 3600 + 600))).toBe('4 h 10 min');
  expect(plain(margin(-300))).toBe('5 min late');
  act(() => setLanguage('fr'));
  expect(plain(margin(-300))).toBe('en retard de 5 min');
});
