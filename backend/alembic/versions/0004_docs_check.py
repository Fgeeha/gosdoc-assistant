"""Нормативная база и проверка документов.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "npa_acts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("organization_id", sa.Integer, sa.ForeignKey("organizations.id"), index=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("number", sa.String(100), nullable=False),
        sa.Column("adopted_at", sa.Date),
        sa.Column("title", sa.Text, nullable=False, server_default=""),
        sa.Column("issuer", sa.String(300), nullable=False, server_default=""),
        sa.Column("repealed_at", sa.Date),
        sa.Column("repealed_note", sa.Text, nullable=False, server_default=""),
        sa.Column("canonical_key", sa.String(200), nullable=False, index=True),
    )
    # Один акт на ключ: общий — один на систему, ведомственный — один на организацию.
    op.create_index(
        "uq_npa_acts_shared_key",
        "npa_acts",
        ["canonical_key"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NULL"),
    )
    op.create_index(
        "uq_npa_acts_org_key",
        "npa_acts",
        ["organization_id", "canonical_key"],
        unique=True,
        postgresql_where=sa.text("organization_id IS NOT NULL"),
    )
    op.create_table(
        "npa_editions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "act_id",
            sa.Integer,
            sa.ForeignKey("npa_acts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("effective_from", sa.Date, nullable=False),
        sa.Column("effective_to", sa.Date),
        sa.Column("source_file_id", sa.Integer, sa.ForeignKey("files.id")),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("error", sa.Text),
        sa.Column("indexed_at", TZ),
        sa.UniqueConstraint("act_id", "effective_from"),
    )
    op.create_table(
        "npa_chunks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "edition_id",
            sa.Integer,
            sa.ForeignKey("npa_editions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("path", sa.String(100), nullable=False),
        sa.Column("article", sa.String(20), nullable=False, server_default=""),
        sa.Column("heading", sa.Text, nullable=False, server_default=""),
        sa.Column("text", sa.Text, nullable=False),
    )
    op.create_table(
        "npa_change_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "act_id",
            sa.Integer,
            sa.ForeignKey("npa_acts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("change", sa.String(30), nullable=False),
        sa.Column("details", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("at", TZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "check_reports",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer,
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("job_id", sa.Integer, sa.ForeignKey("jobs.id"), nullable=False, unique=True),
        sa.Column("summary", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "result_docx_file_id", sa.Integer, sa.ForeignKey("files.id", ondelete="SET NULL")
        ),
        sa.Column(
            "report_docx_file_id", sa.Integer, sa.ForeignKey("files.id", ondelete="SET NULL")
        ),
        sa.Column("is_draft", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "findings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "report_id",
            sa.Integer,
            sa.ForeignKey("check_reports.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("doc_fragment", sa.Text, nullable=False),
        sa.Column("npa_ref", sa.Text, nullable=False, server_default=""),
        sa.Column("npa_chunk_id", sa.Integer, sa.ForeignKey("npa_chunks.id", ondelete="SET NULL")),
        sa.Column("norm", sa.Text, nullable=False, server_default=""),
        sa.Column("quote", sa.Text, nullable=False, server_default=""),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("recommendation", sa.Text, nullable=False, server_default=""),
        sa.Column("user_verdict", sa.String(20)),
    )


def downgrade() -> None:
    for table in (
        "findings",
        "check_reports",
        "npa_change_log",
        "npa_chunks",
        "npa_editions",
        "npa_acts",
    ):
        op.drop_table(table)
