"""Coverage beyond Île-de-France, on a graph built from the pinned France extract.

Run with RUN_ENGINE_TESTS=1 and FRANCE_URL pointing at a router serving
`deploy/build_graph.py region --extract france`. Points are public addresses from the
Base Adresse Nationale (streets and squares), used as fictional delivery points.
"""
import json
import os

import pytest

from app.fleet import Location
from app.planning import PlanningInput
from app.routing import Deadline, EngineError, Valhalla
from app.spike import calculate
from test_routing_core import FIXTURE

pytestmark = [pytest.mark.engines,
              pytest.mark.skipif(os.environ.get('RUN_ENGINE_TESTS') != '1' or not os.environ.get('FRANCE_URL'),
                                 reason='Set RUN_ENGINE_TESTS=1 and FRANCE_URL to a router on the France graph.')]

# 100 Rue de Gerland as the depot, then ten delivery points on Lyon and Villeurbanne main roads.
# House numbers or street centres that a 19 t truck reaches in both directions; a pedestrian
# square or a street centre on a truck-restricted segment would instead be reported unreachable.
LYON = [(45.736019, 4.839537), (45.745707, 4.83999), (45.763417, 4.849871), (45.753181, 4.841104),
        (45.773108, 4.851582), (45.752894, 4.853795), (45.753079, 4.849971), (45.768106, 4.886373),
        (45.74077, 4.886623), (45.737935, 4.85224), (45.740524, 4.863821)]
# Two points on ordinary through-roads in each city, far from Île-de-France.
CITIES = {'Lille': [(50.636895, 3.0636), (50.633192, 3.053514)],
          'Bordeaux': [(44.841464, -0.570493), (44.841913, -0.577161)],
          'Marseille': [(43.295992, 5.368267), (43.297298, 5.380267)],
          'Strasbourg': [(48.590012, 7.750366), (48.587058, 7.738274)],
          'Rennes': [(48.108363, -1.682976), (48.088798, -1.674537)],
          'Toulouse': [(43.604354, 1.443054), (43.604291, 1.445669)],
          'Nice': [(43.70592, 7.256191), (43.689609, 7.241097)]}


def france():
    return Valhalla(os.environ['FRANCE_URL'], 'France graph')


def lyon_day(objective: str) -> PlanningInput:
    data = json.loads(FIXTURE.read_text())
    data['objective'] = objective
    (depot_lat, depot_lon), *stops = LYON
    data['depot']['data']['location'] = {'latitude': depot_lat, 'longitude': depot_lon, 'reviewed': True}
    for order, (lat, lon) in zip(data['orders'], stops, strict=True):
        order['data']['location'] = {'latitude': lat, 'longitude': lon, 'reviewed': True}
    return PlanningInput.model_validate(data)


@pytest.mark.parametrize('objective', ['TRAVEL_TIME', 'MODELED_TRAVEL_COST'])
def test_a_lyon_day_is_planned_and_independently_validated(objective):
    model = lyon_day(objective)
    result = calculate(model, france())
    assert result.validation == 'PASS', result.blocking_issues
    served = sum(len(route.stops) for route in result.routes)
    assert served == len(model.orders) and not result.unassigned, result.unassigned


@pytest.mark.parametrize('city', sorted(CITIES))
def test_roads_connect_in_both_directions_far_from_ile_de_france(city):
    truck = lyon_day('TRAVEL_TIME').vehicles[0].data.routing
    points = [Location(latitude=lat, longitude=lon, reviewed=True) for lat, lon in CITIES[city]]
    matrix = france().matrix(points, truck, 'TRUCK_STANDARD', Deadline.after(30))
    assert matrix.durations[0][1] is not None and matrix.durations[1][0] is not None
    assert 0 < matrix.distances[0][1] < 10000


def test_one_calculation_cannot_span_more_than_the_built_matrix_distance():
    # Paris and Lyon are about 390 km apart; the graph is built with --max-matrix-km 200,
    # which the application checks before queueing (MATRIX_MAX_KM).
    truck = lyon_day('TRAVEL_TIME').vehicles[0].data.routing
    points = [Location(latitude=48.8566, longitude=2.3522, reviewed=True), Location(latitude=45.7576, longitude=4.8317, reviewed=True)]
    with pytest.raises(EngineError):
        france().matrix(points, truck, 'TRUCK_STANDARD', Deadline.after(30))
