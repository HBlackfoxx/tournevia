"""Owned source records and transactional edits; no record history or client ownership."""
from datetime import date
from typing import Literal
from uuid import UUID, uuid4

from pydantic import Field

from sqlalchemy import CheckConstraint, ColumnElement, Date, ForeignKey, Index, String, UniqueConstraint, cast, func, or_, select, text, true
from sqlalchemy.dialects.postgresql import JSONB, JSONPATH
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.accounts import Organization
from app.db import Base
from app.fleet import Depot, DepotInput, DepotRecord, depot_record
from app.planning import DAYS, CustomerInput, Model, OrderInput, Owned, VehicleSource


class DataError(Exception):
    def __init__(self, status: int, code: str, message: str, identifiers: list[str] | None = None):
        # Identifiers name the records concerned, so the message itself stays a fixed, translatable text.
        self.status, self.code, self.message, self.identifiers = status, code, message, identifiers


class Vehicle(Base):
    __tablename__ = 'vehicles'
    __table_args__ = (UniqueConstraint('organization_id', 'identifier', name='uq_vehicles_identifier'),
                      CheckConstraint('version >= 1', name='ck_vehicles_version'))
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey('organizations.id', ondelete='RESTRICT'))
    version: Mapped[int] = mapped_column(default=1)
    identifier: Mapped[str] = mapped_column(String(40))
    example: Mapped[bool] = mapped_column(default=False, server_default='false')
    data: Mapped[dict] = mapped_column(JSONB)


class Order(Base):
    __tablename__ = 'orders'
    __table_args__ = (UniqueConstraint('organization_id', 'identifier', name='uq_orders_identifier'),
                      CheckConstraint('version >= 1', name='ck_orders_version'),
                      Index('ix_orders_day', 'organization_id', 'service_date', 'id'))
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey('organizations.id', ondelete='RESTRICT'))
    version: Mapped[int] = mapped_column(default=1)
    identifier: Mapped[str] = mapped_column(String(100))
    service_date: Mapped[date] = mapped_column(Date)
    example: Mapped[bool] = mapped_column(default=False, server_default='false')
    data: Mapped[dict] = mapped_column(JSONB)


class Customer(Base):
    __tablename__ = 'customers'
    __table_args__ = (UniqueConstraint('organization_id', 'identifier', name='uq_customers_identifier'),
                      CheckConstraint('version >= 1', name='ck_customers_version'))
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey('organizations.id', ondelete='RESTRICT'))
    version: Mapped[int] = mapped_column(default=1)
    identifier: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict] = mapped_column(JSONB)


VehicleRecord = Owned[VehicleSource]
OrderRecord = Owned[OrderInput]
CustomerRecord = Owned[CustomerInput]


def lock_owner(session: Session, owner: UUID):
    # All source writes/imports take this ordinary transaction lock in the same
    # order. It prevents preview/commit races without a ledger or lock service.
    if session.scalar(select(Organization).where(Organization.id == owner).with_for_update()) is None:
        raise DataError(404, 'ORGANIZATION_MISSING', 'Organization has not been provisioned.')


def record(row: Vehicle | Order | Customer):
    model = VehicleRecord if isinstance(row, Vehicle) else CustomerRecord if isinstance(row, Customer) else OrderRecord
    return model(id=row.id, organization_id=row.organization_id, version=row.version,
                 example=bool(getattr(row, 'example', False)), data=row.data)


def find_row(session: Session, owner: UUID, table, key: UUID):
    row = session.scalar(select(table).where(table.organization_id == owner, table.id == key))
    if row is None:
        raise DataError(404, 'NOT_FOUND', 'Record not found in this organization.')
    return row


OrderFilter = Literal['all', 'active', 'inactive', 'review', 'missing', 'ready']
VehicleFilter = Literal['all', 'active', 'inactive', 'off_day']
CustomerFilter = Literal['all', 'active', 'inactive', 'review', 'missing']


class OrderSummary(Model):
    """Delivery counts for one service day, after the optional search."""
    total: int
    active: int
    inactive: int
    review: int = Field(description='Located, but the location is not reviewed yet.')
    missing: int = Field(description='No location yet.')
    ready: int = Field(description='Active with a reviewed location: selectable for a calculation.')


class CustomerSummary(Model):
    """Customer counts after the optional search."""
    total: int
    active: int
    inactive: int
    review: int = Field(description='Located, but the entrance is not reviewed yet.')
    missing: int = Field(description='No entrance yet.')


class VehicleSummary(Model):
    """Fleet counts after the optional search; `off_day` is relative to the planning day."""
    total: int
    active: int
    inactive: int
    off_day: int = Field(description='Active vehicles that do not work on service_date: no weekly hours that weekday, or a day off.')
    active_payload_kg: float
    equipment: list[str] = Field(description='Equipment codes held by at least one vehicle.')


def works_on(day: date) -> ColumnElement[bool]:
    """A stored vehicle works on `day`: that date's exception has hours, or else its weekly hours do."""
    variables = func.jsonb_build_object('day', day.isoformat())
    path = lambda text: cast(text, JSONPATH)
    excepted = func.jsonb_path_exists(Vehicle.data, path('$.exceptions[*] ? (@.date == $day)'), variables)
    excepted_hours = func.jsonb_path_exists(Vehicle.data, path('$.exceptions[*] ? (@.date == $day && @.hours != null)'), variables)
    weekly = func.jsonb_typeof(Vehicle.data[('hours', DAYS[day.weekday()])]) == 'object'
    return excepted_hours | (~excepted & weekly)


def rules(table, day: date | None) -> dict[str, ColumnElement[bool]]:
    """Each list filter as a condition on the stored record."""
    active = table.data['active'].as_boolean()
    if table in (Order, Customer):
        reviewed = table.data[('location', 'reviewed')].as_boolean()
        found = {'active': active, 'inactive': ~active, 'review': reviewed.is_(False),
                 'missing': table.data['location'].astext.is_(None)}
        return {**found, 'ready': active & reviewed.is_(True)} if table is Order else found
    found = {'active': active, 'inactive': ~active}
    if day is not None:
        found['off_day'] = active & ~works_on(day)
    return found


def matching(table, q: str | None) -> ColumnElement[bool]:
    """Case-insensitive search in the identifier, and in a delivery's address."""
    if not q:
        return true()
    found = table.identifier.icontains(q, autoescape=True)
    if table in (Order, Customer):
        found = found | table.data['address'].astext.icontains(q, autoescape=True)
    if table is Customer:
        found = found | Customer.data['name'].astext.icontains(q, autoescape=True)
    return found


def scope(table, owner: UUID, day: date | None, q: str | None) -> list[ColumnElement[bool]]:
    where = [table.organization_id == owner, matching(table, q)]
    if table is Order:
        where.append(Order.service_date == day)
    return where


def list_records(session: Session, owner: UUID, table, limit: int, offset: int, day: date | None = None,
                 status: str = 'all', q: str | None = None):
    query = select(table).where(*scope(table, owner, day, q))
    if status != 'all':
        conditions = rules(table, day)
        if status not in conditions:
            raise DataError(422, 'SERVICE_DATE_REQUIRED', 'Give service_date to find vehicles that do not work that day.')
        query = query.where(conditions[status])
    # Identifiers are unique per company: a stable order people can scan and page through.
    query = query.order_by(table.identifier, table.id).limit(limit).offset(offset)
    return [record(row) for row in session.scalars(query)]


def order_summary(session: Session, owner: UUID, day: date, q: str | None = None) -> OrderSummary:
    conditions = rules(Order, day)
    counts = session.execute(select(func.count(), *(func.count().filter(c) for c in conditions.values()))
                             .where(*scope(Order, owner, day, q))).one()
    return OrderSummary(total=counts[0], **dict(zip(conditions, counts[1:])))


def customer_summary(session: Session, owner: UUID, q: str | None = None) -> CustomerSummary:
    conditions = rules(Customer, None)
    counts = session.execute(select(func.count(), *(func.count().filter(c) for c in conditions.values()))
                             .where(*scope(Customer, owner, None, q))).one()
    return CustomerSummary(total=counts[0], **dict(zip(conditions, counts[1:])))


def vehicle_summary(session: Session, owner: UUID, day: date, q: str | None = None) -> VehicleSummary:
    conditions, where = rules(Vehicle, day), scope(Vehicle, owner, None, q)
    payload = func.coalesce(func.sum(Vehicle.data[('capacity', 'weight_kg')].as_float()).filter(conditions['active']), 0)
    counts = session.execute(select(func.count(), *(func.count().filter(c) for c in conditions.values()), payload)
                             .where(*where)).one()
    code = func.jsonb_array_elements_text(Vehicle.data['skills']).label('code')
    equipment = session.scalars(select(code).where(*where).distinct().order_by(code))
    return VehicleSummary(total=counts[0], **dict(zip(conditions, counts[1:-1])), active_payload_kg=counts[-1],
                          equipment=list(equipment))


def identifier(data: VehicleSource | OrderInput | CustomerInput) -> str:
    if isinstance(data, CustomerInput):
        return data.code
    return data.fleet_number if isinstance(data, VehicleSource) else data.external_id


def same_point(a: dict, b: dict) -> bool:
    return all(a[k] == b[k] for k in ('latitude', 'longitude'))


def find_customer(session: Session, owner: UUID, code: str) -> Customer:
    customer = session.scalar(select(Customer).where(Customer.organization_id == owner, Customer.identifier == code))
    if customer is None:
        raise DataError(422, 'CUSTOMER_UNKNOWN', 'No saved customer has this code.', [code])
    return customer


CUSTOMER_IN_USE_MESSAGE = 'Deliveries name this customer by its code. Keep the code, or give those deliveries another customer first.'


def write_record(session: Session, owner: UUID, data: VehicleSource | OrderInput | CustomerInput,
                 key: UUID | None = None, version: int | None = None):
    lock_owner(session, owner)
    table = Vehicle if isinstance(data, VehicleSource) else Customer if isinstance(data, CustomerInput) else Order
    current = find_row(session, owner, table, key) if key else None
    if current and current.version != version:
        raise DataError(412, 'SOURCE_CHANGED', 'This record changed. Reload it before applying your edits.')
    duplicate = session.scalar(select(table).where(table.organization_id == owner, table.identifier == identifier(data)))
    if duplicate is not None and (current is None or duplicate.id != current.id):
        raise DataError(409, 'IDENTIFIER_CONFLICT', 'That identifier already exists. Edit its current version explicitly.')
    if table in (Vehicle, Order):
        check_places(session, owner, data, example=bool(current and current.example))
    if table is Customer and current is not None and current.identifier != data.code:
        # Deliveries name their customer by code; a new code would leave them naming none.
        users = session.scalars(select(Order.identifier).where(
            Order.organization_id == owner, Order.data['customer'].astext == current.identifier)
            .order_by(Order.identifier).limit(10)).all()
        if users:
            raise DataError(409, 'CUSTOMER_IN_USE', CUSTOMER_IN_USE_MESSAGE, users)
    value = data.model_dump(mode='json')
    if table in (Order, Customer) and value['location'] is not None:
        old = current.data.get('location') if current else None
        if old is None or not same_point(value['location'], old):
            value['location']['reviewed'] = False
    if table is Order and data.customer:
        # A delivery takes its customer's entrance, and that entrance's review, when it has no
        # point of its own or the very same one; any other point stays its own to review.
        entrance = find_customer(session, owner, data.customer).data.get('location')
        if entrance and value['location'] is None:
            value['location'] = dict(entrance)
        elif entrance and same_point(value['location'], entrance):
            value['location']['reviewed'] = entrance['reviewed']
    if current is None:
        current = table(organization_id=owner, identifier=identifier(data), data=value, version=1)
        session.add(current)
    else:
        current.data, current.identifier = value, identifier(data)
        current.version += 1
    if table is Order:
        current.service_date = data.service_date
    session.flush()
    return record(current)


class Versioned(Model):
    id: UUID
    version: int = Field(ge=1)


def current_rows(session: Session, owner: UUID, items: list[Versioned]) -> list[Order]:
    """The selected deliveries, each still at the version the person saw."""
    if len({item.id for item in items}) != len(items):
        raise DataError(422, 'DUPLICATE_RECORD', 'Select each delivery once.')
    rows = []
    for item in items:
        row = find_row(session, owner, Order, item.id)
        if row.version != item.version:
            raise DataError(412, 'SOURCE_CHANGED', 'This record changed. Reload it before applying your edits.', [row.identifier])
        rows.append(row)
    return rows


def set_active(session: Session, owner: UUID, items: list[Versioned], active: bool) -> list[OrderRecord]:
    lock_owner(session, owner)
    rows = current_rows(session, owner, items)
    for row in rows:
        if row.data.get('active') != active:
            row.data = {**row.data, 'active': active}
            row.version += 1
    session.flush()
    return [record(row) for row in rows]


ACTIVE_JOB_MESSAGE = 'A calculation that uses these deliveries is waiting or running. Wait for it to finish or cancel it, then try again.'


def delete_orders(session: Session, owner: UUID, items: list[Versioned]) -> int:
    lock_owner(session, owner)
    rows = current_rows(session, owner, items)
    keys = [row.id for row in rows]
    from app.jobs import Job  # Jobs depend on records; import here to keep that order.
    if session.scalar(select(func.count()).select_from(Job).where(
            Job.organization_id == owner, Job.status.in_(('QUEUED', 'RUNNING')),
            or_(*(Job.input_data['orders'].contains([{'id': str(key)}]) for key in keys)))):
        raise DataError(409, 'JOB_ACTIVE', ACTIVE_JOB_MESSAGE)
    # The runtime role may not delete records itself: this database function checks the owner and
    # waiting calculations again, and deletes nothing else.
    session.execute(text('SELECT public.delete_orders(:owner, :keys)'), {'owner': owner, 'keys': keys})
    session.expire_all()
    return len(keys)


def find_depot(session: Session, owner: UUID, key: UUID | None = None) -> Depot:
    """A depot or place of the company; no key is the main depot."""
    row = session.scalar(select(Depot).where(Depot.organization_id == owner, Depot.main if key is None else Depot.id == key))
    if row is None:
        raise DataError(404, 'NOT_FOUND', 'Depot not found in this organization.')
    return row


def named_by(session: Session, owner: UUID, code: str, *, vehicles: bool = True) -> list[str]:
    """Vehicles that start or end at this depot or place, and deliveries that ship from it (ten of each)."""
    found = []
    if vehicles:
        found += session.scalars(select(Vehicle.identifier).where(
            Vehicle.organization_id == owner, or_(Vehicle.data['start'].astext == code, Vehicle.data['end'].astext == code))
            .order_by(Vehicle.identifier).limit(10))
    found += session.scalars(select(Order.identifier).where(Order.organization_id == owner, Order.data['ships_from'].astext == code)
                             .order_by(Order.identifier).limit(10))
    return found


IN_USE_MESSAGE = 'Vehicles or deliveries name this depot or place. Choose another for them first.'


EXAMPLE_PLACE_MESSAGE = 'Only example vehicles and deliveries use an example depot.'


def check_places(session: Session, owner: UUID, data: VehicleSource | OrderInput, *, example: bool = False) -> None:
    """Every depot or place a vehicle or delivery names exists; a delivery ships from a depot;
    only an example (`example`) names an example depot."""
    codes = [data.start, data.end] if isinstance(data, VehicleSource) else [data.ships_from]
    for code in filter(None, codes):
        place = session.execute(select(Depot.kind, Depot.example).where(Depot.organization_id == owner, Depot.code == code)).first()
        if place is None:
            raise DataError(422, 'PLACE_UNKNOWN', 'No depot or place has this code.', [code])
        if isinstance(data, OrderInput) and place.kind != 'DEPOT':
            raise DataError(422, 'NOT_A_DEPOT', 'A delivery ships from a depot, not from another place.', [code])
        if place.example and not example:
            raise DataError(422, 'EXAMPLE_PLACE', EXAMPLE_PLACE_MESSAGE, [code])


def create_depot(session: Session, owner: UUID, data: DepotInput) -> DepotRecord:
    """A new depot or place. Its point starts unreviewed, as every new point does."""
    lock_owner(session, owner)
    if session.scalar(select(Depot.id).where(Depot.organization_id == owner, Depot.code == data.code)):
        raise DataError(409, 'IDENTIFIER_CONFLICT', 'That identifier already exists. Edit its current version explicitly.')
    row = Depot(organization_id=owner, code=data.code, name=data.name, kind=data.kind, address=data.address,
                latitude=data.location.latitude, longitude=data.location.longitude, reviewed=False,
                timezone=data.timezone, main=False, version=1)
    session.add(row)
    session.flush()
    return depot_record(row)


def write_depot(session: Session, owner: UUID, data: DepotInput, version: int, key: UUID | None = None) -> DepotRecord:
    """Change a depot or place at its version; no key is the main depot."""
    lock_owner(session, owner)
    row = find_depot(session, owner, key)
    if row.version != version:
        raise DataError(412, 'SOURCE_CHANGED', 'Depot changed. Reload before editing.')
    if data.code != row.code:
        if session.scalar(select(Depot.id).where(Depot.organization_id == owner, Depot.code == data.code)):
            raise DataError(409, 'IDENTIFIER_CONFLICT', 'That identifier already exists. Edit its current version explicitly.')
        if users := named_by(session, owner, row.code):
            raise DataError(409, 'DEPOT_IN_USE', IN_USE_MESSAGE, users)
    if data.kind == 'PLACE' and row.main:
        raise DataError(422, 'MAIN_DEPOT_KIND', 'The main depot is a depot. Make another depot the main depot first.')
    if data.kind == 'PLACE' and row.kind == 'DEPOT' and (users := named_by(session, owner, row.code, vehicles=False)):
        raise DataError(409, 'DEPOT_IN_USE', 'Deliveries ship from this depot. Choose another depot for them first.', users)
    moved = (row.latitude, row.longitude) != (data.location.latitude, data.location.longitude)
    row.code, row.name, row.kind, row.address, row.timezone = data.code, data.name, data.kind, data.address, data.timezone
    row.latitude, row.longitude = data.location.latitude, data.location.longitude
    row.reviewed = False if moved else data.location.reviewed
    row.version += 1
    session.flush()
    return depot_record(row)


def make_main(session: Session, owner: UUID, key: UUID, version: int) -> DepotRecord:
    """Make this depot the main one: vehicles without another start begin and end their day there."""
    lock_owner(session, owner)
    row = find_depot(session, owner, key)
    if row.version != version:
        raise DataError(412, 'SOURCE_CHANGED', 'Depot changed. Reload before editing.')
    if row.kind != 'DEPOT':
        raise DataError(422, 'MAIN_DEPOT_KIND', 'Only a depot can be the main depot.')
    if row.example:
        raise DataError(422, 'EXAMPLE_NOT_MAIN', 'An example depot cannot be the main depot.')
    if not row.main:
        current = find_depot(session, owner)
        current.main, current.version = False, current.version + 1
        session.flush()  # One main depot at a time.
        row.main, row.version = True, row.version + 1
        session.flush()
    return depot_record(row)


def remove_depot(session: Session, owner: UUID, key: UUID, version: int) -> None:
    lock_owner(session, owner)
    row = find_depot(session, owner, key)
    if row.version != version:
        raise DataError(412, 'SOURCE_CHANGED', 'Depot changed. Reload before editing.')
    if row.main:
        raise DataError(409, 'MAIN_DEPOT', 'The main depot cannot be deleted. Make another depot the main depot first.')
    if users := named_by(session, owner, row.code):
        raise DataError(409, 'DEPOT_IN_USE', IN_USE_MESSAGE, users)
    # The runtime role may not delete records itself; this function checks the same again.
    session.execute(text('SELECT public.delete_depot(:owner, :key)'), {'owner': owner, 'key': key})
    session.expire_all()
