import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { ErrorBoundary } from './ErrorBoundary';
import { clock, dayMonth } from './format';
import { parisTime } from './RouteReview';

vi.mock('./MapPanel', () => ({ MapPanel: () => null }));

function Broken(): never {
  throw new Error('render failed');
}

it('replaces a screen that fails to render with an explanation and a reload, not a blank page', () => {
  const quiet = vi.spyOn(console, 'error').mockImplementation(() => undefined);
  const reload = vi.fn();
  vi.stubGlobal('location', { ...window.location, reload });
  render(
    <ErrorBoundary>
      <Broken />
    </ErrorBoundary>,
  );
  expect(screen.getByRole('heading', { name: 'Something went wrong' })).toBeVisible();
  expect(screen.getByRole('alert')).toHaveTextContent('Everything already saved is safe');
  fireEvent.click(screen.getByRole('button', { name: 'Reload the page' }));
  expect(reload).toHaveBeenCalledOnce();
  quiet.mockRestore();
  vi.unstubAllGlobals();
});

it('shows an unreadable time as given instead of throwing while rendering', () => {
  expect(clock('2026-09-23T06:05:09Z', true)).toBe('08:05:09');
  expect(clock('not a time', true)).toBe('not a time');
  expect(dayMonth('not a time')).toBe('not a time');
  expect(parisTime('', 3600)).toBe('');
  expect(parisTime('2026-09-23', 8 * 3600)).toBe('08:00');
});
