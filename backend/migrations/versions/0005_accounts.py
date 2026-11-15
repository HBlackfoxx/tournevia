"""Named users, revocable opaque sessions and bounded login throttles."""
from alembic import op
import sqlalchemy as sa

revision = '0005_accounts'
down_revision = '0004_edits'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('users',
        sa.Column('id', sa.Uuid(), primary_key=True),
        sa.Column('organization_id', sa.Uuid(), sa.ForeignKey('organizations.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('username', sa.String(80), nullable=False, unique=True),
        sa.Column('display_name', sa.String(120), nullable=False),
        sa.Column('password_hash', sa.String(255), nullable=False),
        sa.Column('role', sa.String(16), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.CheckConstraint("role IN ('admin','planner')", name='ck_users_role'))
    op.create_index('ix_users_organization_id', 'users', ['organization_id'])
    op.create_table('login_sessions',
        sa.Column('token_hash', sa.String(64), primary_key=True),
        sa.Column('user_id', sa.Uuid(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False))
    op.create_index('ix_login_sessions_user_id', 'login_sessions', ['user_id'])
    op.create_index('ix_login_sessions_expires_at', 'login_sessions', ['expires_at'])
    op.create_table('login_throttles',
        sa.Column('key', sa.String(64), primary_key=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT EXISTS(SELECT 1 FROM users)')):
        raise RuntimeError('Restore a pre-account backup instead of discarding named accounts.')
    op.drop_table('login_sessions')
    op.drop_table('login_throttles')
    op.drop_table('users')
