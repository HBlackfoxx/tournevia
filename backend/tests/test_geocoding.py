"""Address lookup through the installation's own geocoder: proposals only, never reviewed points."""
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import threading
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from app.config import ROOT
from app.geocoding import PUBLIC_SERVICE_NAME, GeocoderUnavailable, Locator, search
from app.main import create_app
from app.provision import provision_demo
from test_postgres import example

WRITE = {'Origin': 'http://localhost:8000', 'X-VRP-Request': '1'}
# addok answers in the api-adresse.data.gouv.fr GeoJSON shape.
PLACES = {
    'hotel de ville paris': [('Place de l’Hôtel de Ville 75004 Paris', 2.3522, 48.8566, 0.94, 'street')],
    '12 rue de rivoli paris': [('12 Rue de Rivoli 75004 Paris', 2.3601, 48.8553, 0.97, 'housenumber'),
                               ('Rue de Rivoli 75001 Paris', 2.3388, 48.8606, 0.71, 'street')],
    'somewhere vague': [('Paris', 2.3488, 48.8534, 0.55, 'municipality')],
    'weak match': [('1 Rue Inconnue 75012 Paris', 2.38, 48.84, 0.41, 'housenumber')],
}


# Answers a geocoder should not give: results with null or wrong fields beside a good one, and
# payloads that are not a feature collection at all.
ODD_FEATURES = [
    {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [2.3601, 48.8553]},
     'properties': {'label': '5 Rue Fictive 75004 Paris', 'score': 0.9, 'type': 'housenumber', 'postcode': 75004}},
    {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [2.36, 48.85]},
     'properties': {'label': 'Null score', 'score': None, 'type': 'street', 'city': None}},
    {'type': 'Feature', 'geometry': None, 'properties': {'label': 'No geometry', 'score': 0.9, 'type': 'street'}},
    {'type': 'Feature', 'geometry': {'coordinates': [2.36, 48.85]}, 'properties': None},
    {'type': 'Feature', 'geometry': {'coordinates': ['east', 'north']}, 'properties': {'label': 'Words', 'type': 'street'}},
    {'type': 'Feature', 'geometry': {'coordinates': [2.36, 148.85]}, 'properties': {'label': 'Off the world', 'type': 'street'}},
    {'type': 'Feature', 'geometry': {'coordinates': [2.36, 48.85]}, 'properties': {'label': 'Text score', 'score': 'high', 'type': 'street'}},
    'not a feature', None, 7,
]
ODD_PAYLOADS = {'odd answers': {'features': ODD_FEATURES}, 'not a collection': ['features'], 'null features': {'features': None}}


class FakeAddok(BaseHTTPRequestHandler):
    # Each search's query parameters, for checking what was sent.
    received: list[dict[str, list[str]]] = []
    # Called on every search, to observe the application while it waits for an answer.
    on_search = None

    def do_GET(self):
        url = urlsplit(self.path)
        FakeAddok.received.append(parse_qs(url.query))
        if FakeAddok.on_search:
            FakeAddok.on_search()
        query = parse_qs(url.query).get('q', [''])[0].lower()
        if url.path != '/search/' or query == 'broken':
            self.send_response(500); self.end_headers(); return
        if query in ODD_PAYLOADS:
            body = json.dumps(ODD_PAYLOADS[query]).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(body)
            return
        limit = int(parse_qs(url.query).get('limit', ['5'])[0])
        features = [{'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [lon, lat]},
                     'properties': {'label': label, 'score': score, 'type': kind, 'postcode': '75004', 'city': 'Paris'}}
                    for label, lon, lat, score, kind in PLACES.get(query, [])[:limit]]
        features.append({'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [2.3, 48.8]},
                         'properties': {'label': 'ignored', 'score': 0.9, 'type': 'poi'}})
        body = json.dumps({'type': 'FeatureCollection', 'features': features}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers(); self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def geocoder():
    server = ThreadingHTTPServer(('127.0.0.1', 0), FakeAddok)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    yield f'http://127.0.0.1:{server.server_address[1]}'
    server.shutdown(); server.server_close()


def unused_url():
    server = ThreadingHTTPServer(('127.0.0.1', 0), FakeAddok)
    port = server.server_address[1]; server.server_close()
    return f'http://127.0.0.1:{port}'


def test_search_returns_known_kinds_best_first_and_reports_failures(geocoder):
    found = search(geocoder, '12 rue de rivoli paris')
    assert [c.label for c in found] == ['12 Rue de Rivoli 75004 Paris', 'Rue de Rivoli 75001 Paris']
    assert (found[0].latitude, found[0].longitude, found[0].kind) == (48.8553, 2.3601, 'housenumber')
    assert search(geocoder, 'unknown place') == []  # Only the unsupported 'poi' feature came back.
    for url, query in [(geocoder, 'broken'), (unused_url(), 'paris')]:
        with pytest.raises(GeocoderUnavailable):
            search(url, query)


def test_odd_answers_skip_their_bad_results_or_count_as_an_outage(geocoder):
    found = search(geocoder, 'odd answers')
    assert [(c.label, c.score, c.postcode, c.city) for c in found] == [
        ('5 Rue Fictive 75004 Paris', 0.9, '75004', None), ('Null score', 0, None, None)]
    assert Locator(geocoder)('odd answers').label == '5 Rue Fictive 75004 Paris'
    for query in ['not a collection', 'null features']:
        with pytest.raises(GeocoderUnavailable):
            search(geocoder, query)
        locate = Locator(geocoder)
        assert locate(query) is None and locate.stopped == 'UNAVAILABLE'


def test_import_lookup_accepts_only_precise_confident_matches_and_says_why_it_stopped(geocoder):
    locate = Locator(geocoder)
    assert locate('12 rue de rivoli paris').kind == 'housenumber'
    assert locate('hotel de ville paris').kind == 'street'
    assert locate('somewhere vague') is None  # A whole town is not a delivery point.
    assert locate('weak match') is None
    assert locate.stopped is None
    down = Locator(unused_url())
    assert down('12 rue de rivoli paris') is None and down.stopped == 'UNAVAILABLE'
    late = Locator(geocoder, budget_seconds=-1)
    assert late('12 rue de rivoli paris') is None and late.stopped == 'INCOMPLETE'


def client_for(database, **settings_update):
    engine, _, settings = database
    with Session(engine) as session, session.begin():
        provision_demo(session, settings, example())
    return TestClient(create_app(settings.model_copy(update=settings_update)), base_url='http://localhost:8000')


@pytest.mark.postgres
def test_address_lookup_endpoint_is_off_by_default_and_explains_an_outage(database, geocoder):
    with client_for(database) as client:
        assert client.get('/api/v1/configuration').json()['address_lookup'] is None
        off = client.get('/api/v1/geocode', params={'q': 'hotel de ville paris'})
        assert off.status_code == 404 and off.json()['code'] == 'GEOCODER_DISABLED'
    with client_for(database, geocoder_url=geocoder) as client:
        assert client.get('/api/v1/configuration').json()['address_lookup'] == 'Base Adresse Nationale (Licence Ouverte 2.0)'
        found = client.get('/api/v1/geocode', params={'q': '12 rue de rivoli paris'})
        assert found.status_code == 200 and found.json()[0]['kind'] == 'housenumber'
        assert client.get('/api/v1/geocode', params={'q': 'ab'}).status_code == 422
        broken = client.get('/api/v1/geocode', params={'q': 'broken'})
        assert broken.status_code == 503 and broken.json()['code'] == 'GEOCODER_UNAVAILABLE'
        odd = client.get('/api/v1/geocode', params={'q': 'not a collection'})
        assert odd.status_code == 503 and odd.json()['code'] == 'GEOCODER_UNAVAILABLE'
        assert [c['label'] for c in client.get('/api/v1/geocode', params={'q': 'odd answers'}).json()] == [
            '5 Rue Fictive 75004 Paris', 'Null score']


@pytest.mark.postgres
def test_an_administrator_turns_on_the_public_address_service_for_the_company(database, geocoder, monkeypatch):
    # IGN's service, played by the fake geocoder: nothing leaves the test.
    monkeypatch.setattr('app.geocoding.PUBLIC_SERVICE_URL', geocoder)
    switch = lambda client, on: client.put('/api/v1/workspace/address-lookup', json={'public_service': on}, headers=WRITE)
    with client_for(database) as client:
        assert client.get('/api/v1/geocode', params={'q': '12 rue de rivoli paris'}).status_code == 404
        assert switch(client, True).json() == {'public_service': True}
        configuration = client.get('/api/v1/configuration').json()
        assert configuration['address_lookup_source'] == 'public'
        assert configuration['address_lookup'] == PUBLIC_SERVICE_NAME
        FakeAddok.received.clear()
        found = client.get('/api/v1/geocode', params={'q': '12 rue de rivoli paris'})
        assert found.status_code == 200 and found.json()[0]['label'] == '12 Rue de Rivoli 75004 Paris'
        # Only the address goes out, with the depot as the point to rank matches around.
        depot = client.get('/api/v1/depot').json()['data']['location']
        assert FakeAddok.received == [{'q': ['12 rue de rivoli paris'], 'limit': ['5'],
                                       'lat': [str(depot['latitude'])], 'lon': [str(depot['longitude'])]}]
        # Imported deliveries without coordinates go through the same service.
        preview = client.post('/api/v1/imports/orders?dry_run=true', content=orders_csv(['12 rue de rivoli paris']).encode(),
                              headers={**WRITE, 'Content-Type': 'text/csv'}).json()
        assert preview['rows'][0]['data']['location'] == {'latitude': 48.8553, 'longitude': 2.3601, 'reviewed': False}
        assert switch(client, False).status_code == 200
        assert client.get('/api/v1/configuration').json()['address_lookup_source'] is None
        assert client.get('/api/v1/geocode', params={'q': '12 rue de rivoli paris'}).status_code == 404
        switch(client, True)
    # An installation's own geocoder always wins: addresses then never reach the public service.
    with client_for(database, geocoder_url=unused_url()) as client:
        configuration = client.get('/api/v1/configuration').json()
        assert configuration['address_lookup_source'] == 'installation'
        assert client.get('/api/v1/geocode', params={'q': '12 rue de rivoli paris'}).json()['code'] == 'GEOCODER_UNAVAILABLE'


def orders_csv(addresses):
    """The example delivery file, one row per address, without coordinates."""
    header, template = list(csv.reader(io.StringIO((ROOT / 'examples/orders.csv').read_text(encoding='utf-8'))))[:2]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(header)
    for i, address in enumerate(addresses):
        row = dict(zip(header, template), external_id=f'GEO-{i + 1}', address=address, latitude='', longitude='')
        writer.writerow([row[name] for name in header])
    return out.getvalue()


@pytest.mark.postgres
def test_import_proposes_unreviewed_coordinates_for_rows_without_them(database, geocoder):
    content = orders_csv(['12 rue de rivoli paris', 'weak match']).encode()
    upload = lambda client, dry: client.post(f'/api/v1/imports/orders?dry_run={str(dry).lower()}', content=content,
                                             headers={**WRITE, 'Content-Type': 'text/csv'})
    with client_for(database, geocoder_url=geocoder) as client:
        # While the import waits for each answer it holds no database connection, so no lock either.
        held = []
        FakeAddok.on_search = lambda: held.append(client.app.state.engine.pool.checkedout())
        try:
            preview = upload(client, True).json()
        finally:
            FakeAddok.on_search = None
        assert held == [0, 0]
        assert preview['errors'] == [] and preview['address_lookup'] is None
        first, second = preview['rows']
        assert first['address_match']['label'] == '12 Rue de Rivoli 75004 Paris'
        assert first['data']['location'] == {'latitude': 48.8553, 'longitude': 2.3601, 'reviewed': False}
        assert second['address_match'] is None and second['data']['location'] is None
        assert upload(client, False).json()['committed'] is True
        saved = {o['data']['external_id']: o['data']['location'] for o in
                 client.get('/api/v1/orders?service_date=2026-09-23').json()}
        assert saved['GEO-1']['reviewed'] is False and saved['GEO-2'] is None
    # The same file without a geocoder still imports; nothing is looked up.
    with client_for(database, geocoder_url=unused_url()) as client:
        preview = client.post('/api/v1/imports/orders?dry_run=true', content=orders_csv(['12 rue de rivoli paris']).replace('GEO-1', 'GEO-9').encode(),
                              headers={**WRITE, 'Content-Type': 'text/csv'}).json()
        assert preview['address_lookup'] == 'UNAVAILABLE' and preview['rows'][0]['data']['location'] is None
