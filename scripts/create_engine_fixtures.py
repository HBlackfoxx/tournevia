"""Create synthetic fixtures; writes no customer data and makes no HTTP requests."""
from datetime import date, datetime, time
import json
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'backend/tests/fixtures'


def dated(vehicle, day, zone):
    """An example vehicle, saved with weekly hours, as a calculation takes it: that day's hours as a dated window."""
    hours = vehicle['hours'][day.strftime('%A').lower()]
    window = {edge: datetime.combine(day, time.fromisoformat(hours[edge]), zone).isoformat() for edge in ('start', 'end')}
    return {'available_window' if key == 'hours' else key: window if key == 'hours' else value
            for key, value in vehicle.items()}


def paris():
    def read(name):
        return json.loads((ROOT / f'examples/{name}.json').read_text(encoding='utf-8'))
    owner = '00000000-0000-4000-8000-000000000001'
    def owned(number, data):
        return {'id': str(UUID(int=number)), 'organization_id': owner, 'version': 1, 'data': data}
    # Stop 3 was corrected to an engine-observed road point after the original pin
    # failed the unchanged 50 m cutoff. Synthetic access review is not customer review.
    points = [(48.841, 2.386), (48.831, 2.367), (48.835281, 2.374645), (48.849, 2.374),
              (48.855, 2.381), (48.858, 2.359), (48.829, 2.350), (48.823, 2.377),
              (48.838, 2.395), (48.850, 2.401)]
    orders = []
    for index, (lat, lon) in enumerate(points, 1):
        order = read('order')
        order.update(external_id=f'DEMO-ORDER-{index:03}', address=f'Synthetic Paris stop {index}',
                     location={'latitude': lat, 'longitude': lon, 'reviewed': True},
                     service_seconds=300, priority=10 if index == 1 else 0,
                     required_skills=['TAIL_LIFT'] if index in (1, 2) else [],
                     demand={'weight_kg': 300 if index < 3 else 100, 'volume_dm3': 500,
                             'pallet_eur_units': 1, 'pallet_ind_units': 0, 'loading_length_mm': 400},
                     time_window={'start': '2026-09-23T08:00:00+02:00', 'end': '2026-09-23T16:00:00+02:00'})
        orders.append(owned(100 + index, order))
    depot = read('depot')
    day, zone = date(2026, 9, 23), ZoneInfo(depot['timezone'])
    vehicles = [owned(number, dated(read(name), day, zone)) for number, name in ((10, 'vehicle'), (11, 'vehicle-van'))]
    # A stated synthetic stop limit exercises both vehicles, without claiming customer requirements.
    vehicles[0]['data']['limits'] = {'max_stops': 6}
    return {'organization_id': owner, 'data_kind': 'SYNTHETIC', 'service_date': day.isoformat(),
            'depot': owned(1, depot), 'vehicles': vehicles, 'orders': orders,
            'routing_policy': 'TRUCK_STANDARD', 'objective': 'TRAVEL_TIME', 'solver_time_limit_seconds': 5}


def create_paris():
    TARGET.mkdir(parents=True, exist_ok=True)
    (TARGET / 'paris-ten.json').write_text(json.dumps(paris(), indent=2) + '\n', encoding='utf-8', newline='\n')


def create_restrictions():
    import osmium
    path = ROOT / 'deploy/runtime/restrictions.osm.pbf'
    if path.exists():
        print('Existing controlled PBF preserved.')
        return
    points = {1: (2, 48), 2: (2.002, 48), 3: (2.004, 48), 4: (2.006, 48),
              5: (2, 48.002), 6: (2, 48.004), 7: (2.02, 48), 8: (2.024, 48),
              9: (2, 47.998), 10: (2, 47.996), 11: (2, 47.994)}
    roads = [([1, 2], {}), ([2, 3], {'maxheight': '3', 'maxwidth': '2.3', 'maxlength': '7',
                                     'maxweight': '7.5', 'maxaxleload': '4', 'maxaxles': '2'}),
             ([3, 4], {}), ([1, 5, 6], {'oneway': 'yes'}), ([7, 8], {}),
             ([1, 9], {}), ([9, 10], {'hgv': 'no'}), ([10, 11], {})]
    path.parent.mkdir(parents=True, exist_ok=True)
    with osmium.SimpleWriter(str(path)) as writer:
        for key, point in points.items():
            writer.add_node(osmium.osm.mutable.Node(id=key, version=1, location=point))
        for index, (nodes, tags) in enumerate(roads, 1):
            writer.add_way(osmium.osm.mutable.Way(id=index, version=1, nodes=nodes,
                           tags={'highway': 'tertiary', 'maxspeed': '30', 'name': f'Synthetic road {index}', **tags}))
    print('Created isolated synthetic restriction PBF; never use as regional road data.')


if __name__ == '__main__':
    create_paris()
    create_restrictions()
