"""Let a person delete deliveries, through one function the runtime role may call.

The runtime role still cannot delete records itself. This function, owned by the migration role,
deletes only the given deliveries of one company, and none while a waiting or running
calculation uses them. Saved plans keep their own copy of each delivery.
"""
from alembic import op

revision = '0014_delete_orders'
down_revision = '0013_customers'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
CREATE FUNCTION public.delete_orders(owner uuid, keys uuid[]) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE removed integer;
BEGIN
    IF EXISTS (
        SELECT 1 FROM planning_jobs j
        CROSS JOIN LATERAL jsonb_array_elements(
            CASE WHEN jsonb_typeof(j.input_data -> 'orders') = 'array' THEN j.input_data -> 'orders' ELSE '[]'::jsonb END) o
        WHERE j.organization_id = owner AND j.status IN ('QUEUED', 'RUNNING') AND (o ->> 'id')::uuid = ANY(keys)
    ) THEN
        RAISE EXCEPTION 'a calculation that uses these deliveries is waiting or running' USING ERRCODE = '55006';
    END IF;
    DELETE FROM orders WHERE organization_id = owner AND id = ANY(keys);
    GET DIAGNOSTICS removed = ROW_COUNT;
    RETURN removed;
END
$$""")


def downgrade():
    op.execute('DROP FUNCTION public.delete_orders(uuid, uuid[])')
