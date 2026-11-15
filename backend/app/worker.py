"""Single-connection worker. Lost guard connections are terminal, never reconnected."""
import argparse
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.config import Settings
from app.accounts import User
from app.jobs import ACTIVE_STAGES, WORKER_LOCK_KEY, Job, Plan, now, save_result
from app.edits import EditRequest, fail_work, reroute
from app.planning import Draft, PlanningInput
from app.processes import die_with_parent
from app.records import DataError
from app.routing import Deadline, EngineError, Valhalla
from app.spike import calculate

LOCK_KEY=WORKER_LOCK_KEY
logger=logging.getLogger(__name__)


class GuardLost(RuntimeError):
    pass


class CalculationFailure(Exception):
    def __init__(self,code,message):
        self.code,self.message=code,message


class StatementFailed(CalculationFailure):
    """A database statement failed (timeout, lock wait, constraint) but the guard
    connection and its singleton lock are intact: fail the job, keep the worker."""
    def __init__(self):
        super().__init__('DATABASE_ERROR','A database operation failed during this calculation; no result was published. Retry explicitly.')


# Touched on every guard check (about every 0.25 s while working, 2 s while idle). A container
# health check reports the worker unhealthy when it stops changing.
HEARTBEAT = Path(tempfile.gettempdir()) / 'vrp-worker-alive'

# Saving a large result takes a few seconds; computation stops early enough that
# the whole job, including SAVE, finishes within JOB_TIMEOUT_SECONDS.
SAVE_RESERVE_SECONDS=5


class Worker:
    def __init__(self,settings:Settings):
        self.settings=settings
        self.engine=create_engine(settings.database_url,poolclass=NullPool,
            connect_args={'connect_timeout':3,'application_name':'tournevia-worker',
                          'options':'-c statement_timeout=2000','keepalives':1,
                          'keepalives_idle':2,'keepalives_interval':1,'keepalives_count':2,'tcp_user_timeout':4000})
        self.connection=None
        self.stopping=False
        self.broken=False

    @contextmanager
    def transaction(self):
        connection=self.connection
        if self.broken or connection is None or connection.closed or connection.invalidated:
            self.broken=True
            raise GuardLost('Worker database guard unavailable.')
        try:
            with Session(bind=connection) as session,session.begin():
                yield session
        except SQLAlchemyError as error:
            if self.guard_intact(error):
                raise StatementFailed() from None
            self.broken=True
            raise GuardLost('Worker database guard lost; exiting without reconnecting.') from None

    def guard_intact(self,error:SQLAlchemyError) -> bool:
        """After a rolled-back statement, whether this connection still holds the singleton lock."""
        connection=self.connection
        if connection is None or connection.closed or connection.invalidated:
            return False
        if isinstance(error,DBAPIError) and error.connection_invalidated:
            return False
        try:
            with connection.begin():
                return bool(connection.scalar(text(
                    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted AND pid = pg_backend_pid() "
                    "AND classid = 0 AND objid = :key AND objsubid = 1)"),{'key':LOCK_KEY}))
        except SQLAlchemyError:
            return False

    def acquire(self):
        self.connection=self.engine.connect()
        with self.transaction() as session:
            acquired=session.scalar(text('SELECT pg_try_advisory_lock(:key)'),{'key':LOCK_KEY})
        if not acquired:
            raise GuardLost('Another worker owns the singleton guard.')
        # Holding the singleton lock, no other worker can be using these. A killed
        # predecessor leaves private workspaces (with job inputs) behind; remove them.
        root=Path(tempfile.gettempdir())
        abandoned_workspaces=[*root.glob('vrp-job-*'),*root.glob('vrp-solve-*')]
        for path in abandoned_workspaces:
            shutil.rmtree(path,ignore_errors=True)
        # This runs only after acquiring the singleton lock, never on a timer.
        with self.transaction() as session:
            abandoned=list(session.scalars(select(Job).where(Job.status=='RUNNING').with_for_update()))
            for row in abandoned:
                fail_work(session,row,'WORKER_INTERRUPTED','Previous worker stopped before saving a result. The edit was not applied; retry explicitly.')
        logger.info('worker_started recovered=%d removed_workspaces=%d',len(abandoned),len(abandoned_workspaces))

    def close(self):
        if self.connection is not None:
            self.connection.close()
        self.engine.dispose()

    def tick(self,job_id:UUID|None=None,stage:str|None=None):
        with self.transaction() as session:
            session.execute(text('SELECT 1'))
            HEARTBEAT.touch()
            if job_id is not None:
                row=session.get(Job,job_id)
                if row is None or row.status!='RUNNING':
                    raise CalculationFailure('JOB_NOT_RUNNING','Calculation cancelled because its job is no longer running.')
                if stage in ACTIVE_STAGES and row.stage!=stage:
                    row.stage=stage
                    logger.info('job_stage job_id=%s stage=%s',job_id,stage)

    def claim(self):
        with self.transaction() as session:
            row=session.scalar(select(Job).where(Job.status=='QUEUED').order_by(Job.created_at,Job.id).with_for_update().limit(1))
            if row is None:
                return None
            if row.submitted_by != 'local-demo':
                try:
                    author = session.get(User, UUID(row.submitted_by))
                except ValueError:
                    author = None
                if author is None or author.organization_id != row.organization_id:
                    fail_work(session, row, 'INPUT_INVALID', 'Job submitter ownership is invalid.')
                    return None
            plan=session.get(Plan,row.target_plan_id) if row.kind=='EDIT' else None
            if row.kind=='EDIT' and (plan is None or plan.organization_id!=row.organization_id or plan.version!=row.reserved_version or plan.active_job_id!=row.id):
                fail_work(session,row,'PLAN_CHANGED','The reserved plan version changed; edit was not applied.')
                return None
            try:
                inputs=PlanningInput.model_validate(plan.input_data if plan else row.input_data)
            except ValidationError:
                fail_work(session,row,'INPUT_INVALID','Saved job input is invalid. Reload source records and submit again.')
                return None
            if inputs.organization_id!=row.organization_id:
                fail_work(session,row,'INPUT_INVALID','Job input ownership is invalid.')
                return None
            row.status,row.stage,row.started_at='RUNNING','PRECHECK',now()
            session.flush()
            return row.id,inputs

    def process_one(self) -> bool:
        claimed=self.claim()
        if claimed is None:
            return False
        job_id,inputs=claimed
        started=time.monotonic()
        timeout=self.settings.job_timeout_seconds
        compute_end=started+timeout-min(SAVE_RESERVE_SECONDS,timeout/10)
        logger.info('job_started job_id=%s',job_id)
        try:
            result=self.compute(job_id,inputs,compute_end)
            if self.stopping:
                raise CalculationFailure('WORKER_INTERRUPTED','Worker stopped. Start a new calculation explicitly.')
            if time.monotonic()>=compute_end:
                raise CalculationFailure('JOB_TIMEOUT','Whole-job time budget exceeded; no result published.')
            self.tick(job_id,'SAVE')
            with self.transaction() as session:
                save_result(session,job_id,result)
            logger.info('job_succeeded job_id=%s elapsed=%.3f',job_id,time.monotonic()-started)
        except GuardLost:
            raise
        except Exception as error:
            code=error.code if isinstance(error,(CalculationFailure,DataError)) else 'CALCULATION_FAILED'
            message=error.message if isinstance(error,(CalculationFailure,DataError)) else UNEXPECTED_FAILURE
            with self.transaction() as session:
                row=session.get(Job,job_id)
                if row is not None and row.status=='RUNNING':
                    fail_work(session,row,code,message)
            logger.warning('job_failed job_id=%s code=%s error_type=%s elapsed=%.3f',job_id,code,type(error).__name__,time.monotonic()-started)
        return True

    def compute(self,job_id:UUID,inputs:PlanningInput,end:float) -> Draft:
        """Run the calculation child until the monotonic time `end`, which leaves room to save."""
        import resource
        parent_pid=os.getpid()
        def child_limits():
            die_with_parent(parent_pid,signal.SIGTERM)
            resource.setrlimit(resource.RLIMIT_AS,(1536*1024**2,1536*1024**2))
            resource.setrlimit(resource.RLIMIT_FSIZE,(16_000_000,16_000_000))
        with tempfile.TemporaryDirectory(prefix='vrp-job-') as directory:
            path=Path(directory)
            (path/'input.json').write_text(inputs.model_dump_json())
            with self.transaction() as session:
                row=session.get(Job,job_id)
                if row.kind=='EDIT':
                    (path/'edit.json').write_text(EditRequest.model_validate(row.request_data['edit']).model_dump_json())
            with (path/'stdout').open('wb') as stdout,(path/'stderr').open('wb') as stderr:
                process=subprocess.Popen([sys.executable,'-m','app.worker','--compute',str(path)],
                    stdout=stdout,stderr=stderr,stdin=subprocess.DEVNULL,start_new_session=True,preexec_fn=child_limits,
                    env=child_environment(self.settings,max(1,int(end-time.monotonic()))))
                try:
                    while True:
                        if self.stopping:
                            raise CalculationFailure('WORKER_INTERRUPTED','Worker stopped. Start a new calculation explicitly.')
                        if time.monotonic()>=end:
                            raise CalculationFailure('JOB_TIMEOUT','Whole-job time budget exceeded; no result published.')
                        if any((path/name).stat().st_size>4_000_000 for name in ('stdout','stderr')):
                            raise CalculationFailure('OUTPUT_LIMIT','Calculation logs exceeded their bounded output budget.')
                        stage=(path/'stage').read_text() if (path/'stage').exists() else 'PRECHECK'
                        self.tick(job_id,stage)
                        if process.poll() is not None:
                            break
                        time.sleep(.25)
                    if process.returncode!=0:
                        raise CalculationFailure('CALCULATION_FAILED','Calculation process stopped unexpectedly. Check local engine availability and retry explicitly.')
                    result_path=path/'result.json'
                    if not result_path.exists() or result_path.stat().st_size>12_000_000:
                        raise CalculationFailure('OUTPUT_LIMIT','Calculation output missing or too large; no draft accepted.')
                    payload=json.loads(result_path.read_text())
                    if 'error' in payload:
                        raise CalculationFailure('ENGINE_FAILED',engine_message(payload))
                    return Draft.model_validate(payload)
                finally:
                    # Termination runs the calculation child's cleanup; its solver also
                    # has a Linux parent-death SIGKILL for abrupt process termination.
                    try:os.killpg(process.pid,signal.SIGTERM)
                    except ProcessLookupError:pass
                    try:process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        try:os.killpg(process.pid,signal.SIGKILL)
                        except ProcessLookupError:pass
                        process.wait()


NO_DRAFT_MESSAGE='No draft was saved.'
UNEXPECTED_FAILURE='Calculation failed; no draft saved. Check local engines and retry explicitly.'
GENERIC_ENGINE_FAILURE='Routing or solver failed. Check saved coordinates, engine coverage and service availability; then start a new calculation.'
# The calculation child needs routing settings only, never database or setup secrets.
# TLS trust and proxy settings still apply to its routing-service requests.
CHILD_ENVIRONMENT=('PATH','HOME','LANG','LC_ALL','TZ','PYTHONPATH','PYTHONDONTWRITEBYTECODE','PYTHONUNBUFFERED','TMPDIR',
                   'SSL_CERT_FILE','SSL_CERT_DIR','HTTP_PROXY','HTTPS_PROXY','NO_PROXY','http_proxy','https_proxy','no_proxy')


def child_environment(settings:Settings,compute_seconds:int|None=None) -> dict[str,str]:
    """`compute_seconds` is the child's own deadline: what remains of the job budget after its save reserve."""
    environment={key:os.environ[key] for key in CHILD_ENVIRONMENT if key in os.environ}
    environment.update({'VALHALLA_URL':settings.valhalla_url,'ROUTING_DATASET':settings.routing_dataset,
                        'JOB_TIMEOUT_SECONDS':str(compute_seconds or settings.job_timeout_seconds),
                        'MATRIX_CONCURRENCY':str(settings.matrix_concurrency)})
    return environment


def engine_message(payload:dict) -> str:
    """Only the adapter's own EngineError text is shown; anything else stays generic."""
    message=payload.get('message')
    if payload.get('error')!='EngineError' or not isinstance(message,str) or not message.strip():
        return GENERIC_ENGINE_FAILURE
    return ' '.join(message.split())[:600]+' '+NO_DRAFT_MESSAGE


def compute_child(path:Path,settings:Settings):
    def interrupted(*_):
        raise SystemExit(3)
    signal.signal(signal.SIGTERM,interrupted)
    signal.signal(signal.SIGINT,interrupted)
    def stage(value):
        (path/'stage.tmp').write_text(value)
        (path/'stage.tmp').replace(path/'stage')
    try:
        inputs=PlanningInput.model_validate_json((path/'input.json').read_text())
        engine=Valhalla(settings.valhalla_url,settings.routing_dataset,matrix_concurrency=settings.matrix_concurrency)
        options={'deadline':Deadline.after(settings.job_timeout_seconds),'on_stage':stage}
        if (path/'edit.json').exists():
            result=reroute(inputs,EditRequest.model_validate_json((path/'edit.json').read_text()),engine,**options)
        else:
            result=calculate(inputs,engine,**options)
        output=result.model_dump_json()
    except EngineError as error:
        # Adapter messages are written by this application (pin, limit and engine
        # failure explanations); never engine stderr or driver exception strings.
        output=json.dumps({'error':'EngineError','message':str(error)})
    except Exception as error:
        # Never write engine stderr, inputs or driver exception strings to public errors.
        output=json.dumps({'error':type(error).__name__})
    (path/'result.json').write_text(output)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--compute',type=Path)
    args=parser.parse_args()
    if sys.platform!='linux':
        raise SystemExit('Run the worker in the Linux application image.')
    settings=Settings()
    if args.compute:
        compute_child(args.compute,settings);return
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s', datefmt='%Y-%m-%dT%H:%M:%S%z')
    worker=Worker(settings)
    def stop(*_):worker.stopping=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:
        worker.acquire()
        while not worker.stopping:
            try:
                did_work=worker.process_one()
                if args.once:break
                if not did_work:
                    for _ in range(8):
                        if worker.stopping:break
                        time.sleep(.25)
                    worker.tick()
            except StatementFailed:
                # The guard connection and lock are intact; wait briefly and continue.
                logger.warning('worker_statement_failed')
                time.sleep(2)
    except (GuardLost,SQLAlchemyError) as error:
        logger.error('worker_stopped reason=%s',type(error).__name__)
        raise SystemExit(1) from None
    finally:
        worker.close()


if __name__=='__main__':main()
