"""Owned jobs, request replay and one mutable draft per explicit optimization."""
from datetime import date, datetime, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AfterValidator, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db import Base
from app.fleet import read_depot, read_depots
from app.planning import Draft, Model, Objective, Owned, PlanningInput, Policy, Positive, VehicleInput
from app.records import DataError, Order, Vehicle, find_row, lock_owner, record
from app.accounts import Organization
from app.routing import EngineError, coordinates, meters
from app.spike import points_and_profiles

ACTIVE_STAGES = ('PRECHECK','MATRIX','SOLVE','ROUTE','VALIDATE','SAVE')
# Session advisory lock held by the single active worker on its guard connection.
WORKER_LOCK_KEY = 74682041
Status = Literal['QUEUED','RUNNING','SUCCEEDED','FAILED']
Stage = Literal['QUEUED','PRECHECK','MATRIX','SOLVE','ROUTE','VALIDATE','SAVE','DONE','FAILED']


def unique_ids(ids: list[UUID]) -> list[UUID]:
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate selected IDs are not supported.')
    return sorted(ids, key=str)


class OptimizationRequest(Model):
    service_date: date
    vehicle_ids: Annotated[list[UUID], Field(min_length=1,max_length=25,json_schema_extra={'uniqueItems':True}),AfterValidator(unique_ids)]
    order_ids: Annotated[list[UUID], Field(min_length=1,max_length=250,json_schema_extra={'uniqueItems':True}),AfterValidator(unique_ids)]
    routing_policy: Policy
    solver_time_limit_seconds: Positive = Field(le=60)
    objective: Objective = 'TRAVEL_TIME'
    # Optional for API callers taking a fresh snapshot; UI always supplies all versions.
    source_versions: dict[UUID, Positive] | None = None


class JobError(Model):
    code: str
    message: str
    request_id: UUID
    details: list[dict] = Field(default_factory=list)


class JobRecord(Model):
    model_config=ConfigDict(json_schema_extra={'allOf':[
        {'if':{'properties':{'status':{'const':status}}},'then':{'properties':props}}
        for status,props in {
            'QUEUED':{'stage':{'const':'QUEUED'},**{k:{'type':'null'} for k in ('started_at','finished_at','plan_id','error')}},
            'RUNNING':{'stage':{'enum':list(ACTIVE_STAGES)},'started_at':{'type':'string'},**{k:{'type':'null'} for k in ('finished_at','plan_id','error')}},
            'SUCCEEDED':{'stage':{'const':'DONE'},'error':{'type':'null'},**{k:{'type':'string'} for k in ('started_at','finished_at','plan_id')}},
            'FAILED':{'stage':{'const':'FAILED'},'finished_at':{'type':'string'},'plan_id':{'type':'null'},'error':{'type':'object'}}
        }.items()]})
    id: UUID
    organization_id: UUID
    kind: Literal['OPTIMIZE','EDIT']
    target_plan_id: UUID | None = None
    reserved_version: Positive | None = None
    status: Status
    stage: Stage
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    plan_id: UUID | None
    error: JobError | None

    @model_validator(mode='after')
    def consistent_state(self):
        if self.status == 'QUEUED':
            valid = self.stage=='QUEUED' and self.started_at is None and self.finished_at is None and self.plan_id is None and self.error is None
        elif self.status == 'RUNNING':
            valid = self.stage in ACTIVE_STAGES and self.started_at is not None and self.finished_at is None and self.plan_id is None and self.error is None
        elif self.status == 'SUCCEEDED':
            valid = self.stage=='DONE' and self.started_at is not None and self.finished_at is not None and self.plan_id is not None and self.error is None
        else:
            valid = self.stage=='FAILED' and self.finished_at is not None and self.plan_id is None and self.error is not None
        if not valid:
            raise ValueError('Inconsistent job state.')
        return self


class PlanSummary(Model):
    id: UUID
    organization_id: UUID
    version: Positive
    service_date: date
    created_at: datetime
    updated_at: datetime
    status: Literal['DRAFT','READY'] = 'DRAFT'
    validation: Literal['PENDING','PASS','FAIL']
    result_stale: bool
    active_job_id: UUID | None
    finalized_at: datetime | None


class PlanListItem(PlanSummary):
    """A saved plan in a list, with totals computed from its stored result."""
    served_deliveries: int
    unassigned_deliveries: int
    vehicles_used: int
    distance_m: int
    known_cost_minor: int


class PlanRecord(PlanSummary):
    input_data: PlanningInput
    result: Draft
    edit_error: JobError | None
    # False once the depot or a selected vehicle/delivery changed after this calculation;
    # Ready and export then refuse with SOURCE_CHANGED until a fresh calculation.
    sources_current: bool


class Plan(Base):
    __tablename__ = 'plans'
    __table_args__ = (UniqueConstraint('id','organization_id',name='uq_plans_owner'),
                      CheckConstraint('version >= 1',name='ck_plans_version'),
                      CheckConstraint("status IN ('DRAFT','READY')",name='ck_plans_status'),
                      CheckConstraint("(validation = 'PENDING' AND active_job_id IS NOT NULL AND result_stale) OR (validation IN ('PASS','FAIL') AND active_job_id IS NULL)",name='ck_plans_edit_state'),
                      CheckConstraint("(status = 'DRAFT' AND finalized_at IS NULL) OR (status = 'READY' AND finalized_at IS NOT NULL AND validation = 'PASS' AND NOT result_stale)",name='ck_plans_ready'),
                      Index('ix_plans_day','organization_id','service_date','created_at','id'))
    id: Mapped[UUID] = mapped_column(primary_key=True,default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey('organizations.id',ondelete='RESTRICT'))
    version: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(16),default='DRAFT')
    service_date: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    input_data: Mapped[dict] = mapped_column(JSONB)
    result: Mapped[dict] = mapped_column(JSONB)
    validation: Mapped[str] = mapped_column(String(16))
    result_stale: Mapped[bool] = mapped_column(Boolean,default=False)
    active_job_id: Mapped[UUID | None]
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    edit_error: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))


class Job(Base):
    __tablename__ = 'planning_jobs'
    __table_args__ = (
        UniqueConstraint('organization_id','submitted_by','operation_scope','idempotency_key',name='uq_jobs_replay'),
        ForeignKeyConstraint(['plan_id','organization_id'],['plans.id','plans.organization_id'],ondelete='RESTRICT',name='fk_jobs_plan_owner'),
        ForeignKeyConstraint(['target_plan_id','organization_id'],['plans.id','plans.organization_id'],ondelete='RESTRICT',name='fk_jobs_target_owner'),
        CheckConstraint("(kind = 'OPTIMIZE' AND target_plan_id IS NULL AND reserved_version IS NULL AND operation_scope = 'OPTIMIZE') OR (kind = 'EDIT' AND target_plan_id IS NOT NULL AND reserved_version >= 1)",name='ck_jobs_kind'),
        CheckConstraint("(kind = 'OPTIMIZE' AND ((status = 'SUCCEEDED' AND input_data IS NULL) OR (status <> 'SUCCEEDED' AND input_data IS NOT NULL))) OR (kind = 'EDIT' AND input_data IS NULL)",name='ck_jobs_input'),
        CheckConstraint("(status = 'QUEUED' AND stage = 'QUEUED' AND started_at IS NULL AND finished_at IS NULL AND plan_id IS NULL AND error IS NULL) OR "
                        "(status = 'RUNNING' AND stage IN ('PRECHECK','MATRIX','SOLVE','ROUTE','VALIDATE','SAVE') AND started_at IS NOT NULL AND finished_at IS NULL AND plan_id IS NULL AND error IS NULL) OR "
                        "(status = 'SUCCEEDED' AND stage = 'DONE' AND started_at IS NOT NULL AND finished_at IS NOT NULL AND plan_id IS NOT NULL AND error IS NULL AND input_data IS NULL) OR "
                        "(status = 'FAILED' AND stage = 'FAILED' AND finished_at IS NOT NULL AND plan_id IS NULL AND error IS NOT NULL)",name='ck_jobs_state'),
        Index('ix_jobs_queue','status','created_at','id'),
        Index('ix_jobs_owner_day','organization_id','service_date','created_at','id'))
    id: Mapped[UUID] = mapped_column(primary_key=True,default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey('organizations.id',ondelete='RESTRICT'))
    submitted_by: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(16),default='OPTIMIZE')
    operation_scope: Mapped[str] = mapped_column(String(50),default='OPTIMIZE')
    target_plan_id: Mapped[UUID | None]
    reserved_version: Mapped[int | None]
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_data: Mapped[dict] = mapped_column(JSONB)
    input_data: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    service_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16))
    stage: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    plan_id: Mapped[UUID | None]
    error: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))


def now() -> datetime:
    return datetime.now(timezone.utc)


def job_record(row: Job) -> JobRecord:
    return JobRecord.model_validate({key:getattr(row,key) for key in JobRecord.model_fields})


def plan_record(row: Plan, *, sources_current: bool) -> PlanRecord:
    return PlanRecord.model_validate({**{key:getattr(row,key) for key in PlanSummary.model_fields},
                                      'input_data':row.input_data,'result':row.result,'edit_error':row.edit_error,
                                      'sources_current':sources_current})


AREA_TOO_WIDE_MESSAGE=('The depot and the selected deliveries are farther apart than this installation\'s routing allows '
                       'in one calculation. Select deliveries closer together, or ask your installation operator about the distance limit.')


def farthest_apart(inputs: PlanningInput, limit_km: int) -> list[str]:
    """The two farthest-apart places (depot codes or delivery identifiers) when they exceed the router's limit.

    Only the depots and places a selected vehicle starts or ends at count: the main depot too is left
    out when no selected vehicle uses it, as the calculation leaves it out of every matrix."""
    used={p.id for v in inputs.vehicles for p in (inputs.start_of(v.data),inputs.end_of(v.data))}
    ends=[('depot' if row.id==inputs.depot.id else row.data.code,row.data.location)
          for row in (inputs.depot,*inputs.depots) if row.id in used]
    places=[*ends,*((o.data.external_id,o.data.location) for o in inputs.orders)]
    worst=max(((meters(coordinates(a),coordinates(b)),first,second) for i,(first,a) in enumerate(places)
               for second,b in places[i+1:]),default=(0,'',''))
    return [worst[1],worst[2]] if worst[0]>limit_km*1000 else []


EXAMPLES_MIXED_MESSAGE=('Example vehicles and deliveries are planned only with other examples. '
                        'Select only examples, or only your own records.')


def submit(session: Session, owner: UUID, request: OptimizationRequest, key: str, *, actor: str = 'local-demo',
           max_km: int = 200) -> JobRecord:
    validate_key(key)
    lock_owner(session,owner)
    organization = session.get(Organization, owner)
    normalized=request.model_dump(mode='json')
    prior=find_replay(session,owner,'OPTIMIZE',key,normalized,actor)
    if prior:return prior
    require_queue_capacity(session,owner)
    depot=read_depot(session,owner)
    if depot is None:
        raise DataError(404,'NOT_FOUND','Depot not provisioned.')
    sources=[record(find_row(session,owner,Vehicle,k)) for k in request.vehicle_ids]
    orders=[record(find_row(session,owner,Order,k)) for k in request.order_ids]
    if request.source_versions is not None:
        versions={r.id:r.version for r in [depot,*sources,*orders]}
        if request.source_versions!=versions:
            raise DataError(412,'SOURCE_CHANGED','Selected source versions changed. Reload records and select again.')
    # The main depot's point matters only when a selected vehicle starts or ends there.
    main_used=any(depot.data.code in (v.data.start or depot.data.code,v.data.end or v.data.start or depot.data.code)
                  for v in sources)
    if any(r.data.location is None or not r.data.location.reviewed for r in [*([depot] if main_used else []),*orders]):
        raise DataError(422,'LOCATION_NOT_REVIEWED','Every selected delivery and the depot require reviewed coordinates.')
    if len({row.example for row in [*sources,*orders]})>1:
        raise DataError(422,'EXAMPLES_MIXED',EXAMPLES_MIXED_MESSAGE)
    # The other depots and places the selected vehicles start or end at, or the deliveries ship from.
    places={row.data.code:row for row in read_depots(session,owner) if not row.main}
    named=[code for v in sources for code in (v.data.start,v.data.end) if code]+[o.data.ships_from for o in orders if o.data.ships_from]
    if unknown:=sorted({code for code in named if code!=depot.data.code and code not in places}):
        raise DataError(422,'PLACE_UNKNOWN','No depot or place has this code.',unknown)
    used=[places[code] for code in dict.fromkeys(named) if code!=depot.data.code]
    starts=[code for v in sources for code in (v.data.start,v.data.end) if code and code!=depot.data.code]
    if unreviewed:=[row.data.code for row in used if row.data.code in starts and not row.data.location.reviewed]:
        raise DataError(422,'PLACE_NOT_REVIEWED','Review the entrance of every depot and place the selected vehicles start or end at '
                        'before calculating.',unreviewed)
    off_day=[v.data.fleet_number for v in sources if v.data.hours_on(request.service_date) is None]
    if off_day:
        raise DataError(422,'VEHICLE_OFF_DAY','Some selected vehicles do not work on the planning day. Check their weekly hours and days off in Fleet.',off_day)
    # The calculation copies each vehicle as it is that day: its hours become a dated window.
    vehicles=[Owned[VehicleInput](id=v.id,organization_id=v.organization_id,version=v.version,example=v.example,
                                  data=v.data.on(request.service_date)) for v in sources]
    try:
        inputs=PlanningInput(organization_id=owner,data_kind='SYNTHETIC' if organization.is_demo else 'ROUTED',service_date=request.service_date,
                             depot=depot,depots=used,vehicles=vehicles,orders=orders,routing_policy=request.routing_policy,
                             objective=request.objective,solver_time_limit_seconds=request.solver_time_limit_seconds,
                             safety_margin_seconds=organization.safety_margin_minutes*60)
        points_and_profiles(inputs)
    except ValidationError:
        raise DataError(422,'INVALID_SELECTION','Select active records with windows and delivery dates inside the planning day.') from None
    except EngineError as error:
        raise DataError(422,'MODEL_LIMIT',str(error)) from None
    # The router refuses a matrix whose points are farther apart; say so before queueing.
    if far:=farthest_apart(inputs,max_km):
        raise DataError(422,'AREA_TOO_WIDE',AREA_TOO_WIDE_MESSAGE,far)
    row=Job(organization_id=owner,submitted_by=actor,kind='OPTIMIZE',idempotency_key=key,
            request_data=normalized,input_data=inputs.model_dump(mode='json'),service_date=request.service_date,
            status='QUEUED',stage='QUEUED',created_at=now(),started_at=None,finished_at=None,plan_id=None,error=None)
    session.add(row);session.flush()
    return job_record(row)


# One worker runs calculations in order; each company may keep only a few waiting so a
# loop of submissions cannot starve everyone else's planning for hours.
MAX_ACTIVE_JOBS=5


def require_queue_capacity(session:Session,owner:UUID) -> None:
    """Call while holding the owner lock, so concurrent submissions count each other."""
    active=session.scalar(select(func.count()).select_from(Job).where(Job.organization_id==owner,Job.status.in_(('QUEUED','RUNNING'))))
    if active>=MAX_ACTIVE_JOBS:
        raise DataError(429,'QUEUE_FULL','This company already has the maximum of five calculations waiting or running. '
                        'Wait for one to finish or cancel one, then submit again.')


def validate_key(key:str) -> None:
    if not 1 <= len(key) <= 128 or any(ord(c)<33 or ord(c)>126 for c in key):
        raise DataError(400,'INVALID_IDEMPOTENCY_KEY','Use a 1–128 character printable ASCII idempotency key without spaces.')


def find_replay(session:Session,owner:UUID,scope:str,key:str,normalized:dict,actor:str='local-demo') -> JobRecord | None:
    # Actor and organization come from the server-resolved identity, never request fields.
    prior=session.scalar(select(Job).where(Job.organization_id==owner,Job.submitted_by==actor,Job.operation_scope==scope,Job.idempotency_key==key))
    if prior:
        if prior.request_data!=normalized:
            raise DataError(409,'IDEMPOTENCY_CONFLICT','This key was already used for a different request.')
        return job_record(prior)
    return None


def fail_job(row: Job, code: str, message: str):
    row.status,row.stage,row.finished_at='FAILED','FAILED',now()
    row.error=JobError(code=code,message=message,request_id=row.id).model_dump(mode='json')


def save_result(session: Session, job_id: UUID, result: Draft) -> UUID:
    row=session.scalar(select(Job).where(Job.id==job_id).with_for_update())
    if row is None or row.status!='RUNNING':
        raise DataError(409,'JOB_NOT_RUNNING','Late result rejected: job is no longer running.')
    plan=None
    if row.kind=='EDIT':
        plan=session.scalar(select(Plan).where(Plan.id==row.target_plan_id,Plan.organization_id==row.organization_id).with_for_update())
        if plan is None or plan.version!=row.reserved_version or plan.active_job_id!=row.id:
            raise DataError(409,'PLAN_CHANGED','Late edit result rejected: the reserved plan version changed.')
    inputs=PlanningInput.model_validate(plan.input_data if plan else row.input_data)
    if (inputs.organization_id!=row.organization_id or result.organization_id!=row.organization_id or inputs.service_date!=row.service_date
            or result.objective!=inputs.objective or result.routing_policy!=inputs.routing_policy or result.data_kind!=inputs.data_kind
            or result.status!='DRAFT' or result.operational_ready):
        raise DataError(409,'RESULT_CONTEXT','Result and owned input snapshot disagree.')
    known_vehicles={v.id:v.data for v in inputs.vehicles}
    coverage=[s.order_id for r in result.routes for s in r.stops]+[u.order_id for u in result.unassigned]
    if (len(coverage)!=len(set(coverage)) or set(coverage)!={o.id for o in inputs.orders}
            or len({r.vehicle_id for r in result.routes})!=len(result.routes)
            or any(r.vehicle_id not in known_vehicles or r.profile!=known_vehicles[r.vehicle_id].routing for r in result.routes)):
        raise DataError(409,'RESULT_CONTEXT','Result IDs or truck profiles differ from the saved input.')
    if (result.validation=='PASS') != (not result.blocking_issues):
        raise DataError(409,'RESULT_CONTEXT','Result validation flag and blocking issues disagree.')
    if row.kind=='EDIT':
        from app.edits import EditRequest
        expected=EditRequest.model_validate(row.request_data['edit'])
        actual={r.vehicle_id:[s.order_id for s in r.stops] for r in result.routes}
        if actual!={r.vehicle_id:r.order_ids for r in expected.routes} or {u.order_id for u in result.unassigned}!=set(expected.unassigned_order_ids):
            raise DataError(409,'RESULT_CONTEXT','Edited result changed the requested assignment or order.')
    stamp=now()
    if plan is None:
        plan=Plan(organization_id=row.organization_id,version=1,status='DRAFT',service_date=row.service_date,
                  input_data=row.input_data,result=result.model_dump(mode='json'),created_at=stamp,updated_at=stamp,
                  validation=result.validation,result_stale=False,active_job_id=None,finalized_at=None,edit_error=None)
        session.add(plan)
    else:
        plan.version+=1;plan.result=result.model_dump(mode='json');plan.updated_at=stamp
        plan.validation=result.validation;plan.result_stale=False;plan.active_job_id=None;plan.edit_error=None
    session.flush()
    row.plan_id=plan.id;row.input_data=None
    row.status,row.stage,row.finished_at='SUCCEEDED','DONE',stamp
    session.flush()
    return plan.id
