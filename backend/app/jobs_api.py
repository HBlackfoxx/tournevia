"""Short owned job and plan handlers; computation is exclusively in the worker."""
from datetime import date
import json
from typing import Annotated, Literal
from urllib.request import urlopen
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy import String, cast, func, literal_column, or_, select, text
from sqlalchemy.orm import Session, load_only

from app.auth import current_session, who
from app.config import Settings
from app.data_api import SourceProblem, expected_version
from app.edits import EditRequest, FinalizeRequest, RouteSheets, cancel_job, export_csv, finalize, route_sheets, source_change, submit_edit
from app.jobs import WORKER_LOCK_KEY, Job, JobRecord, OptimizationRequest, Plan, PlanListItem, PlanRecord, PlanSummary, job_record, now, plan_record, submit
from app.planning import Model, PlanningInput
from app.records import DataError, find_row


class SystemStatus(Model):
    worker: Literal['RUNNING', 'NOT_RUNNING']
    routing: Literal['AVAILABLE', 'UNAVAILABLE']
    dataset: str
    queued_jobs: int
    running_jobs: int
    oldest_queued_seconds: int | None


def worker_running(session: Session) -> bool:
    # The active worker holds this session advisory lock for its whole lifetime.
    return bool(session.scalar(text(
        "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted AND classid = 0 "
        "AND objid = :key AND objsubid = 1 AND database = (SELECT oid FROM pg_database WHERE datname = current_database()))"),
        {'key': WORKER_LOCK_KEY}))


def routing_available(url: str) -> bool:
    try:
        with urlopen(f"{url.rstrip('/')}/status", timeout=2) as response:
            return response.status == 200 and isinstance(json.loads(response.read(65536)), dict)
    except (OSError, ValueError):
        return False


# Totals for plan lists, computed in PostgreSQL so the large route payloads are not loaded.
_ROUTES="jsonb_array_elements(plans.result->'routes')"
PLAN_TOTALS=(
    literal_column(f"(SELECT count(*) FROM {_ROUTES} r, jsonb_array_elements(r->'stops'))").label('served_deliveries'),
    literal_column("jsonb_array_length(plans.result->'unassigned')").label('unassigned_deliveries'),
    literal_column(f"(SELECT count(*) FROM {_ROUTES} r WHERE jsonb_array_length(r->'stops') > 0)").label('vehicles_used'),
    literal_column(f"(SELECT coalesce(sum((r->>'distance_m')::bigint), 0) FROM {_ROUTES} r)").label('distance_m'),
    literal_column(f"(SELECT coalesce(sum((r->>'known_cost_minor')::bigint), 0) FROM {_ROUTES} r)").label('known_cost_minor'),
)


def jobs_router(settings:Settings):
    api=APIRouter(prefix='/api/v1',dependencies=[Depends(current_session)],responses={**{code:{'model':SourceProblem} for code in (400,401,403,404,409,412,428)},
        422:{'content':{'application/json':{'schema':{'anyOf':[{'$ref':'#/components/schemas/SourceProblem'},
                                                            {'$ref':'#/components/schemas/HTTPValidationError'}]}}}}})

    @api.get('/system-status',response_model=SystemStatus,operation_id='systemStatus')
    def system_status(request:Request):
        """Whether calculations can currently run: worker, queue and routing service."""
        owner=who(request).organization_id
        with Session(request.app.state.engine) as session:
            counts=dict(session.execute(select(Job.status,func.count()).where(Job.organization_id==owner,Job.status.in_(('QUEUED','RUNNING')))
                                        .group_by(Job.status)).all())
            oldest=session.scalar(select(func.min(Job.created_at)).where(Job.organization_id==owner,Job.status=='QUEUED'))
            running=worker_running(session)
        return SystemStatus(worker='RUNNING' if running else 'NOT_RUNNING',
                            routing='AVAILABLE' if routing_available(settings.valhalla_url) else 'UNAVAILABLE',
                            dataset=settings.routing_dataset,queued_jobs=counts.get('QUEUED',0),running_jobs=counts.get('RUNNING',0),
                            oldest_queued_seconds=None if oldest is None else max(0,int((now()-oldest).total_seconds())))

    @api.post('/optimization-jobs',response_model=JobRecord,status_code=202,operation_id='createOptimizationJob',
              responses={409:{'model':SourceProblem},412:{'model':SourceProblem},428:{'model':SourceProblem},
                         422:{'content':{'application/json':{'schema':{'anyOf':[
                             {'$ref':'#/components/schemas/SourceProblem'},{'$ref':'#/components/schemas/HTTPValidationError'}]}}}}})
    def create_job(data:OptimizationRequest,request:Request,response:Response,
                   idempotency_key:Annotated[str|None,Header()] = None):
        if idempotency_key is None:
            raise DataError(428,'IDEMPOTENCY_REQUIRED','Supply Idempotency-Key; reuse it only for an identical submission retry.')
        with Session(request.app.state.engine) as session,session.begin():
            result=submit(session,who(request).organization_id,data,idempotency_key,actor=who(request).actor,
                          max_km=settings.matrix_max_km)
        response.headers['Location']=f'/api/v1/jobs/{result.id}'
        return result

    @api.get('/jobs',response_model=list[JobRecord],operation_id='listJobs')
    def jobs(request:Request,service_date:date,limit:int=Query(50,ge=1,le=250),offset:int=Query(0,ge=0)):
        with Session(request.app.state.engine) as session:
            rows=session.scalars(select(Job).where(Job.organization_id==who(request).organization_id,Job.service_date==service_date)
                                  .order_by(Job.created_at.desc(),Job.id).limit(limit).offset(offset))
            return [job_record(row) for row in rows]

    @api.get('/jobs/{key}',response_model=JobRecord,operation_id='getJob')
    def job(key:UUID,request:Request):
        with Session(request.app.state.engine) as session:
            return job_record(find_row(session,who(request).organization_id,Job,key))

    @api.post('/jobs/{key}/cancel',response_model=JobRecord,operation_id='cancelJob')
    def cancel(key:UUID,request:Request):
        """Cancel a waiting or running calculation. No result is saved; an edit releases its plan."""
        with Session(request.app.state.engine) as session,session.begin():
            return cancel_job(session,who(request).organization_id,key)

    @api.get('/plans',response_model=list[PlanListItem],operation_id='listPlans')
    def plans(request:Request,service_date:date,limit:int=Query(50,ge=1,le=250),offset:int=Query(0,ge=0),
              status:Literal['DRAFT','READY']|None=None,
              search:Annotated[str|None,Query(min_length=1,max_length=100,description=
                  'A plan ID prefix, or the exact external ID of a delivery the plan includes.')]=None):
        """Saved plans for a day, newest first, with totals computed from each stored result."""
        query=(select(Plan,*PLAN_TOTALS).options(load_only(*(getattr(Plan,k) for k in PlanSummary.model_fields)))
               .where(Plan.organization_id==who(request).organization_id,Plan.service_date==service_date))
        if status:query=query.where(Plan.status==status)
        if search:query=query.where(or_(cast(Plan.id,String).startswith(search.strip().lower()),
                                        Plan.input_data['orders'].contains([{'data':{'external_id':search.strip()}}])))
        with Session(request.app.state.engine) as session:
            rows=session.execute(query.order_by(Plan.created_at.desc(),Plan.id).limit(limit).offset(offset)).all()
            return [PlanListItem.model_validate({**{key:getattr(row.Plan,key) for key in PlanSummary.model_fields},
                                                 **{column.name:getattr(row,column.name) for column in PLAN_TOTALS}}) for row in rows]

    @api.get('/plans/{key}',response_model=PlanRecord,operation_id='getPlan')
    def plan(key:UUID,request:Request,response:Response):
        owner=who(request).organization_id
        with Session(request.app.state.engine) as session:
            row=find_row(session,owner,Plan,key)
            result=plan_record(row,sources_current=source_change(session,owner,PlanningInput.model_validate(row.input_data)) is None)
        response.headers['ETag']=f'"{result.version}"'
        return result

    @api.post('/plans/{key}/edits',response_model=JobRecord,status_code=202,operation_id='editPlan')
    def edit(key:UUID,data:EditRequest,request:Request,response:Response,
             if_match:Annotated[str|None,Header()]=None,idempotency_key:Annotated[str|None,Header()]=None):
        if idempotency_key is None:raise DataError(428,'IDEMPOTENCY_REQUIRED','Supply Idempotency-Key for safe edit submission retries.')
        with Session(request.app.state.engine) as session,session.begin():
            result=submit_edit(session,who(request).organization_id,key,data,expected_version(if_match),idempotency_key,actor=who(request).actor)
        response.headers['Location']=f'/api/v1/jobs/{result.id}'
        return result

    @api.post('/plans/{key}/finalize',response_model=PlanRecord,operation_id='finalizePlan')
    def ready(key:UUID,data:FinalizeRequest,request:Request,response:Response,if_match:Annotated[str|None,Header()]=None):
        with Session(request.app.state.engine) as session,session.begin():
            result=finalize(session,who(request).organization_id,key,expected_version(if_match),data)
        response.headers['ETag']=f'"{result.version}"'
        return result

    @api.get('/plans/{key}/export.csv',operation_id='exportPlan',response_class=Response,
             responses={200:{'content':{'text/csv':{'schema':{'type':'string'}}}}})
    def download(key:UUID,request:Request,if_match:Annotated[str|None,Header()]=None,
                 layout:Annotated[Literal['standard','spreadsheet'],Query(description=
                     'standard: comma-separated UTF-8. spreadsheet: UTF-8 byte-order mark and semicolons, for spreadsheet '
                     'programs that use a decimal comma (for example French Excel).')]='standard'):
        identity=who(request)
        with Session(request.app.state.engine) as session,session.begin():
            data,version=export_csv(session,identity.organization_id,key,expected_version(if_match),
                                    spreadsheet=layout=='spreadsheet',language=identity.language)
        return Response(data,media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="plan-{key}-v{version}.csv"',
                                                        'ETag':f'"{version}"','Cache-Control':'no-store'})

    @api.get('/plans/{key}/sheets',response_model=RouteSheets,operation_id='planSheets')
    def sheets(key:UUID,request:Request,response:Response,if_match:Annotated[str|None,Header()]=None,
               vehicle:Annotated[UUID|None,Query(description="Only this vehicle's sheet.")]=None):
        """Route sheets of the current READY plan: the same checks as the CSV export."""
        with Session(request.app.state.engine) as session,session.begin():
            result=route_sheets(session,who(request).organization_id,key,expected_version(if_match),vehicle=vehicle)
        response.headers['ETag']=f'"{result.plan_version}"';response.headers['Cache-Control']='no-store'
        return result
    return api
