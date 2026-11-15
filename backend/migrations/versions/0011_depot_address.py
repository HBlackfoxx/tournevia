"""Keep the depot's postal address beside its reviewed entrance point.

Replacing the sample also clears the sample depot's address: the placeholder depot has none.
"""
from alembic import op
import sqlalchemy as sa

revision = '0011_depot_address'
down_revision = '0010_order_numbers'
branch_labels = None
depends_on = None

FUNCTION = """CREATE OR REPLACE FUNCTION public.start_company_from_sample(owner uuid, company text) RETURNS void
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
    -- The fictional depot stays only as a placeholder to move: unreviewed, it blocks calculations.
    UPDATE depots SET code = 'MAIN', name = 'Main depot', {address}reviewed = false, version = version + 1
        WHERE organization_id = owner;
    UPDATE organizations SET name = btrim(company), is_demo = false WHERE id = owner;
END
$$"""


def upgrade():
    op.add_column('depots', sa.Column('address', sa.String(500), nullable=False, server_default=''))
    op.execute(FUNCTION.replace('{address}', "address = '', "))


def downgrade():
    op.execute(FUNCTION.replace('{address}', ''))
    op.drop_column('depots', 'address')
