import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { it, expect, vi } from 'vitest';
import { ImportPanel } from './ImportPanel';
import columns from './csv-columns.json';
import { saved } from './test-server';
import fixture from '../../backend/tests/fixtures/paris-ten.json';

it.each(['orders', 'vehicles'] as const)(
  'shows parsed %s units and values before committing',
  async (kind) => {
    const data =
      kind === 'orders'
        ? { ...fixture.orders[0].data, location: null, instructions: 'Use rear entrance' }
        : saved(fixture.vehicles[0]).data;
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => ({
        ok: true,
        json: async () => ({
          dry_run: true,
          committed: false,
          inserted: 1,
          rows: [{ row: 2, action: 'INSERT', data }],
          errors: [],
        }),
      })),
    );
    const commit = vi.fn();
    render(<ImportPanel kind={kind} onCommit={commit} />);
    fireEvent.change(screen.getByLabelText(kind === 'orders' ? 'Deliveries CSV file' : 'Vehicles CSV file'), {
      target: { files: [new File(['csv'], 'preview.csv')] },
    });
    fireEvent.click(screen.getByText('Preview CSV'));
    fireEvent.click(await screen.findByText(/Review values for/));
    const value = (label: string) =>
      screen.getByText(label, { selector: 'dt' }).nextElementSibling?.textContent;
    if (kind === 'orders') {
      expect(value('Latitude')).toBe('Unresolved');
      expect(value('Point reviewed')).toBe('No — review after import');
      expect(value('Load — weight (kg)')).toBe(String(fixture.orders[0].data.demand.weight_kg));
      expect(value('Service window start')).toBe(fixture.orders[0].data.time_window.start);
      expect(value('Instructions')).toBe('Use rear entrance');
    } else {
      expect(value('Gross routing weight (kg)')).toBe(
        String(fixture.vehicles[0].data.routing.gross_weight_kg),
      );
      expect(value('Capacity — weight (kg)')).toBe(String(fixture.vehicles[0].data.capacity.weight_kg));
      expect(value('Distance rate (EUR cents/km)')).toBe(String(fixture.vehicles[0].data.cost.per_km_minor));
      expect(value('Weekly hours')).toBe('Mon–Sun 07:30–17:00');
    }
    expect(commit).not.toHaveBeenCalled();
    expect(screen.getByText(/^Import (deliveries|vehicles)$/)).toBeEnabled();
  },
);

it('invalidates a preview when the selected file changes and disables duplicate commits', async () => {
  const fetch = vi.fn(async () => ({
    ok: true,
    json: async () => ({ dry_run: true, committed: false, inserted: 1, unchanged: 0, rows: [], errors: [] }),
  }));
  vi.stubGlobal('fetch', fetch);
  render(<ImportPanel kind="orders" onCommit={vi.fn()} />);
  const input = screen.getByLabelText('Deliveries CSV file');
  fireEvent.change(input, { target: { files: [new File(['first'], 'first.csv')] } });
  fireEvent.click(screen.getByText('Preview CSV'));
  await screen.findByText('New');
  expect(screen.getByText('Import deliveries')).toBeEnabled();
  fireEvent.change(input, { target: { files: [new File(['second'], 'second.csv')] } });
  expect(screen.queryByText('Import deliveries')).not.toBeInTheDocument();
  expect(screen.queryByText('New')).not.toBeInTheDocument();
});
it('shows row errors and cannot commit an invalid file', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: false,
      status: 422,
      json: async () => ({
        dry_run: true,
        committed: false,
        inserted: 0,
        unchanged: 0,
        rows: [],
        errors: [{ row: 2, field: 'weight_kg', message: 'Use whole kilograms.' }],
      }),
    })),
  );
  render(<ImportPanel kind="vehicles" onCommit={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('Vehicles CSV file'), {
    target: { files: [new File(['bad'], 'bad.csv')] },
  });
  fireEvent.click(screen.getByText('Preview CSV'));
  expect(await screen.findByText('weight_kg')).toBeInTheDocument();
  expect(screen.getByText(/Use whole kilograms/)).toBeInTheDocument();
  expect(screen.getByText('Import vehicles')).toBeDisabled();
});

it('shows the exact header row the server expects, copies it and offers it as a template file', async () => {
  const view = render(<ImportPanel kind="orders" onCommit={vi.fn()} />);
  // The row comes from the server's own column list (csv-columns.json, generated from the backend).
  const row = [...columns.orders.required, ...columns.orders.optional].join(',');
  expect(row.startsWith('external_id,service_date,active,address,latitude,longitude')).toBe(true);
  expect(document.getElementById('header-row-orders')).toHaveTextContent(row);
  expect(screen.getByText('priority,customer,ships_from')).toHaveClass('optional-columns');
  expect(screen.getByText(/The columns from priority on are optional/)).toBeInTheDocument();
  const writeText = vi.fn(async () => {});
  vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } });
  fireEvent.click(screen.getByRole('button', { name: 'Copy the header row' }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith(row));
  expect(await screen.findByText('Header row copied.')).toBeInTheDocument();
  const created: Blob[] = [];
  vi.stubGlobal('URL', {
    ...URL,
    createObjectURL: (blob: Blob) => (created.push(blob), 'blob:template'),
    revokeObjectURL: vi.fn(),
  });
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  fireEvent.click(screen.getByRole('button', { name: 'Download the template' }));
  expect(click).toHaveBeenCalledOnce();
  // A UTF-8 byte-order mark first (decoding text drops it), then the header row.
  expect([...new Uint8Array(await created[0].arrayBuffer()).slice(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
  expect(await created[0].text()).toBe(row + '\r\n');
  click.mockRestore();
  view.unmount();
  render(<ImportPanel kind="vehicles" onCommit={vi.fn()} />);
  expect(screen.getByText('max_stops,max_distance_m,max_driving_seconds,start,end')).toHaveClass(
    'optional-columns',
  );
  // Vehicles have no review step after importing.
  expect(screen.getAllByRole('listitem').map((li) => li.textContent)).not.toContain('4 Review locations');
  expect(screen.getByText('1 Choose file')).toHaveAttribute('aria-current', 'step');
});

it('says where each delivery point comes from and which rows to check, before and after importing', async () => {
  const placed = { latitude: 48.850699, longitude: 2.308628, reviewed: false };
  const row = (n: number, external_id: string, location: typeof placed | null) => ({
    ...fixture.orders[0].data,
    external_id,
    location,
  });
  const match = {
    label: '20 Avenue de Ségur 75007 Paris',
    latitude: 48.850699,
    longitude: 2.308628,
    score: 0.97,
    kind: 'housenumber',
  };
  const preview = (committed: boolean) => ({
    dry_run: !committed,
    committed,
    rows: [
      { row: 2, action: 'INSERT', data: row(2, 'D001', placed), location_source: 'FILE', warnings: [] },
      {
        row: 3,
        action: 'INSERT',
        data: row(3, 'D002', placed),
        address_match: match,
        numbered: true,
        location_source: 'ADDRESS',
        warnings: [],
      },
      {
        row: 4,
        action: 'INSERT',
        data: row(4, 'D003', null),
        numbered: true,
        location_source: 'NONE',
        warnings: [
          {
            code: 'ADDRESS_VAGUE',
            message: 'Only the town or place was found (Paris), so no point was placed.',
            template: 'Only the town or place was found ({0}), so no point was placed.',
            values: ['Paris'],
          },
        ],
      },
    ],
    errors: [],
    address_lookup: 'INCOMPLETE',
  });
  let committed = false;
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({ ok: true, json: async () => preview(committed) })),
  );
  const onReview = vi.fn();
  render(<ImportPanel kind="orders" onCommit={vi.fn()} onReview={onReview} />);
  expect(
    screen.getByText(/Leave external_id empty to give a row the next delivery number/),
  ).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Deliveries CSV file'), {
    target: { files: [new File(['csv'], 'preview.csv')] },
  });
  fireEvent.click(screen.getByText('Preview CSV'));
  expect(
    await screen.findByText(
      '0 from saved customers · 1 from the file · 1 found from the address · 1 without a point',
    ),
  ).toBeVisible();
  expect(screen.getByText('Point from the file')).toBeInTheDocument();
  expect(screen.getByText('Found from the address · match 97 %')).toBeInTheDocument();
  expect(screen.getByText('No point')).toBeInTheDocument();
  expect(screen.getAllByText('New number')).toHaveLength(2);
  expect(screen.getByText('Only the town or place was found (Paris), so no point was placed.')).toBeVisible();
  expect(screen.getByText(/Address lookup ran out of time/)).toBeVisible();
  fireEvent.click(screen.getByLabelText('Only rows to check (1)'));
  expect(screen.queryByText('D001')).toBeNull();
  expect(screen.getByText('D003')).toBeInTheDocument();
  committed = true;
  fireEvent.click(screen.getByRole('button', { name: 'Import deliveries' }));
  fireEvent.click(await screen.findByRole('button', { name: 'Place the missing points' }));
  expect(onReview).toHaveBeenCalledWith('missing');
  fireEvent.click(screen.getByRole('button', { name: 'Review the imported points' }));
  expect(onReview).toHaveBeenLastCalledWith('review');
});

it('lists what a row changes in an existing record, and keeps a saved point when the row has none', async () => {
  const kept = { latitude: 48.85, longitude: 2.4, reviewed: true };
  const urls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      urls.push(url);
      return {
        ok: true,
        json: async () => ({
          dry_run: true,
          committed: false,
          rows: [
            {
              row: 2,
              action: 'UPDATE',
              changes: ['service_seconds', 'time_window'],
              data: { ...fixture.orders[0].data, external_id: 'D001', location: kept },
              location_source: 'SAVED',
              warnings: [],
            },
            {
              row: 3,
              action: 'UNCHANGED',
              changes: [],
              data: { ...fixture.orders[1].data, external_id: 'D002' },
              location_source: 'FILE',
              warnings: [],
            },
          ],
          errors: [],
        }),
      };
    }),
  );
  render(<ImportPanel kind="orders" onCommit={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('Deliveries CSV file'), {
    target: { files: [new File(['csv'], 'changed.csv')] },
  });
  fireEvent.click(screen.getByText('Preview CSV'));
  expect(await screen.findByText('Service duration · Service window')).toBeVisible();
  expect(urls[0]).toBe('/api/v1/imports/orders?dry_run=true&update_existing=true');
  expect(screen.getByText('Changed', { selector: '.badge' })).toBeInTheDocument();
  expect(screen.getByText('Saved point kept')).toBeInTheDocument();
  expect(screen.getByText('1 existing delivery keeps its saved point')).toBeVisible();
  const metric = (title: string) =>
    screen.getByText(title, { selector: '.import-metrics small' }).nextElementSibling;
  expect(metric('Changed')).toHaveTextContent('1');
  expect(metric('Unchanged')).toHaveTextContent('1');
  expect(screen.getByText(/A changed delivery without coordinates keeps its saved point/)).toBeVisible();
});
