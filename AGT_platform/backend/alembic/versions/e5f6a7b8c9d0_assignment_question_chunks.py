"""assignment_question_chunks for editable Q&A pairs from Assignment Creation

Revision ID: e5f6a7b8c9d0
Revises: b1c2d3e4f5a6
Create Date: 2026-07-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assignment_question_chunks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("assignment_id", sa.Integer(), nullable=False),
        sa.Column("question_id", sa.String(length=120), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("is_edited", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["assignment_id"], ["assignments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assignment_question_chunks_assignment_id",
        "assignment_question_chunks",
        ["assignment_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_assignment_question_chunks_assignment_id",
        table_name="assignment_question_chunks",
    )
    op.drop_table("assignment_question_chunks")
