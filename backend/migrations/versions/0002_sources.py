"""Owned fleet and delivery inputs with unique business identifiers."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0002_sources'
down_revision = '0001_foundation'
branch_labels = None
depends_on = None


def upgrade():
    for table, length in [('vehicles', 40), ('orders', 100)]:
        columns = [sa.Column('id', sa.Uuid(), nullable=False),
                   sa.Column('organization_id', sa.Uuid(), nullable=False),
                   sa.Column('version', sa.Integer(), nullable=False),
                   sa.Column('identifier', sa.String(length), nullable=False),
                   sa.Column('data', JSONB(), nullable=False)]
        if table == 'orders':
            columns.append(sa.Column('service_date', sa.Date(), nullable=False))
        op.create_table(table, *columns, sa.PrimaryKeyConstraint('id'),
                        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
                        sa.CheckConstraint('version >= 1', name=f'ck_{table}_version'),
                        sa.UniqueConstraint('organization_id', 'identifier', name=f'uq_{table}_identifier'))
    op.create_index('ix_orders_day', 'orders', ['organization_id', 'service_date', 'id'])


def downgrade():
    op.drop_table('orders')
    op.drop_table('vehicles')
