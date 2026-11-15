"""Explicit assignments, same-plan reservations and guarded dispatch output."""
import csv
from datetime import date, datetime
import io
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import AfterValidator, Field, StrictBool, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts import Organization
from app.fleet import Depot, DepotRecord, read_depot
from app.jobs import Job, JobRecord, Plan, PlanRecord, fail_job, find_replay, job_record, now, plan_record, require_queue_capacity, validate_key
from app.planning import DIMENSIONS, PARIS, Capacity, Draft, Model, PlanningInput, Window
from app.records import DataError, Order, Vehicle, lock_owner
from app.routing import Deadline, Valhalla
from app.spike import build_routes, points_and_profiles, require_routable_assignments
from app.validation import validate


def no_duplicates(value:list[UUID]) -> list[UUID]:
    if len(value)!=len(set(value)):raise ValueError('Duplicate order IDs are not allowed.')
    return value


IDs=Annotated[list[UUID],Field(max_length=250,json_schema_extra={'uniqueItems':True}),AfterValidator(no_duplicates)]


class RouteAssignment(Model):
    vehicle_id:UUID
    order_ids:IDs=Field(min_length=1)


class EditRequest(Model):
    routes:list[RouteAssignment]=Field(max_length=25)
    unassigned_order_ids:IDs

    @model_validator(mode='after')
    def normalize(self):
        vehicles=[r.vehicle_id for r in self.routes]
        ids=[k for r in self.routes for k in r.order_ids]+self.unassigned_order_ids
        if len(vehicles)!=len(set(vehicles)) or len(ids)!=len(set(ids)):
            raise ValueError('Each vehicle and delivery can occur only once.')
        self.routes.sort(key=lambda r:str(r.vehicle_id))
        self.unassigned_order_ids.sort(key=str)
        return self


class FinalizeRequest(Model):
    accept_unassigned:StrictBool=False
    # Another READY plan for the same day that dispatches any of the same vehicles or
    # deliveries is refused unless the dispatcher explicitly replaces it (it returns to DRAFT).
    replace_conflicting_ready:StrictBool=False


def check_assignment(inputs:PlanningInput,edit:EditRequest) -> None:
    ids=[k for r in edit.routes for k in r.order_ids]+edit.unassigned_order_ids
    if set(ids)!={o.id for o in inputs.orders} or any(r.vehicle_id not in {v.id for v in inputs.vehicles} for r in edit.routes):
        raise DataError(422,'INVALID_ASSIGNMENT','Assign every plan delivery exactly once to a selected vehicle or the unassigned list.')


DEPOT_CHANGED_MESSAGE='Depot changed. Start a fresh calculation from current sources.'
SOURCES_CHANGED_MESSAGE='Selected source records changed. Start a fresh calculation from current sources.'


def source_change(session:Session,owner:UUID,inputs:PlanningInput) -> str|None:
    """Why the plan's saved inputs no longer match current sources, or None if they still do.

    The main depot counts only when one of the plan's vehicles starts or ends there; a plan whose
    vehicles all use other depots is not changed by editing the main one."""
    if inputs.uses_main_depot:
        depot=read_depot(session,owner)
        if depot is None or depot.id!=inputs.depot.id or depot.version!=inputs.depot.version:
            return DEPOT_CHANGED_MESSAGE
    places={row.id:row.version for row in inputs.depots}
    if places and dict(session.execute(select(Depot.id,Depot.version).where(Depot.organization_id==owner,Depot.id.in_(places))).all())!=places:
        return DEPOT_CHANGED_MESSAGE
    for table,rows in [(Vehicle,inputs.vehicles),(Order,inputs.orders)]:
        saved={row.id:row.version for row in rows}
        if saved and dict(session.execute(select(table.id,table.version).where(table.organization_id==owner,table.id.in_(saved))).all())!=saved:
            return SOURCES_CHANGED_MESSAGE
    return None


def current_sources(session:Session,owner:UUID,inputs:PlanningInput) -> None:
    if (problem:=source_change(session,owner,inputs)) is not None:
        raise DataError(412,'SOURCE_CHANGED',problem)


def owned_plan(session:Session,owner:UUID,key:UUID) -> Plan:
    plan=session.scalar(select(Plan).where(Plan.id==key,Plan.organization_id==owner).with_for_update())
    if plan is None:raise DataError(404,'NOT_FOUND','Plan not found in this organization.')
    return plan


def submit_edit(session:Session,owner:UUID,plan_id:UUID,edit:EditRequest,version:int,key:str,*,actor:str='local-demo') -> JobRecord:
    validate_key(key);lock_owner(session,owner)
    normalized={'plan_id':str(plan_id),'expected_version':version,'edit':edit.model_dump(mode='json')}
    prior=find_replay(session,owner,f'EDIT:{plan_id}',key,normalized,actor)
    if prior:return prior
    require_queue_capacity(session,owner)
    plan=owned_plan(session,owner,plan_id)
    if plan.active_job_id is not None:raise DataError(409,'PLAN_BUSY','This plan already has an active edit. Wait for its result.')
    if plan.version!=version:raise DataError(412,'PLAN_CHANGED','Plan changed. Reload its current version before editing.')
    inputs=PlanningInput.model_validate(plan.input_data)
    current_sources(session,owner,inputs);check_assignment(inputs,edit)
    stamp=now();job_id=uuid4()
    plan.version+=1;plan.status='DRAFT';plan.finalized_at=None;plan.validation='PENDING'
    plan.active_job_id=job_id;plan.result_stale=True;plan.updated_at=stamp;plan.edit_error=None
    plan.result={**plan.result,'status':'DRAFT','operational_ready':False}
    job=Job(id=job_id,organization_id=owner,submitted_by=actor,kind='EDIT',operation_scope=f'EDIT:{plan_id}',
        idempotency_key=key,request_data=normalized,input_data=None,service_date=plan.service_date,status='QUEUED',stage='QUEUED',
        created_at=stamp,started_at=None,finished_at=None,plan_id=None,error=None,target_plan_id=plan_id,reserved_version=plan.version)
    session.add(job);session.flush()
    return job_record(job)


def reroute(inputs:PlanningInput,edit:EditRequest,engine:Valhalla,*,deadline:Deadline,on_stage=lambda stage:None) -> Draft:
    on_stage('PRECHECK');check_assignment(inputs,edit)
    points,profiles=points_and_profiles(inputs)
    # The sequence is already fixed. Request its actual road legs directly;
    # an optimizer matrix adds no evidence to a manual assignment. Invalid
    # schedules still return fixable drafts with independent validation issues.
    require_routable_assignments(inputs,engine,[(r.vehicle_id,r.order_ids) for r in edit.routes],deadline)
    # No proposed departure: route construction arrives as the first stop opens, so
    # edits add no avoidable waiting before it, still within the final legs' bounds.
    decoded=[(r.vehicle_id,None,r.order_ids) for r in edit.routes]
    return build_routes(inputs,engine,decoded,edit.unassigned_order_ids,points,profiles,None,deadline,on_stage,manual=True)


def fail_work(session:Session,row:Job,code:str,message:str) -> None:
    fail_job(row,code,message)
    if row.kind=='EDIT':
        plan=session.scalar(select(Plan).where(Plan.id==row.target_plan_id,Plan.organization_id==row.organization_id).with_for_update())
        # Clear only this job's marker, even if its reserved version was invalidated.
        # A newer edit has another job ID and is never touched; route data stays intact.
        if plan and plan.active_job_id==row.id:
            plan.active_job_id=None;plan.validation='FAIL';plan.result_stale=True
            plan.edit_error=row.error;plan.version+=1;plan.updated_at=now()


def cancel_job(session:Session,owner:UUID,key:UUID) -> JobRecord:
    """Fail a waiting or running job. A running calculation stops at the worker's next check."""
    lock_owner(session,owner)
    row=session.scalar(select(Job).where(Job.id==key,Job.organization_id==owner).with_for_update())
    if row is None:raise DataError(404,'NOT_FOUND','Job not found in this organization.')
    if row.status not in ('QUEUED','RUNNING'):raise DataError(409,'JOB_FINISHED','This calculation has already finished.')
    fail_work(session,row,'CANCELLED','Calculation cancelled. No result was saved.')
    session.flush()
    return job_record(row)


def dispatched_work(result:Draft) -> tuple[set[UUID],set[UUID]]:
    routes=[r for r in result.routes if r.stops]
    return {r.vehicle_id for r in routes},{s.order_id for r in routes for s in r.stops}


def conflicting_ready_plans(session:Session,owner:UUID,plan:Plan,result:Draft) -> list[Plan]:
    """Other READY plans for the same day that dispatch any of the same vehicles or deliveries."""
    vehicles,orders=dispatched_work(result)
    others=session.scalars(select(Plan).where(Plan.organization_id==owner,Plan.service_date==plan.service_date,
                                                Plan.status=='READY',Plan.id!=plan.id).order_by(Plan.id).with_for_update())
    conflicts=[]
    for other in others:
        other_vehicles,other_orders=dispatched_work(Draft.model_validate(other.result))
        if vehicles&other_vehicles or orders&other_orders:
            conflicts.append(other)
    return conflicts


def return_to_draft(plan:Plan) -> None:
    plan.status='DRAFT';plan.finalized_at=None;plan.version+=1;plan.updated_at=now()
    plan.result={**plan.result,'status':'DRAFT','operational_ready':False}


def finalize(session:Session,owner:UUID,key:UUID,version:int,request:FinalizeRequest) -> PlanRecord:
    lock_owner(session,owner);plan=owned_plan(session,owner,key)
    if plan.version!=version:raise DataError(412,'PLAN_CHANGED','Plan changed. Reload before finalizing.')
    inputs=PlanningInput.model_validate(plan.input_data);result=Draft.model_validate(plan.result)
    current_sources(session,owner,inputs)
    dispatch_checks(plan,inputs,result)
    if plan.status=='READY':return plan_record(plan,sources_current=True)
    if result.unassigned and not request.accept_unassigned:
        raise DataError(422,'UNASSIGNED_NOT_ACCEPTED','Review unassigned work and explicitly accept this partial plan.')
    # The owner lock serializes finalization, so two plans cannot both become READY for the same work.
    conflicts=conflicting_ready_plans(session,owner,plan,result)
    if conflicts and not request.replace_conflicting_ready:
        listed=', '.join(f'{other.id} (version {other.version})' for other in conflicts)
        raise DataError(409,'READY_CONFLICT',f'Another Ready plan for {plan.service_date} already dispatches some of the same vehicles '
                        f'or deliveries: {listed}. Replace it explicitly or return it to Draft first.')
    for other in conflicts:
        return_to_draft(other)
    plan.status='READY';plan.finalized_at=now();plan.updated_at=plan.finalized_at;plan.version+=1
    result.status='READY';result.operational_ready=True
    plan.result=Draft.model_validate(result.model_dump()).model_dump(mode='json')
    session.flush();return plan_record(plan,sources_current=True)


def dispatch_checks(plan:Plan,inputs:PlanningInput,result:Draft) -> None:
    if plan.active_job_id or plan.validation=='PENDING':raise DataError(409,'PLAN_BUSY','Recalculation is pending. Existing route display is stale.')
    if plan.result_stale or plan.validation!='PASS' or result.validation!='PASS' or result.blocking_issues:
        raise DataError(422,'VALIDATION_FAILED','Correct this draft and obtain a complete passing recalculation first.')
    if inputs.data_kind!='ROUTED' or result.data_kind!='ROUTED':
        raise DataError(422,'SYNTHETIC_NOT_OPERATIONAL','Synthetic plans cannot be finalized or exported as operational routes.')
    if not result.routes or not any(r.stops for r in result.routes):raise DataError(422,'NO_SERVED_WORK','No deliveries were served; there is nothing to dispatch.')
    if inputs.organization_id!=plan.organization_id or result.organization_id!=plan.organization_id:
        raise DataError(422,'RESULT_CONTEXT','Plan ownership is inconsistent.')
    # Re-run the independent model checks on the saved inputs and final legs, so a PASS
    # stored by an older application version is never trusted for dispatch on its own.
    # The optimizer's reachability matrices are not retained; that one check ran when saving.
    points,profiles=points_and_profiles(inputs)
    if issues:=validate(inputs,result,points,profiles,None):
        raise DataError(422,'VALIDATION_FAILED','Independent re-validation failed: '+'; '.join(issues[:5]))


def safe_cell(value:object) -> str:
    text=str(value)
    # Check after whitespace/control removal too; quotes alone do not prevent formulas.
    probe=text
    while probe and (probe[0].isspace() or ord(probe[0])<32 or probe[0]=='\ufeff'):probe=probe[1:]
    return "'"+text if probe.startswith(('=','+','-','@')) or (text and ord(text[0])<32) else text


PLANNING_NOTICE={
    'en':'Planning estimate. Verify current road access and driver requirements. Tolls are not included.',
    'fr':'Estimation de planification. Vérifiez les accès routiers actuels et les exigences des conducteurs. Péages non inclus.',
}
# A plan of example records: fictional deliveries, not routes to drive.
EXAMPLE_NOTICE={'en':'Example data, not for driving.','fr':'Données d’exemple, pas pour la route.'}
# Delivery columns carry the same names as the delivery CSV import.
# start_place and end_place are the codes of the depot or place the vehicle leaves from and returns to.
EXPORT_COLUMNS=['plan_id','plan_version','exported_at','service_date','vehicle','vehicle_departure','vehicle_return',
                'start_place','end_place','sequence','order',
                'address','latitude','longitude','time_window_start','time_window_end','arrival','service_start','stop_departure',
                'service_seconds',*DIMENSIONS,'required_skills','instructions','planning_notice']


def ready_plan(session:Session,owner:UUID,key:UUID,version:int) -> tuple[Plan,PlanningInput,Draft]:
    """The current READY plan at this version, its sources unchanged and its result re-validated:
    the one guard before any dispatch output (CSV file or route sheets)."""
    # Same owner -> plan lock order as source writes and finalization. Build the
    # bounded response while locked so all rows belong to one current plan version.
    lock_owner(session,owner);plan=owned_plan(session,owner,key)
    if plan.version!=version:raise DataError(412,'PLAN_CHANGED','Plan changed. Reload before downloading.')
    inputs=PlanningInput.model_validate(plan.input_data);result=Draft.model_validate(plan.result)
    current_sources(session,owner,inputs);dispatch_checks(plan,inputs,result)
    if plan.status!='READY' or result.status!='READY' or not result.operational_ready:
        raise DataError(409,'PLAN_NOT_READY','Mark this current plan ready before export.')
    return plan,inputs,result


def export_csv(session:Session,owner:UUID,key:UUID,version:int,*,spreadsheet:bool=False,language:str='en') -> tuple[str,int]:
    """Dispatch CSV. Every time is Europe/Paris ISO 8601. The spreadsheet variant (UTF-8 byte-order
    mark, semicolon separator, decimal comma in coordinates) opens in spreadsheet programs that use a
    decimal comma, such as French Excel."""
    plan,inputs,result=ready_plan(session,owner,key,version)
    output=io.StringIO(newline='')
    if spreadsheet:output.write('﻿')
    writer=csv.writer(output,delimiter=';' if spreadsheet else ',',lineterminator='\r\n')
    writer.writerow(EXPORT_COLUMNS)
    at=lambda seconds:datetime.fromtimestamp(inputs.epoch+seconds,PARIS).isoformat()
    paris=lambda moment:moment.astimezone(PARIS).isoformat()
    decimal=(lambda value:str(value).replace('.',',')) if spreadsheet else str
    exported=now().astimezone(PARIS).isoformat();notice=PLANNING_NOTICE.get(language,PLANNING_NOTICE['en'])
    if inputs.example:notice=EXAMPLE_NOTICE.get(language,EXAMPLE_NOTICE['en'])+' '+notice
    orders={o.id:o.data for o in inputs.orders};vehicles={v.id:v.data for v in inputs.vehicles}
    for route in result.routes:
        vehicle=vehicles[route.vehicle_id];start,end=inputs.start_of(vehicle).data.code,inputs.end_of(vehicle).data.code
        for index,stop in enumerate(route.stops,1):
            order=orders[stop.order_id];point=order.location  # Dispatch checks require a reviewed point.
            writer.writerow([str(plan.id),plan.version,exported,str(plan.service_date),safe_cell(vehicle.fleet_number),
                at(route.departure),at(route.end),safe_cell(start),safe_cell(end),index,safe_cell(order.external_id),safe_cell(order.address),
                decimal(point.latitude),decimal(point.longitude),paris(order.time_window.start),paris(order.time_window.end),
                at(stop.arrival),at(stop.service_start),at(stop.departure),order.service_seconds,
                *(getattr(order.demand,name) for name in DIMENSIONS),'|'.join(order.required_skills),
                safe_cell(order.instructions),notice])
    return output.getvalue(),plan.version


class SheetPlace(Model):
    """A depot or place a route leaves from or returns to, at its reviewed vehicle entrance."""
    code:str
    name:str
    address:str
    latitude:float
    longitude:float


class SheetStop(Model):
    """One delivery in driving order. Times are Europe/Paris; the point is the reviewed vehicle entrance."""
    sequence:int=Field(ge=1)
    order_id:UUID
    delivery:str=Field(description='The delivery number (external_id).')
    customer:str|None=Field(description='Code of the saved customer, when the delivery names one.')
    address:str
    latitude:float
    longitude:float
    time_window:Window
    arrival:datetime
    service_start:datetime=Field(description='Later than arrival when the vehicle waits for the time window.')
    departure:datetime
    service_seconds:int
    demand:Capacity
    required_skills:list[str]
    instructions:str


class RouteSheet(Model):
    """One vehicle's day: where it leaves from and returns to, and its deliveries in order."""
    vehicle_id:UUID
    vehicle:str=Field(description='The fleet number.')
    start:SheetPlace
    end:SheetPlace
    departure:datetime
    arrival:datetime=Field(description='Back at its end place.')
    distance_m:int
    driving_seconds:int
    service_seconds:int
    waiting_seconds:int
    load:Capacity=Field(description='Everything it carries when it leaves.')
    stops:list[SheetStop]


class RouteSheets(Model):
    """Route sheets of a current READY plan: one per vehicle that has deliveries."""
    plan_id:UUID
    plan_version:int
    service_date:date
    company:str
    generated_at:datetime
    example:bool=Field(description='A plan of example records: fictional deliveries, not routes to drive.')
    sheets:list[RouteSheet]


def sheet_place(place:DepotRecord) -> SheetPlace:
    data=place.data
    return SheetPlace(code=data.code,name=data.name,address=data.address,latitude=data.location.latitude,longitude=data.location.longitude)


def route_sheets(session:Session,owner:UUID,key:UUID,version:int,*,vehicle:UUID|None=None) -> RouteSheets:
    """Each route of a current READY plan as a driver's sheet; the same guard as the CSV export."""
    plan,inputs,result=ready_plan(session,owner,key,version)
    at=lambda seconds:datetime.fromtimestamp(inputs.epoch+seconds,PARIS)
    orders={o.id:o.data for o in inputs.orders};vehicles={v.id:v.data for v in inputs.vehicles}
    routes=[r for r in result.routes if r.stops and vehicle in (None,r.vehicle_id)]
    if vehicle is not None and not routes:raise DataError(404,'NOT_FOUND','This vehicle has no route in this plan.')
    sheets=[]
    for route in routes:
        data=vehicles[route.vehicle_id];stops=[]
        for index,stop in enumerate(route.stops,1):
            order=orders[stop.order_id];point=order.location  # Dispatch checks require a reviewed point.
            stops.append(SheetStop(sequence=index,order_id=stop.order_id,delivery=order.external_id,customer=order.customer,
                address=order.address,latitude=point.latitude,longitude=point.longitude,
                time_window=Window(start=order.time_window.start.astimezone(PARIS),end=order.time_window.end.astimezone(PARIS)),
                arrival=at(stop.arrival),service_start=at(stop.service_start),departure=at(stop.departure),
                service_seconds=order.service_seconds,demand=order.demand,required_skills=order.required_skills,
                instructions=order.instructions))
        load=Capacity(**{name:sum(getattr(orders[s.order_id].demand,name) for s in route.stops) for name in DIMENSIONS})
        sheets.append(RouteSheet(vehicle_id=route.vehicle_id,vehicle=data.fleet_number,start=sheet_place(inputs.start_of(data)),
            end=sheet_place(inputs.end_of(data)),departure=at(route.departure),arrival=at(route.end),distance_m=route.distance_m,
            driving_seconds=route.driving_seconds,service_seconds=route.service_seconds,waiting_seconds=route.waiting_seconds,
            load=load,stops=stops))
    return RouteSheets(plan_id=plan.id,plan_version=plan.version,service_date=plan.service_date,example=inputs.example,
                       company=session.get(Organization,owner).name,generated_at=now().astimezone(PARIS),sheets=sheets)
