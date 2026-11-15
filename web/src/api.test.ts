import { expect, it, vi } from 'vitest';
import { api, ApiError } from './api';
it('retains field-level backend validation and rejects unreadable gateway responses', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: false,
      status: 422,
      json: async () => ({ detail: [{ loc: ['body', 'demand', 'weight_kg'], msg: 'Must be nonnegative' }] }),
    })),
  );
  try {
    await api('orders');
    throw new Error('Expected rejection');
  } catch (e) {
    expect(e).toBeInstanceOf(ApiError);
    expect((e as ApiError).fields).toEqual({ 'demand.weight_kg': 'Must be nonnegative' });
  }
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: false,
      status: 502,
      json: async () => {
        throw new Error('HTML');
      },
    })),
  );
  await expect(api('orders')).rejects.toThrow('changes have not been confirmed');
});
it('signals expiry even when an authentication error body is not JSON, keeping retry data for re-sign-in', async () => {
  sessionStorage.setItem('vrp-pending:test', 'private');
  const expired = vi.fn();
  window.addEventListener('vrp-session-expired', expired);
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: false,
      status: 401,
      json: async () => {
        throw new Error('HTML');
      },
    })),
  );
  await expect(api('plans')).rejects.toBeInstanceOf(ApiError);
  expect(expired).toHaveBeenCalledOnce();
  expect(sessionStorage.getItem('vrp-pending:test')).toBe('private');
  window.removeEventListener('vrp-session-expired', expired);
});

it('treats a success with no body as success, but an empty error as unreadable', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(null, { status: 204 })),
  );
  await expect(api('depots/id-HOME', { method: 'DELETE' })).resolves.toBeUndefined();
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response('', { status: 200, headers: { 'Content-Length': '0' } })),
  );
  await expect(api('depots/id-HOME', { method: 'DELETE' })).resolves.toBeUndefined();
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response('', { status: 500, headers: { 'Content-Length': '0' } })),
  );
  await expect(api('orders')).rejects.toThrow('changes have not been confirmed');
});

it('explains an unreachable server instead of showing the browser error, and lets aborts through', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))),
  );
  const failure = await api('vehicles').catch((e: unknown) => e);
  expect(failure).toBeInstanceOf(ApiError);
  expect((failure as ApiError).status).toBe(0);
  expect((failure as ApiError).message).toBe(
    'The server could not be reached. Your changes have not been confirmed; check the connection and try again.',
  );
  const abort = new DOMException('stopped', 'AbortError');
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => Promise.reject(abort)),
  );
  await expect(api('vehicles')).rejects.toBe(abort);
});

it('shows a problem with the whole request as its message alone, without an empty field name', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: false,
      status: 422,
      json: async () => ({
        detail: [
          { loc: ['body'], msg: 'Value error, Review the depot location before creating the workspace.' },
        ],
      }),
    })),
  );
  const failure = (await api('setup').catch((e: unknown) => e)) as ApiError;
  expect(failure.message).toBe('Value error, Review the depot location before creating the workspace.');
});
