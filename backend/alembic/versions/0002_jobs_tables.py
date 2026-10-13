"""Фоновые задачи и сохранённые маппинги таблиц.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer,
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        # Файл с ПДн удаляется по сроку хранения, задача и её счётчики остаются.
        sa.Column("input_file_id", sa.Integer, sa.ForeignKey("files.id", ondelete="SET NULL")),
        sa.Column("params", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("result", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("error", sa.Text),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), index=True),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", TZ),
        sa.Column("finished_at", TZ),
    )
    op.create_table(
        "table_mappings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer,
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("spec", postgresql.JSONB, nullable=False),
        sa.Column("target_template_file_id", sa.Integer, sa.ForeignKey("files.id"), nullable=False),
        sa.Column("owner_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("shared", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TZ, nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("table_mappings")
    op.drop_table("jobs")
