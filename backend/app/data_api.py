"""HTTP bindings for source data; transactions and ownership stay explicit."""
from datetime import date, datetime
import re
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import Field, StrictInt
from sqlalchemy.orm import Session

from app.accounts import Organization
from app.auth import current_session, who, require_admin
from app.config import Settings
from app.fleet import DepotInput, DepotRecord, read_depot, read_depots
from app.geocoding import AddressCandidate, AddressService, Locator, address_service, lookup
from app.imports import ImportResult, Kind, import_csv
from app.numbering import NumberingRecord, NumberingSettings, next_identifier, suggest
from app.examples import ExampleRequest, ExamplesAdded, ExamplesRemoved, add_examples, remove_examples
from app.planning import CustomerInput, Leg, Model, VehicleSource
from app.records import (Customer, CustomerFilter, CustomerRecord, CustomerSummary, DataError, lock_owner, Order, OrderFilter,
                         OrderInput, OrderRecord, OrderSummary, Vehicle, VehicleFilter, VehicleRecord, VehicleSummary,
                         Versioned, create_depot, customer_summary, delete_orders, find_row, list_records, make_main,
                         order_summary, record, remove_depot, set_active, vehicle_summary, write_depot, write_record)
from app.routing import Deadline, EngineError, Valhalla


Search = Annotated[str | None, Query(min_length=1, max_length=100,
                                    description='Case-insensitive text in the identifier (and a delivery address).')]


def expected_version(value: str | None) -> int:
    if value is None:
        raise DataError(428, 'VERSION_REQUIRED', 'Send the current quoted version in If-Match.')
    if not re.fullmatch(r'"[1-9][0-9]{0,9}"', value):
        raise DataError(400, 'INVALID_VERSION', 'If-Match must be one quoted positive integer.')
    return int(value[1:-1])


def etag(response: Response, row):
    response.headers['ETag'] = f'"{row.version}"'
    return row


class AccessCheck(Model):
    status: Literal['REACHABLE', 'NO_ROUTE_IN_DATASET']
    order_version: int
    vehicle_version: int
    depot_version: int
    snap_limit_m: int = 50
    message: str
    outbound: Leg | None = None
    inbound: Leg | None = None


class NextIdentifier(Model):
    identifier: str | None = Field(description='The number a new delivery on that day would receive now; null without a numbering format.')


def numbering_of(session: Session, owner: UUID) -> NumberingSettings:
    organization = session.get(Organization, owner)
    if organization is None:
        return NumberingSettings()
    return NumberingSettings(format=organization.order_number_format, start=organization.order_number_start)


def paris_today() -> date:
    return datetime.now(ZoneInfo('Europe/Paris')).date()


class OrderSelection(Model):
    orders: list[Versioned] = Field(min_length=1, max_length=250)


class OrderActivation(OrderSelection):
    active: bool


class Deleted(Model):
    deleted: int


class SaveAsCustomer(Model):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)


class SavedCustomer(Model):
    customer: CustomerRecord
    order: OrderRecord


class AddressLookupSetting(Model):
    public_service: bool = Field(description="Look addresses up with IGN's public service; every looked-up address is sent to it.")


class SafetyMarginSetting(Model):
    minutes: StrictInt = Field(ge=0, le=120, description='Calculations plan every delivery at least this many minutes '
                               'before its window ends (never before it opens). Validation still checks the real windows.')


class SourceProblem(Model):
    code: str
    detail: str
    identifiers: list[str] | None = None


def check_access(o: OrderRecord, v: VehicleRecord, depots: list[DepotRecord], engine: Valhalla) -> AccessCheck:
    """A road from where the vehicle starts its day to the delivery, and on to where it ends it."""
    places = {row.data.code: row for row in depots}
    start = places.get(v.data.start) if v.data.start else next((row for row in depots if row.main), None)
    end = places.get(v.data.end) if v.data.end else start
    if start is None or end is None or o.data.location is None:
        raise DataError(422, 'LOCATION_MISSING', 'Set the depot and delivery coordinates before checking road access.')
    deadline = Deadline.after(10)
    try:
        points = [start.data.location, o.data.location] + ([end.data.location] if end.id != start.id else [])
        matrix = engine.matrix(points, v.data.routing, 'TRUCK_STANDARD', deadline)
        result = AccessCheck(status='NO_ROUTE_IN_DATASET', order_version=o.version, vehicle_version=v.version,
                             depot_version=start.version, message='No round trip in this dataset for the selected truck; check access, pins and graph coverage.')
        back = len(points) - 1 if len(points) == 3 else 0
        if matrix.durations[0][1] is not None and matrix.durations[1][back] is not None:
            result.outbound = engine.leg(points[0], points[1], v.data.routing, 'TRUCK_STANDARD', deadline)
            result.inbound = engine.leg(points[1], points[back], v.data.routing, 'TRUCK_STANDARD', deadline)
            result.status = 'REACHABLE'
            result.message = 'Static road connection found within 50 m. Inspect the service and snapped points; this does not confirm legal or operational access.'
        return result
    except EngineError as error:
        raise DataError(503, 'ACCESS_CHECK_FAILED', 'Road check failed: correct off-road pins or check local graph/service availability. '+str(error)) from None


def company_address_service(request: Request, settings: Settings) -> tuple[AddressService | None, tuple[float, float] | None]:
    """The company's address lookup, and its depot as the point to rank matches around."""
    with Session(request.app.state.engine) as session:
        organization = session.get(Organization, who(request).organization_id)
        depot = read_depot(session, who(request).organization_id)
    near = (depot.data.location.latitude, depot.data.location.longitude) if depot else None
    return address_service(settings, bool(organization and organization.public_address_lookup)), near


def router(settings: Settings) -> APIRouter:
    api = APIRouter(prefix='/api/v1', dependencies=[Depends(current_session)], responses={code:{'model':SourceProblem} for code in (400,401,403,404,409,412,413,415,428)})

    @api.put('/depot', response_model=DepotRecord, operation_id='updateDepot')
    def update_depot(data: DepotInput, request: Request, response: Response, if_match: Annotated[str | None, Header()] = None):
        require_admin(who(request))
        version = expected_version(if_match)
        with Session(request.app.state.engine) as session, session.begin():
            return etag(response, write_depot(session, who(request).organization_id, data, version))

    @api.get('/depots', response_model=list[DepotRecord], operation_id='listDepots')
    def depots(request: Request):
        """Every depot and place of the company: the main depot first, then depots, then places."""
        with Session(request.app.state.engine) as session:
            return read_depots(session, who(request).organization_id)

    @api.post('/depots', response_model=DepotRecord, status_code=201, operation_id='createDepot')
    def add_depot(data: DepotInput, request: Request, response: Response):
        """Add a depot, or another place a vehicle starts or ends its day at. Its point starts unreviewed."""
        require_admin(who(request))
        with Session(request.app.state.engine) as session, session.begin():
            return etag(response, create_depot(session, who(request).organization_id, data))

    @api.put('/depots/{key}', response_model=DepotRecord, operation_id='updateDepotByKey')
    def change_depot(key: UUID, data: DepotInput, request: Request, response: Response,
                     if_match: Annotated[str | None, Header()] = None):
        """Change a depot or place. Its code cannot change while vehicles or deliveries name it."""
        require_admin(who(request))
        version = expected_version(if_match)
        with Session(request.app.state.engine) as session, session.begin():
            return etag(response, write_depot(session, who(request).organization_id, data, version, key))

    @api.post('/depots/{key}/main', response_model=DepotRecord, operation_id='makeMainDepot')
    def main_depot(key: UUID, request: Request, response: Response, if_match: Annotated[str | None, Header()] = None):
        """Make this depot the main one, where vehicles without another start begin and end their day."""
        require_admin(who(request))
        version = expected_version(if_match)
        with Session(request.app.state.engine) as session, session.begin():
            return etag(response, make_main(session, who(request).organization_id, key, version))

    @api.delete('/depots/{key}', status_code=204, operation_id='deleteDepot')
    def delete_depot(key: UUID, request: Request, if_match: Annotated[str | None, Header()] = None):
        """Delete a depot or place that is not the main depot and that no vehicle or delivery names."""
        require_admin(who(request))
        version = expected_version(if_match)
        with Session(request.app.state.engine) as session, session.begin():
            remove_depot(session, who(request).organization_id, key, version)
        return Response(status_code=204)

    @api.post('/examples', response_model=ExamplesAdded, status_code=201, operation_id='addExamples')
    def add_example_records(data: ExampleRequest, request: Request):
        """Add ten fictional example deliveries for a day, with an example depot and two example vehicles
        if they are missing. Examples are planned only with other examples (admin)."""
        require_admin(who(request))
        with Session(request.app.state.engine) as session, session.begin():
            return add_examples(session, who(request).organization_id, data)

    @api.post('/examples/remove', response_model=ExamplesRemoved, operation_id='removeExamples')
    def remove_example_records(request: Request):
        """Delete every example depot, vehicle and delivery, with the plans of example vehicles (admin)."""
        require_admin(who(request))
        with Session(request.app.state.engine) as session, session.begin():
            return remove_examples(session, who(request).organization_id)

    @api.put('/workspace/address-lookup', response_model=AddressLookupSetting, operation_id='setAddressLookup')
    def set_address_lookup(data: AddressLookupSetting, request: Request):
        """Turn address lookup with IGN's public service on or off for the company. An installation
        with its own geocoder always uses that one instead."""
        require_admin(who(request))
        with Session(request.app.state.engine) as session, session.begin():
            organization = session.get(Organization, who(request).organization_id, with_for_update=True)
            if organization is None:
                raise DataError(404, 'ORGANIZATION_MISSING', 'Workspace setup is incomplete.')
            organization.public_address_lookup = data.public_service
        return data

    @api.put('/workspace/safety-margin', response_model=SafetyMarginSetting, operation_id='setSafetyMargin')
    def set_safety_margin(data: SafetyMarginSetting, request: Request):
        """Set the company's safety margin for new calculations. Saved plans keep the margin they were
        calculated with."""
        require_admin(who(request))
        with Session(request.app.state.engine) as session, session.begin():
            organization = session.get(Organization, who(request).organization_id, with_for_update=True)
            if organization is None:
                raise DataError(404, 'ORGANIZATION_MISSING', 'Workspace setup is incomplete.')
            organization.safety_margin_minutes = data.minutes
        return data

    def numbering_record(session: Session, owner: UUID) -> NumberingRecord:
        current = numbering_of(session, owner)
        return NumberingRecord(**current.model_dump(), suggestion=suggest(session, owner),
                               next=next_identifier(session, owner, current, paris_today()) if current.format else None)

    @api.get('/workspace/numbering', response_model=NumberingRecord, operation_id='getNumbering')
    def get_numbering(request: Request):
        """The company's delivery number format, the next number for today and a format that would
        continue the existing identifiers."""
        with Session(request.app.state.engine) as session:
            return numbering_record(session, who(request).organization_id)

    @api.get('/workspace/numbering/preview', response_model=NextIdentifier, operation_id='previewNumbering')
    def preview_numbering(request: Request, settings: Annotated[NumberingSettings, Query()]):
        """The number the next delivery today would receive with this format, before it is saved."""
        if not settings.format:
            return NextIdentifier(identifier=None)
        with Session(request.app.state.engine) as session:
            return NextIdentifier(identifier=next_identifier(session, who(request).organization_id, settings, paris_today()))

    @api.put('/workspace/numbering', response_model=NumberingRecord, operation_id='setNumbering')
    def set_numbering(data: NumberingSettings, request: Request):
        """Set or clear the format of new delivery identifiers. Existing identifiers never change."""
        require_admin(who(request))
        owner = who(request).organization_id
        with Session(request.app.state.engine) as session, session.begin():
            lock_owner(session, owner)
            organization = session.get(Organization, owner)
            if organization is None:
                raise DataError(404, 'ORGANIZATION_MISSING', 'Workspace setup is incomplete.')
            organization.order_number_format, organization.order_number_start = data.format, data.start
            session.flush()
            return numbering_record(session, owner)

    @api.get('/vehicles', response_model=list[VehicleRecord], operation_id='listVehicles')
    def vehicles(request: Request, limit: int = Query(50, ge=1, le=250), offset: int = Query(0, ge=0),
                 status: VehicleFilter = 'all', q: Search = None,
                 service_date: date | None = Query(None, description='Planning day; required by status=off_day.')):
        """Vehicles in fleet-number order, filtered and searched across the whole fleet."""
        with Session(request.app.state.engine) as session:
            return list_records(session, who(request).organization_id, Vehicle, limit, offset, service_date, status, q)

    @api.get('/vehicles/summary', response_model=VehicleSummary, operation_id='summarizeVehicles')
    def vehicles_summary(request: Request, service_date: date, q: Search = None):
        """Counts behind the fleet tabs, for the whole fleet (or the search)."""
        with Session(request.app.state.engine) as session:
            return vehicle_summary(session, who(request).organization_id, service_date, q)

    @api.get('/vehicles/{key}', response_model=VehicleRecord, operation_id='getVehicle')
    def vehicle(key: UUID, request: Request, response: Response):
        with Session(request.app.state.engine) as session:
            return etag(response, record(find_row(session, who(request).organization_id, Vehicle, key)))

    @api.post('/vehicles', response_model=VehicleRecord, status_code=201, operation_id='createVehicle')
    def create_vehicle(data: VehicleSource, request: Request, response: Response):
        with Session(request.app.state.engine) as session, session.begin():
            return etag(response, write_record(session, who(request).organization_id, data))

    @api.put('/vehicles/{key}', response_model=VehicleRecord, operation_id='updateVehicle')
    def update_vehicle(key: UUID, data: VehicleSource, request: Request, response: Response, if_match: Annotated[str | None, Header()] = None):
        version = expected_version(if_match)
        with Session(request.app.state.engine) as session, session.begin():
            return etag(response, write_record(session, who(request).organization_id, data, key, version))

    @api.get('/customers', response_model=list[CustomerRecord], operation_id='listCustomers')
    def customers(request: Request, limit: int = Query(50, ge=1, le=250), offset: int = Query(0, ge=0),
                  status: CustomerFilter = 'all',
                  q: Annotated[str | None, Query(min_length=1, max_length=100,
                                                 description='Case-insensitive text in the code, name or address.')] = None):
        """Saved customers in code order, filtered and searched across all of them."""
        with Session(request.app.state.engine) as session:
            return list_records(session, who(request).organization_id, Customer, limit, offset, None, status, q)

    @api.get('/customers/summary', response_model=CustomerSummary, operation_id='summarizeCustomers')
    def customers_summary(request: Request, q: Search = None):
        with Session(request.app.state.engine) as session:
            return customer_summary(session, who(request).organization_id, q)

    @api.get('/customers/{key}', response_model=CustomerRecord, operation_id='getCustomer')
    def customer(key: UUID, request: Request, response: Response):
        with Session(request.app.state.engine) as session:
            return etag(response, record(find_row(session, who(request).organization_id, Customer, key)))

    @api.post('/customers', response_model=CustomerRecord, status_code=201, operation_id='createCustomer')
    def create_customer(data: CustomerInput, request: Request, response: Response):
        with Session(request.app.state.engine) as session, session.begin():
            return etag(response, write_record(session, who(request).organization_id, data))

    @api.put('/customers/{key}', response_model=CustomerRecord, operation_id='updateCustomer')
    def update_customer(key: UUID, data: CustomerInput, request: Request, response: Response,
                        if_match: Annotated[str | None, Header()] = None):
        """Edit a customer. Deliveries already saved keep their own copy of its entrance."""
        version = expected_version(if_match)
        with Session(request.app.state.engine) as session, session.begin():
            return etag(response, write_record(session, who(request).organization_id, data, key, version))

    @api.post('/orders/{key}/customer', response_model=SavedCustomer, status_code=201, operation_id='saveOrderAsCustomer')
    def save_as_customer(key: UUID, data: SaveAsCustomer, request: Request,
                         if_match: Annotated[str | None, Header()] = None):
        """Save a delivery's address, entrance and needs as a new customer and link the delivery to it.
        A reviewed entrance stays reviewed: it is the same point a person already checked."""
        version = expected_version(if_match)
        owner = who(request).organization_id
        with Session(request.app.state.engine) as session, session.begin():
            lock_owner(session, owner)
            row = find_row(session, owner, Order, key)
            if row.version != version:
                raise DataError(412, 'SOURCE_CHANGED', 'This record changed. Reload it before applying your edits.')
            order = OrderInput.model_validate(row.data)
            start, end = (t.strftime('%H:%M') for t in (order.time_window.start, order.time_window.end))
            created = write_record(session, owner, CustomerInput(
                code=data.code, name=data.name, address=order.address, location=None,
                usual_hours={'start': start, 'end': end}, service_seconds=order.service_seconds,
                required_skills=order.required_skills, instructions=order.instructions))
            customer = session.get(Customer, created.id)
            if order.location is not None:
                customer.data = {**customer.data, 'location': order.location.model_dump(mode='json')}
            linked = write_record(session, owner, order.model_copy(update={'customer': data.code}), key, version)
            session.flush()
            return SavedCustomer(customer=record(customer), order=linked)

    @api.post('/orders/activation', response_model=list[OrderRecord], operation_id='setOrdersActive')
    def set_orders_active(data: OrderActivation, request: Request):
        """Cancel deliveries (inactive: they are not planned) or restore them, each at its current version."""
        with Session(request.app.state.engine) as session, session.begin():
            return set_active(session, who(request).organization_id, data.orders, data.active)

    @api.post('/orders/delete', response_model=Deleted, operation_id='deleteOrders')
    def delete_deliveries(data: OrderSelection, request: Request):
        """Delete deliveries for good, each at its current version. Saved plans keep their own copy and
        then ask for a fresh calculation; a delivery in a waiting or running calculation is refused."""
        with Session(request.app.state.engine) as session, session.begin():
            return Deleted(deleted=delete_orders(session, who(request).organization_id, data.orders))

    @api.get('/orders', response_model=list[OrderRecord], operation_id='listOrders')
    def orders(request: Request, service_date: date, limit: int = Query(50, ge=1, le=250), offset: int = Query(0, ge=0),
               status: OrderFilter = 'all', q: Search = None):
        """Deliveries of one day in identifier order, filtered and searched across the whole day."""
        with Session(request.app.state.engine) as session:
            return list_records(session, who(request).organization_id, Order, limit, offset, service_date, status, q)

    @api.get('/orders/summary', response_model=OrderSummary, operation_id='summarizeOrders')
    def orders_summary(request: Request, service_date: date, q: Search = None):
        """Counts behind the delivery tabs, for the whole day (or the search)."""
        with Session(request.app.state.engine) as session:
            return order_summary(session, who(request).organization_id, service_date, q)

    @api.get('/orders/next-identifier', response_model=NextIdentifier, operation_id='nextOrderIdentifier')
    def next_order_identifier(request: Request, service_date: date):
        """The number a new delivery on `service_date` would receive now. Another user may take it
        first: create the delivery with assign_identifier=true to receive the next free one."""
        with Session(request.app.state.engine) as session:
            settings = numbering_of(session, who(request).organization_id)
            return NextIdentifier(identifier=next_identifier(session, who(request).organization_id, settings, service_date)
                                  if settings.format else None)

    @api.get('/orders/{key}', response_model=OrderRecord, operation_id='getOrder')
    def order(key: UUID, request: Request, response: Response):
        with Session(request.app.state.engine) as session:
            return etag(response, record(find_row(session, who(request).organization_id, Order, key)))

    @api.post('/orders', response_model=OrderRecord, status_code=201, operation_id='createOrder')
    def create_order(data: OrderInput, request: Request, response: Response, assign_identifier: bool = Query(
            False, description="Replace external_id with the company's next delivery number when saving.")):
        owner = who(request).organization_id
        with Session(request.app.state.engine) as session, session.begin():
            if assign_identifier:
                # Under the company lock, so two people adding deliveries never receive the same number.
                lock_owner(session, owner)
                settings = numbering_of(session, owner)
                if not settings.format:
                    raise DataError(409, 'NUMBERING_OFF', 'Automatic delivery numbers are not set up. Enter an identifier.')
                data = data.model_copy(update={'external_id': next_identifier(session, owner, settings, data.service_date)})
            return etag(response, write_record(session, owner, data))

    @api.put('/orders/{key}', response_model=OrderRecord, operation_id='updateOrder')
    def update_order(key: UUID, data: OrderInput, request: Request, response: Response, if_match: Annotated[str | None, Header()] = None):
        version = expected_version(if_match)
        with Session(request.app.state.engine) as session, session.begin():
            return etag(response, write_record(session, who(request).organization_id, data, key, version))

    async def uploaded(request: Request) -> bytes:
        if request.headers.get('content-type', '').split(';')[0] != 'text/csv':
            raise DataError(415, 'CSV_REQUIRED', 'Send UTF-8 CSV as text/csv.')
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 1_048_576:
                raise DataError(413, 'UPLOAD_TOO_LARGE', 'CSV limit is 1 MiB.')
        return bytes(content)

    csv_body = {'requestBody': {'required': True, 'content': {'text/csv': {'schema': {'type': 'string'}}}}}

    @api.post('/imports/{kind}', response_model=ImportResult, operation_id='importCsv', openapi_extra=csv_body,
              responses={422:{'content':{'application/json':{'schema':{'anyOf':[
                  {'$ref':'#/components/schemas/ImportResult'}, {'$ref':'#/components/schemas/HTTPValidationError'}]}}}}})
    async def import_data(kind: Kind, request: Request, response: Response, dry_run: bool = True,
                          update_existing: bool = Query(False, description=(
                              'Let a row for an existing identifier change that record; shown as UPDATE with its '
                              'changed fields. A delivery row without coordinates keeps the saved point.'))):
        content = await uploaded(request)
        # Run database work and address lookups in a worker thread; streaming stays on the event loop.
        from starlette.concurrency import run_in_threadpool
        def perform():
            # Missing delivery coordinates are proposed from the address when address lookup is on.
            service, near = company_address_service(request, settings) if kind == 'orders' else (None, None)
            locate = Locator(service.url, near=near) if service else None
            # No Session.begin(): the import ends its read transaction before looking addresses up.
            with Session(request.app.state.engine) as session:
                owner = who(request).organization_id
                numbering = numbering_of(session, owner) if kind == 'orders' else None
                result = import_csv(session, owner, kind, content, dry_run, locate,
                                    numbering if numbering and numbering.format else None, paris_today(), update_existing)
                session.commit()
                return result
        result = await run_in_threadpool(perform)
        if result.errors:
            response.status_code = 422
        return result

    @api.get('/geocode', response_model=list[AddressCandidate], operation_id='lookupAddress', responses={503: {'model': SourceProblem}})
    def geocode(request: Request, q: Annotated[str, Query(min_length=3, max_length=200, description='Address to look up in France.')]):
        """Candidate points for an address, best first, from the installation's own geocoder or, when
        the company turned it on, IGN's public service. They are proposals: save one as the location,
        then review the vehicle entrance."""
        service, near = company_address_service(request, settings)
        if service is None:
            raise DataError(404, 'GEOCODER_DISABLED', 'Address lookup is not turned on for this workspace.')
        return lookup(service.url, q, near)

    @api.get('/orders/{key}/access-check', response_model=AccessCheck, operation_id='checkOrderAccess',
             responses={503:{'model':SourceProblem},422:{'content':{'application/json':{'schema':{'anyOf':[
                 {'$ref':'#/components/schemas/SourceProblem'}, {'$ref':'#/components/schemas/HTTPValidationError'}]}}}}})
    def access_check(key: UUID, vehicle_id: UUID, request: Request):
        with Session(request.app.state.engine) as session:
            o = record(find_row(session, who(request).organization_id, Order, key))
            v = record(find_row(session, who(request).organization_id, Vehicle, vehicle_id))
            depots = read_depots(session, who(request).organization_id)
        return check_access(o,v,depots,Valhalla(settings.valhalla_url,settings.routing_dataset))

    return api
