"""assignment_question_chunks.rubric_criteria for per-question rubric routing

Revision ID: g7h8i9j0k1l2
Revises: f6a7b8c9d0e1
Create Date: 2026-08-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "g7h8i9j0k1l2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("assignment_question_chunks")}
    # Earlier draft of this revision added ``rubric_criteria``; a later draft renamed it to
    # ``rubric_criterion_ids`` in the migration file only. Prefer the JSON criteria column.
    if "rubric_criterion_ids" in cols and "rubric_criteria" not in cols:
        op.alter_column(
            "assignment_question_chunks",
            "rubric_criterion_ids",
            new_column_name="rubric_criteria",
        )
    elif "rubric_criteria" not in cols:
        op.add_column(
            "assignment_question_chunks",
            sa.Column("rubric_criteria", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in inspect(bind).get_columns("assignment_question_chunks")}
    if "rubric_criteria" in cols:
        op.drop_column("assignment_question_chunks", "rubric_criteria")
