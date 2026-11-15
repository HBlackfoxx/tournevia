"""Let an administrator replace the fictional sample with their company.

The runtime role cannot delete deliveries, vehicles or calculations. This function, owned by
the migration role, is the one way it can: only for a sample workspace, never while a
calculation waits or runs. setup_database grants the runtime role EXECUTE on it alone.
"""
from alembic import op

revision = '0008_start_company'
down_revision = '0007_language'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE FUNCTION public.start_company_from_sample(owner uuid, company text) RETURNS void
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
    UPDATE depots SET code = 'MAIN', name = 'Main depot', reviewed = false, version = version + 1
        WHERE organization_id = owner;
    UPDATE organizations SET name = btrim(company), is_demo = false WHERE id = owner;
END
$$""")
    op.execute('REVOKE ALL ON FUNCTION public.start_company_from_sample(uuid, text) FROM PUBLIC')


def downgrade():
    op.execute('DROP FUNCTION public.start_company_from_sample(uuid, text)')
