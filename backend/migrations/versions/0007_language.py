"""Persist each named user's interface language."""
from alembic import op
import sqlalchemy as sa

revision = '0007_language'
down_revision = '0006_onboarding'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('language', sa.String(2), nullable=False, server_default='en'))
    op.create_check_constraint('ck_users_language', 'users', "language IN ('en','fr')")


def downgrade():
    op.drop_constraint('ck_users_language', 'users', type_='check')
    op.drop_column('users', 'language')
