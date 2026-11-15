import { expect, it } from 'vitest';
import { parisCandidates, scaledValue } from './formValues';
it('rejects nonexistent Paris times and exposes both instances of the repeated hour', () => {
  expect(parisCandidates('2026-03-29T02:30')).toEqual([]);
  expect(parisCandidates('2026-10-25T02:30')).toEqual([
    '2026-10-25T02:30:00+01:00',
    '2026-10-25T02:30:00+02:00',
  ]);
  expect(parisCandidates('2026-01-15T08:00')).toEqual(['2026-01-15T08:00:00+01:00']);
  expect(parisCandidates('2026-07-15T08:00')).toEqual(['2026-07-15T08:00:00+02:00']);
  expect(parisCandidates('2026-02-30T08:00')).toEqual([]);
});
it('converts human units without rounding fractional API units into accepted integers', () => {
  expect(scaledValue(1.1, 100)).toBe(110);
  expect(scaledValue(2.55, 1000)).toBe(2550);
  expect(scaledValue(0.1, 60)).toBe(6);
  expect(scaledValue(0.001, 60)).toBe(0.06);
});
