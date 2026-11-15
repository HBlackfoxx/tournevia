import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { CalculationSettings } from './CalculationSettings';

it('saves a safety margin in whole minutes from 0 to 120, and refreshes the workspace', async () => {
  const fetch = vi.fn(async () => ({ ok: true, json: async () => ({ minutes: 20 }) }));
  vi.stubGlobal('fetch', fetch);
  const changed = vi.fn();
  render(<CalculationSettings minutes={15} onChanged={changed} />);
  const field = screen.getByLabelText('Safety margin before each window ends');
  const save = screen.getByRole('button', { name: 'Save the safety margin' });
  // Unchanged: nothing to save.
  expect(field).toHaveValue(15);
  expect(save).toBeDisabled();
  fireEvent.change(field, { target: { value: '121' } });
  expect(screen.getByRole('alert')).toHaveTextContent('Enter whole minutes from 0 to 120.');
  expect(save).toBeDisabled();
  fireEvent.change(field, { target: { value: '20' } });
  fireEvent.click(save);
  expect(await screen.findByText('New calculations keep 20 min before each window ends.')).toBeVisible();
  const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe('/api/v1/workspace/safety-margin');
  expect(init.method).toBe('PUT');
  expect(JSON.parse(String(init.body))).toEqual({ minutes: 20 });
  await waitFor(() => expect(changed).toHaveBeenCalledOnce());
});
