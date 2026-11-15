from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore', hide_input_in_errors=True)

    app_mode: Literal['local', 'local_demo', 'shared'] = 'local'
    setup_token: SecretStr = SecretStr('')
    # Enables GET /api/v1/metrics for a monitor that sends it as a bearer token (at least 32 characters).
    monitor_token: SecretStr = SecretStr('')
    public_origin: str = 'http://localhost:8000'
    # Shared mode only: use X-VRP-Client as the sign-in throttle address. Enable only behind a
    # proxy that overwrites that header with the connecting address (the bundled nginx does).
    trust_proxy_client_header: bool = False
    bind_host: str = '127.0.0.1'
    port: int = 8000
    db_host: str = '127.0.0.1'
    db_port: int = 5432
    db_name: str = 'vrp'
    db_user: str = 'vrp_app'
    db_password: SecretStr = SecretStr('')
    demo_organization_id: UUID = UUID('00000000-0000-4000-8000-000000000001')
    map_style_url: str = 'https://tiles.openfreemap.org/styles/liberty'
    valhalla_url: str = 'http://127.0.0.1:8002'
    routing_dataset: str = 'Geofabrik Ile-de-France downloaded 2026-09-22; static OSM speeds'
    # The installation's own BAN geocoder (addok). Empty: no address lookup. Never a public service
    # by default, because every looked-up delivery address would leave the installation.
    geocoder_url: str = ''
    geocoder_dataset: str = 'Base Adresse Nationale (Licence Ouverte 2.0)'
    # Regional 250-delivery jobs measured 118–123 s including the save, so 120 s left no headroom.
    job_timeout_seconds: int = Field(default=180,ge=1,le=600)
    matrix_concurrency: int = Field(default=1,ge=1,le=4)
    # The router's max_matrix_distance: deploy/build_graph.py --max-matrix-km (default 200).
    matrix_max_km: int = Field(default=200,ge=1,le=2000)
    web_dist: Path = ROOT / 'web' / 'dist'

    @field_validator('valhalla_url', mode='before')
    @classmethod
    def empty_routing_url(cls, value):
        return value or 'http://127.0.0.1:8002'

    @model_validator(mode='after')
    def validate_origin(self) -> 'Settings':
        origin = urlsplit(self.public_origin)
        if (origin.scheme not in {'http', 'https'} or not origin.hostname
                or origin.username or origin.password or '*' in origin.netloc
                or origin.path or origin.query or origin.fragment):
            raise ValueError('PUBLIC_ORIGIN must be an HTTP(S) origin without credentials or a path.')
        # Compose publishes the API on IPv4 loopback only, so an IPv6 loopback origin could never be reached.
        if self.app_mode in {'local', 'local_demo'} and origin.hostname not in {'localhost', '127.0.0.1'}:
            raise ValueError('Local PUBLIC_ORIGIN must be loopback: http://localhost or http://127.0.0.1 with a port.')
        if self.setup_token.get_secret_value() and len(self.setup_token.get_secret_value()) < 32:
            raise ValueError('SETUP_TOKEN must contain at least 32 characters.')
        if self.monitor_token.get_secret_value() and len(self.monitor_token.get_secret_value()) < 32:
            raise ValueError('MONITOR_TOKEN must contain at least 32 characters.')
        if self.app_mode == 'shared' and origin.scheme != 'https':
            raise ValueError('Shared authentication requires an HTTPS PUBLIC_ORIGIN and a private backend behind TLS.')
        return self

    @field_validator('geocoder_url')
    @classmethod
    def validate_geocoder(cls, value: str) -> str:
        url = urlsplit(value)
        if value and (url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password
                      or url.query or url.fragment):
            raise ValueError('GEOCODER_URL must be an HTTP(S) URL without credentials, or empty.')
        return value

    @field_validator('map_style_url')
    @classmethod
    def validate_map_style(cls, value: str) -> str:
        url = urlsplit(value)
        if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password:
            raise ValueError('MAP_STYLE_URL must be an HTTP(S) URL without credentials.')
        return value

    @property
    def database_url(self) -> URL:
        return URL.create('postgresql+psycopg', username=self.db_user,
                          password=self.db_password.get_secret_value(), host=self.db_host,
                          port=self.db_port, database=self.db_name)
