"""A company's safety margin: calculations plan every delivery this many minutes before its window ends."""
from alembic import op
import sqlalchemy as sa

revision = '0017_safety_margin'
down_revision = '0016_examples'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('organizations', sa.Column('safety_margin_minutes', sa.Integer(), nullable=False, server_default='15'))
    op.create_check_constraint('ck_organizations_safety_margin', 'organizations',
                               'safety_margin_minutes BETWEEN 0 AND 120')


def downgrade():
    op.drop_constraint('ck_organizations_safety_margin', 'organizations', type_='check')
    op.drop_column('organizations', 'safety_margin_minutes')
