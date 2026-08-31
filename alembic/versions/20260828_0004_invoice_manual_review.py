"""Preserve compatibility with production databases already at this revision.

Revision ID: 20260828_0004
Revises: 20260826_0003
"""

import sqlalchemy as sa

from alembic import op

revision = "20260828_0004"
down_revision = "20260826_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("invoice_jobs") as batch_op:
        batch_op.add_column(sa.Column("final_file_ref", sa.String(512), nullable=True))
        batch_op.add_column(sa.Column("review_json", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("review_revision", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("finalizing", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("finalized_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("invoice_jobs") as batch_op:
        batch_op.drop_column("finalized_at")
        batch_op.drop_column("finalizing")
        batch_op.drop_column("review_revision")
        batch_op.drop_column("review_json")
        batch_op.drop_column("final_file_ref")
