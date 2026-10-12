"""Ядро: организации, пользователи, файлы, аудит.

Revision ID: 0001
Revises:
Create Date: 2026-10-12
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("short_name", sa.String(200), nullable=False),
        sa.Column("settings", postgresql.JSONB, nullable=False, server_default="{}"),
    )
    op.execute(
        "INSERT INTO organizations (id, name, short_name) "
        "VALUES (1, 'Организация по умолчанию', 'Организация')"
    )
    op.execute("SELECT setval('organizations_id_seq', 1)")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer,
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("login", sa.String(100), nullable=False, unique=True),
        sa.Column("full_name", sa.String(300), nullable=False),
        sa.Column("department", sa.String(300)),
        sa.Column("roles", postgresql.ARRAY(sa.String(50)), nullable=False, server_default="{}"),
        sa.Column("password_hash", sa.String(200)),
        sa.Column("auth_source", sa.String(20), nullable=False, server_default="local"),
        sa.Column("failed_attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("locked_until", TZ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("must_change_password", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_login_at", TZ),
    )

    op.create_table(
        "files",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer,
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("owner_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("storage_key", sa.String(200), nullable=False, unique=True),
        sa.Column("original_name", sa.String(500), nullable=False),
        sa.Column("mime", sa.String(200), nullable=False),
        sa.Column("size", sa.BigInteger, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("contains_pdn", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", TZ, index=True),
    )
    op.create_table(
        "file_blobs",
        sa.Column("storage_key", sa.String(200), primary_key=True),
        sa.Column("data", sa.LargeBinary, nullable=False),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("at", TZ, nullable=False, server_default=sa.func.now()),
        sa.Column(
            "organization_id",
            sa.Integer,
            sa.ForeignKey("organizations.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("ip", sa.String(64)),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("object_type", sa.String(100)),
        sa.Column("object_id", sa.String(100)),
        sa.Column("details", postgresql.JSONB, nullable=False, server_default="{}"),
    )
    # Журнал аудита только дополняется. В проде у роли приложения дополнительно
    # отзываются права UPDATE/DELETE/TRUNCATE на эту таблицу.
    op.execute("""
        CREATE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log: изменение и удаление записей запрещено';
        END $$;
    """)
    op.execute("""
        CREATE TRIGGER audit_log_no_update BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_append_only();
    """)
    op.execute("""
        CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
        FOR EACH STATEMENT EXECUTE FUNCTION audit_log_append_only();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_log_no_truncate ON audit_log")
    op.execute("DROP TRIGGER audit_log_no_update ON audit_log")
    op.execute("DROP FUNCTION audit_log_append_only()")
    for table in ("audit_log", "file_blobs", "files", "users", "organizations"):
        op.drop_table(table)
