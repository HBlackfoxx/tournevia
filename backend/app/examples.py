"""Example records: a fictional depot, two vehicles and ten deliveries a company adds to try the planner.

Examples are planned only with other examples, a plan of them says so on every route sheet and
CSV row, and they are removed together, with their plans, through one database function.
"""
from datetime import date

from pydantic import Field
from sqlalchemy import exists, func, or_, select, text
from sqlalchemy.orm import Session

from app.config import ROOT
from app.fleet import Depot, DepotInput
from app.jobs import Job
from app.planning import PARIS, Model, OrderInput, PlanningInput, VehicleSource
from app.records import DataError, Order, Vehicle, lock_owner

EXAMPLE_DEPOT = 'EXAMPLE'
FIXTURE = ROOT / 'backend/tests/fixtures/paris-ten.json'
BUSY_MESSAGE = 'A calculation is waiting or running. Wait for it to finish or cancel it, then try again.'
EXISTS_MESSAGE = 'Example deliveries already exist for this day.'
CONFLICT_MESSAGE = 'Your own records already use an identifier the examples need.'
IN_USE_MESSAGE = 'Your own vehicles or deliveries name an example depot. Choose another for them first.'


class ExampleRequest(Model):
    service_date: date = Field(description='The day the example deliveries are for.')


class ExamplesAdded(Model):
    service_date: date
    depot: str = Field(description='Code of the example depot the example vehicles start from.')
    vehicles: list[str] = Field(description='Fleet numbers of the example vehicles added now; empty when they existed.')
    deliveries: list[str]


class ExamplesRemoved(Model):
    removed: int = Field(ge=0, description='Example depots, vehicles and deliveries removed, with their plans.')


def has_examples(session: Session, owner) -> bool:
    return bool(session.scalar(select(or_(*(exists().where(table.organization_id == owner, table.example)
                                             for table in (Depot, Vehicle, Order))))))


def example_records(day: date) -> tuple[DepotInput, list[VehicleSource], list[OrderInput]]:
    """The fictional Paris examples for `day`, from the ten-delivery test fixture: vehicles work every
    day from the example depot, and deliveries keep their local service windows on that day."""
    fixture = PlanningInput.model_validate_json(FIXTURE.read_text(encoding='utf-8'))
    source = fixture.depot.data
    depot = DepotInput(code=EXAMPLE_DEPOT, name='Example depot (Paris)', kind='DEPOT', address='Fictional address, Paris',
                       location=source.location, timezone=source.timezone)
    vehicles = []
    for row in fixture.vehicles:
        vehicle = VehicleSource.weekly(row.data)
        vehicles.append(vehicle.model_copy(update={'fleet_number': 'EX-' + vehicle.fleet_number.replace('-DEMO', ''),
                                                   'start': EXAMPLE_DEPOT, 'end': None}))
    delta = day - fixture.service_date
    orders = []
    for index, row in enumerate(fixture.orders, 1):
        window = row.data.time_window
        shifted = {edge: getattr(window, edge).replace(tzinfo=PARIS) + delta for edge in ('start', 'end')}
        orders.append(OrderInput.model_validate({
            **row.data.model_dump(mode='json'), 'external_id': f'EX-{day:%Y%m%d}-{index:02d}', 'service_date': day.isoformat(),
            'address': row.data.address.replace('Synthetic Paris stop', 'Example stop') + ', Paris',
            'time_window': {edge: value.isoformat() for edge, value in shifted.items()}}))
    return depot, vehicles, orders


def add_examples(session: Session, owner, data: ExampleRequest) -> ExamplesAdded:
    """Add the example deliveries for one day, and the example depot and vehicles if they are missing."""
    lock_owner(session, owner)
    day = data.service_date
    if session.scalar(select(Order.id).where(Order.organization_id == owner, Order.example, Order.service_date == day).limit(1)):
        raise DataError(409, 'EXAMPLES_EXIST', EXISTS_MESSAGE, [day.isoformat()])
    depot, vehicles, orders = example_records(day)
    place = session.scalar(select(Depot).where(Depot.organization_id == owner, Depot.example).limit(1))
    have_vehicles = session.scalar(select(Vehicle.id).where(Vehicle.organization_id == owner, Vehicle.example).limit(1))
    taken = sorted(
        ([depot.code] if place is None and session.scalar(select(Depot.id).where(Depot.organization_id == owner, Depot.code == depot.code))
         else [])
        + ([] if have_vehicles else list(session.scalars(select(Vehicle.identifier).where(
            Vehicle.organization_id == owner, Vehicle.identifier.in_([v.fleet_number for v in vehicles])))))
        + list(session.scalars(select(Order.identifier).where(Order.organization_id == owner,
                                                               Order.identifier.in_([o.external_id for o in orders])))))
    if taken:
        raise DataError(409, 'IDENTIFIER_CONFLICT', CONFLICT_MESSAGE, taken)
    if place is None:
        place = Depot(organization_id=owner, code=depot.code, name=depot.name, kind=depot.kind, address=depot.address,
                      latitude=depot.location.latitude, longitude=depot.location.longitude, reviewed=True,
                      timezone=depot.timezone, main=False, example=True, version=1)
        session.add(place)
    added = []
    if not have_vehicles:
        # Their start is the example depot, whatever its code became.
        for vehicle in vehicles:
            vehicle = vehicle.model_copy(update={'start': place.code})
            session.add(Vehicle(organization_id=owner, identifier=vehicle.fleet_number, example=True, version=1,
                                data=vehicle.model_dump(mode='json')))
            added.append(vehicle.fleet_number)
    for order in orders:
        # Fictional points, given as reviewed: the examples can be planned at once.
        session.add(Order(organization_id=owner, identifier=order.external_id, service_date=day, example=True, version=1,
                          data=order.model_dump(mode='json')))
    session.flush()
    return ExamplesAdded(service_date=day, depot=place.code, vehicles=added, deliveries=[o.external_id for o in orders])


def remove_examples(session: Session, owner) -> ExamplesRemoved:
    """Delete every example depot, vehicle and delivery, with the plans and calculations of example vehicles."""
    lock_owner(session, owner)
    if session.scalar(select(func.count()).select_from(Job).where(Job.organization_id == owner, Job.status.in_(('QUEUED', 'RUNNING')))):
        raise DataError(409, 'JOB_ACTIVE', BUSY_MESSAGE)
    codes = list(session.scalars(select(Depot.code).where(Depot.organization_id == owner, Depot.example)))
    if codes:
        users = list(session.scalars(select(Vehicle.identifier).where(
            Vehicle.organization_id == owner, ~Vehicle.example,
            or_(Vehicle.data['start'].astext.in_(codes), Vehicle.data['end'].astext.in_(codes))).limit(10)))
        users += session.scalars(select(Order.identifier).where(
            Order.organization_id == owner, ~Order.example, Order.data['ships_from'].astext.in_(codes)).limit(10))
        if users:
            raise DataError(409, 'EXAMPLE_PLACE_IN_USE', IN_USE_MESSAGE, users)
    # The runtime role may not delete records itself; this function checks the same again.
    removed = session.scalar(text('SELECT public.remove_examples(:owner)'), {'owner': owner})
    session.expire_all()
    return ExamplesRemoved(removed=removed)
