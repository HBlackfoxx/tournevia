"""Saved customers: a reviewed entrance and the usual needs of a company's recurring deliveries.

Replacing a sample workspace also deletes the customers saved in it.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0013_customers'
down_revision = '0012_weekly_hours'
branch_labels = None
depends_on = None

WITH_CUSTOMERS = """CREATE OR REPLACE FUNCTION public.start_company_from_sample(owner uuid, company text) RETURNS void
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
    -- The fictional depot stays only as a placeholder to move: unreviewed, it blocks calculations.
    UPDATE depots SET code = 'MAIN', name = 'Main depot', address = '', reviewed = false, version = version + 1
        WHERE organization_id = owner;
    UPDATE organizations SET name = btrim(company), is_demo = false WHERE id = owner;
END
$$"""
WITHOUT_CUSTOMERS = """CREATE OR REPLACE FUNCTION public.start_company_from_sample(owner uuid, company text) RETURNS void
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
    UPDATE depots SET code = 'MAIN', name = 'Main depot', address = '', reviewed = false, version = version + 1
        WHERE organization_id = owner;
    UPDATE organizations SET name = btrim(company), is_demo = false WHERE id = owner;
END
$$"""


def upgrade():
    op.create_table(
        'customers',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('organization_id', sa.Uuid(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('identifier', sa.String(40), nullable=False),
        sa.Column('data', JSONB(), nullable=False),
        sa.UniqueConstraint('organization_id', 'identifier', name='uq_customers_identifier'),
        sa.CheckConstraint('version >= 1', name='ck_customers_version'),
    )
    op.execute(WITH_CUSTOMERS)


def downgrade():
    op.execute(WITHOUT_CUSTOMERS)
    op.drop_table('customers')
