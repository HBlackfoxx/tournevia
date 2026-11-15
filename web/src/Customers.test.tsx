import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { Customers, fromCustomer, SaveAsCustomer } from './Customers';
import type { Customer, Order } from './api';
import fixture from '../../backend/tests/fixtures/paris-ten.json';

const customer: Customer = {
  id: 'c-1',
  organization_id: 'company',
  example: false,
  version: 2,
  data: {
    code: 'CLI-1',
    name: 'Boulangerie Fictive',
    active: true,
    address: '5 Rue Fictive, 94200 Ivry-sur-Seine',
    location: { latitude: 48.8156, longitude: 2.3889, reviewed: true },
    usual_hours: { start: '06:00', end: '09:00' },
    service_seconds: 600,
    required_skills: ['TAIL_LIFT'],
    instructions: 'Back door.',
  },
};

it('lists saved customers with their entrance, searches them and opens one', async () => {
  const urls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      urls.push(url);
      return {
        ok: true,
        json: async () =>
          url.includes('/summary')
            ? { total: 2, active: 2, inactive: 0, review: 1, missing: 0 }
            : [
                customer,
                {
                  ...customer,
                  id: 'c-2',
                  data: {
                    ...customer.data,
                    code: 'CLI-2',
                    location: { ...customer.data.location!, reviewed: false },
                  },
                },
              ],
      };
    }),
  );
  const onEdit = vi.fn();
  render(<Customers reload={0} onEdit={onEdit} />);
  expect(await screen.findByText('CLI-1', { selector: 'strong' })).toBeInTheDocument();
  expect(screen.getByText('All customers 2')).toBeInTheDocument();
  expect(screen.getByText('Reviewed')).toBeInTheDocument();
  expect(screen.getByText('Needs review')).toBeInTheDocument();
  expect(screen.getAllByText('06:00–09:00')).toHaveLength(2);
  fireEvent.click(screen.getAllByRole('button', { name: /^Edit/ })[0]);
  expect(onEdit).toHaveBeenCalledWith(customer);
  fireEvent.change(screen.getByLabelText('Search customers'), { target: { value: 'boulangerie' } });
  await waitFor(() => expect(urls.some((u) => u.includes('q=boulangerie'))).toBe(true));
  fireEvent.click(screen.getByRole('button', { name: 'Needs review 1' }));
  await waitFor(() => expect(urls.at(-2)).toContain('status=review'));
});

it('fills a delivery from a customer: address, reviewed entrance and usual needs on its own day', () => {
  const order = { ...fixture.orders[0].data, service_date: '2026-11-04' } as unknown as Order['data'];
  expect(Object.fromEntries(fromCustomer(customer, order))).toEqual({
    customer: 'CLI-1',
    address: '5 Rue Fictive, 94200 Ivry-sur-Seine',
    location: { latitude: 48.8156, longitude: 2.3889, reviewed: true },
    time_window: { start: '2026-11-04T06:00:00+01:00', end: '2026-11-04T09:00:00+01:00' },
    service_seconds: 600,
    required_skills: ['TAIL_LIFT'],
    instructions: 'Back door.',
  });
});

it('saves a reviewed delivery as a customer at its current version', async () => {
  const order = {
    id: 'o-1',
    organization_id: 'company',
    version: 7,
    data: fixture.orders[0].data,
  } as unknown as Order;
  const linked = { ...order, version: 8 };
  const fetch = vi.fn(async () => ({ ok: true, json: async () => ({ customer, order: linked }) }));
  vi.stubGlobal('fetch', fetch);
  const onSaved = vi.fn();
  render(<SaveAsCustomer order={order} onSaved={onSaved} />);
  fireEvent.click(screen.getByText('Save as customer…'));
  fireEvent.click(screen.getByRole('button', { name: 'Save customer' }));
  expect(screen.getByRole('alert')).toHaveTextContent('Give the customer a code and a name.');
  fireEvent.change(screen.getByLabelText('Customer code'), { target: { value: ' CLI-9 ' } });
  fireEvent.change(screen.getByLabelText('Customer name'), { target: { value: 'Client' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save customer' }));
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith(linked));
  const [url, options] = fetch.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe('/api/v1/orders/o-1/customer');
  expect((options.headers as Record<string, string>)['If-Match']).toBe('"7"');
  expect(JSON.parse(options.body as string)).toEqual({ code: 'CLI-9', name: 'Client' });
});
