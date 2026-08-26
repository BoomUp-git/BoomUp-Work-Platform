"""Add Phase 1E invoice processing audit jobs.

Revision ID: 20260826_0002
Revises: 20260825_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260826_0002"
down_revision: str | None = "20260825_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    status = sa.Enum("PROCESSING", "SUCCESS", "MANUAL_REVIEW", "FAILED", name="invoicejobstatus")
    op.create_table(
        "invoice_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("operator_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("original_file_ref", sa.String(512), nullable=False),
        sa.Column("output_file_ref", sa.String(512), nullable=True),
        sa.Column("invoice_number", sa.String(100), nullable=True),
        sa.Column("customer", sa.String(255), nullable=True),
        sa.Column("invoice_date", sa.Date(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("price_retrieved_at", sa.DateTime(), nullable=True),
        sa.Column("rule_engine_version", sa.String(64), nullable=False),
        sa.Column("platform_version", sa.String(64), nullable=False),
        sa.Column("status", status, nullable=False),
        sa.Column("manual_review", sa.Boolean(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("safe_error", sa.String(500), nullable=True),
        sa.Column("retention_expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_invoice_jobs_operator_id", "invoice_jobs", ["operator_id"])
    op.create_index("ix_invoice_jobs_invoice_number", "invoice_jobs", ["invoice_number"])
    op.create_index("ix_invoice_jobs_status", "invoice_jobs", ["status"])
    op.create_index(
        "ix_invoice_jobs_retention_expires_at", "invoice_jobs", ["retention_expires_at"]
    )


def downgrade() -> None:
    op.drop_table("invoice_jobs")
