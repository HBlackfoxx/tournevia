"""Distinguish existing demonstration workspaces from newly configured companies."""
from alembic import op
import sqlalchemy as sa

revision = '0006_onboarding'
down_revision = '0005_accounts'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('organizations', sa.Column('is_demo', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT EXISTS(SELECT 1 FROM organizations WHERE NOT is_demo)')):
        raise RuntimeError('Restore a compatible backup rather than relabel company data as synthetic.')
    op.drop_column('organizations', 'is_demo')
