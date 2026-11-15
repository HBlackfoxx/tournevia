import { t, useLanguage } from './i18n';
import { useEffect, useRef, useState } from 'react';
import type { Map as MapType, Marker as MarkerType, GeoJSONSource, Popup as PopupType } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import type { Location } from './api';
import { Icon, iconElement } from './Brand';

/**
 * Basemap names in the interface language where the tiles carry them (OpenMapTiles `name:fr`, `name:en`),
 * the local name otherwise. Only labels that show a name change; road numbers and shields stay as drawn.
 */
function localiseLabels(map: MapType, language: string) {
  for (const layer of map.getStyle().layers ?? []) {
    if (layer.type !== 'symbol') continue;
    const field: unknown = map.getLayoutProperty(layer.id, 'text-field');
    if (!field || !JSON.stringify(field).includes('name')) continue;
    map.setLayoutProperty(layer.id, 'text-field', ['coalesce', ['get', `name:${language}`], ['get', 'name']]);
  }
}
/**
 * Room around the fitted points for what floats over the map (its heading, a route legend), so no pin
 * is fitted underneath them. An overlay in the lower half pushes the bottom edge up, one in the upper
 * half the top edge down; together they never take more than the map can give.
 */
function fitPadding(map: HTMLElement, overlays: (Element | null | undefined)[]) {
  const box = map.getBoundingClientRect();
  const padding = { top: 45, right: 45, bottom: 45, left: 45 };
  for (const overlay of overlays) {
    const r = overlay?.getBoundingClientRect();
    if (!r?.width || r.bottom <= box.top || r.top >= box.bottom || r.right <= box.left || r.left >= box.right)
      continue;
    if (r.top + r.height / 2 > box.top + box.height / 2)
      padding.bottom = Math.max(padding.bottom, box.bottom - r.top + 24);
    else padding.top = Math.max(padding.top, r.bottom - box.top + 24);
  }
  const room = box.height - 80;
  if (room > 0 && padding.top + padding.bottom > room) {
    const scale = room / (padding.top + padding.bottom);
    padding.top = Math.round(padding.top * scale);
    padding.bottom = Math.round(padding.bottom * scale);
  }
  return padding;
}
/**
 * Every route is drawn on the right of its own direction, like traffic: a route that goes and comes
 * back along a street shows both ways side by side. Routes sharing a road keep different lanes, each
 * a line's width further out (a fourth route takes the first lane again).
 */
const LANE_WIDTH = 4;
const FIRST_LANE = 3;
export const routeLane = (index: number) => index % 3;
const laneOffset = (lane: number) => FIRST_LANE + lane * LANE_WIDTH;
/** Arrows along each route show its direction: a white chevron with a dark rim, so it reads on any
 * route colour and on the map, drawn pixel by pixel (no canvas). */
function arrowImage(size = 18) {
  const data = new Uint8Array(size * size * 4);
  const segments = [
    [5, 4, 12, 9],
    [12, 9, 5, 14],
  ];
  const distance = (x: number, y: number, [x1, y1, x2, y2]: number[]) => {
    const dx = x2 - x1,
      dy = y2 - y1,
      along = Math.max(0, Math.min(1, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)));
    return Math.hypot(x - (x1 + along * dx), y - (y1 + along * dy));
  };
  for (let y = 0; y < size; y++)
    for (let x = 0; x < size; x++) {
      const near = Math.min(...segments.map((s) => distance(x + 0.5, y + 0.5, s)));
      const core = Math.max(0, Math.min(1, 1.9 - near)),
        rim = Math.max(0, Math.min(1, 3.1 - near));
      // White inside, dark at the edge (straight alpha: the colour blends from rim to core).
      const shade = Math.round(20 + 235 * core);
      data.set([shade, shade, shade, Math.round(Math.max(core, rim * 0.7) * 255)], (y * size + x) * 4);
    }
  return { width: size, height: size, data };
}
const ARROW_SIZE = 0.75;
/** A lane's offset across the line, in the arrow's own scaled pixels. */
const across = (lane: number): [number, number] => [0, laneOffset(lane) / ARROW_SIZE];
export type MapPoint = {
  id: string;
  label: string;
  location: Location;
  depot?: boolean;
  /** A depot pin for another place, such as a driver's home: a flag instead of the warehouse. */
  place?: boolean;
  number?: number;
  /** A depot's or place's code, shown on its marker beside the icon. */
  code?: string;
  /** Short route letter shown before the stop number, so routes are distinguishable without colour. */
  tag?: string;
  color?: string;
  detail?: string;
  address?: string;
};
export type MapRoute = { id: string; color: string; geometry: number[][]; selected?: boolean };
export function MapPanel({
  style,
  points,
  selected,
  draft,
  snapped,
  picking,
  onPick,
  onSelect,
  routes,
  onRouteSelect,
  overlays,
  stale = false,
}: {
  style: string;
  points: MapPoint[];
  selected?: string;
  draft?: Location | null;
  snapped?: [number, number][];
  picking: boolean;
  onPick: (point: Location) => void;
  onSelect: (id: string) => void;
  routes?: MapRoute[];
  onRouteSelect?: (id: string) => void;
  /** Elements drawn over the map, such as a route legend, that fitted points should stay clear of. */
  overlays?: () => (Element | null)[];
  stale?: boolean;
}) {
  const language = useLanguage();
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapType | null>(null);
  const markers = useRef<MarkerType[]>([]);
  // Fit/recenter only when the shown points or draft coordinates change, so users can pan and zoom between renders.
  const fitted = useRef<string | null>(null);
  const centered = useRef<string | null>(null);
  const popup = useRef<PopupType | null>(null);
  const heading = useRef<HTMLDivElement>(null);
  const callbacks = useRef({ picking, onPick, onSelect, onRouteSelect, overlays });
  callbacks.current = { picking, onPick, onSelect, onRouteSelect, overlays };
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!container.current || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => map.current?.resize());
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    let active = true;
    setFailed(false);
    setReady(false);
    fitted.current = null;
    centered.current = null;
    void import('maplibre-gl')
      .then((lib) => {
        if (!active || !container.current) return;
        try {
          lib.setWorkerUrl(workerUrl);
          const instance = new lib.Map({
            container: container.current,
            style,
            center: [2.375, 48.835],
            zoom: 11,
            locale: {
              'NavigationControl.ZoomIn': t('Zoom in'),
              'NavigationControl.ZoomOut': t('Zoom out'),
              'NavigationControl.ResetBearing': t('Reset bearing to north'),
            },
            attributionControl: false,
          });
          map.current = instance;
          instance.addControl(new lib.NavigationControl(), 'top-right');
          // Routes and road geometry come from OpenStreetMap whatever basemap style is configured.
          instance.addControl(
            new lib.AttributionControl({
              compact: false,
              customAttribution: t('Routes © OpenStreetMap contributors'),
            }),
            'bottom-right',
          );
          instance.on('style.load', () => localiseLabels(instance, language));
          instance.on('error', () => {
            if (active) setFailed(true);
          });
          instance.on('load', () => {
            if (active) setReady(true);
          });
          instance.on('click', (event) => {
            if (callbacks.current.picking)
              callbacks.current.onPick({
                latitude: +event.lngLat.lat.toFixed(6),
                longitude: +event.lngLat.wrap().lng.toFixed(6),
                reviewed: false,
              });
          });
        } catch {
          setFailed(true);
        }
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
      markers.current.forEach((m) => m.remove());
      markers.current = [];
      map.current?.remove();
      map.current = null;
    };
  }, [style, language]);
  useEffect(() => {
    if (!ready || !map.current) return;
    let active = true;
    void import('maplibre-gl').then((lib) => {
      if (!active || !map.current) return;
      const key = points.map((p) => `${p.id}:${p.location.latitude},${p.location.longitude}`).join('|');
      if ((fitted.current === null || (!selected && fitted.current !== key)) && !draft && points.length) {
        fitted.current = key;
        const xs = points.map((p) => p.location.longitude),
          ys = points.map((p) => p.location.latitude);
        map.current.fitBounds(
          [
            [Math.min(...xs), Math.min(...ys)],
            [Math.max(...xs), Math.max(...ys)],
          ],
          {
            padding: container.current
              ? fitPadding(container.current, [heading.current, ...(callbacks.current.overlays?.() ?? [])])
              : 45,
            maxZoom: 14,
            duration: 0,
          },
        );
      }
      markers.current.forEach((m) => m.remove());
      markers.current = [];
      popup.current?.remove();
      popup.current = null;
      for (const point of points) {
        if (point.id === selected && !routes) continue;
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `pin ${point.depot ? 'depot-pin' : ''} ${routes && point.id === selected ? 'selected-pin' : ''}`;
        if (point.color) button.style.background = point.color;
        // Depots and places carry an icon, not a letter, so they never read as route D or route P.
        if (point.depot) {
          button.append(iconElement(point.place ? 'place' : 'depot', 15));
          if (point.code) button.append(point.code);
        } else button.textContent = point.number ? `${point.tag ?? ''}${point.number}` : '•';
        // Pins are pointer shortcuts; lists beside every map are the keyboard path, so hundreds of
        // pins never sit in the tab order. The label still names the stop for assistive tech.
        button.tabIndex = -1;
        button.setAttribute('aria-label', point.detail ? `${point.label} · ${point.detail}` : point.label);
        button.addEventListener('click', (e) => {
          e.stopPropagation();
          callbacks.current.onSelect(point.id);
        });
        markers.current.push(
          new lib.Marker({ element: button })
            .setLngLat([point.location.longitude, point.location.latitude])
            .addTo(map.current),
        );
      }
      if (
        draft &&
        Number.isFinite(draft.latitude) &&
        Number.isFinite(draft.longitude) &&
        Math.abs(draft.latitude) <= 90 &&
        Math.abs(draft.longitude) <= 180
      ) {
        markers.current.push(
          new lib.Marker({ color: '#1766e8' })
            .setLngLat([draft.longitude, draft.latitude])
            .addTo(map.current),
        );
        const at = `${draft.latitude},${draft.longitude}`;
        if (centered.current !== at) {
          centered.current = at;
          map.current.easeTo({ center: [draft.longitude, draft.latitude], zoom: 14, duration: 0 });
        }
      }
      for (const point of snapped ?? []) {
        const element = document.createElement('span');
        element.className = 'snap-pin';
        element.title = t('Road-snapped endpoint');
        markers.current.push(new lib.Marker({ element }).setLngLat(point).addTo(map.current));
      }
      if (routes && selected) {
        const point = points.find((p) => p.id === selected);
        if (point) {
          map.current.easeTo({ center: [point.location.longitude, point.location.latitude], duration: 0 });
          const card = document.createElement('div');
          card.className = 'map-stop-popup';
          for (const [tag, text] of [
            ['strong', point.label],
            ['p', point.address],
            ['small', stale ? t('Previous result · not recalculated') : point.detail],
          ] as [string, string | undefined][]) {
            if (text) {
              const line = document.createElement(tag);
              line.textContent = text;
              card.append(line);
            }
          }
          popup.current = new lib.Popup({
            closeButton: false,
            closeOnClick: false,
            offset: 18,
            anchor: 'bottom-left',
          })
            .setLngLat([point.location.longitude, point.location.latitude])
            .setDOMContent(card)
            .addTo(map.current);
        }
      }
    });
    return () => {
      active = false;
    };
  }, [points, selected, draft, snapped, ready, routes, stale]);
  useEffect(() => {
    const instance = map.current;
    if (!ready || !instance || !routes) return;
    const data = {
      type: 'FeatureCollection' as const,
      features: routes
        .filter((r) => r.geometry.length >= 2)
        .map((r) => ({
          type: 'Feature' as const,
          properties: {
            id: r.id,
            color: r.color,
            selected: r.selected ?? false,
            lane: routeLane(routes.indexOf(r)),
          },
          geometry: { type: 'LineString' as const, coordinates: r.geometry },
        })),
    };
    const source = instance.getSource<GeoJSONSource>('planned-routes');
    if (source) source.setData(data);
    else {
      instance.addSource('planned-routes', { type: 'geojson', data });
      instance.addLayer({
        id: 'planned-routes',
        type: 'line',
        source: 'planned-routes',
        // The selected route is drawn last, on top of any route sharing its road.
        layout: { 'line-sort-key': ['case', ['get', 'selected'], 1, 0] },
        paint: {
          'line-color': ['get', 'color'],
          'line-width': ['case', ['get', 'selected'], 8, 4],
          'line-opacity': stale ? 0.35 : 0.8,
          'line-offset': ['+', FIRST_LANE, ['*', ['get', 'lane'], LANE_WIDTH]],
        },
      });
      if (!instance.hasImage('route-arrow')) instance.addImage('route-arrow', arrowImage());
      instance.addLayer({
        id: 'planned-route-arrows',
        type: 'symbol',
        source: 'planned-routes',
        layout: {
          'symbol-placement': 'line',
          'symbol-spacing': 90,
          'symbol-sort-key': ['case', ['get', 'selected'], 1, 0],
          'icon-image': 'route-arrow',
          'icon-size': ARROW_SIZE,
          'icon-rotation-alignment': 'map',
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
          // Across the line, in the arrow's own scaled pixels: the arrow sits on its route's lane.
          'icon-offset': [
            'match',
            ['get', 'lane'],
            1,
            ['literal', across(1)],
            2,
            ['literal', across(2)],
            ['literal', across(0)],
          ],
        },
        paint: { 'icon-opacity': stale ? 0.35 : 1 },
      });
      instance.on('click', 'planned-routes', (event) => {
        const id = event.features?.[0]?.properties?.id;
        if (typeof id === 'string') callbacks.current.onRouteSelect?.(id);
      });
    }
    instance.setPaintProperty('planned-routes', 'line-opacity', stale ? 0.35 : 0.8);
    instance.setPaintProperty('planned-route-arrows', 'icon-opacity', stale ? 0.35 : 1);
  }, [ready, routes, stale]);
  return (
    <section className="map-panel" aria-label={routes ? t('Route review map') : t('Coordinate review map')}>
      <div ref={heading} className="map-heading">
        <span>{t('Map')}</span>
        {stale && <span className="badge attention">{t('Previous result · not recalculated')}</span>}
      </div>
      <div ref={container} className="map-canvas" />
      {failed && (
        <p role="alert" className="map-error">
          {t(
            'Basemap unavailable. Coordinates and tables remain usable; edit the latitude and longitude fields.',
          )}
        </p>
      )}
      <details className="map-note">
        <summary aria-label={t('Map information')}>
          <Icon name="info" />
        </summary>
        <p>
          {routes
            ? t('Numbered service points; lines use actual saved engine geometry.')
            : t('Blue: draft service pin · Blue square: road-snapped endpoint.')}{' '}
          {t('Basemap requests reveal the viewed area; record details stay local.')}
        </p>
      </details>
    </section>
  );
}
