"""users password_hash institution timestamps; issued_jwts

Revision ID: 1b2c3d4e5f6a
Revises: 0a9fbf797fc1
Create Date: 2026-03-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1b2c3d4e5f6a"
down_revision: Union[str, None] = "0a9fbf797fc1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("institution_domain", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("first_login_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(), nullable=True))

    op.create_table(
        "issued_jwts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_issued_jwts_jti"), "issued_jwts", ["jti"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_issued_jwts_jti"), table_name="issued_jwts")
    op.drop_table("issued_jwts")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "first_login_at")
    op.drop_column("users", "institution_domain")
    op.drop_column("users", "password_hash")
