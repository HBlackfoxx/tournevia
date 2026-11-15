"""Organizations and one explicitly owned depot per organization."""
from alembic import op
import sqlalchemy as sa

revision = '0001_foundation'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('organizations',
                    sa.Column('id', sa.Uuid(), nullable=False),
                    sa.Column('name', sa.String(120), nullable=False),
                    sa.PrimaryKeyConstraint('id'))
    op.create_table('depots',
                    sa.Column('id', sa.Uuid(), nullable=False),
                    sa.Column('organization_id', sa.Uuid(), nullable=False),
                    sa.Column('version', sa.Integer(), nullable=False),
                    sa.Column('code', sa.String(40), nullable=False),
                    sa.Column('name', sa.String(120), nullable=False),
                    sa.Column('latitude', sa.Float(), nullable=False),
                    sa.Column('longitude', sa.Float(), nullable=False),
                    sa.Column('reviewed', sa.Boolean(), nullable=False),
                    sa.Column('timezone', sa.String(40), nullable=False),
                    sa.CheckConstraint('version >= 1', name='ck_depots_version'),
                    sa.CheckConstraint('latitude BETWEEN -90 AND 90', name='ck_depots_latitude'),
                    sa.CheckConstraint('longitude BETWEEN -180 AND 180', name='ck_depots_longitude'),
                    sa.CheckConstraint("timezone = 'Europe/Paris'", name='ck_depots_timezone'),
                    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
                    sa.PrimaryKeyConstraint('id'),
                    sa.UniqueConstraint('organization_id', name='uq_depots_organization'))


def downgrade() -> None:
    op.drop_table('depots')
    op.drop_table('organizations')
