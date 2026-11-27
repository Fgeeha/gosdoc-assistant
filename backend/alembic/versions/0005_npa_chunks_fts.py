"""Полнотекстовый поиск по фрагментам НПА средствами PostgreSQL (работа без Elasticsearch).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-27
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE npa_chunks ADD COLUMN tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('russian', text)) STORED"
    )
    op.execute("CREATE INDEX ix_npa_chunks_tsv ON npa_chunks USING gin (tsv)")


def downgrade() -> None:
    op.execute("DROP INDEX ix_npa_chunks_tsv")
    op.execute("ALTER TABLE npa_chunks DROP COLUMN tsv")
