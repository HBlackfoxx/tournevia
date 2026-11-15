"""Fixed regional synthetic workload; no customer addresses or inferred requirements."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import UUID

from app.planning import PlanningInput

POINTS = Path(__file__).with_name('regional-250-points.json')


def regional_input(seed: PlanningInput) -> PlanningInput:
    points = json.loads(POINTS.read_text())['points']
    coordinates = [tuple(p['coordinates']) for p in points]
    depot = (seed.depot.data.location.longitude, seed.depot.data.location.latitude)
    if len(coordinates) != 250 or len(set(coordinates)) != 250 or depot in coordinates:
        raise ValueError('Regional workload requires 250 fixed distinct service points.')
    data = seed.model_dump(mode='json')
    data['vehicles'], data['orders'] = [], []
    for group in range(25):
        kind = group % 3
        vehicle = deepcopy(seed.vehicles[0 if kind == 0 else 1].model_dump(mode='json'))
        vehicle['id'] = str(UUID(int=3000 + group))
        v = vehicle['data']
        v.update(fleet_number=f'REGION-{group+1:02}',
                 skills=[['TAIL_LIFT', 'REFRIGERATED'], ['TAIL_LIFT'], []][kind],
                 limits={'max_stops': 12, 'max_distance_m': 180000, 'max_driving_seconds': 18000})
        v['capacity']['pallet_ind_units'] = [6, 4, 2][kind]
        if kind == 1:
            v['routing'].update(height_mm=3000, width_mm=2300, length_mm=7200,
                                gross_weight_kg=7500, axle_load_kg=4000)
            v['capacity'].update(weight_kg=3500, volume_dm3=22000, pallet_eur_units=10, loading_length_mm=5000)
            v['cost'].update(fixed_minor=6000, per_km_minor=60, per_hour_minor=3100)
        data['vehicles'].append(vehicle)
        for stop in range(10):
            index = group * 10 + stop
            lon, lat = coordinates[index]
            order = deepcopy(seed.orders[0].model_dump(mode='json'))
            order['id'] = str(UUID(int=4000 + index))
            o = order['data']
            o.update(external_id=f'REGION-ORDER-{index+1:03}',
                     address=f'Synthetic regional grid cell {group+1:02}, point {stop+1:02}',
                     instructions='', priority=[0, 50, 100][stop % 3],
                     service_seconds=120 + 60 * (stop % 4),
                     required_skills=(['REFRIGERATED'] if kind == 0 else ['TAIL_LIFT'])
                     if kind != 2 and stop % 3 == 0 else [],
                     demand={'weight_kg': 40 + 10 * stop, 'volume_dm3': 200 + 50 * stop,
                             'pallet_eur_units': int(stop % 4 == 0), 'pallet_ind_units': int(stop == 7),
                             'loading_length_mm': 100 + 20 * stop},
                     location={'longitude': lon, 'latitude': lat, 'reviewed': True})
            start = seed.vehicles[0].data.available_window.start
            o['time_window'] = {'start': start.replace(hour=8 if stop < 5 else 12, minute=0, second=0).isoformat(),
                                'end': start.replace(hour=12 if stop < 5 else 16, minute=0, second=0).isoformat()}
            data['orders'].append(order)
    return PlanningInput.model_validate(data)


def equipped_regional_input(seed: PlanningInput) -> PlanningInput:
    """Separate fleet scenario, fixed before objective comparisons.

    Replace REGION-01 and REGION-25 with explicitly refrigerated 7.5-tonne
    vehicles. Keep every delivery/pin/window and all other vehicles unchanged.
    This is synthetic fleet planning, not an edit to the original negative case
    or a claim that adding refrigeration to a real truck preserves its payload.
    """
    model = regional_input(seed)
    medium = model.vehicles[1].data
    for index in (0, 24):
        vehicle = model.vehicles[index].data
        vehicle.routing = medium.routing.model_copy(deep=True)
        vehicle.capacity = medium.capacity.model_copy(deep=True)
        vehicle.cost = medium.cost.model_copy(deep=True)
        vehicle.skills = ['TAIL_LIFT', 'REFRIGERATED']
    return PlanningInput.model_validate(model.model_dump())


def sample_points(url: str) -> dict:
    """One-time fixture construction, before timing or solver outcomes are known.

    Correlate a fixed grid to nearby local roads. This is synthetic point creation,
    not a relaxed application snap limit or evidence of a real unloading location.
    Do not skip a grid point when no eligible edge is found.
    """
    points = []
    for row in range(5):
        for column in range(5):
            for stop in range(10):
                lon = round(2.20 + column * .10 + (stop % 5 - 2) * .006, 6)
                lat = round(48.75 + row * .06 + (stop // 5 - .5) * .006, 6)
                eligible = []
                # Locate may return only the closest edge (e.g. a forest track).
                # Inspect a fixed surrounding lattice before selecting a local road.
                locations = [{'lon': lon + dx * .003, 'lat': lat + dy * .002,
                              'radius': 0, 'search_cutoff': 1000}
                             for dx in range(-3, 4) for dy in range(-3, 4)]
                payload = {'locations': locations, 'costing': 'truck', 'verbose': True}
                request = Request(url.rstrip('/') + '/locate', json.dumps(payload).encode(),
                                  {'Content-Type': 'application/json'})
                with urlopen(request, timeout=10) as response:
                    located = json.load(response)
                from app.routing import meters
                used = {tuple(p['coordinates']) for p in points}
                for location in located:
                    for candidate in location.get('edges') or []:
                        edge = candidate['edge']; road = edge['classification']
                        pin = (candidate['correlated_lon'], candidate['correlated_lat'])
                        distance = meters((lon, lat), pin)
                        if (distance <= 1000 and pin not in used and edge['access']['truck']
                                and road['classification'] in {'residential', 'service_other', 'unclassified', 'tertiary', 'secondary'}
                                and road['use'] in {'road', 'service_road'} and not road['link'] and not road['internal']):
                            eligible.append((distance, candidate))
                if not eligible:
                    raise ValueError(f'No eligible local-road point for grid cell {row},{column}, stop {stop}.')
                distance, chosen = min(eligible, key=lambda pair: (pair[0], pair[1]['correlated_lon'], pair[1]['correlated_lat'], pair[1]['edge_id']['value']))
                points.append({'grid': [lon, lat], 'coordinates': [chosen['correlated_lon'], chosen['correlated_lat']],
                               'grid_distance_m': round(distance, 2),
                               'road_class': chosen['edge']['classification']['classification']})
    if len({tuple(p['coordinates']) for p in points}) != 250:
        raise ValueError('Grid correlated to duplicate pins; fixture rejected, no pins silently removed.')
    return {'source': 'Local Valhalla graph from Geofabrik Ile-de-France downloaded 2026-09-22; OpenStreetMap contributors (ODbL).',
            'scope': 'Synthetic 5-by-5 grid, ten nearby local-road points per cell. Not actual delivery premises or operator-reviewed access.',
            'selection': 'Nearest unused truck-accessible local-road pin among a fixed 7-by-7 locate lattice, within 1000 m of each grid point; fixed before benchmark results. Application snap limit remains 50 m.',
            'points': points}


def main():
    parser = argparse.ArgumentParser(description='Create regional synthetic points once; never resample during acceptance.')
    parser.add_argument('--valhalla-url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    points = sample_points(args.valhalla_url)
    with args.output.open('x') as output:
        output.write(json.dumps(points, indent=2) + '\n')


if __name__ == '__main__':
    main()
