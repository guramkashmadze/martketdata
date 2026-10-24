"""Raw objects: one row per file kept in the raw store (PLAN.md §7, E1-S3).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-24
"""

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # The §7 table, plus checks on sha256 and size_bytes (D-047).
    op.execute("""
    create table raw_objects (
      key           text primary key,             -- path in the raw store
      url           text not null,
      sha256        text not null check (sha256 ~ '^[0-9a-f]{64}$'),
      size_bytes    bigint not null check (size_bytes >= 0),
      content_type  text,
      fetched_at    timestamptz not null default now()
    )
    """)


def downgrade() -> None:
    op.execute("drop table raw_objects")
