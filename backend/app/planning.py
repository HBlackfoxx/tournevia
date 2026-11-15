"""Typed planning inputs and independently checked route results."""
from datetime import date, datetime, time, timezone
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator, model_serializer

from app.fleet import DepotRecord, Location

DIMENSIONS = ('weight_kg', 'volume_dm3', 'pallet_eur_units', 'pallet_ind_units', 'loading_length_mm')
Nonnegative = Annotated[StrictInt, Field(ge=0)]
Positive = Annotated[StrictInt, Field(ge=1)]
Skill = Annotated[str, Field(pattern=r'^[A-Z][A-Z0-9_]{0,39}$')]
def unique_skills(value: list[str]) -> list[str]:
    if len(value) != len(set(value)):
        raise ValueError('Duplicate skills are not supported.')
    return value


Skills = Annotated[list[Skill], Field(max_length=30, json_schema_extra={'uniqueItems': True}), AfterValidator(unique_skills)]
PlaceCode = Annotated[str, Field(min_length=1, max_length=40)]
Objective = Literal['TRAVEL_TIME', 'MODELED_TRAVEL_COST']
Policy = Literal['TRUCK_STANDARD', 'TRUCK_PREFER_NO_TOLLS', 'TRUCK_PREFER_NO_MOTORWAYS']
PARIS = ZoneInfo('Europe/Paris')


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Capacity(Model):
    weight_kg: Nonnegative
    volume_dm3: Nonnegative
    pallet_eur_units: Nonnegative
    pallet_ind_units: Nonnegative
    loading_length_mm: Nonnegative

    def vector(self) -> list[int]:
        return [getattr(self, key) for key in DIMENSIONS]


class Truck(Model):
    height_mm: Positive = Field(le=10000)
    width_mm: Positive = Field(le=10000)
    length_mm: Positive = Field(le=50000)
    gross_weight_kg: Positive = Field(le=100000)
    axle_load_kg: Positive = Field(le=40000)
    axle_count: Annotated[StrictInt, Field(ge=2, le=12)]

    @model_validator(mode='after')
    def engine_bounds(self):
        bounds = {'height_mm': 10000, 'width_mm': 10000, 'length_mm': 50000,
                  'gross_weight_kg': 100000, 'axle_load_kg': 40000}
        if any(getattr(self, key) > limit for key, limit in bounds.items()):
            raise ValueError('Truck value exceeds selected Valhalla build bounds; clamping is not allowed.')
        return self


class Window(Model):
    start: datetime
    end: datetime

    @model_validator(mode='after')
    def finite_window(self):
        if any(t.tzinfo is None or t.microsecond for t in (self.start, self.end)):
            raise ValueError('Windows require offset-bearing timestamps with whole-second precision.')
        for timestamp in (self.start, self.end):
            local = timestamp.astimezone(PARIS)
            if timestamp.utcoffset() != local.utcoffset() or timestamp.replace(tzinfo=None) != local.replace(tzinfo=None):
                raise ValueError('Timestamp has a nonexistent Paris local time or inconsistent UTC offset.')
        if self.start.timestamp() >= self.end.timestamp():
            raise ValueError('Window start must precede end.')
        return self


class Rates(Model):
    currency: Literal['EUR']
    fixed_minor: Nonnegative
    per_km_minor: Nonnegative
    per_hour_minor: Nonnegative


class VehicleLimits(Model):
    max_stops: Annotated[StrictInt, Field(ge=1, le=250)] | None = None
    max_distance_m: Annotated[StrictInt, Field(ge=1, le=2000000)] | None = None
    max_driving_seconds: Annotated[StrictInt, Field(ge=1, le=90000)] | None = None

    @model_validator(mode='before')
    @classmethod
    def omitted_not_null(cls, data):
        if isinstance(data, dict) and any(value is None for value in data.values()):
            raise ValueError('Omit unset limits; explicit null is unsupported.')
        return data

    @model_serializer(mode='wrap')
    def omit_unset_limits(self, handler):
        return {key: value for key, value in handler(self).items() if value is not None}

    @classmethod
    def __get_pydantic_json_schema__(cls, core_schema, handler):
        schema = handler(core_schema)
        # None is an internal sentinel only; the input validator requires omission.
        for field in schema.get('properties', {}).values():
            alternatives = field.pop('anyOf', [])
            field.pop('default', None)
            for alternative in alternatives:
                if alternative.get('type') != 'null':
                    field.update(alternative)
        return schema


class VehicleBase(Model):
    fleet_number: str = Field(min_length=1, max_length=40)
    active: StrictBool
    routing: Truck
    capacity: Capacity
    skills: Skills
    cost: Rates
    limits: VehicleLimits = Field(default_factory=VehicleLimits)
    start: PlaceCode | None = Field(None, description=(
        'Code of the depot or place where the vehicle starts its day and is loaded; null: the main depot.'))
    end: PlaceCode | None = Field(None, description='Code of the depot or place where it ends its day; null: where it started.')

    @model_validator(mode='after')
    def payload_within_gross(self):
        if self.capacity.weight_kg > self.routing.gross_weight_kg:
            raise ValueError('Payload capacity cannot exceed gross routing weight.')
        return self


class VehicleInput(VehicleBase):
    """A vehicle as one calculation uses it: its window on the planning day."""
    available_window: Window

    @model_validator(mode='after')
    def same_day(self):
        if self.available_window.start.date() != self.available_window.end.date():
            raise ValueError('Overnight vehicle availability is unsupported.')
        return self


Clock = Annotated[str, Field(pattern=r'^([01][0-9]|2[0-3]):[0-5][0-9]$', description='Europe/Paris local time, HH:MM.')]
DAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')


class Hours(Model):
    start: Clock
    end: Clock

    @model_validator(mode='after')
    def ends_after_start(self):
        if self.end <= self.start:
            raise ValueError('Working hours must end after they start on the same day; overnight work is unsupported.')
        return self


class WeeklyHours(Model):
    """Regular working hours for each day of the week; null for a day the vehicle does not work."""
    monday: Hours | None = None
    tuesday: Hours | None = None
    wednesday: Hours | None = None
    thursday: Hours | None = None
    friday: Hours | None = None
    saturday: Hours | None = None
    sunday: Hours | None = None


class DayException(Model):
    date: date
    hours: Hours | None = Field(None, description='Hours on that date instead of the weekly ones; null for a day off.')


def local_instant(day: date, clock: str, latest: bool) -> datetime:
    """A Paris local time on `day` as an existing instant. Around a clock change, the start of work
    takes the later and the end the earlier possible instant, so a vehicle never works longer than declared."""
    # Through UTC: converting to the same zone directly would keep a nonexistent wall time as is.
    moments = [datetime.combine(day, time.fromisoformat(clock), PARIS).replace(fold=f).astimezone(timezone.utc).astimezone(PARIS)
               for f in (0, 1)]
    chosen = (max if latest else min)(moments, key=lambda m: m.timestamp())
    # A fixed offset, as every other stored timestamp.
    return chosen.replace(tzinfo=timezone(chosen.utcoffset()), fold=0)


class VehicleSource(VehicleBase):
    """A saved vehicle: weekly working hours and dated exceptions, not one dated window."""
    hours: WeeklyHours
    exceptions: list[DayException] = Field(default_factory=list, max_length=100, description=(
        'Days off and days with other hours, at most one per date; they replace the weekly hours on that date.'))

    @model_validator(mode='after')
    def one_exception_per_date(self):
        if len({e.date for e in self.exceptions}) != len(self.exceptions):
            raise ValueError('Each date can have only one exception.')
        self.exceptions.sort(key=lambda e: e.date)
        return self

    @classmethod
    def weekly(cls, vehicle: VehicleInput, days: tuple[str, ...] = DAYS) -> 'VehicleSource':
        """A saved vehicle working, on each of `days`, the local hours of a calculation's window."""
        start, end = (t.astimezone(PARIS).strftime('%H:%M') for t in (vehicle.available_window.start, vehicle.available_window.end))
        return cls(**vehicle.model_dump(exclude={'available_window'}),
                   hours=WeeklyHours(**{day: Hours(start=start, end=end) for day in days}))

    def hours_on(self, day: date) -> Hours | None:
        """The working hours on `day`: its exception, or else the weekly hours for that weekday."""
        for exception in self.exceptions:
            if exception.date == day:
                return exception.hours
        return getattr(self.hours, DAYS[day.weekday()])

    def on(self, day: date) -> VehicleInput | None:
        """The vehicle as a calculation on `day` uses it, or None when it does not work that day."""
        hours = self.hours_on(day)
        if hours is None:
            return None
        window = Window(start=local_instant(day, hours.start, True), end=local_instant(day, hours.end, False))
        return VehicleInput(**self.model_dump(exclude={'hours', 'exceptions'}), available_window=window)


class OrderInput(Model):
    external_id: str = Field(min_length=1, max_length=100)
    service_date: date
    active: StrictBool
    address: str = Field(min_length=1, max_length=500)
    location: Location | None
    time_window: Window
    service_seconds: Annotated[StrictInt, Field(ge=0, le=28800)]
    demand: Capacity
    required_skills: Skills
    instructions: str = Field(max_length=1000)
    priority: Annotated[StrictInt, Field(ge=0, le=100)] = 0
    customer: str | None = Field(None, min_length=1, max_length=40, description=(
        'Code of the saved customer this delivery is for; a delivery at its reviewed entrance needs no new review.'))
    ships_from: PlaceCode | None = Field(None, description=(
        'Code of the depot this delivery is loaded at: only vehicles starting there carry it. Null: any vehicle.'))

    @model_validator(mode='after')
    def planning_date(self):
        if any(t.date() != self.service_date for t in (self.time_window.start, self.time_window.end)):
            raise ValueError('Order windows must be within its Paris service date.')
        return self


class CustomerInput(Model):
    """A saved customer: its reviewed vehicle entrance and what its deliveries usually need."""
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    active: StrictBool = True
    address: str = Field(min_length=1, max_length=500)
    location: Location | None = None
    usual_hours: Hours | None = Field(None, description='Usual delivery window, Europe/Paris local time.')
    service_seconds: Annotated[StrictInt, Field(ge=0, le=28800)] | None = None
    required_skills: Skills = Field(default_factory=list)
    instructions: str = Field('', max_length=1000)


T = TypeVar('T')


class Owned(Model, Generic[T]):
    id: UUID
    organization_id: UUID
    version: Positive
    example: StrictBool = Field(False, description=(
        'An example: fictional, planned only with other examples, and removed with them.'))
    data: T


class PlanningInput(Model):
    organization_id: UUID
    # Server-controlled workspace origin. ROUTED still needs actual road calculation,
    # independent validation and explicit finalization before dispatch.
    data_kind: Literal['SYNTHETIC', 'ROUTED']
    service_date: date
    # The main depot, and the other depots and places the selected vehicles and deliveries name.
    depot: DepotRecord
    depots: list[DepotRecord] = Field(default_factory=list, max_length=60)
    vehicles: list[Owned[VehicleInput]] = Field(min_length=1, max_length=25)
    orders: list[Owned[OrderInput]] = Field(min_length=1, max_length=250)
    routing_policy: Policy
    objective: Objective = 'TRAVEL_TIME'
    solver_time_limit_seconds: Annotated[StrictInt, Field(ge=1, le=60)]
    # The company's safety margin when calculated: every delivery is planned to start at least this long
    # before its window ends, never earlier than the window opens. Validation still checks the real
    # windows, so a schedule that cannot keep the margin stays valid. Plans made before it existed: 0.
    safety_margin_seconds: Annotated[StrictInt, Field(ge=0, le=7200)] = 0

    @property
    def epoch(self) -> int:
        return int(datetime.combine(self.service_date, time.min, PARIS).timestamp())

    def seconds(self, timestamp: datetime) -> int:
        return int(timestamp.timestamp()) - self.epoch

    def closes(self, order: 'OrderInput') -> int:
        """When a delivery's service must start by in the plan: its window end less the safety margin,
        never before the window opens."""
        start, end = self.seconds(order.time_window.start), self.seconds(order.time_window.end)
        return max(start, end - self.safety_margin_seconds)

    def place(self, code: str | None) -> DepotRecord:
        """The depot or place with this code; no code is the main depot."""
        if code is None:
            return self.depot
        for row in (self.depot, *self.depots):
            if row.data.code == code:
                return row
        raise KeyError(code)

    def start_of(self, vehicle: 'VehicleBase') -> DepotRecord:
        return self.place(vehicle.start)

    def end_of(self, vehicle: 'VehicleBase') -> DepotRecord:
        return self.place(vehicle.end or vehicle.start)

    @property
    def uses_main_depot(self) -> bool:
        """Whether a selected vehicle starts or ends its day at the main depot. Only then does the main
        depot's point take part in the calculation, its review and its distance limit."""
        return any(self.depot.id in (self.start_of(row.data).id, self.end_of(row.data).id) for row in self.vehicles)

    def carries(self, vehicle: 'VehicleBase', order: 'OrderInput') -> bool:
        """Whether this vehicle may carry this delivery: it starts at the depot the delivery ships from."""
        return order.ships_from is None or self.start_of(vehicle).data.code == order.ships_from

    def route_ends(self) -> list[Location]:
        """Distinct points where routes start or end: the main depot first, then in vehicle order."""
        ends = [self.depot.data.location]
        for row in self.vehicles:
            for place in (self.start_of(row.data), self.end_of(row.data)):
                point = place.data.location
                if all((point.latitude, point.longitude) != (p.latitude, p.longitude) for p in ends):
                    ends.append(point)
        return ends

    @property
    def example(self) -> bool:
        """A calculation of example vehicles and deliveries, which are never planned with a company's own."""
        return any(row.example for row in [*self.vehicles, *self.orders])

    @model_validator(mode='after')
    def selected_inputs(self):
        if len({row.example for row in [*self.vehicles, *self.orders]}) > 1:
            raise ValueError('Example vehicles and deliveries are planned only with other examples.')
        if not self.example and any(row.example for row in self.depots):
            raise ValueError('Only example vehicles and deliveries use an example depot.')
        places = [self.depot, *self.depots]
        if any(row.organization_id != self.organization_id for row in places):
            raise ValueError('All input records must belong to the selected organization.')
        if len({row.data.code for row in places}) != len(places):
            raise ValueError('Depot and place codes must be unique.')
        known = {row.data.code: row.data.kind for row in places}
        for row in self.vehicles:
            if any(code is not None and code not in known for code in (row.data.start, row.data.end)):
                raise ValueError('Every vehicle start and end needs its depot or place.')
        for row in self.orders:
            if row.data.ships_from is not None and known.get(row.data.ships_from) != 'DEPOT':
                raise ValueError('A delivery ships from one of the selected depots.')
        rows = [*places, *self.vehicles, *self.orders]
        if any(row.organization_id != self.organization_id for row in rows):
            raise ValueError('All input records must belong to the selected organization.')
        if len({row.id for row in rows}) != len(rows):
            raise ValueError('Selected record IDs must be unique.')
        if len({row.data.fleet_number for row in self.vehicles}) != len(self.vehicles):
            raise ValueError('Duplicate fleet number.')
        if len({row.data.external_id for row in self.orders}) != len(self.orders):
            raise ValueError('Duplicate order identifier.')
        used = [place for row in self.vehicles for place in (self.start_of(row.data), self.end_of(row.data))]
        locations = [*(row.data.location for row in used), *[row.data.location for row in self.orders]]
        if any(p is None or not p.reviewed for p in locations):
            raise ValueError('All selected coordinates need explicit review.')
        for row in [*self.vehicles, *self.orders]:
            if not row.data.active:
                raise ValueError('Inactive records cannot be selected.')
            window = row.data.available_window if isinstance(row.data, VehicleInput) else row.data.time_window
            if any(t.astimezone(PARIS).date() != self.service_date for t in (window.start, window.end)):
                raise ValueError('All windows must be inside the selected Europe/Paris day.')
            skills = row.data.skills if isinstance(row.data, VehicleInput) else row.data.required_skills
            if len(set(skills)) != len(skills):
                raise ValueError('Duplicate skills are not supported.')
        if any(row.data.service_date != self.service_date for row in self.orders):
            raise ValueError('Order date does not match planning date.')
        return self


class SyntheticInput(PlanningInput):
    """The CLI cannot establish operational provenance."""
    data_kind:Literal['SYNTHETIC']


class Leg(Model):
    from_order_id: UUID | None
    to_order_id: UUID | None
    duration_seconds: Nonnegative
    distance_m: Nonnegative
    geometry: list[tuple[float, float]] = Field(min_length=2)


class Stop(Model):
    order_id: UUID
    arrival: Nonnegative
    service_start: Nonnegative
    departure: Nonnegative
    load_after: list[Nonnegative]


class Route(Model):
    vehicle_id: UUID
    profile: Truck
    departure: Nonnegative
    end: Nonnegative
    stops: list[Stop]
    legs: list[Leg]
    distance_m: Nonnegative
    driving_seconds: Nonnegative
    waiting_seconds: Nonnegative
    service_seconds: Nonnegative
    fixed_minor: Nonnegative
    distance_minor: Nonnegative
    time_minor: Nonnegative
    known_cost_minor: Nonnegative


class Unassigned(Model):
    order_id: UUID
    reason: Literal['NO_COMPATIBLE_VEHICLE', 'NO_ROAD_NEAR_PIN', 'NO_ROUTE_IN_DATASET', 'SOLVER_UNASSIGNED', 'MANUALLY_UNASSIGNED']


class Draft(Model):
    model_config=ConfigDict(json_schema_extra={'allOf':[
        {'if':{'properties':{'status':{'const':'READY'}}},'then':{'properties':{
            'data_kind':{'const':'ROUTED'},'validation':{'const':'PASS'},'blocking_issues':{'maxItems':0},
            'operational_ready':{'const':True},'routes':{'contains':{'properties':{'stops':{'minItems':1}}}}}},
         'else':{'properties':{'operational_ready':{'const':False}}}}
    ]})
    status: Literal['DRAFT', 'READY'] = 'DRAFT'
    data_kind: Literal['SYNTHETIC', 'ROUTED'] = 'SYNTHETIC'
    organization_id: UUID
    objective: Objective
    routing_policy: Policy
    dataset: str
    routes: list[Route]
    unassigned: list[Unassigned]
    validation: Literal['PASS', 'FAIL']
    blocking_issues: list[str]
    toll_cost_minor: None = None
    total_cost_minor: None = None
    operational_ready: StrictBool = False

    @model_validator(mode='after')
    def readiness(self):
        if self.status=='READY':
            if self.data_kind!='ROUTED' or self.validation!='PASS' or self.blocking_issues or not any(r.stops for r in self.routes) or not self.operational_ready:
                raise ValueError('Only validated, served, routed work can be ready.')
        elif self.operational_ready:
            raise ValueError('A draft cannot be operationally ready.')
        return self


class SyntheticDraft(Draft):
    """Strict synthetic CLI output; shared result types do not widen this contract."""
    status:Literal['DRAFT']='DRAFT'
    data_kind:Literal['SYNTHETIC']='SYNTHETIC'
    operational_ready:Literal[False]=False
