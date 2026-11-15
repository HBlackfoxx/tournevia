from contextlib import asynccontextmanager
import logging
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.accounts import Organization
from app.auth import current_session, who
from app.auth_api import auth_router
from app.config import Settings
from app.db import make_engine
from app.examples import has_examples
from app.fleet import DepotRecord, read_depot
from app.geocoding import address_service
from app.records import DataError
from app.data_api import router
from app.jobs_api import jobs_router
from app.http_limits import BodyLimit
from app.http_logging import RequestLog, trace
from app.monitoring import monitoring_router
from app.onboarding import onboarding_router
from app.messages import plain

logger = logging.getLogger(__name__)
# PostgreSQL error codes for waiting too long on another transaction: statement timeout (most often a
# lock wait under the 5 s limit), lock not available, deadlock and serialization failure.
BUSY_STATES = {'57014', '55P03', '40P01', '40001'}
SCHEMA_REVISION = '0017_safety_margin'


class Health(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['ok'] = 'ok'


class Readiness(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['ready', 'unavailable']


class Configuration(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['local', 'local_demo', 'shared'] = 'local'
    data_kind: Literal['SYNTHETIC', 'UNVERIFIED'] = 'SYNTHETIC'
    map_style_url: str
    company_name: str
    # Attribution for address lookup when it is on; absent otherwise.
    address_lookup: str | None = None
    # Where looked-up addresses go: the installation's own geocoder or IGN's public service.
    address_lookup_source: Literal['installation', 'public'] | None = None
    # Whether the company has example records (app.examples) to try the planner with.
    examples: bool = False
    # Minutes before each window ends that new calculations keep free (app.planning).
    safety_margin_minutes: int = 15


def database_ready(session: Session, settings: Settings) -> bool:
    revision = session.scalar(text('SELECT version_num FROM alembic_version'))
    return revision == SCHEMA_REVISION and (settings.app_mode != 'local_demo' or (
        session.get(Organization, settings.demo_organization_id) is not None
        and read_depot(session, settings.demo_organization_id) is not None))


# The planner uses none of these browser features; deny them to every frame.
PERMISSIONS_POLICY = ('accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), '
                      'payment=(), usb=()')


def content_security_policy(settings: Settings) -> str:
    """Same-origin scripts only. The basemap style can name any HTTPS tile,
    glyph or sprite host, so data/image fetches allow HTTPS plus the style origin."""
    style = urlsplit(settings.map_style_url)
    remote = ' '.join(dict.fromkeys(['https:', f'{style.scheme}://{style.netloc}']))
    return ("default-src 'self'; script-src 'self'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
            f"img-src 'self' data: blob: {remote}; font-src 'self' data:; connect-src 'self' {remote}; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    policy = content_security_policy(settings)
    # Interactive API docs load Swagger UI from a CDN without the application's CSP, so a
    # shared installation does not serve them; the contract is committed as backend/openapi.json.
    developer_docs = settings.app_mode != 'shared'

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.engine = make_engine(settings)
        try:
            yield
        finally:
            app.state.engine.dispose()

    app = FastAPI(title='Tournevia Community — planning workspace', version='0.8.0',
                  description='Company setup, named sessions, road-based calculation and manual review. Dispatch export requires a current validated READY plan; the demonstration mode (fictional data) cannot mark plans ready or export them.',
                  lifespan=lifespan, docs_url='/api/docs' if developer_docs else None,
                  openapi_url='/api/openapi.json' if developer_docs else None, redoc_url=None)
    app.state.settings = settings
    app.add_middleware(BodyLimit)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list({'localhost', '127.0.0.1', urlsplit(settings.public_origin).hostname}))

    @app.middleware('http')
    async def local_origin(request: Request, call_next):
        origin = request.headers.get('origin')
        if origin and origin != settings.public_origin:
            return JSONResponse({'detail': 'Origin not allowed'}, status_code=403)
        if request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            if origin != settings.public_origin or request.headers.get('x-vrp-request') != '1':
                return JSONResponse({'detail': 'Writes require the configured Origin and X-VRP-Request: 1.'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Permissions-Policy'] = PERMISSIONS_POLICY
        if request.url.path != '/api/docs':  # Swagger UI needs its own inline/CDN scripts.
            response.headers['Content-Security-Policy'] = policy
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    app.add_middleware(RequestLog)

    @app.exception_handler(DataError)
    async def source_error(request: Request, error: DataError):
        retry = {'LOGIN_THROTTLED': '900', 'QUEUE_FULL': '30'}.get(error.code) if error.status == 429 else None
        body = {'code': error.code, 'detail': error.message, **({'identifiers': error.identifiers} if error.identifiers else {})}
        return JSONResponse(body, status_code=error.status,
                            headers={'Retry-After': retry} if retry else None)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        # FastAPI's default includes raw input values, including passwords.
        detail = []
        for e in error.errors():
            message, template, values = plain(e)
            detail.append({'loc': e['loc'], 'msg': message, 'type': e['type'], 'template': template, 'values': values})
        return JSONResponse({'detail': detail}, status_code=422)

    app.include_router(router(settings))
    app.include_router(jobs_router(settings))
    app.include_router(auth_router())
    app.include_router(onboarding_router())
    app.include_router(monitoring_router(settings))

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError):
        # Driver exception strings can contain connection credentials or record values: log the
        # error's class, PostgreSQL's error code and where it happened, never its text.
        state = str(getattr(error.orig, 'sqlstate', None) or getattr(error.orig, 'pgcode', None) or '') \
            if isinstance(error, DBAPIError) else ''
        if state in BUSY_STATES:
            # Waiting too long for a lock another change holds: the database works; retrying will.
            status, body, headers = 409, {'code': 'DATABASE_BUSY', 'detail': (
                'Another change to the same records is still being saved. Try again in a moment.')}, {'Retry-After': '5'}
        elif state.startswith('22'):
            status, body, headers = 422, {'code': 'INVALID_VALUE', 'detail': (
                'A value is too long or outside what can be saved. Check the values entered.')}, None
        elif state.startswith('23'):
            status, body, headers = 409, {'code': 'CONFLICT', 'detail': (
                'This change conflicts with records saved meanwhile. Reload and try again.')}, None
        else:
            status, body, headers = 503, {'code': 'DATABASE_UNAVAILABLE', 'detail': (
                'Database unavailable; check migrations and database service.')}, None
        log = logger.warning if status == 503 else logger.info
        log('database_error status=%d error_type=%s sqlstate=%s trace=%s', status, type(error).__name__, state or 'none', trace(error))
        return JSONResponse(body, status_code=status, headers=headers)

    @app.get('/api/v1/health', response_model=Health, operation_id='health')
    def health() -> Health:
        return Health()

    @app.get('/api/v1/ready', response_model=Readiness, operation_id='readiness',
             responses={503: {'model': Readiness}})
    def readiness(request: Request, response: Response) -> Readiness:
        try:
            with Session(request.app.state.engine) as session:
                ready = database_ready(session, settings)
        except SQLAlchemyError as error:
            logger.warning('readiness_unavailable error_type=%s', type(error).__name__)
            ready = False
        if not ready:
            response.status_code = 503
        return Readiness(status='ready' if ready else 'unavailable')

    @app.get('/api/v1/configuration', response_model=Configuration, operation_id='configuration', dependencies=[Depends(current_session)])
    def configuration(request: Request) -> Configuration:
        examples = False
        try:
            with Session(request.app.state.engine) as session:
                organization = session.get(Organization, who(request).organization_id)
                examples = organization is not None and has_examples(session, organization.id)
        except SQLAlchemyError:
            # The demonstration still describes itself without its database; only the company's
            # own address lookup choice is then unknown, so it is reported as off.
            if settings.app_mode != 'local_demo':
                raise
            logger.warning('configuration_without_database')
            organization = None
        if organization is None and settings.app_mode != 'local_demo':
            raise DataError(404, 'ORGANIZATION_MISSING', 'Workspace setup is incomplete.')
        service = address_service(settings, bool(organization and organization.public_address_lookup))
        lookup = {'address_lookup': service.attribution, 'address_lookup_source': service.source} if service else {}
        if settings.app_mode == 'local_demo':
            return Configuration(mode='local_demo', map_style_url=settings.map_style_url, company_name='Sample workspace',
                                 **lookup, **({'safety_margin_minutes': organization.safety_margin_minutes}
                                              if organization is not None else {}))
        return Configuration(mode=settings.app_mode, map_style_url=settings.map_style_url, **lookup,
                             company_name=organization.name, data_kind='SYNTHETIC' if organization.is_demo else 'UNVERIFIED',
                             examples=examples, safety_margin_minutes=organization.safety_margin_minutes)

    @app.get('/api/v1/depot', response_model=DepotRecord, operation_id='getDepot', dependencies=[Depends(current_session)],
             responses={404: {'description': 'No depot provisioned'},
                        503: {'description': 'Database unavailable'},
                        200: {'headers': {'ETag': {'schema': {'type': 'string'}}}}})
    def depot(request: Request, response: Response) -> DepotRecord:
        with Session(request.app.state.engine) as session:
            record = read_depot(session, who(request).organization_id)
        if record is None:
            raise HTTPException(404, 'No depot provisioned for this organization.')
        response.headers['ETag'] = f'"{record.version}"'
        return record

    if (settings.web_dist / 'assets').is_dir():
        app.mount('/assets', StaticFiles(directory=settings.web_dist / 'assets'), name='assets')

    @app.get('/', include_in_schema=False)
    def index():
        path = settings.web_dist / 'index.html'
        if not path.is_file():
            return JSONResponse({'detail': 'Frontend not built. Run npm ci and npm run build in web.'}, status_code=503)
        # Always revalidate the entry page: it names the current hashed bundles, so a cached
        # copy would keep running the previous frontend after an upgrade.
        return FileResponse(path, headers={'Cache-Control': 'no-cache'})

    generate = app.openapi

    def contract() -> dict:
        # Handlers accept these headers as optional only to answer a missing one with 428 and
        # an explanation instead of a generic 422; the contract states that they are required.
        if app.openapi_schema is None:
            for operation in (op for item in generate()['paths'].values() for op in item.values()):
                for parameter in operation.get('parameters', []):
                    if parameter['in'] == 'header' and parameter['name'] in REQUIRED_HEADERS:
                        parameter.update(required=True, description=REQUIRED_HEADERS[parameter['name']],
                                         schema={'type': 'string', 'title': parameter['schema']['title']})
        return app.openapi_schema

    app.openapi = contract
    return app


REQUIRED_HEADERS = {
    'if-match': 'The current version, quoted, for example "3". Missing: 428 VERSION_REQUIRED.',
    'idempotency-key': 'A 1–128 character printable ASCII key; reuse it only to retry the identical request. Missing: 428 IDEMPOTENCY_REQUIRED.',
}


if __name__ == '__main__':
    import uvicorn
    config = Settings()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', datefmt='%Y-%m-%dT%H:%M:%S%z')
    uvicorn.run(create_app(config), host=config.bind_host, port=config.port, proxy_headers=False, access_log=False)
