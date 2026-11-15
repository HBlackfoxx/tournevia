import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { AddressLookupSettings } from './addressLookup';

it('lets an administrator turn on the IGN address service, saying what is sent', async () => {
  const fetch = vi.fn(async () => ({ ok: true, json: async () => ({ public_service: true }) }));
  vi.stubGlobal('fetch', fetch);
  const onChanged = vi.fn();
  render(<AddressLookupSettings source={null} onChanged={onChanged} />);
  expect(screen.getByText(/are sent to the IGN Géoplateforme \(data\.geopf\.fr\)/)).toBeInTheDocument();
  const save = screen.getByRole('button', { name: 'Save address search' });
  expect(save).toBeDisabled();
  fireEvent.click(screen.getByLabelText('Use the IGN address service (France)'));
  fireEvent.click(save);
  expect(await screen.findByText('Address search is on.')).toBeInTheDocument();
  const [url, options] = fetch.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe('/api/v1/workspace/address-lookup');
  expect(options.method).toBe('PUT');
  expect(JSON.parse(options.body as string)).toEqual({ public_service: true });
  await waitFor(() => expect(onChanged).toHaveBeenCalledOnce());
});

it('offers no choice when the server sets address search for the installation', () => {
  render(<AddressLookupSettings source="installation" attribution="BAN" onChanged={vi.fn()} />);
  expect(screen.queryByRole('checkbox')).toBeNull();
  expect(
    screen.getByText('Address search is set on the server for every company on this installation: BAN.'),
  ).toBeInTheDocument();
});
