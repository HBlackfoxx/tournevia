import { act, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { SystemStatusBanner, SYSTEM_STATUS_POLL_MS } from './SystemStatus';
import type { SystemStatus } from './api';

const healthy: SystemStatus = {
  worker: 'RUNNING',
  routing: 'AVAILABLE',
  dataset: 'Test',
  queued_jobs: 0,
  running_jobs: 0,
  oldest_queued_seconds: null,
};
const ok = (data: unknown) => ({ ok: true, json: async () => data });
const tick = async (ms: number) =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });

it('warns that calculations wait while the worker is stopped and polls in the background', async () => {
  vi.useFakeTimers();
  try {
    let status: SystemStatus = {
      ...healthy,
      worker: 'NOT_RUNNING',
      queued_jobs: 2,
      oldest_queued_seconds: 125,
    };
    const fetch = vi.fn(async (_url: string, _options: RequestInit) => ok(status));
    vi.stubGlobal('fetch', fetch);
    render(<SystemStatusBanner />);
    await tick(0);
    expect(screen.getByRole('alert')).toHaveTextContent('calculation worker is not running');
    // The queue age changes on every poll, so it is shown but not inside the live region.
    expect(screen.getByText('2 calculation(s) queued; the oldest has waited 2 min.')).toBeInTheDocument();
    expect(screen.getByRole('alert')).not.toHaveTextContent('queued');
    expect(fetch.mock.calls[0][0]).toBe('/api/v1/system-status');
    expect(fetch.mock.calls[0][1].headers).toMatchObject({ 'X-VRP-Background': '1' });
    status = healthy;
    await tick(SYSTEM_STATUS_POLL_MS - 1);
    expect(fetch).toHaveBeenCalledTimes(1);
    await tick(1);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
});

it('warns that calculations and road checks fail while routing is unavailable', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ok({ ...healthy, routing: 'UNAVAILABLE' })),
  );
  render(<SystemStatusBanner />);
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Calculations and road-access checks will fail until the routing service is running.',
  );
  expect(screen.getByRole('alert')).not.toHaveTextContent('worker');
});

it('stays silent when services are healthy, briefly queued, or the status cannot be read', async () => {
  const fetch = vi.fn(async () => ok({ ...healthy, queued_jobs: 1, oldest_queued_seconds: 30 }));
  vi.stubGlobal('fetch', fetch);
  const view = render(<SystemStatusBanner />);
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  expect(fetch).toHaveBeenCalledOnce();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  view.unmount();
  const failing = vi.fn(async () => {
    throw new Error('Status unavailable');
  });
  vi.stubGlobal('fetch', failing);
  render(<SystemStatusBanner />);
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  expect(failing).toHaveBeenCalledOnce();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(screen.queryByText(/Status unavailable/)).not.toBeInTheDocument();
});
