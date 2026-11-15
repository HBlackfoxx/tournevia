"""Operator retention purge and per-delivery erasure (run with the migration role in production)."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.jobs import Job, OptimizationRequest, Plan, fail_job, now, save_result
from app.records import DataError, Order
from app.retention import ERASED, erase_customer, erase_order, erase_user, purge
from test_edits import edit_job, saved_plan
from app.worker import Worker
from test_jobs import empty_result, enqueue, seed

pytestmark = pytest.mark.postgres


def age(database, plan_id, days):
    engine, _, _ = database
    with Session(engine) as session, session.begin():
        plan = session.get(Plan, plan_id)
        plan.updated_at = now() - timedelta(days=days)
        for job in session.scalars(select(Job).where((Job.plan_id == plan_id) | (Job.target_plan_id == plan_id))):
            job.created_at = job.started_at = job.finished_at = now() - timedelta(days=days)


def run(database, action, *args, **options):
    engine, _, settings = database
    with Session(engine) as session, session.begin():
        return action(session, settings.demo_organization_id, *args, **options)


def count(database, table):
    engine, _, _ = database
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(table))


def another_plan(database, request, key):
    """A second saved plan from the already seeded records (saved_plan seeds them)."""
    _, _, settings = database
    enqueue(database, request, key)
    worker = Worker(settings)
    try:
        worker.acquire()
        job_id, snapshot = worker.claim()
        with worker.transaction() as session:
            return save_result(session, job_id, empty_result(snapshot)), snapshot
    finally:
        worker.close()


def test_purge_removes_old_plans_and_finished_jobs_but_keeps_recent_and_busy_work(database):
    engine, _, _ = database
    old, snapshot, edit = saved_plan(database)
    age(database, old, 100)
    request = OptimizationRequest(service_date=snapshot.service_date, vehicle_ids=[v.id for v in snapshot.vehicles],
                                  order_ids=[o.id for o in snapshot.orders], routing_policy=snapshot.routing_policy,
                                  objective=snapshot.objective, solver_time_limit_seconds=1)
    busy, _ = another_plan(database, request, 'busy')
    age(database, busy, 100)
    edit_job(database, busy, edit)  # A waiting edit protects its plan.
    recent, _ = another_plan(database, request, 'recent')
    failed = enqueue(database, request, 'failed-old')
    with Session(engine) as session, session.begin():
        row = session.get(Job, failed.id)
        fail_job(row, 'ENGINE_FAILED', 'Old failure with copied delivery data.')
        row.finished_at = now() - timedelta(days=100)
    plans, jobs = count(database, Plan), count(database, Job)
    # The old plan with its optimization job, and the old failed job that still holds delivery data.
    assert run(database, purge, 90, dry_run=True) == {'plans': 1, 'jobs': 2, 'orders': 0}
    assert (count(database, Plan), count(database, Job)) == (plans, jobs)  # A dry run changes nothing.
    assert run(database, purge, 90) == {'plans': 1, 'jobs': 2, 'orders': 0}
    assert (count(database, Plan), count(database, Job)) == (plans - 1, jobs - 2)
    with Session(engine) as session:
        assert session.get(Plan, old) is None and session.get(Job, failed.id) is None
        assert session.get(Plan, busy) is not None and session.get(Plan, recent) is not None
    with pytest.raises(DataError):
        run(database, purge, 0)


def test_purge_can_also_delete_deliveries_dated_before_the_period(database):
    seed(database)
    total = count(database, Order)
    assert run(database, purge, 10000, orders=True)['orders'] == 0
    assert run(database, purge, 1, orders=True)['orders'] == total == 10  # The fixture day is in the past.
    assert count(database, Order) == 0


def test_erasing_a_delivery_anonymizes_it_and_removes_calculations_that_copied_it(database):
    engine, _, _ = database
    plan_id, snapshot, _ = saved_plan(database)
    target = snapshot.orders[0]
    assert run(database, erase_order, target.data.external_id, dry_run=True) == {'plans': 1, 'jobs': 1, 'orders': 1}
    with Session(engine) as session:
        assert session.get(Plan, plan_id) is not None
    assert run(database, erase_order, target.data.external_id)['plans'] == 1
    with Session(engine) as session:
        assert session.get(Plan, plan_id) is None
        order = session.get(Order, target.id)
        assert (order.data['address'], order.data['instructions'], order.data['location'], order.data['active']) == (ERASED, '', None, False)
        assert order.version == target.version + 1
        others = session.scalars(select(Order).where(Order.id != target.id)).all()
        assert all(o.data['address'] != ERASED for o in others)
    with pytest.raises(DataError) as error:
        run(database, erase_order, 'NO-SUCH-ORDER')
    assert error.value.status == 404


def test_erasure_waits_for_calculations_that_include_the_delivery(database):
    request = seed(database)
    enqueue(database, request, 'queued')
    engine, _, _ = database
    with Session(engine) as session:
        external_id = session.get(Order, request.order_ids[0]).identifier
    with pytest.raises(DataError) as error:
        run(database, erase_order, external_id)
    assert error.value.code == 'JOB_ACTIVE'


def test_a_delivery_deleted_in_the_application_can_still_be_erased_from_the_calculations_that_copied_it(database):
    from app.records import Versioned, delete_orders
    engine, _, _ = database
    plan_id, snapshot, _ = saved_plan(database)
    target = snapshot.orders[0]
    run(database, delete_orders, [Versioned(id=target.id, version=target.version)])
    assert run(database, erase_order, target.data.external_id) == {'plans': 1, 'jobs': 1, 'orders': 0}
    with Session(engine) as session:
        assert session.get(Plan, plan_id) is None
    with pytest.raises(DataError) as error:  # Nothing is left to erase.
        run(database, erase_order, target.data.external_id)
    assert error.value.status == 404


def test_erasure_waits_for_an_edit_of_a_plan_that_copied_the_delivery(database):
    engine, _, _ = database
    plan_id, snapshot, edit = saved_plan(database)
    waiting = edit_job(database, plan_id, edit)  # An edit carries no copy of the deliveries itself.
    with pytest.raises(DataError) as error:
        run(database, erase_order, snapshot.orders[0].data.external_id)
    assert error.value.code == 'JOB_ACTIVE'
    with Session(engine) as session:
        assert session.get(Plan, plan_id) is not None and session.get(Job, waiting.id).status == 'QUEUED'


def test_erasing_a_customer_erases_it_and_the_deliveries_naming_it(database):
    from app.planning import CustomerInput
    from app.records import Customer, write_record
    engine, _, settings = database
    plan_id, snapshot, _ = saved_plan(database)
    with Session(engine) as session, session.begin():
        write_record(session, settings.demo_organization_id, CustomerInput(code='CLI-1', name='Boulangerie Fictive', address='5 Rue Fictive'))
        named = session.get(Order, snapshot.orders[1].id)
        named.data = {**named.data, 'customer': 'CLI-1'}
    assert run(database, erase_customer, 'CLI-1', dry_run=True) == {'customers': 1, 'orders': 1, 'plans': 1, 'jobs': 1}
    assert run(database, erase_customer, 'CLI-1') == {'customers': 1, 'orders': 1, 'plans': 1, 'jobs': 1}
    with Session(engine) as session:
        customer = session.scalar(select(Customer).where(Customer.identifier == 'CLI-1'))
        assert (customer.data['name'], customer.data['address'], customer.data['location'], customer.data['active']) == (ERASED, ERASED, None, False)
        assert session.get(Order, snapshot.orders[1].id).data['address'] == ERASED
        assert session.get(Order, snapshot.orders[0].id).data['address'] != ERASED
        assert session.get(Plan, plan_id) is None
    with pytest.raises(DataError):
        run(database, erase_customer, 'NO-SUCH-CUSTOMER')


def test_erasing_an_account_removes_its_names_and_sessions_but_keeps_an_administrator(database):
    from app.accounts import LoginSession, User
    from app.auth import CreateUser, create_user
    engine, _, settings = database
    seed(database)
    password = 'correct horse battery staple'
    with Session(engine) as session, session.begin():
        admin = create_user(session, settings.demo_organization_id, CreateUser(username='admin', password=password, display_name='Admin', role='admin'))
        planner = create_user(session, settings.demo_organization_id, CreateUser(username='j.martin', password=password, display_name='Jeanne Martin'))
        session.add(LoginSession(token_hash='a' * 64, user_id=planner.id, expires_at=now() + timedelta(hours=1), last_seen_at=now()))
    assert run(database, erase_user, 'j.martin') == {'users': 1}
    with Session(engine) as session:
        user = session.get(User, planner.id)
        assert (user.username, user.display_name, user.active) == (f'erased-{planner.id.hex}', ERASED, False)
        assert session.scalar(select(func.count()).select_from(LoginSession).where(LoginSession.user_id == planner.id)) == 0
    with pytest.raises(DataError) as error:
        run(database, erase_user, 'admin')
    assert error.value.code == 'LAST_ADMIN'
    with Session(engine) as session:
        assert session.get(User, admin.id).username == 'admin'
