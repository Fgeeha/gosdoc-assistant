"""Год принятия в ключе закона: номера федеральных законов повторяются по годам.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-27
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE npa_acts SET canonical_key = canonical_key || ':' || "
        "EXTRACT(YEAR FROM adopted_at)::int "
        "WHERE kind IN ('federal_law', 'federal_constitutional_law') AND adopted_at IS NOT NULL AND canonical_key NOT LIKE '%:%:%'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE npa_acts SET canonical_key = regexp_replace(canonical_key, ':[0-9]{4}$', '') "
        "WHERE kind IN ('federal_law', 'federal_constitutional_law')"
    )
