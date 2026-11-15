"""Address lookup through a BAN geocoder (addok's search API).

Either the installation's own geocoder (GEOCODER_URL), running privately next to the
application so delivery addresses never leave the installation, or, when an administrator
turns it on for the company, IGN's public Géoplateforme service, which answers in the same
format but receives every looked-up address. Off otherwise. A result only proposes
coordinates: the entrance still needs review.
"""
import http.client
import json
import time
from typing import Literal, NamedTuple
from urllib.parse import urlencode, urlsplit

from pydantic import Field

from app.planning import Model
from app.records import DataError

REQUEST_SECONDS = 4
# Below this, a match names a different place often enough that it is not proposed during import.
IMPORT_MIN_SCORE = 0.6
Kind = Literal['housenumber', 'street', 'locality', 'municipality']


class AddressCandidate(Model):
    label: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    score: float = Field(ge=0, le=1)
    kind: Kind
    postcode: str | None = None
    city: str | None = None


class GeocoderUnavailable(Exception):
    pass


UNAVAILABLE_MESSAGE = 'Address lookup is unavailable. Enter the coordinates or place the pin on the map.'

# IGN's public geocoding service (Géoplateforme): free, no account, Base Adresse Nationale data.
PUBLIC_SERVICE_URL = 'https://data.geopf.fr/geocodage'
PUBLIC_SERVICE_NAME = 'IGN Géoplateforme · Base Adresse Nationale (Licence Ouverte 2.0)'


class AddressService(NamedTuple):
    url: str
    attribution: str
    source: Literal['installation', 'public']


def address_service(settings, public_lookup: bool) -> AddressService | None:
    """The installation's own geocoder when there is one; otherwise IGN's public service if the
    company's administrator turned it on; otherwise none."""
    if settings.geocoder_url:
        return AddressService(settings.geocoder_url, settings.geocoder_dataset, 'installation')
    if public_lookup:
        return AddressService(PUBLIC_SERVICE_URL, PUBLIC_SERVICE_NAME, 'public')
    return None


def search(url: str, query: str, limit: int = 5, near: tuple[float, float] | None = None) -> list[AddressCandidate]:
    """Candidates for one address, best first; `near` (latitude, longitude) ranks matches around a
    point first, so the same street name near the depot comes before one across France."""
    origin = urlsplit(url)
    kind = http.client.HTTPSConnection if origin.scheme == 'https' else http.client.HTTPConnection
    connection = kind(origin.hostname, origin.port, timeout=REQUEST_SECONDS)
    params = {'q': query, 'limit': limit, **({'lat': near[0], 'lon': near[1]} if near else {})}
    path = f"{origin.path.rstrip('/')}/search/?{urlencode(params)}"
    try:
        connection.request('GET', path, headers={'Accept': 'application/json'})
        response = connection.getresponse()
        body = response.read(2_000_000)
        if response.status != 200:
            raise GeocoderUnavailable(f'status {response.status}')
        payload = json.loads(body)
    except (OSError, http.client.HTTPException, ValueError) as error:
        raise GeocoderUnavailable(type(error).__name__) from None
    finally:
        connection.close()
    features = payload.get('features') if isinstance(payload, dict) else None
    if not isinstance(features, list):
        raise GeocoderUnavailable('unexpected answer')
    return [found for found in map(candidate, features) if found is not None]


def candidate(feature) -> AddressCandidate | None:
    """One answer as a candidate, or None when it is not a usable address result: a missing or null
    field, a score or point that is not a number, or a point outside the world all skip it."""
    try:
        properties, geometry = feature.get('properties') or {}, feature.get('geometry') or {}
        point = geometry.get('coordinates') or []
        if properties.get('type') not in Kind.__args__ or len(point) != 2:
            return None
        score = float(properties.get('score') or 0)
        text = {key: None if properties.get(key) is None else str(properties[key]) for key in ('label', 'postcode', 'city')}
        return AddressCandidate(label=text['label'] or '', longitude=point[0], latitude=point[1],
                                score=min(max(score, 0), 1) if score == score else 0, kind=properties['type'],
                                postcode=text['postcode'], city=text['city'])
    except (AttributeError, TypeError, ValueError):  # A pydantic ValidationError is a ValueError.
        return None


def lookup(url: str, query: str, near: tuple[float, float] | None = None) -> list[AddressCandidate]:
    """For the API: a missing service is an explained 503, never an empty answer."""
    try:
        return search(url, query, near=near)
    except GeocoderUnavailable:
        raise DataError(503, 'GEOCODER_UNAVAILABLE', UNAVAILABLE_MESSAGE) from None


class Locator:
    """Best match per address during one import, within a total time budget."""

    def __init__(self, url: str, budget_seconds: float = 20, near: tuple[float, float] | None = None):
        self.url, self.near, self.deadline = url, near, time.monotonic() + budget_seconds
        # Why some addresses were not looked up: the service failed, or the budget ran out.
        self.stopped: Literal['UNAVAILABLE', 'INCOMPLETE'] | None = None
        # The best answer for the last address when it was too vague or uncertain to use.
        self.weak: AddressCandidate | None = None

    def __call__(self, address: str) -> AddressCandidate | None:
        self.weak = None
        if self.stopped:
            return None
        if time.monotonic() > self.deadline:
            self.stopped = 'INCOMPLETE'
            return None
        try:
            best = next(iter(search(self.url, address, limit=1, near=self.near)), None)
        except GeocoderUnavailable:
            self.stopped = 'UNAVAILABLE'
            return None
        if best is None:
            return None
        if best.kind not in ('housenumber', 'street') or best.score < IMPORT_MIN_SCORE:
            self.weak = best
            return None
        return best
