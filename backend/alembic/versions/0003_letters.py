"""Письма: бланки, письма, версии, производственный календарь.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def _org() -> sa.Column[int]:
    return sa.Column(
        "organization_id", sa.Integer, sa.ForeignKey("organizations.id"), nullable=False, index=True
    )


def upgrade() -> None:
    op.create_table(
        "letter_blanks",
        sa.Column("id", sa.Integer, primary_key=True),
        _org(),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("template_file_id", sa.Integer, sa.ForeignKey("files.id"), nullable=False),
        sa.Column("fields", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("uploaded_by", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("active", sa.Boolean, nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "letters",
        sa.Column("id", sa.Integer, primary_key=True),
        _org(),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("blank_id", sa.Integer, sa.ForeignKey("letter_blanks.id")),
        sa.Column("incoming_file_id", sa.Integer, sa.ForeignKey("files.id", ondelete="SET NULL")),
        sa.Column("outgoing_number", sa.String(100)),
        sa.Column("date", sa.Date),
        sa.Column("addressee", sa.Text, nullable=False, server_default=""),
        sa.Column("executor", sa.String(300), nullable=False, server_default=""),
        sa.Column("deadline", sa.Date),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("is_draft", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("requisites", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id"), index=True),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "letter_versions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "letter_id",
            sa.Integer,
            sa.ForeignKey("letters.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("body", postgresql.JSONB, nullable=False),
        sa.Column("author_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("docx_file_id", sa.Integer, sa.ForeignKey("files.id", ondelete="SET NULL")),
        sa.Column("pdf_file_id", sa.Integer, sa.ForeignKey("files.id", ondelete="SET NULL")),
        sa.Column("meta", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("letter_id", "version"),
    )
    op.create_table(
        "holidays",
        sa.Column("date", sa.Date, primary_key=True),
        sa.Column("is_workday", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("note", sa.String(200), nullable=False, server_default=""),
    )


def downgrade() -> None:
    for table in ("holidays", "letter_versions", "letters", "letter_blanks"):
        op.drop_table(table)
