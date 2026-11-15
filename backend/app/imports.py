"""Bounded basic CSV import. Preview and commit share validation, never saved uploads."""
import csv
from datetime import date
import io
from math import asin, cos, radians, sin, sqrt
import re
from typing import Literal
from uuid import UUID

from pydantic import Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fleet import read_depots
from app.geocoding import AddressCandidate, Locator
from app.numbering import NumberingSettings, next_identifier
from app.planning import DAYS, DIMENSIONS, CustomerInput, Model, OrderInput, VehicleSource
from app.records import Customer, DataError, Order, Vehicle, identifier, lock_owner, write_record
from app.messages import plain

Kind = Literal['vehicles', 'orders']
TRUCK_FIELDS = ['height_mm', 'width_mm', 'length_mm', 'gross_weight_kg', 'axle_load_kg', 'axle_count']
HEADERS = {
    # One column per weekday: working hours as 07:30-17:00, or empty for a day off.
    'vehicles': ['fleet_number', 'active', *TRUCK_FIELDS, *DIMENSIONS, 'skills', *DAYS,
                 'fixed_minor', 'per_km_minor', 'per_hour_minor'],
    'orders': ['external_id', 'service_date', 'active', 'address', 'latitude', 'longitude',
               'time_window_start', 'time_window_end', 'service_seconds', *DIMENSIONS,
               'required_skills', 'instructions'],
}
# Optional trailing columns, in this order. Empty cells keep the model default: priority 0,
# no customer, no route limit, the main depot as start, the start as end and any depot.
OPTIONAL_HEADERS = {
    'vehicles': ['max_stops', 'max_distance_m', 'max_driving_seconds', 'start', 'end'],
    'orders': ['priority', 'customer', 'ships_from'],
}
# Optional cells that hold a code rather than a number.
CODES = ('customer', 'ships_from', 'start', 'end')


class RowError(Model):
    row: int
    field: str
    message: str
    # Translatable wording: `template` with `{0}` placeholders filled from `values` (see app.messages).
    template: str | None = None
    values: list[str] = Field(default_factory=list)


# Model paths shown as the CSV column a person can correct.
COLUMNS = {
    ('location', 'latitude'): 'latitude', ('location', 'longitude'): 'longitude',
    ('time_window', 'start'): 'time_window_start', ('time_window', 'end'): 'time_window_end',
    ('time_window',): 'time_window_start, time_window_end',
}


def column(loc: tuple) -> str:
    path = tuple(str(part) for part in loc)
    if not path:
        return 'row'
    if path in COLUMNS:
        return COLUMNS[path]
    if path[0] in ('demand', 'capacity', 'routing', 'cost', 'limits', 'hours') and len(path) > 1:
        return path[1]
    return path[0]


class RowWarning(Model):
    """Something in a row to check; unlike an error it does not stop the import."""
    code: Literal['NO_LOCATION', 'ADDRESS_NOT_FOUND', 'ADDRESS_VAGUE', 'ADDRESS_UNCERTAIN', 'COORDINATES_SWAPPED',
                  'FAR_FROM_DEPOT', 'SAME_ADDRESS_ELSEWHERE', 'PAST_DATE', 'CUSTOMER_POINT_DIFFERS']
    message: str
    template: str | None = None
    values: list[str] = Field(default_factory=list)


class PreviewRow(Model):
    row: int
    # UPDATE: an existing record the import changes, only when changes to existing records were asked for.
    action: Literal['INSERT', 'UNCHANGED', 'UPDATE']
    # For UPDATE, the fields the row changes.
    changes: list[str] = Field(default_factory=list)
    data: VehicleSource | OrderInput
    # Set when the row had no coordinates and the address lookup proposed these (unreviewed).
    address_match: AddressCandidate | None = None
    # The row had no identifier and received the company's next delivery number.
    numbered: bool = False
    # Deliveries: where the point comes from. Only a saved customer's reviewed entrance, or the point an
    # existing delivery already has (SAVED), arrives reviewed.
    location_source: Literal['FILE', 'ADDRESS', 'CUSTOMER', 'SAVED', 'NONE'] | None = None
    # How the row's customer was found: its `customer` cell, or the same address as a saved customer.
    customer_match: Literal['CODE', 'ADDRESS'] | None = None
    warnings: list[RowWarning] = Field(default_factory=list)


class ImportResult(Model):
    dry_run: bool
    committed: bool = False
    inserted: int = 0
    unchanged: int = 0
    updated: int = 0
    rows: list[PreviewRow] = Field(default_factory=list)
    errors: list[RowError] = Field(default_factory=list)
    # Why some rows without coordinates were not looked up: the service failed or ran out of time.
    address_lookup: Literal['UNAVAILABLE', 'INCOMPLETE'] | None = None


# A row without an identifier until it receives the company's next delivery number.
AUTO = '\x00row-'
# Farther than this from the depot, a point is more likely a wrong address than a delivery.
FAR_FROM_DEPOT_KM = 100
SAME_POINT_KM = 0.3


def normal(address: str) -> str:
    """An address compared without case or spacing differences."""
    return ' '.join(address.casefold().split())


def kilometres(a, b) -> float:
    """Great-circle distance between two points with latitude and longitude."""
    la1, lo1, la2, lo2 = map(radians, (a.latitude, a.longitude, b.latitude, b.longitude))
    h = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * asin(sqrt(h))


def location_warnings(model: OrderInput, source: str, weak: AddressCandidate | None, looked_up: bool,
                      depots: list, today: date | None) -> list[RowWarning]:
    """What to check about a delivery's point. Its distance is to the depot it ships from, or the nearest depot."""
    found = []
    if today and model.service_date < today:
        found.append(RowWarning(code='PAST_DATE', message=f'The service date {model.service_date} is in the past.',
                                template='The service date {0} is in the past.', values=[str(model.service_date)]))
    point = model.location
    if point is None:
        if weak is not None and weak.kind in ('municipality', 'locality'):
            found.append(RowWarning(code='ADDRESS_VAGUE',
                                    message=f'Only the town or place was found ({weak.label}), so no point was placed.',
                                    template='Only the town or place was found ({0}), so no point was placed.',
                                    values=[weak.label]))
        elif weak is not None:
            score = round(weak.score * 100)
            found.append(RowWarning(code='ADDRESS_UNCERTAIN',
                                    message=f'Only an uncertain match was found ({weak.label}, {score} %), so no point was placed.',
                                    template='Only an uncertain match was found ({0}, {1} %), so no point was placed.',
                                    values=[weak.label, str(score)]))
        elif looked_up:
            found.append(RowWarning(code='ADDRESS_NOT_FOUND', message='The address was not found. Place the pin after the import.'))
        else:
            found.append(RowWarning(code='NO_LOCATION', message='No coordinates, and the address was not looked up. Place the pin after the import.'))
        return found
    if source == 'FILE' and -6 <= point.latitude <= 10 and 41 <= point.longitude <= 52:
        # Swapped coordinates are far from everything; their distance says nothing more.
        found.append(RowWarning(code='COORDINATES_SWAPPED',
                                message='Latitude and longitude look swapped: in France latitude is about 41 to 51 and longitude about -5 to 10.'))
    elif (near := [d for d in depots if model.ships_from in (None, d.data.code)]) and \
            (distance := min(kilometres(point, d.data.location) for d in near)) > FAR_FROM_DEPOT_KM:
        found.append(RowWarning(code='FAR_FROM_DEPOT', message=f'{round(distance)} km from the depot. Check the address or the coordinates.',
                                template='{0} km from the depot. Check the address or the coordinates.', values=[str(round(distance))]))
    return found


class CellError(ValueError):
    def __init__(self, field, message):
        self.field, self.message = field, message


# Spreadsheet programs write their own words for yes and no: TRUE/FALSE, or VRAI/FAUX in French.
BOOLEANS = {'true': True, 'false': False, 'vrai': True, 'faux': False}


def decimal(cell: str) -> float:
    """A number with a decimal point, or the decimal comma spreadsheets write in French."""
    cell = cell.strip()
    return float(cell.replace(',', '.') if cell.count(',') == 1 and '.' not in cell else cell)


def separator(source: str, kind: Kind) -> str:
    """Semicolons when the header row is this kind's headers separated by semicolons, as spreadsheets set
    to French conventions save CSV; commas otherwise."""
    first = next(csv.reader([source.partition('\n')[0].rstrip('\r')], delimiter=';'), [])
    return ';' if len(first) > 1 and first[:len(HEADERS[kind])] == HEADERS[kind] else ','


def parse_row(kind: Kind, row: dict):
    def integer(key):
        if not re.fullmatch(r'[0-9]{1,18}', row[key]):
            raise CellError(key, 'Use a nonnegative whole number in the stated unit.')
        return int(row[key])
    if (active := BOOLEANS.get(row['active'].strip().casefold())) is None:
        raise CellError('active', 'Use true or false.')
    amounts = {key: integer(key) for key in DIMENSIONS}
    optional = {key: integer(key) for key in OPTIONAL_HEADERS[kind] if row.get(key) and key not in CODES}
    codes = {key: row[key].strip() for key in CODES if row.get(key, '').strip()}
    if kind == 'vehicles':
        def hours(day):
            if not row[day].strip():
                return None
            if not (m := re.fullmatch(r'\s*([0-9]{2}:[0-9]{2})\s*-\s*([0-9]{2}:[0-9]{2})\s*', row[day])):
                raise CellError(day, 'Write working hours as 07:30-17:00, or leave the cell empty for a day off.')
            return {'start': m.group(1), 'end': m.group(2)}
        return VehicleSource.model_validate({
            'fleet_number': row['fleet_number'], 'active': active,
            'routing': {key: integer(key) for key in TRUCK_FIELDS}, 'capacity': amounts,
            'skills': row['skills'].split('|') if row['skills'] else [],
            'hours': {day: hours(day) for day in DAYS},
            'cost': {'currency': 'EUR', **{key: integer(key) for key in ['fixed_minor', 'per_km_minor', 'per_hour_minor']}},
            'limits': optional, **codes})
    location = None
    if row['latitude'] or row['longitude']:
        try:
            location = {'latitude': decimal(row['latitude']), 'longitude': decimal(row['longitude']), 'reviewed': False}
        except ValueError:
            raise CellError('location', 'Provide both numeric latitude and longitude, or leave both empty.') from None
    return OrderInput.model_validate({
        'external_id': row['external_id'], 'service_date': row['service_date'], 'active': active,
        'address': row['address'], 'location': location,
        'time_window': {'start': row['time_window_start'], 'end': row['time_window_end']},
        'service_seconds': integer('service_seconds'), 'demand': amounts,
        'required_skills': row['required_skills'].split('|') if row['required_skills'] else [],
        'instructions': row['instructions'], **optional, **codes})


def changed_fields(prior: dict, value: dict) -> list[str]:
    """The fields of a record that differ, a point compared by its place and not its review."""
    def comparable(name, item):
        return {k: v for k, v in item.items() if k != 'reviewed'} if name == 'location' and item else item
    return [name for name in value if comparable(name, prior.get(name)) != comparable(name, value[name])]


def import_csv(session: Session, owner: UUID, kind: Kind, content: bytes, dry_run: bool,
               locate: Locator | None = None, numbering: NumberingSettings | None = None,
               today: date | None = None, update_existing: bool = False) -> ImportResult:
    """Preview or commit one CSV. With `numbering`, delivery rows without an identifier receive the
    company's next numbers; deliveries carry where their point comes from and what to check. With
    `update_existing`, a row for an existing record changes it instead of being refused.

    With `locate`, the session must not be inside `Session.begin()`: the read transaction ends before
    addresses are looked up, and the caller commits the session afterwards."""
    if len(content) > 1_048_576:
        raise DataError(413, 'UPLOAD_TOO_LARGE', 'CSV limit is 1 MiB.')
    result = ImportResult(dry_run=dry_run)
    # Saved customers, by code and by their exact address (an address two customers share matches neither).
    customers = {row.identifier: CustomerInput.model_validate(row.data) for row in session.scalars(
        select(Customer).where(Customer.organization_id == owner))} if kind == 'orders' else {}
    by_address: dict[str, str | None] = {}
    for code, saved in customers.items():
        if saved.active and saved.location is not None:
            key = normal(saved.address)
            by_address[key] = None if key in by_address else code
    try:
        source = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        result.errors.append(RowError(row=1, field='file', message='CSV must be UTF-8.'))
        return result
    # Depots and places by code, for the codes vehicles and deliveries name.
    sites = {row.data.code: row for row in read_depots(session, owner)}
    reader = csv.reader(io.StringIO(source, newline=''), delimiter=separator(source, kind), strict=True)
    parsed, seen, extra = [], set(), []
    try:
        columns = next(reader, [])
        basic, extra = columns[:len(HEADERS[kind])], columns[len(HEADERS[kind]):]
        # Route limits come together, then start and end; a delivery file may stop after any optional column.
        optional_columns = OPTIONAL_HEADERS[kind]
        allowed = ([], optional_columns[:3], optional_columns) if kind == 'vehicles' else \
            [optional_columns[:size] for size in range(len(optional_columns) + 1)]
        if basic != HEADERS[kind] or extra not in allowed:
            template = 'Expected exact headers: {0}, optionally followed by: {1}'
            values = [','.join(HEADERS[kind]), ','.join(OPTIONAL_HEADERS[kind])]
            result.errors.append(RowError(row=1, field='headers', message=template.format(*values), template=template, values=values))
            return result
        for number, cells in enumerate(reader, 2):
            if number > 251:
                result.errors.append(RowError(row=number, field='file', message='Maximum 250 data rows.'))
                return result
            if len(cells) != len(columns):
                result.errors.append(RowError(row=number, field='row', message='Column count differs from headers.'))
                continue
            values = dict(zip(columns, cells))
            if kind == 'orders' and numbering is not None and not values['external_id'].strip():
                values['external_id'] = f'{AUTO}{number}'
            if code := values.get('customer', '').strip():
                if code not in customers:
                    result.errors.append(RowError(row=number, field='customer', message='No saved customer has this code.'))
                    continue
                # A row for a saved customer may leave the address to the customer.
                if not values['address'].strip():
                    values['address'] = customers[code].address
            try:
                model = parse_row(kind, values)
                named = [('start', model.start), ('end', model.end)] if kind == 'vehicles' else [('ships_from', model.ships_from)]
                if problem := next(((field, code) for field, code in named if code and code not in sites), None):
                    result.errors.append(RowError(row=number, field=problem[0], message='No depot or place has this code.'))
                    continue
                if problem := next(((field, code) for field, code in named if code and sites[code].example), None):
                    result.errors.append(RowError(row=number, field=problem[0],
                                                  message='Only example vehicles and deliveries use an example depot.'))
                    continue
                if kind == 'orders' and model.ships_from and sites[model.ships_from].data.kind != 'DEPOT':
                    result.errors.append(RowError(row=number, field='ships_from',
                                                  message='A delivery ships from a depot, not from another place.'))
                    continue
                key = identifier(model)
                if key in seen:
                    result.errors.append(RowError(row=number, field='identifier', message='Duplicate identifier within upload.'))
                else:
                    parsed.append((number, model))
                    seen.add(key)
            except CellError as error:
                result.errors.append(RowError(row=number, field=error.field, message=error.message))
            except ValidationError as error:
                for e in error.errors(include_input=False, include_url=False):
                    message, template, values = plain(e)
                    result.errors.append(RowError(row=number, field=column(e['loc']), message=message, template=template, values=values))
    except csv.Error:
        result.errors.append(RowError(row=reader.line_num, field='file', message='Malformed CSV quoting or oversized field.'))
    if not parsed and not result.errors:
        result.errors.append(RowError(row=2, field='file', message='Provide at least one data row.'))
    # Before any database work: the lookup holds neither a connection nor the owner lock.
    matches, weak, looked_up, matched = {}, {}, set(), {}
    from_file = {number for number, model in parsed if kind == 'orders' and model.location is not None}
    # A saved customer's entrance, review included, before any address lookup.
    for index, (number, model) in enumerate(parsed):
        if kind != 'orders' or model.location is not None:
            continue
        code, how = model.customer, 'CODE'
        if code is None and (code := by_address.get(normal(model.address))):
            how = 'ADDRESS'
        if code and customers[code].location is not None:
            parsed[index] = (number, model.model_copy(update={'customer': code, 'location': customers[code].location}))
            matched[number] = how
    if locate is not None and not result.errors and any(model.location is None for _, model in parsed):
        # A lookup can take seconds per address. End the read transaction first (nothing is written
        # before this point), so the wait holds neither a database connection nor the owner lock;
        # the commit below checks every saved record again under the lock.
        session.commit()
        for index, (number, model) in enumerate(parsed):
            if model.location is None and not locate.stopped:
                found = locate(model.address)
                if found is not None:
                    location = {'latitude': found.latitude, 'longitude': found.longitude, 'reviewed': False}
                    parsed[index] = (number, OrderInput.model_validate({**model.model_dump(mode='json'), 'location': location}))
                    matches[number] = found
                elif locate.weak is not None:
                    weak[number] = locate.weak
                # A row the lookup stopped before is unlooked-up, not unfound.
                if not locate.stopped:
                    looked_up.add(number)
        result.address_lookup = locate.stopped
    # Serialize against all other source mutations; recheck even after a successful preview.
    lock_owner(session, owner)
    # Numbers are given under the company lock, after the identifiers the file names itself.
    numbered = set()
    if numbering is not None:
        taken = {identifier(model) for _, model in parsed if not identifier(model).startswith(AUTO)}
        saved = {}  # The highest saved number per date, read once for the whole file.
        for index, (number, model) in enumerate(parsed):
            if model.external_id.startswith(AUTO):
                given = next_identifier(session, owner, numbering, model.service_date, taken, saved)
                taken.add(given)
                numbered.add(number)
                parsed[index] = (number, model.model_copy(update={'external_id': given}))
    depots = [row for row in sites.values() if row.data.kind == 'DEPOT' and not row.example]
    places: dict[str, tuple[int, object]] = {}
    table = Vehicle if kind == 'vehicles' else Order
    keys = [identifier(model) for _, model in parsed]
    existing = {r.identifier: r for r in session.scalars(select(table).where(table.organization_id == owner, table.identifier.in_(keys)))}
    kept = set()
    for number, model in parsed:
        old = existing.get(identifier(model))
        changes = []
        if old:
            prior = (VehicleSource if kind == 'vehicles' else OrderInput).model_validate(old.data)
            # A file without an optional column says nothing about it: the record keeps its value.
            # (An empty cell in a column the file has is the default, a change like any other.)
            if kind == 'vehicles':
                # Days off are kept in the application; a file of weekly hours does not list them.
                model = model.model_copy(update={'exceptions': prior.exceptions,
                                                 **({} if 'max_stops' in extra else {'limits': prior.limits}),
                                                 **({} if 'start' in extra else {'start': prior.start, 'end': prior.end})})
            else:
                model = model.model_copy(update={
                    **({} if 'priority' in extra else {'priority': prior.priority}),
                    **({} if 'customer' in extra or number in matched else {'customer': prior.customer}),
                    **({} if 'ships_from' in extra else {'ships_from': prior.ships_from})})
                if number not in from_file and prior.location is not None:
                    # A row without coordinates keeps the delivery's saved point and its review; a lookup or
                    # an address match does not move it.
                    update = {'location': prior.location}
                    if matched.get(number) == 'ADDRESS':
                        update['customer'] = prior.customer
                    model = model.model_copy(update=update)
                    for found in (matches, weak, matched):
                        found.pop(number, None)
                    kept.add(number)
                elif number in from_file and prior.location is not None and                         (model.location.latitude, model.location.longitude) == (prior.location.latitude, prior.location.longitude):
                    # The same point again keeps its review.
                    model = model.model_copy(update={'location': prior.location})
            changes = changed_fields(prior.model_dump(mode='json'), model.model_dump(mode='json'))
            if changes and not update_existing:
                result.errors.append(RowError(row=number, field='identifier', message='Existing identifier has different values; use a version-checked edit.'))
                continue
        if kind == 'orders' and model.customer and model.customer not in customers:
            # A saved delivery can still name a customer code that no longer exists (renamed before
            # renames were refused); say so on its row rather than failing the whole file.
            result.errors.append(RowError(row=number, field='customer', message='No saved customer has this code.'))
            continue
        preview = PreviewRow(row=number, action='UPDATE' if changes else 'UNCHANGED' if old else 'INSERT', data=model,
                             changes=changes, address_match=matches.get(number), numbered=number in numbered)
        if kind == 'orders':
            preview.location_source = ('FILE' if number in from_file else 'SAVED' if number in kept
                                       else 'CUSTOMER' if number in matched else 'ADDRESS' if number in matches else 'NONE')
            preview.customer_match = matched.get(number) or ('CODE' if model.customer else None)
            preview.warnings = location_warnings(model, preview.location_source, weak.get(number),
                                                 number in looked_up, depots, today)
            entrance = customers[model.customer].location if model.customer else None
            if number in from_file and entrance is not None and kilometres(model.location, entrance) > SAME_POINT_KM / 6:
                metres = round(kilometres(model.location, entrance) * 1000)
                preview.warnings.append(RowWarning(
                    code='CUSTOMER_POINT_DIFFERS', message=f'This point is {metres} m from the entrance saved for customer {model.customer}.',
                    template="This point is {0} m from the entrance saved for customer {1}.", values=[str(metres), model.customer]))
            key = normal(model.address)
            if model.location is not None:
                if key in places and kilometres(places[key][1], model.location) > SAME_POINT_KM:
                    preview.warnings.append(RowWarning(
                        code='SAME_ADDRESS_ELSEWHERE', message=f'The same address has another point in row {places[key][0]}.',
                        template='The same address has another point in row {0}.', values=[str(places[key][0])]))
                places.setdefault(key, (number, model.location))
        result.rows.append(preview)
    if result.errors:
        return result
    result.inserted = sum(row.action == 'INSERT' for row in result.rows)
    result.updated = sum(row.action == 'UPDATE' for row in result.rows)
    result.unchanged = len(result.rows)-result.inserted-result.updated
    if not dry_run:
        for row in result.rows:
            if row.action == 'INSERT':
                write_record(session, owner, row.data)
            elif row.action == 'UPDATE':
                # Under the company lock taken above: the version read here is the current one.
                old = existing[identifier(row.data)]
                write_record(session, owner, row.data, old.id, old.version)
        result.committed = True
    return result
