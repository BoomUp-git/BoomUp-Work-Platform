"""Allow public invoice jobs without a platform user.

Revision ID: 20260826_0003
Revises: 20260826_0002
"""

import sqlalchemy as sa

from alembic import op

revision = "20260826_0003"
down_revision = "20260826_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("invoice_jobs") as batch_op:
        batch_op.alter_column(
            "operator_id", existing_type=sa.String(length=36), nullable=True
        )


def downgrade() -> None:
    with op.batch_alter_table("invoice_jobs") as batch_op:
        batch_op.alter_column(
            "operator_id", existing_type=sa.String(length=36), nullable=False
        )
