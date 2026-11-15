"""Operator data lifecycle: purge old calculations and erase one delivery's, customer's or user's
personal data.

Plans and jobs copy delivery addresses, instructions and coordinates. The runtime role cannot
delete them, so run this with the migration role, as setup does, for example in Compose:

    docker compose ... run --rm setup python -m app.retention purge --older-than-days 90 --dry-run
    docker compose ... run --rm setup python -m app.retention erase-order --external-id ORDER-123
    docker compose ... run --rm setup python -m app.retention erase-customer --code CLI-1
    docker compose ... run --rm setup python -m app.retention erase-user --username j.martin

Each command prints counts only. Waiting or running calculations are never deleted.
"""
import argparse
from datetime import date, timedelta
import json
import secrets
from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.accounts import LoginSession, User
from app.auth import HASHER
from app.database_roles import DatabaseAdminSettings
from app.db import make_engine
from app.jobs import Job, Plan, now
from app.records import Customer, DataError, Order, lock_owner

FINISHED = ('SUCCEEDED', 'FAILED')
ERASED = '[erased]'


def _delete(session: Session, plan_ids: set[UUID], job_ids: set[UUID]) -> None:
    # Jobs first: they reference plans with restricted foreign keys.
    if job_ids:
        session.execute(delete(Job).where(Job.id.in_(job_ids)))
    if plan_ids:
        session.execute(delete(Plan).where(Plan.id.in_(plan_ids)))


def _jobs_of(session: Session, plan_ids: set[UUID]) -> set[UUID]:
    if not plan_ids:
        return set()
    return set(session.scalars(select(Job.id).where(or_(Job.plan_id.in_(plan_ids), Job.target_plan_id.in_(plan_ids)))))


def purge(session: Session, owner: UUID, older_than_days: int, *, orders: bool = False, dry_run: bool = False) -> dict:
    """Delete plans not updated, and finished jobs not finished, within the period; optionally old deliveries."""
    if older_than_days < 1:
        raise DataError(422, 'RETENTION_PERIOD', 'Keep at least one day of plans and calculations.')
    lock_owner(session, owner)
    cutoff = now() - timedelta(days=older_than_days)
    plan_ids = set(session.scalars(select(Plan.id).where(
        Plan.organization_id == owner, Plan.updated_at < cutoff, Plan.active_job_id.is_(None))))
    # A plan still targeted by a waiting or running edit stays.
    plan_ids -= set(session.scalars(select(Job.target_plan_id).where(
        Job.organization_id == owner, Job.target_plan_id.in_(plan_ids), Job.status.not_in(FINISHED))))
    # Old finished jobs go too, except those still attached to a kept plan (as its creating
    # optimization or an edit of it), which the plan's foreign keys protect.
    kept = select(Plan.id).where(Plan.organization_id == owner, Plan.id.not_in(plan_ids))
    job_ids = _jobs_of(session, plan_ids) | set(session.scalars(select(Job.id).where(
        Job.organization_id == owner, Job.status.in_(FINISHED), Job.finished_at < cutoff,
        Job.plan_id.is_(None) | Job.plan_id.not_in(kept), Job.target_plan_id.is_(None) | Job.target_plan_id.not_in(kept))))
    order_ids = set(session.scalars(select(Order.id).where(
        Order.organization_id == owner, Order.service_date < date.today() - timedelta(days=older_than_days)))) if orders else set()
    if not dry_run:
        _delete(session, plan_ids, job_ids)
        if order_ids:
            session.execute(delete(Order).where(Order.id.in_(order_ids)))
    return {'plans': len(plan_ids), 'jobs': len(job_ids), 'orders': len(order_ids)}


def erase_order(session: Session, owner: UUID, external_id: str, *, dry_run: bool = False) -> dict:
    """Erase one delivery's address, instructions and location, and delete calculations that copied them.

    A delivery already deleted in the application is found by its identifier in the calculations that
    copied it, so those copies can still be erased."""
    lock_owner(session, owner)
    order = session.scalar(select(Order).where(Order.organization_id == owner, Order.identifier == external_id).with_for_update())
    copied = [{'id': str(order.id)}] if order else [{'data': {'external_id': external_id}}]
    plan_ids = set(session.scalars(select(Plan.id).where(Plan.organization_id == owner, Plan.input_data['orders'].contains(copied))))
    # The plans' own jobs, edits of those plans included (an edit carries no copy of its own), and
    # the calculations that copied the delivery.
    job_ids = _jobs_of(session, plan_ids) | set(session.scalars(select(Job.id).where(
        Job.organization_id == owner, Job.input_data['orders'].contains(copied))))
    if order is None and not plan_ids and not job_ids:
        raise DataError(404, 'NOT_FOUND', 'No delivery with that external ID in this company.')
    if job_ids and session.scalar(select(func.count()).select_from(Job).where(Job.id.in_(job_ids), Job.status.not_in(FINISHED))):
        raise DataError(409, 'JOB_ACTIVE', 'A waiting or running calculation includes this delivery; cancel it first.')
    if not dry_run:
        _delete(session, plan_ids, job_ids)
        if order is not None:
            order.data = {**order.data, 'address': ERASED, 'instructions': '', 'location': None, 'active': False}
            order.version += 1
    return {'plans': len(plan_ids), 'jobs': len(job_ids), 'orders': 1 if order else 0}


def erase_customer(session: Session, owner: UUID, code: str, *, dry_run: bool = False) -> dict:
    """Erase one saved customer's name, address, instructions and entrance, and with them every delivery
    that names the customer (each as `erase_order` does). Nothing changes while one of those deliveries
    is in a waiting or running calculation."""
    lock_owner(session, owner)
    customer = session.scalar(select(Customer).where(Customer.organization_id == owner, Customer.identifier == code).with_for_update())
    if customer is None:
        raise DataError(404, 'NOT_FOUND', 'No saved customer with that code in this company.')
    totals = {'customers': 1, 'orders': 0, 'plans': 0, 'jobs': 0}
    for external_id in session.scalars(select(Order.identifier).where(
            Order.organization_id == owner, Order.data['customer'].astext == code).order_by(Order.identifier)).all():
        for key, value in erase_order(session, owner, external_id, dry_run=dry_run).items():
            totals[key] += value
    if not dry_run:
        customer.data = {**customer.data, 'name': ERASED, 'address': ERASED, 'instructions': '', 'location': None,
                         'active': False}
        customer.version += 1
    return totals


def erase_user(session: Session, owner: UUID, username: str, *, dry_run: bool = False) -> dict:
    """Erase one account's sign-in name and display name, disable it and end its sessions. Calculations
    keep only the account's internal ID, which names nobody once the account is erased."""
    lock_owner(session, owner)
    user = session.scalar(select(User).where(User.organization_id == owner, User.username == username).with_for_update())
    if user is None:
        raise DataError(404, 'NOT_FOUND', 'Account not found in this installation.')
    if user.active and user.role == 'admin' and session.scalar(select(func.count()).select_from(User).where(
            User.organization_id == owner, User.active.is_(True), User.role == 'admin')) < 2:
        raise DataError(409, 'LAST_ADMIN', 'Keep at least one active administrator in this company.')
    if not dry_run:
        user.username, user.display_name, user.active = f'erased-{user.id.hex}', ERASED, False
        user.password_hash = HASHER.hash(secrets.token_urlsafe(32))  # Nobody knows this password.
        session.execute(delete(LoginSession).where(LoginSession.user_id == user.id))
    return {'users': 1}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='action', required=True)
    old = sub.add_parser('purge', help='Delete plans and finished calculations older than a retention period.')
    old.add_argument('--older-than-days', type=int, required=True)
    old.add_argument('--orders', action='store_true', help='Also delete deliveries dated before the period.')
    one = sub.add_parser('erase-order', help="Erase one delivery's personal data and the calculations that copied it.")
    one.add_argument('--external-id', required=True)
    customer = sub.add_parser('erase-customer', help="Erase one customer's personal data and that of the deliveries naming it.")
    customer.add_argument('--code', required=True)
    account = sub.add_parser('erase-user', help="Erase one account's names, disable it and end its sessions.")
    account.add_argument('--username', required=True)
    for command in (old, one, customer, account):
        command.add_argument('--dry-run', action='store_true', help='Report counts without changing anything.')
    args = parser.parse_args()
    settings = DatabaseAdminSettings()
    engine = make_engine(settings.migration_settings(), statement_timeout_ms=0)
    try:
        with Session(engine) as session, session.begin():
            owner = settings.demo_organization_id
            if args.action == 'purge':
                counts = purge(session, owner, args.older_than_days, orders=args.orders, dry_run=args.dry_run)
            elif args.action == 'erase-order':
                counts = erase_order(session, owner, args.external_id, dry_run=args.dry_run)
            elif args.action == 'erase-customer':
                counts = erase_customer(session, owner, args.code, dry_run=args.dry_run)
            else:
                counts = erase_user(session, owner, args.username, dry_run=args.dry_run)
    except DataError as error:
        raise SystemExit(f'{error.code}: {error.message}') from None
    except Exception as error:
        # Driver errors can include connection details or record values.
        raise SystemExit(f'Retention command failed ({type(error).__name__}); nothing was changed.') from None
    finally:
        engine.dispose()
    print(json.dumps({'dry_run': args.dry_run, **counts}, sort_keys=True))


if __name__ == '__main__':
    main()
