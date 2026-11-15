"""Explicit, idempotent synthetic provisioning; never run on ordinary API startup."""
import json

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.accounts import Organization
from app.config import ROOT, Settings
from app.db import make_engine
from app.fleet import Depot, DepotInput


def provision_demo(session: Session, settings: Settings, depot: DepotInput) -> None:
    # Serialize duplicate setup invocations; this is a transaction-local setup lock.
    session.execute(text('SELECT pg_advisory_xact_lock(73645101)'))
    organization = session.get(Organization, settings.demo_organization_id)
    if organization is None:
        session.add(Organization(id=settings.demo_organization_id, name='Synthetic Paris company'))
        session.flush()
    existing = session.scalar(select(Depot.id).where(Depot.organization_id == settings.demo_organization_id))
    if existing is None:
        session.add(Depot(organization_id=settings.demo_organization_id, code=depot.code,
                          name=depot.name, address=depot.address, latitude=depot.location.latitude,
                          longitude=depot.location.longitude, reviewed=depot.location.reviewed,
                          timezone=depot.timezone, kind='DEPOT', main=True))


def main() -> None:
    settings = Settings()
    depot = DepotInput.model_validate(json.loads((ROOT / 'examples/depot.json').read_text(encoding='utf-8')))
    engine = make_engine(settings)
    try:
        with Session(engine) as session, session.begin():
            provision_demo(session, settings, depot)
    finally:
        engine.dispose()
    print('Synthetic company and depot provisioned; existing records preserved.')


if __name__ == '__main__':
    main()
