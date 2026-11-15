"""Example records instead of a sample mode.

Depots, vehicles and deliveries carry `example`: fictional records a company adds to try the
planner, planned only with other examples and removed together. A sample workspace created by
first-use setup (one with a user account) becomes an ordinary company workspace whose vehicles
and deliveries are examples; its main depot stays. The legacy local demonstration, which has no
user account, keeps its sample mode. The runtime role removes examples only through one
function, which replaces the sample's `start_company_from_sample`.
"""
import importlib.util
from pathlib import Path

from alembic import op
import sqlalchemy as sa

revision = '0016_examples'
down_revision = '0015_depots'
branch_labels = None
depends_on = None

SETUP_SAMPLES = 'SELECT o.id FROM organizations o WHERE o.is_demo AND EXISTS (SELECT 1 FROM users u WHERE u.organization_id = o.id)'

REMOVE_EXAMPLES = """CREATE FUNCTION public.remove_examples(owner uuid) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE removed integer := 0;
DECLARE counted integer;
DECLARE plans_of uuid[];
BEGIN
    PERFORM 1 FROM organizations WHERE id = owner FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no such company' USING ERRCODE = '55000';
    END IF;
    IF EXISTS (SELECT 1 FROM planning_jobs WHERE organization_id = owner AND status IN ('QUEUED', 'RUNNING')) THEN
        RAISE EXCEPTION 'a calculation is waiting or running' USING ERRCODE = '55006';
    END IF;
    -- The company's own records never name an example depot (every write checks it).
    IF EXISTS (SELECT 1 FROM vehicles v JOIN depots d ON d.organization_id = owner AND d.example
                   AND d.code IN (v.data ->> 'start', v.data ->> 'end')
               WHERE v.organization_id = owner AND NOT v.example)
            OR EXISTS (SELECT 1 FROM orders o JOIN depots d ON d.organization_id = owner AND d.example
                   AND d.code = o.data ->> 'ships_from'
               WHERE o.organization_id = owner AND NOT o.example) THEN
        RAISE EXCEPTION 'own records name an example depot' USING ERRCODE = '55006';
    END IF;
    -- Plans of example vehicles, and the calculations of those plans or vehicles (they reference plans).
    SELECT COALESCE(array_agg(p.id), '{}') INTO plans_of FROM plans p WHERE p.organization_id = owner AND EXISTS (
        SELECT 1 FROM jsonb_array_elements(p.input_data -> 'vehicles') v
        JOIN vehicles x ON x.organization_id = owner AND x.example AND x.id = (v ->> 'id')::uuid);
    DELETE FROM planning_jobs j WHERE j.organization_id = owner AND (
        j.plan_id = ANY (plans_of) OR j.target_plan_id = ANY (plans_of)
        OR EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE(j.request_data -> 'vehicle_ids', '[]'::jsonb)) k
                   JOIN vehicles x ON x.organization_id = owner AND x.example AND x.id = k::uuid));
    DELETE FROM plans WHERE organization_id = owner AND id = ANY (plans_of);
    DELETE FROM orders WHERE organization_id = owner AND example;
    GET DIAGNOSTICS counted = ROW_COUNT;
    removed := removed + counted;
    DELETE FROM vehicles WHERE organization_id = owner AND example;
    GET DIAGNOSTICS counted = ROW_COUNT;
    removed := removed + counted;
    DELETE FROM depots WHERE organization_id = owner AND example AND NOT main;
    GET DIAGNOSTICS counted = ROW_COUNT;
    RETURN removed + counted;
END
$$"""

NOT_DOWNGRADABLE = """DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM depots WHERE example) OR EXISTS (SELECT 1 FROM vehicles WHERE example)
            OR EXISTS (SELECT 1 FROM orders WHERE example) THEN
        RAISE EXCEPTION 'example records exist; remove them before downgrading' USING ERRCODE = '55000';
    END IF;
END
$$"""


def previous_start_company() -> str:
    """The sample-replacing function as migration 0015 left it."""
    spec = importlib.util.spec_from_file_location('depots_0015', Path(__file__).with_name('0015_depots.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.START_COMPANY


def upgrade():
    for table in ('depots', 'vehicles', 'orders'):
        op.add_column(table, sa.Column('example', sa.Boolean(), nullable=False, server_default=sa.false()))
    # An example depot is never the main depot.
    op.create_check_constraint('ck_depots_example_main', 'depots', 'NOT (example AND main)')
    op.execute(f'UPDATE vehicles SET example = true WHERE organization_id IN ({SETUP_SAMPLES})')
    op.execute(f'UPDATE orders SET example = true WHERE organization_id IN ({SETUP_SAMPLES})')
    op.execute(f'UPDATE organizations SET is_demo = false WHERE id IN ({SETUP_SAMPLES})')
    op.execute('DROP FUNCTION public.start_company_from_sample(uuid, text)')
    op.execute(REMOVE_EXAMPLES)


def downgrade():
    op.execute(NOT_DOWNGRADABLE)
    op.execute('DROP FUNCTION public.remove_examples(uuid)')
    op.execute(previous_start_company())
    op.drop_constraint('ck_depots_example_main', 'depots', type_='check')
    for table in ('orders', 'vehicles', 'depots'):
        op.drop_column(table, 'example')
