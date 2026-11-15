import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { example, formatOf, NumberingSettings, partsOf } from './Numbering';

it('turns the simple fields into a format and back', () => {
  const parts = { prefix: 'LIV-', date: '{YYYY}' as const, separator: '-', digits: 4 };
  expect(formatOf(parts)).toBe('LIV-{YYYY}-{N:4}');
  expect(partsOf('LIV-{YYYY}-{N:4}')).toEqual(parts);
  expect(partsOf('LEM-{N:4}')).toEqual({ prefix: 'LEM-', date: '', separator: '', digits: 4 });
  expect(partsOf('{DD}-{N}')).toBeNull(); // Only the API can write this one.
  expect(example('LIV-{YYYY}{MM}-{N:3}', 7, new Date(2026, 10, 3))).toBe('LIV-202611-007');
});

it('proposes a format from existing identifiers and saves the chosen one', async () => {
  const calls: [string, RequestInit | undefined][] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options?: RequestInit) => {
      calls.push([url, options]);
      return {
        ok: true,
        json: async () =>
          options?.method === 'PUT'
            ? { format: 'LEM-{N:4}', start: 1, next: 'LEM-1011', suggestion: 'LEM-{N:4}' }
            : url.includes('/preview')
              ? { identifier: 'LEM-1011' }
              : { format: null, start: 1, next: null, suggestion: 'LEM-{N:4}' },
      };
    }),
  );
  render(<NumberingSettings />);
  fireEvent.click(await screen.findByLabelText('Number new deliveries automatically'));
  expect(screen.getByLabelText('Text before the number')).toHaveValue('LEM-');
  expect(screen.getByText(/Proposed from the shape of your existing identifiers/)).toBeInTheDocument();
  // The real next number, continuing the existing identifiers, before anything is saved.
  expect(await screen.findByText('LEM-1011')).toBeInTheDocument();
  expect(calls.map(([url]) => url)).toContain(
    '/api/v1/workspace/numbering/preview?format=LEM-%7BN%3A4%7D&start=1',
  );
  fireEvent.click(screen.getByRole('button', { name: 'Save delivery numbers' }));
  expect(await screen.findByText('Saved. The next delivery today receives LEM-1011.')).toBeInTheDocument();
  const put = calls.find(([, options]) => options?.method === 'PUT')!;
  expect(put[0]).toBe('/api/v1/workspace/numbering');
  expect(JSON.parse(put[1]!.body as string)).toEqual({ format: 'LEM-{N:4}', start: 1 });
});
