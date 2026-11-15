from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase

from app.config import Settings


class Base(DeclarativeBase):
    pass


def make_engine(settings: Settings, *, statement_timeout_ms: int = 5000) -> Engine:
    # Request handlers keep short statements; migrations pass 0 (no limit) so a
    # larger future data migration cannot time out halfway through an upgrade.
    return create_engine(settings.database_url, pool_pre_ping=True, pool_timeout=5,
                         connect_args={'connect_timeout': 3, 'options': f'-c statement_timeout={statement_timeout_ms}'})
