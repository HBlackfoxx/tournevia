import { act, render, screen, waitFor } from '@testing-library/react';
import { it, expect, vi } from 'vitest';
import { MapPanel, type MapPoint } from './MapPanel';
const map = vi.hoisted(() => ({
  fail: true,
  load: null as null | (() => void),
  fitBounds: vi.fn(),
  easeTo: vi.fn(),
  pins: [] as HTMLElement[],
  attribution: null as null | { customAttribution?: string },
  layers: [] as { id: string }[],
  sources: {} as Record<string, unknown>,
}));
vi.mock('maplibre-gl', () => ({
  setWorkerUrl: vi.fn(),
  NavigationControl: class {},
  AttributionControl: class {
    constructor(options: { customAttribution?: string }) {
      map.attribution = options;
    }
  },
  Marker: class {
    constructor(options?: { element?: HTMLElement }) {
      if (options?.element) map.pins.push(options.element);
    }
    setLngLat() {
      return this;
    }
    addTo() {
      return this;
    }
    remove() {}
  },
  Map: class {
    constructor() {
      if (map.fail) throw new Error('WebGL unavailable');
    }
    addControl() {}
    on(event: string, handler: unknown) {
      if (event === 'load') map.load = handler as () => void;
    }
    fitBounds(...args: unknown[]) {
      map.fitBounds(...args);
    }
    easeTo(...args: unknown[]) {
      map.easeTo(...args);
    }
    remove() {}
    resize() {}
    getSource() {
      return undefined;
    }
    addSource(id: string, source: unknown) {
      map.sources[id] = source;
    }
    addLayer(layer: { id: string }) {
      map.layers.push(layer);
    }
    hasImage() {
      return false;
    }
    addImage() {}
    setPaintProperty() {}
  },
}));
it('reports unavailable WebGL without removing other page content', async () => {
  render(
    <>
      <table>
        <tbody>
          <tr>
            <td>Delivery still accessible</td>
          </tr>
        </tbody>
      </table>
      <MapPanel
        style="https://maps.example/style"
        points={[]}
        picking={false}
        onPick={vi.fn()}
        onSelect={vi.fn()}
      />
    </>,
  );
  expect(await screen.findByRole('alert')).toHaveTextContent('Coordinates and tables remain usable');
  expect(screen.getByText('Delivery still accessible')).toBeInTheDocument();
});
it('fits and recenters only when points or the draft actually move, so users can pan and zoom', async () => {
  map.fail = false;
  const points: MapPoint[] = [
    {
      id: 'depot',
      label: 'Depot',
      location: { latitude: 48.8, longitude: 2.3, reviewed: true },
      depot: true,
    },
    { id: 'order', label: 'Order', location: { latitude: 48.9, longitude: 2.4, reviewed: true } },
  ];
  const settle = () =>
    act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
  const panel = (next: MapPoint[], draft?: MapPoint['location']) => (
    <MapPanel
      style="https://maps.example/style"
      points={next}
      draft={draft}
      picking={false}
      onPick={vi.fn()}
      onSelect={vi.fn()}
    />
  );
  const view = render(panel(points));
  await waitFor(() => expect(map.load).not.toBeNull());
  act(() => map.load!());
  await waitFor(() => expect(map.fitBounds).toHaveBeenCalledTimes(1));
  view.rerender(panel(points.map((p) => ({ ...p, location: { ...p.location } }))));
  await settle();
  expect(map.fitBounds).toHaveBeenCalledTimes(1);
  view.rerender(panel([points[0], { ...points[1], location: { ...points[1].location, latitude: 48.95 } }]));
  await settle();
  expect(map.fitBounds).toHaveBeenCalledTimes(2);
  view.rerender(panel([], { latitude: 48.85, longitude: 2.35, reviewed: false }));
  await settle();
  view.rerender(panel([], { latitude: 48.85, longitude: 2.35, reviewed: false }));
  await settle();
  expect(map.easeTo).toHaveBeenCalledTimes(1);
  view.rerender(panel([], { latitude: 48.86, longitude: 2.35, reviewed: false }));
  await settle();
  expect(map.easeTo).toHaveBeenCalledTimes(2);
});

it('labels pins with their route letter, keeps them out of the tab order and credits OpenStreetMap routes', async () => {
  map.fail = false;
  map.load = null;
  map.pins = [];
  render(
    <MapPanel
      style="https://maps.example/style"
      points={[
        {
          id: 'stop',
          label: 'ORDER-7',
          detail: '08:10 · VAN-1 · stop 3',
          tag: 'B',
          number: 3,
          location: { latitude: 48.9, longitude: 2.4, reviewed: true },
        },
      ]}
      routes={[]}
      picking={false}
      onPick={vi.fn()}
      onSelect={vi.fn()}
    />,
  );
  await waitFor(() => expect(map.load).not.toBeNull());
  act(() => map.load!());
  await waitFor(() => expect(map.pins).toHaveLength(1));
  expect(map.pins[0].textContent).toBe('B3');
  expect(map.pins[0].tabIndex).toBe(-1);
  expect(map.pins[0].getAttribute('aria-label')).toBe('ORDER-7 · 08:10 · VAN-1 · stop 3');
  expect(map.attribution?.customAttribution).toBe('Routes © OpenStreetMap contributors');
});

it('marks depots and places with an icon, not a letter, and fits points clear of what covers the map', async () => {
  map.fail = false;
  map.load = null;
  map.pins = [];
  map.fitBounds.mockClear();
  const legend = document.createElement('div');
  const box = (top: number, height: number, width = 800) =>
    ({ top, bottom: top + height, left: 0, right: width, width, height, x: 0, y: top }) as DOMRect;
  // A 600 px map: its heading covers the top 50 px, a route legend the bottom 100 px.
  const rects = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (
    this: HTMLElement,
  ) {
    if (this.classList.contains('map-canvas')) return box(0, 600);
    if (this.classList.contains('map-heading')) return box(10, 40, 80);
    if (this === legend) return box(500, 80, 400);
    return box(0, 0, 0);
  });
  const at = { latitude: 48.85, longitude: 2.35, reviewed: true };
  render(
    <MapPanel
      style="https://maps.example/style"
      points={[
        { id: 'depot', label: 'MAIN · Depot', location: at, depot: true, code: 'MAIN' },
        { id: 'home', label: 'HOME · Driver', location: { ...at, latitude: 48.9 }, depot: true, place: true },
        { id: 'stop', label: 'ORDER-1', tag: 'D', number: 1, location: { ...at, longitude: 2.4 } },
      ]}
      routes={[]}
      overlays={() => [legend]}
      picking={false}
      onPick={vi.fn()}
      onSelect={vi.fn()}
    />,
  );
  await waitFor(() => expect(map.load).not.toBeNull());
  act(() => map.load!());
  await waitFor(() => expect(map.pins).toHaveLength(3));
  expect(map.fitBounds.mock.lastCall?.[1]).toMatchObject({
    padding: { top: 74, right: 45, bottom: 124, left: 45 },
  });
  const [depot, home, stop] = map.pins;
  // Route D's first stop reads "D1"; the depot shows a warehouse, the place a flag.
  expect(stop.textContent).toBe('D1');
  // ... with its code beside it.
  expect(depot.textContent).toBe('MAIN');
  expect(depot.querySelector('svg path')?.getAttribute('d')).toMatch(/^M3 10 12 4.5/);
  expect(home.querySelector('svg path')?.getAttribute('d')).toMatch(/^M6 21V3.5/);
  expect(depot.getAttribute('aria-label')).toBe('MAIN · Depot');
  rects.mockRestore();
});

it('draws routes that share a road side by side, each with arrows in its direction', async () => {
  map.fail = false;
  map.load = null;
  map.layers = [];
  map.sources = {};
  const line = [
    [2.35, 48.85],
    [2.36, 48.86],
  ];
  render(
    <MapPanel
      style="https://maps.example/style"
      points={[]}
      routes={[
        { id: 'a', color: '#1766e8', geometry: line },
        { id: 'b', color: '#008473', geometry: line, selected: true },
        { id: 'c', color: '#8551c9', geometry: line },
      ]}
      picking={false}
      onPick={vi.fn()}
      onSelect={vi.fn()}
    />,
  );
  await waitFor(() => expect(map.load).not.toBeNull());
  act(() => map.load!());
  await waitFor(() =>
    expect(map.layers.map((l) => l.id)).toEqual(['planned-routes', 'planned-route-arrows']),
  );
  const source = map.sources['planned-routes'] as {
    data: { features: { properties: { lane: number; selected: boolean } }[] };
  };
  // The same road, three lanes, each on the right of its route's direction.
  expect(source.data.features.map((f) => f.properties.lane)).toEqual([0, 1, 2]);
  const [lines, arrows] = map.layers as unknown as {
    layout: Record<string, unknown>;
    paint: Record<string, unknown>;
  }[];
  expect(lines.paint['line-offset']).toEqual(['+', 3, ['*', ['get', 'lane'], 4]]);
  expect(lines.layout['line-sort-key']).toEqual(['case', ['get', 'selected'], 1, 0]);
  expect(arrows.layout['symbol-placement']).toBe('line');
  expect(arrows.layout['icon-image']).toBe('route-arrow');
});
