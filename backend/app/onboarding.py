"""One-time, installation-authorized first administrator and workspace setup."""
import hmac

from typing import Annotated, Literal

from fastapi import APIRouter, Header, Query, Request, Response
from pydantic import Field, SecretStr, model_validator
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.accounts import Organization, User
from app.auth import CreateUser, LoginRequest, SessionRecord, create_user, login, set_session_cookie
from app.fleet import Depot, DepotInput
from app.geocoding import AddressCandidate, address_service, lookup
from app.planning import Model
from app.records import DataError


class SetupStatus(Model):
    required: bool
    enabled: bool
    map_style_url: str | None = None
    # The installation's own address lookup, when it has one; otherwise setup may choose IGN's.
    address_lookup: str | None = None


class SetupRequest(LoginRequest):
    password: SecretStr = Field(min_length=15, max_length=128)
    setup_token: SecretStr = Field(min_length=32, max_length=256)
    display_name: str = Field(min_length=1, max_length=120)
    company_name: str = Field(min_length=1, max_length=120)
    depot: DepotInput
    # The company's choice to look addresses up with IGN's public service, as in Settings.
    public_address_lookup: bool = False
    # The language setup was completed in: the first administrator's interface language.
    language: Literal['en', 'fr'] = 'en'

    @model_validator(mode='after')
    def complete(self):
        if not self.company_name.strip() or not self.display_name.strip():
            raise ValueError('Company and administrator names cannot be blank.')
        if not self.depot.location.reviewed:
            raise ValueError('Provide and review your depot location before creating the workspace.')
        return self


def setup_required(session):
    # Existing installations are never adopted, renamed or reset by first-use setup.
    return session.scalar(select(Organization.id).limit(1)) is None and session.scalar(select(User.id).limit(1)) is None


def initialize(session, settings, data: SetupRequest):
    expected = settings.setup_token.get_secret_value()
    if settings.app_mode == 'local_demo' or not expected or not hmac.compare_digest(expected.encode(), data.setup_token.get_secret_value().encode()):
        raise DataError(403, 'SETUP_UNAUTHORIZED', 'Use the private setup link supplied by your installation operator.')
    # This lock also serializes legacy demo provisioning. A concurrent setup can
    # never create a second company/admin; the whole operation is one transaction.
    session.execute(text('SELECT pg_advisory_xact_lock(73645101)'))
    if not setup_required(session):
        raise DataError(409, 'SETUP_COMPLETE', 'This installation is already configured. Sign in instead.')
    depot = data.depot
    owner = settings.demo_organization_id  # Installation-controlled, never accepted from the client.
    session.add(Organization(id=owner, name=data.company_name.strip(), is_demo=False,
                             public_address_lookup=data.public_address_lookup))
    session.flush()
    session.add(Depot(organization_id=owner, code=depot.code, name=depot.name, address=depot.address,
        latitude=depot.location.latitude, longitude=depot.location.longitude,
        reviewed=depot.location.reviewed, timezone=depot.timezone, kind='DEPOT', main=True))
    session.flush()
    create_user(session, owner, CreateUser(username=data.username, password=data.password,
        display_name=data.display_name.strip(), role='admin', language=data.language))
    return login(session, LoginRequest(username=data.username, password=data.password), None)


def onboarding_router():
    api = APIRouter(prefix='/api/v1')

    @api.get('/setup', response_model=SetupStatus, response_model_exclude_none=True, operation_id='getSetupStatus')
    def status(request: Request):
        settings = request.app.state.settings
        if settings.app_mode == 'local_demo':
            return SetupStatus(required=False, enabled=False)
        with Session(request.app.state.engine) as session:
            required = setup_required(session)
        enabled = required and bool(settings.setup_token.get_secret_value())
        lookup = settings.geocoder_dataset if enabled and settings.geocoder_url else None
        return SetupStatus(required=required, enabled=enabled, map_style_url=settings.map_style_url if enabled else None,
                           address_lookup=lookup)

    @api.get('/setup/geocode', response_model=list[AddressCandidate], operation_id='lookupSetupAddress')
    def setup_geocode(request: Request, q: Annotated[str, Query(min_length=3, max_length=200)],
                      x_setup_token: Annotated[str, Header()] = '', public: bool = False):
        """Find the depot's address during first-use setup, with the private setup token. Uses the
        installation's own geocoder, or IGN's public service when `public` is chosen."""
        settings = request.app.state.settings
        expected = settings.setup_token.get_secret_value()
        if settings.app_mode == 'local_demo' or not expected or not hmac.compare_digest(expected.encode(), x_setup_token.encode()):
            raise DataError(403, 'SETUP_UNAUTHORIZED', 'Use the private setup link supplied by your installation operator.')
        with Session(request.app.state.engine) as session:
            if not setup_required(session):
                raise DataError(409, 'SETUP_COMPLETE', 'This installation is already configured. Sign in instead.')
        service = address_service(settings, public)
        if service is None:
            raise DataError(404, 'GEOCODER_DISABLED', 'Address lookup is not turned on for this workspace.')
        return lookup(service.url, q)

    @api.post('/setup', response_model=SessionRecord, status_code=201, operation_id='completeSetup')
    def complete(data: SetupRequest, request: Request, response: Response):
        with Session(request.app.state.engine) as session, session.begin():
            token, record = initialize(session, request.app.state.settings, data)
        set_session_cookie(response, request.app.state.settings, token)
        return record

    return api
