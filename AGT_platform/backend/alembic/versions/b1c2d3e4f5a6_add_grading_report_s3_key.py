"""add grading_report_s3_key to submissions and standalone_submissions

Revision ID: b1c2d3e4f5a6
Revises: 0a9fbf797fc1
Create Date: 2026-04-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "p0q1r2s3t4u5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "submissions",
        sa.Column("grading_report_s3_key", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "standalone_submissions",
        sa.Column("grading_report_s3_key", sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("standalone_submissions", "grading_report_s3_key")
    op.drop_column("submissions", "grading_report_s3_key")
