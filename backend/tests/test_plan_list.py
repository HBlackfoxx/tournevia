"""Saved-plan list: server-side status and search filters, and totals computed from each stored result."""
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from app.jobs import Plan
from app.main import create_app
from app.planning import Draft
from test_dispatch import compatible, ready, routed_plan, snapshot

pytestmark = pytest.mark.postgres


def test_saved_plans_filter_on_the_server_and_carry_their_totals(database):
    engine, _, settings = database
    inputs = snapshot(database)
    first_orders = compatible(inputs, 0, 2)
    second_orders = [key for key in compatible(inputs, 1, 10) if key not in first_orders][:3]
    first = routed_plan(database, inputs, {0: first_orders})
    # The second plan was calculated with only its own deliveries; a plan "includes" its inputs.
    second_inputs = inputs.model_copy(update={'orders': [o for o in inputs.orders if o.id in second_orders]})
    second = routed_plan(database, second_inputs, {1: second_orders})
    ready(database, second, 1)
    with Session(engine) as session:
        expected = {}
        for key in (first, second):
            result = Draft.model_validate(session.get(Plan, key).result)
            expected[str(key)] = (sum(len(r.stops) for r in result.routes), len(result.unassigned),
                                  sum(1 for r in result.routes if r.stops), sum(r.distance_m for r in result.routes),
                                  sum(r.known_cost_minor for r in result.routes))
    external = {o.id: o.data.external_id for o in inputs.orders}
    with TestClient(create_app(settings), base_url='http://localhost:8000') as client:
        def listed(**params):
            response = client.get('/api/v1/plans', params={'service_date': str(inputs.service_date), **params})
            assert response.status_code == 200, response.text
            return response.json()
        rows = listed()
        assert {row['id'] for row in rows} == set(expected)
        for row in rows:
            assert (row['served_deliveries'], row['unassigned_deliveries'], row['vehicles_used'], row['distance_m'],
                    row['known_cost_minor']) == expected[row['id']]
        assert expected[str(first)][:3] == (2, len(inputs.orders) - 2, 1)
        assert [row['id'] for row in listed(status='READY')] == [str(second)]
        assert [row['id'] for row in listed(search=external[first_orders[0]])] == [str(first)]
        assert [row['id'] for row in listed(search=str(second)[:8].upper())] == [str(second)]
        assert listed(search='NO-SUCH-DELIVERY') == []
        assert client.get('/api/v1/plans', params={'service_date': str(inputs.service_date), 'search': 'x' * 101}).status_code == 422
