from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import CheckConstraint, ForeignKey, Index, String, UniqueConstraint, select, text
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db import Base


class Location(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    reviewed: StrictBool


PlaceKind = Literal['DEPOT', 'PLACE']


class DepotInput(BaseModel):
    """A depot, or another place a vehicle starts or ends its day at, such as a driver's home."""
    model_config = ConfigDict(extra='forbid')
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    kind: PlaceKind = Field('DEPOT', description=(
        'DEPOT: goods are loaded there and deliveries can ship from it. PLACE: another start or end of a '
        "vehicle's day, such as a driver's home; a vehicle starting there leaves already loaded."))
    # The depot's postal address, for people; routing uses the reviewed `location`.
    address: str = Field('', max_length=500, description='Postal address of the depot; empty when unknown.')
    location: Location
    timezone: Literal['Europe/Paris']


class DepotRecord(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: UUID
    organization_id: UUID
    version: int = Field(ge=1)
    main: StrictBool = Field(False, description=(
        'The main depot: where a vehicle without another start begins and ends its day.'))
    example: StrictBool = Field(False, description=(
        'An example: fictional, planned only with other examples, and removed with them.'))
    data: DepotInput


class Depot(Base):
    __tablename__ = 'depots'
    __table_args__ = (
        UniqueConstraint('organization_id', 'code', name='uq_depots_code'),
        Index('uq_depots_main', 'organization_id', unique=True, postgresql_where=text('main')),
        CheckConstraint("kind IN ('DEPOT', 'PLACE')", name='ck_depots_kind'),
        CheckConstraint("NOT main OR kind = 'DEPOT'", name='ck_depots_main_kind'),
        CheckConstraint('NOT (example AND main)', name='ck_depots_example_main'),
        CheckConstraint('version >= 1', name='ck_depots_version'),
        CheckConstraint('latitude BETWEEN -90 AND 90', name='ck_depots_latitude'),
        CheckConstraint('longitude BETWEEN -180 AND 180', name='ck_depots_longitude'),
        CheckConstraint("timezone = 'Europe/Paris'", name='ck_depots_timezone'),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey('organizations.id', ondelete='RESTRICT'))
    version: Mapped[int] = mapped_column(default=1)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120))
    address: Mapped[str] = mapped_column(String(500), default='', server_default='')
    latitude: Mapped[float]
    longitude: Mapped[float]
    reviewed: Mapped[bool]
    timezone: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(16), default='DEPOT', server_default='DEPOT')
    main: Mapped[bool] = mapped_column(default=False, server_default='false')
    example: Mapped[bool] = mapped_column(default=False, server_default='false')


def depot_record(depot: Depot) -> DepotRecord:
    return DepotRecord(id=depot.id, organization_id=depot.organization_id, version=depot.version, main=depot.main,
                       example=bool(depot.example),
                       data=DepotInput(code=depot.code, name=depot.name, kind=depot.kind, address=depot.address,
                                       timezone=depot.timezone,
                                       location=Location(latitude=depot.latitude, longitude=depot.longitude,
                                                         reviewed=depot.reviewed)))


def read_depot(session: Session, organization_id: UUID) -> DepotRecord | None:
    """The company's main depot."""
    depot = session.scalar(select(Depot).where(Depot.organization_id == organization_id, Depot.main))
    return None if depot is None else depot_record(depot)


def read_depots(session: Session, organization_id: UUID) -> list[DepotRecord]:
    """Every depot and place of the company: the main depot first, then depots, then places, by code."""
    rows = session.scalars(select(Depot).where(Depot.organization_id == organization_id)
                           .order_by(Depot.main.desc(), Depot.kind, Depot.code))
    return [depot_record(row) for row in rows]
