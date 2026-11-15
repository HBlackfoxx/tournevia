import { t, setLanguage } from './i18n';
import type { components } from './api-types';
export type Depot = components['schemas']['DepotRecord'];
/** A saved vehicle: weekly hours and days off. */
export type Vehicle = components['schemas']['Owned_VehicleSource_'];
export type Order = components['schemas']['Owned_OrderInput_'];
export type Customer = components['schemas']['Owned_CustomerInput_'];
export type CustomerInput = components['schemas']['CustomerInput'];
export type CustomerSummary = components['schemas']['CustomerSummary'];
export type VehicleSource = components['schemas']['VehicleSource'];
/** A vehicle as one calculation used it: its dated window that day. */
export type VehicleInput = components['schemas']['VehicleInput'];
export type OrderInput = components['schemas']['OrderInput'];
export type Location = components['schemas']['Location'];
export type AccessCheck = components['schemas']['AccessCheck'];
export type ImportResult = components['schemas']['ImportResult'];
export type Configuration = components['schemas']['Configuration'];
export type Job = components['schemas']['JobRecord'];
export type Plan = components['schemas']['PlanRecord'];
export type PlanListItem = components['schemas']['PlanListItem'];
export type OrderSummary = components['schemas']['OrderSummary'];
export type AddressCandidate = components['schemas']['AddressCandidate'];
export type VehicleSummary = components['schemas']['VehicleSummary'];
export type OptimizationRequest = components['schemas']['OptimizationRequest'];
export type EditRequest = components['schemas']['EditRequest'];
/** Example deliveries just added for a day, with the example depot and vehicles. */
export type ExamplesAdded = components['schemas']['ExamplesAdded'];
/** A READY plan's route sheets, one per vehicle with deliveries. */
export type RouteSheets = components['schemas']['RouteSheets'];
export type SessionRecord = components['schemas']['SessionRecord'];
export type UserRecord = components['schemas']['UserRecord'];
export type SystemStatus = components['schemas']['SystemStatus'];
/** Automatic polls must not extend the session idle timer; user-initiated reads omit this. */
export const background = { 'X-VRP-Background': '1' };
/** Run a poll after `delay`; while the page is hidden, wait and resume on visibilitychange. Returns a cancel function. */
export function whenVisible(run: () => void, delay: number): () => void {
  const wake = () => {
    if (!document.hidden) {
      document.removeEventListener('visibilitychange', wake);
      run();
    }
  };
  const timer = setTimeout(() => {
    if (document.hidden) document.addEventListener('visibilitychange', wake);
    else run();
  }, delay);
  return () => {
    clearTimeout(timer);
    document.removeEventListener('visibilitychange', wake);
  };
}
let csrfToken: string | null = null;
export function setSession(record: SessionRecord | null) {
  csrfToken = record?.csrf_token ?? null;
  if (record) setLanguage(record.identity.language ?? 'en');
}
export function clearSessionData() {
  setSession(null);
  try {
    for (const key of Object.keys(sessionStorage)) if (key.startsWith('vrp-')) sessionStorage.removeItem(key);
  } catch {
    /* optional storage */
  }
}
/** The server ended this session (idle/absolute expiry or revocation). Retry keys and unsaved work stay in
 * place so the same user can sign in again and continue; another user's sign-in clears them. */
export function sessionExpired() {
  setSession(null);
  window.dispatchEvent(new Event('vrp-session-expired'));
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public result?: ImportResult,
    public fields: Record<string, string> = {},
    public code?: string,
    /** Records the error is about (for example fleet numbers), separate from the translatable message. */
    public identifiers: string[] = [],
  ) {
    super(message);
  }
}
type FieldProblem = { loc: string[]; msg: string; template?: string | null; values?: string[] };
const wording = (e: FieldProblem) => (e.template ? t(e.template, ...(e.values ?? [])) : t(e.msg));
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1/${path}`, {
      credentials: 'same-origin',
      ...options,
      headers: {
        'X-VRP-Request': '1',
        ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
        ...options.headers,
      },
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    // The browser's own text ("Failed to fetch") is neither translated nor explains what happened.
    throw new ApiError(
      0,
      t(
        'The server could not be reached. Your changes have not been confirmed; check the connection and try again.',
      ),
    );
  }
  // A success with no body (204 No Content, as for a deleted depot) is a success, not an unreadable answer.
  if (response.ok && (response.status === 204 || response.headers?.get('Content-Length') === '0')) {
    return undefined as T;
  }
  let data;
  try {
    data = await response.json();
  } catch {
    if (response.status === 401 && path !== 'session') sessionExpired();
    throw new ApiError(
      response.status,
      t(
        'The service returned an unreadable response. Your changes have not been confirmed. Retry the same submission if it was pending.',
      ),
    );
  }
  if (!response.ok) {
    if (response.status === 401 && path !== 'session') sessionExpired();
    const detail = Array.isArray(data.detail)
      ? data.detail
          .map((e: FieldProblem) =>
            e.loc.length > 1 ? `${e.loc.slice(1).join('.')}: ${wording(e)}` : wording(e),
          )
          .join('\n')
      : data.detail;
    throw new ApiError(
      response.status,
      (typeof detail === 'string' ? (Array.isArray(data.detail) ? detail : t(detail)) : detail) ||
        t('Import contains errors; nothing was saved.'),
      data.errors ? data : undefined,
      Array.isArray(data.detail)
        ? Object.fromEntries(data.detail.map((e: FieldProblem) => [e.loc.slice(1).join('.'), wording(e)]))
        : {},
      typeof data.code === 'string' ? data.code : undefined,
      Array.isArray(data.identifiers) ? data.identifiers : [],
    );
  }
  return data as T;
}
export function save<T>(path: string, data: unknown, version?: number) {
  return api<T>(path, {
    method: version ? 'PUT' : 'POST',
    headers: { 'Content-Type': 'application/json', ...(version ? { 'If-Match': `"${version}"` } : {}) },
    body: JSON.stringify(data),
  });
}
const versions = (orders: Order[]) => orders.map(({ id, version }) => ({ id, version }));
/** Cancels deliveries (they stay, inactive, and are not planned) or restores them, each at the version shown. */
export function setOrdersActive(orders: Order[], active: boolean) {
  return api<Order[]>('orders/activation', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ orders: versions(orders), active }),
  });
}
/** Deletes deliveries for good, each at the version shown. */
export function deleteOrders(orders: Order[]) {
  return api<{ deleted: number }>('orders/delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ orders: versions(orders) }),
  });
}
