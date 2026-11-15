"""Same-plan edit reservations and readiness gates; preserve existing drafts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision='0004_edits'
down_revision='0003_jobs'
branch_labels=None
depends_on=None

STATE="(status = 'QUEUED' AND stage = 'QUEUED' AND started_at IS NULL AND finished_at IS NULL AND plan_id IS NULL AND error IS NULL) OR (status = 'RUNNING' AND stage IN ('PRECHECK','MATRIX','SOLVE','ROUTE','VALIDATE','SAVE') AND started_at IS NOT NULL AND finished_at IS NULL AND plan_id IS NULL AND error IS NULL) OR (status = 'SUCCEEDED' AND stage = 'DONE' AND started_at IS NOT NULL AND finished_at IS NOT NULL AND plan_id IS NOT NULL AND error IS NULL AND input_data IS NULL) OR (status = 'FAILED' AND stage = 'FAILED' AND finished_at IS NOT NULL AND plan_id IS NULL AND error IS NOT NULL)"


def upgrade():
    op.add_column('plans',sa.Column('validation',sa.String(16),nullable=True))
    op.add_column('plans',sa.Column('result_stale',sa.Boolean(),nullable=True))
    op.add_column('plans',sa.Column('active_job_id',sa.Uuid(),nullable=True))
    op.add_column('plans',sa.Column('finalized_at',sa.DateTime(timezone=True),nullable=True))
    op.add_column('plans',sa.Column('edit_error',postgresql.JSONB(none_as_null=True),nullable=True))
    op.execute("UPDATE plans SET validation = result->>'validation', result_stale = false")
    op.alter_column('plans','validation',nullable=False)
    op.alter_column('plans','result_stale',nullable=False)
    op.drop_constraint('ck_plans_status','plans',type_='check')
    op.create_check_constraint('ck_plans_status','plans',"status IN ('DRAFT','READY')")
    op.create_check_constraint('ck_plans_edit_state','plans',"(validation = 'PENDING' AND active_job_id IS NOT NULL AND result_stale) OR (validation IN ('PASS','FAIL') AND active_job_id IS NULL)")
    op.create_check_constraint('ck_plans_ready','plans',"(status = 'DRAFT' AND finalized_at IS NULL) OR (status = 'READY' AND finalized_at IS NOT NULL AND validation = 'PASS' AND NOT result_stale)")
    op.add_column('planning_jobs',sa.Column('operation_scope',sa.String(50),nullable=True))
    op.add_column('planning_jobs',sa.Column('target_plan_id',sa.Uuid(),nullable=True))
    op.add_column('planning_jobs',sa.Column('reserved_version',sa.Integer(),nullable=True))
    op.execute("UPDATE planning_jobs SET operation_scope = 'OPTIMIZE'")
    op.alter_column('planning_jobs','operation_scope',nullable=False)
    op.drop_constraint('uq_jobs_replay','planning_jobs',type_='unique')
    op.create_unique_constraint('uq_jobs_replay','planning_jobs',['organization_id','submitted_by','operation_scope','idempotency_key'])
    op.drop_constraint('uq_jobs_plan','planning_jobs',type_='unique')
    op.create_foreign_key('fk_jobs_target_owner','planning_jobs','plans',['target_plan_id','organization_id'],['id','organization_id'],ondelete='RESTRICT')
    op.drop_constraint('ck_jobs_kind','planning_jobs',type_='check')
    op.create_check_constraint('ck_jobs_kind','planning_jobs',"(kind = 'OPTIMIZE' AND target_plan_id IS NULL AND reserved_version IS NULL AND operation_scope = 'OPTIMIZE') OR (kind = 'EDIT' AND target_plan_id IS NOT NULL AND reserved_version >= 1)")
    op.drop_constraint('ck_jobs_state','planning_jobs',type_='check')
    op.create_check_constraint('ck_jobs_state','planning_jobs',STATE)
    op.create_check_constraint('ck_jobs_input','planning_jobs',"(kind = 'OPTIMIZE' AND ((status = 'SUCCEEDED' AND input_data IS NULL) OR (status <> 'SUCCEEDED' AND input_data IS NOT NULL))) OR (kind = 'EDIT' AND input_data IS NULL)")


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM planning_jobs WHERE kind='EDIT') OR EXISTS(SELECT 1 FROM plans WHERE status='READY')")):
        raise RuntimeError('Backout requires restoring a pre-edit backup; do not discard edits or ready plans.')
    op.drop_constraint('fk_jobs_target_owner','planning_jobs',type_='foreignkey')
    for name in ['ck_jobs_input','ck_jobs_kind','ck_jobs_state']:op.drop_constraint(name,'planning_jobs',type_='check')
    op.create_check_constraint('ck_jobs_kind','planning_jobs',"kind = 'OPTIMIZE'")
    old=STATE.replace('error IS NULL)', 'error IS NULL AND input_data IS NOT NULL)').replace('error IS NOT NULL)', 'error IS NOT NULL AND input_data IS NOT NULL)')
    op.create_check_constraint('ck_jobs_state','planning_jobs',old)
    op.drop_constraint('uq_jobs_replay','planning_jobs',type_='unique')
    op.create_unique_constraint('uq_jobs_replay','planning_jobs',['organization_id','submitted_by','kind','idempotency_key'])
    op.create_unique_constraint('uq_jobs_plan','planning_jobs',['plan_id'])
    for name in ['operation_scope','target_plan_id','reserved_version']:op.drop_column('planning_jobs',name)
    for name in ['ck_plans_status','ck_plans_edit_state','ck_plans_ready']:op.drop_constraint(name,'plans',type_='check')
    op.create_check_constraint('ck_plans_status','plans',"status = 'DRAFT'")
    for name in ['validation','result_stale','active_job_id','finalized_at','edit_error']:op.drop_column('plans',name)
