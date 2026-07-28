"""assignments.blank_assignment_text for the Assignment Creation review page

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("assignments", sa.Column("blank_assignment_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("assignments", "blank_assignment_text")
