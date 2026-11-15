"""A company's optional format for new delivery identifiers; none until an administrator sets one."""
from alembic import op
import sqlalchemy as sa

revision = '0010_order_numbers'
down_revision = '0009_address_lookup'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('organizations', sa.Column('order_number_format', sa.String(40), nullable=True))
    op.add_column('organizations', sa.Column('order_number_start', sa.Integer(), nullable=False, server_default='1'))
    op.create_check_constraint('ck_organizations_order_number_start', 'organizations', 'order_number_start >= 1')


def downgrade():
    op.drop_constraint('ck_organizations_order_number_start', 'organizations', type_='check')
    op.drop_column('organizations', 'order_number_start')
    op.drop_column('organizations', 'order_number_format')
