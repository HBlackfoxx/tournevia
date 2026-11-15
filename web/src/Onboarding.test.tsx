import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { Onboarding } from './Onboarding';

const token = 'synthetic-setup-token-that-is-long-enough';
const password = 'synthetic password for onboarding';
function enter(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}
function next() {
  fireEvent.click(screen.getByRole('button', { name: 'Continue' }));
}
function admin() {
  enter('Your name', 'Owner');
  enter('Username', 'owner');
  enter('Password', password);
  enter('Confirm password', password);
  enter('Private setup code', token);
}

it('starts with an empty company, sends the reviewed depot and keeps secrets out of persistent storage', async () => {
  window.history.replaceState(null, '', `/#setup=${token}`);
  const fetch = vi.fn(async () => ({
    ok: true,
    json: async () => ({ identity: { user_id: 'owner' }, csrf_token: 'csrf' }),
  }));
  vi.stubGlobal('fetch', fetch);
  const complete = vi.fn();
  render(<Onboarding enabled onComplete={complete} />);
  expect(screen.getByRole('heading', { name: 'Set up your company and depot' })).toBeVisible();
  // No sample mode to choose: examples are offered later, in the setup guide.
  expect(screen.queryByLabelText(/sample/i)).toBeNull();
  expect(window.location.hash).toBe('');
  enter('Company name', 'Acme');
  enter('Depot name', 'Main depot');
  enter('Depot latitude', '48.835');
  enter('Depot longitude', '2.375');
  fireEvent.click(screen.getByLabelText('I reviewed this vehicle entrance on the map'));
  next();
  admin();
  fireEvent.click(screen.getByRole('button', { name: 'Create workspace' }));
  await waitFor(() => expect(complete).toHaveBeenCalledOnce());
  const request = JSON.parse((fetch.mock.calls[0] as unknown as [string, RequestInit])[1].body as string);
  expect(request).not.toHaveProperty('workspace_kind');
  expect(request.language).toBe('en'); // The interface language setup was completed in.
  expect(request.depot.location.reviewed).toBe(true);
  expect(request.company_name).toBe('Acme');
  expect(request).not.toHaveProperty('organization_id');
  expect(request).not.toHaveProperty('role');
  expect(JSON.stringify({ ...localStorage, ...sessionStorage })).not.toContain(password);
  expect(JSON.stringify({ ...localStorage, ...sessionStorage })).not.toContain(token);
});

it('rejects mismatched passwords and explains a completed installation', async () => {
  const fetch = vi.fn(async () => ({
    ok: false,
    status: 409,
    json: async () => ({ detail: 'Already configured. Sign in instead.' }),
  }));
  vi.stubGlobal('fetch', fetch);
  render(<Onboarding enabled onComplete={vi.fn()} />);
  enter('Company name', 'Demonstration');
  enter('Depot name', 'Main depot');
  enter('Depot latitude', '48.835');
  enter('Depot longitude', '2.375');
  fireEvent.click(screen.getByLabelText('I reviewed this vehicle entrance on the map'));
  next();
  admin();
  enter('Confirm password', 'a different password');
  fireEvent.click(screen.getByRole('button', { name: 'Create workspace' }));
  expect(screen.getByRole('alert')).toHaveTextContent('Passwords do not match');
  expect(fetch).not.toHaveBeenCalled();
  enter('Confirm password', password);
  fireEvent.click(screen.getByRole('button', { name: 'Create workspace' }));
  expect(await screen.findByText('Already configured. Sign in instead.')).toBeInTheDocument();
  const request = JSON.parse((fetch.mock.calls[0] as unknown as [string, RequestInit])[1].body as string);
  expect(request.company_name).toBe('Demonstration');
  expect(screen.getByLabelText('Password')).toHaveValue('');
});

it('shows an installation lock instead of an unprotected signup', () => {
  render(<Onboarding enabled={false} onComplete={vi.fn()} />);
  expect(screen.getByText('Installation setup is locked')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Create workspace' })).not.toBeInTheDocument();
});

it('keeps manual setup-code entry open and resets entrance review after a coordinate change', () => {
  window.history.replaceState(null, '', '/');
  render(<Onboarding enabled onComplete={vi.fn()} />);
  enter('Company name', 'Local company');
  enter('Depot name', 'Main');
  enter('Depot latitude', '48.8');
  enter('Depot longitude', '2.3');
  fireEvent.click(screen.getByLabelText('I reviewed this vehicle entrance on the map'));
  enter('Depot longitude', '2.4');
  expect(screen.getByLabelText('I reviewed this vehicle entrance on the map')).not.toBeChecked();
  fireEvent.click(screen.getByLabelText('I reviewed this vehicle entrance on the map'));
  next();
  enter('Private setup code', 'a');
  expect(screen.getByLabelText('Private setup code').closest('details')).toHaveAttribute('open');
  enter('Password', password);
  fireEvent.click(screen.getByRole('button', { name: 'Show passwords' }));
  expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'text');
});

it('finds the depot address with the setup code once IGN search is chosen, and keeps that choice', async () => {
  window.history.replaceState(null, '', `/#setup=${token}`);
  const calls: [string, RequestInit][] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, options: RequestInit) => {
      calls.push([url, options]);
      return {
        ok: true,
        json: async () =>
          url.includes('setup/geocode')
            ? [
                {
                  label: '20 Avenue de Ségur 75007 Paris',
                  latitude: 48.8507,
                  longitude: 2.3086,
                  score: 0.97,
                  kind: 'housenumber',
                },
              ]
            : { identity: { user_id: 'owner' }, csrf_token: 'csrf' },
      };
    }),
  );
  const complete = vi.fn();
  render(<Onboarding enabled onComplete={complete} />);
  enter('Company name', 'Acme');
  enter('Depot name', 'Main depot');
  // Off until chosen: an ordinary field, nothing is sent anywhere.
  expect(screen.queryByRole('combobox', { name: 'Depot address' })).toBeNull();
  fireEvent.click(screen.getByLabelText(/Find addresses with the IGN address service/));
  fireEvent.change(screen.getByRole('combobox', { name: 'Depot address' }), {
    target: { value: '20 avenue de segur paris' },
  });
  fireEvent.click(await screen.findByRole('option', { name: /20 Avenue de Ségur 75007 Paris/ }));
  expect(screen.getByLabelText('Depot latitude')).toHaveValue(48.8507);
  expect(screen.getByLabelText('I reviewed this vehicle entrance on the map')).not.toBeChecked();
  const [url, options] = calls[0];
  expect(url).toBe('/api/v1/setup/geocode?q=20%20avenue%20de%20segur%20paris&public=true');
  expect((options.headers as Record<string, string>)['X-Setup-Token']).toBe(token);
  fireEvent.click(screen.getByLabelText('I reviewed this vehicle entrance on the map'));
  next();
  admin();
  fireEvent.click(screen.getByRole('button', { name: 'Create workspace' }));
  await waitFor(() => expect(complete).toHaveBeenCalledOnce());
  const request = JSON.parse(calls.at(-1)![1].body as string);
  expect(request.public_address_lookup).toBe(true);
  expect(request.depot.address).toBe('20 Avenue de Ségur 75007 Paris');
  expect(request.depot.location).toEqual({ latitude: 48.8507, longitude: 2.3086, reviewed: true });
});

it('says what is missing on Continue, opening the closed coordinates section, instead of stopping silently', () => {
  window.history.replaceState(null, '', `/#setup=${token}`);
  render(<Onboarding enabled onComplete={vi.fn()} addressLookup="Base Adresse Nationale" />);
  const coordinates = screen.getByText('Enter coordinates').closest('details')!;
  expect(coordinates.open).toBe(false); // Address search is on: the coordinates start folded away.
  enter('Company name', 'Acme');
  enter('Depot name', 'Main depot');
  fireEvent.click(screen.getByLabelText('I reviewed this vehicle entrance on the map'));
  next();
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Pick an address suggestion or place the pin on the map to set the depot coordinates.',
  );
  expect(coordinates.open).toBe(true);
  expect(screen.getByRole('heading', { name: 'Set up your company and depot' })).toBeVisible();
  enter('Depot latitude', '95');
  next();
  expect(screen.getByRole('alert')).toHaveTextContent('Depot latitude: Use a value of at most 90.');
  enter('Depot latitude', '48.835');
  enter('Depot longitude', '2.375');
  next(); // Changing the coordinates cleared the review.
  expect(screen.getByRole('alert')).toHaveTextContent(
    'Confirm that you reviewed the vehicle entrance on the map.',
  );
  fireEvent.click(screen.getByLabelText('I reviewed this vehicle entrance on the map'));
  next();
  expect(screen.getByRole('heading', { name: 'Create the administrator account' })).toBeVisible();
  expect(screen.queryByRole('alert')).toBeNull();
});
