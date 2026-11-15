"""Several depots and places per company, one of them the main depot.

A place (kind PLACE) is another start or end of a vehicle's day, such as a driver's home.
Vehicles name where they start and end (`start`, `end`) and deliveries the depot they ship
from (`ships_from`), by code. The runtime role deletes a depot or place only through one
function, which refuses the main depot and any that a vehicle or delivery still names.
Replacing a sample workspace also deletes every depot and place but the main one.
"""
from alembic import op
import sqlalchemy as sa

revision = '0015_depots'
down_revision = '0014_delete_orders'
branch_labels = None
depends_on = None

START_COMPANY = """CREATE OR REPLACE FUNCTION public.start_company_from_sample(owner uuid, company text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
BEGIN
    IF company IS NULL OR length(btrim(company)) NOT BETWEEN 1 AND 120 THEN
        RAISE EXCEPTION 'company name must be 1-120 characters' USING ERRCODE = '22023';
    END IF;
    PERFORM 1 FROM organizations WHERE id = owner AND is_demo FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'not a sample workspace' USING ERRCODE = '55000';
    END IF;
    IF EXISTS (SELECT 1 FROM planning_jobs WHERE organization_id = owner AND status IN ('QUEUED', 'RUNNING')) THEN
        RAISE EXCEPTION 'a calculation is waiting or running' USING ERRCODE = '55006';
    END IF;
    -- Calculations reference plans; deliveries and vehicles are referenced by neither.
    DELETE FROM planning_jobs WHERE organization_id = owner;
    DELETE FROM plans WHERE organization_id = owner;
    DELETE FROM orders WHERE organization_id = owner;
    DELETE FROM vehicles WHERE organization_id = owner;
    DELETE FROM customers WHERE organization_id = owner;
    DELETE FROM depots WHERE organization_id = owner AND NOT main;
    -- The fictional depot stays only as a placeholder to move: unreviewed, it blocks calculations.
    UPDATE depots SET code = 'MAIN', name = 'Main depot', address = '', reviewed = false, version = version + 1
        WHERE organization_id = owner;
    UPDATE organizations SET name = btrim(company), is_demo = false WHERE id = owner;
END
$$"""

DELETE_DEPOT = """CREATE FUNCTION public.delete_depot(owner uuid, key uuid) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE place text;
DECLARE removed integer;
BEGIN
    SELECT code INTO place FROM depots WHERE organization_id = owner AND id = key AND NOT main FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no such depot, or it is the main depot' USING ERRCODE = '55000';
    END IF;
    IF EXISTS (SELECT 1 FROM vehicles WHERE organization_id = owner AND (data ->> 'start' = place OR data ->> 'end' = place))
            OR EXISTS (SELECT 1 FROM orders WHERE organization_id = owner AND data ->> 'ships_from' = place) THEN
        RAISE EXCEPTION 'vehicles or deliveries still name this depot' USING ERRCODE = '55006';
    END IF;
    DELETE FROM depots WHERE organization_id = owner AND id = key;
    GET DIAGNOSTICS removed = ROW_COUNT;
    RETURN removed;
END
$$"""

# As before this migration (0013): without deleting other depots.
PREVIOUS_START_COMPANY = START_COMPANY.replace("    DELETE FROM depots WHERE organization_id = owner AND NOT main;\n", '')

NOT_DOWNGRADABLE = """DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM depots WHERE NOT main)
            OR EXISTS (SELECT 1 FROM vehicles WHERE jsonb_typeof(data -> 'start') = 'string' OR jsonb_typeof(data -> 'end') = 'string')
            OR EXISTS (SELECT 1 FROM orders WHERE jsonb_typeof(data -> 'ships_from') = 'string') THEN
        RAISE EXCEPTION 'other depots or places are in use; remove them before downgrading' USING ERRCODE = '55000';
    END IF;
END
$$"""


def upgrade():
    op.add_column('depots', sa.Column('kind', sa.String(16), nullable=False, server_default='DEPOT'))
    # Every existing depot is its company's only one, so its main depot.
    op.add_column('depots', sa.Column('main', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.alter_column('depots', 'main', server_default=sa.false())
    op.drop_constraint('uq_depots_organization', 'depots', type_='unique')
    op.create_unique_constraint('uq_depots_code', 'depots', ['organization_id', 'code'])
    op.create_index('uq_depots_main', 'depots', ['organization_id'], unique=True, postgresql_where=sa.text('main'))
    op.create_check_constraint('ck_depots_kind', 'depots', "kind IN ('DEPOT', 'PLACE')")
    op.create_check_constraint('ck_depots_main_kind', 'depots', "NOT main OR kind = 'DEPOT'")
    op.execute(START_COMPANY)
    op.execute(DELETE_DEPOT)


def downgrade():
    op.execute(NOT_DOWNGRADABLE)
    # Older code refuses unknown fields: drop the empty references it does not know.
    op.execute("UPDATE vehicles SET data = data - 'start' - 'end' WHERE data ? 'start' OR data ? 'end'")
    op.execute("UPDATE orders SET data = data - 'ships_from' WHERE data ? 'ships_from'")
    op.execute('DROP FUNCTION public.delete_depot(uuid, uuid)')
    op.execute(PREVIOUS_START_COMPANY)
    op.drop_constraint('ck_depots_main_kind', 'depots', type_='check')
    op.drop_constraint('ck_depots_kind', 'depots', type_='check')
    op.drop_index('uq_depots_main', 'depots')
    op.drop_constraint('uq_depots_code', 'depots', type_='unique')
    op.create_unique_constraint('uq_depots_organization', 'depots', ['organization_id'])
    op.drop_column('depots', 'main')
    op.drop_column('depots', 'kind')
