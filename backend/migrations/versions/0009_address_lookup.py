"""Let an administrator turn on address lookup with IGN's public service; off for every company."""
from alembic import op
import sqlalchemy as sa

revision = '0009_address_lookup'
down_revision = '0008_start_company'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('organizations', sa.Column('public_address_lookup', sa.Boolean(), nullable=False,
                                             server_default=sa.false()))


def downgrade():
    op.drop_column('organizations', 'public_address_lookup')
