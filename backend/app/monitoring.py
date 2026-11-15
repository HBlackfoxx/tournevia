"""Prometheus metrics for uptime monitoring and alerting: installation-wide counts, no personal data.

Disabled (404) unless MONITOR_TOKEN is set; a monitor then sends it as a bearer token. It is
independent of user sessions, so a monitor never holds a dispatcher account.
"""
from datetime import timedelta
import hmac

from fastapi import APIRouter, Request, Response
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.jobs import Job, now
from app.records import DataError

METRICS = (
    ('tournevia_database_ready', 'gauge', 'Database reachable with the expected schema revision (1) or not (0).'),
    ('tournevia_worker_running', 'gauge', 'The calculation worker holds its singleton guard (1) or not (0).'),
    ('tournevia_routing_available', 'gauge', 'The routing service answers its status endpoint (1) or not (0).'),
    ('tournevia_jobs_queued', 'gauge', 'Calculations waiting for the worker.'),
    ('tournevia_jobs_running', 'gauge', 'Calculations running now.'),
    ('tournevia_oldest_queued_job_age_seconds', 'gauge', 'Age of the oldest waiting calculation (0 when none).'),
    ('tournevia_jobs_succeeded_last_hour', 'gauge', 'Calculations that finished successfully in the last hour.'),
    ('tournevia_jobs_failed_last_hour', 'gauge', 'Calculations that failed or were cancelled in the last hour.'),
    ('tournevia_last_success_age_seconds', 'gauge', 'Seconds since the last successful calculation (-1 when none).'),
)


def measure(session: Session, settings: Settings) -> dict[str, float]:
    # Imported here: jobs_api imports this module's router through main.
    from app.jobs_api import routing_available, worker_running
    from app.main import SCHEMA_REVISION
    values = {name: 0.0 for name, _, _ in METRICS}
    values['tournevia_routing_available'] = float(routing_available(settings.valhalla_url))
    try:
        values['tournevia_database_ready'] = float(session.scalar(text('SELECT version_num FROM alembic_version')) == SCHEMA_REVISION)
        values['tournevia_worker_running'] = float(worker_running(session))
        counts = dict(session.execute(select(Job.status, func.count()).where(Job.status.in_(('QUEUED', 'RUNNING'))).group_by(Job.status)).all())
        values['tournevia_jobs_queued'] = float(counts.get('QUEUED', 0))
        values['tournevia_jobs_running'] = float(counts.get('RUNNING', 0))
        stamp = now()
        oldest = session.scalar(select(func.min(Job.created_at)).where(Job.status == 'QUEUED'))
        values['tournevia_oldest_queued_job_age_seconds'] = max(0.0, (stamp - oldest).total_seconds()) if oldest else 0.0
        hour = stamp - timedelta(hours=1)
        finished = dict(session.execute(select(Job.status, func.count()).where(Job.finished_at >= hour).group_by(Job.status)).all())
        values['tournevia_jobs_succeeded_last_hour'] = float(finished.get('SUCCEEDED', 0))
        values['tournevia_jobs_failed_last_hour'] = float(finished.get('FAILED', 0))
        last = session.scalar(select(func.max(Job.finished_at)).where(Job.status == 'SUCCEEDED'))
        values['tournevia_last_success_age_seconds'] = (stamp - last).total_seconds() if last else -1.0
    except SQLAlchemyError:
        values['tournevia_database_ready'] = 0.0  # Other database values stay 0; alert on readiness.
    return values


def exposition(values: dict[str, float]) -> str:
    lines = []
    for name, kind, description in METRICS:
        lines += [f'# HELP {name} {description}', f'# TYPE {name} {kind}', f'{name} {values[name]:g}']
    return '\n'.join(lines) + '\n'


def monitoring_router(settings: Settings) -> APIRouter:
    api = APIRouter(prefix='/api/v1')

    @api.get('/metrics', operation_id='metrics', response_class=Response,
             responses={200: {'content': {'text/plain': {'schema': {'type': 'string'}}}},
                        401: {'description': 'Missing or wrong monitor token'},
                        404: {'description': 'Monitoring is not enabled (MONITOR_TOKEN unset)'}})
    def metrics(request: Request) -> Response:
        """Prometheus text metrics. Requires `Authorization: Bearer <MONITOR_TOKEN>`."""
        expected = settings.monitor_token.get_secret_value()
        if not expected:
            raise DataError(404, 'NOT_FOUND', 'Monitoring is not enabled.')
        supplied = request.headers.get('authorization', '')
        if not hmac.compare_digest(supplied.encode(), f'Bearer {expected}'.encode()):
            raise DataError(401, 'MONITOR_TOKEN_REQUIRED', 'Send the configured monitor token as a bearer token.')
        with Session(request.app.state.engine) as session:
            body = exposition(measure(session, settings))
        return Response(body, media_type='text/plain; version=0.0.4')

    return api
