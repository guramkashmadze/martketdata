"""One concept across companies from the SEC's frames API (E4a-S5, D-085).

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-28
"""

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # A frame holds one fact per filer for a calendar period (CY2025Q2I: the instant closest to
    # 2025-06-30), the one the SEC picked from the latest filing. Universe growth ranks every
    # filer by its latest dei:EntityPublicFloat from these (D-035, D-085), so the CIK is any
    # filer's, not only one in companies.
    op.execute("""
    create table frame_facts (
      taxonomy      text not null check (taxonomy in ('us-gaap','dei')),
      concept       text not null,
      unit          text not null,
      frame         text not null check (frame ~ '^CY[0-9]{4}(Q[1-4]I?)?$'),
      cik           bigint not null,
      entity_name   text,
      period_start  date,                         -- null for instants
      period_end    date not null,
      value         numeric not null,
      accession_no  text not null check (accession_no ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
      raw_key       text not null references raw_objects(key),
      loaded_at     timestamptz not null default now(),
      primary key (taxonomy, concept, unit, frame, cik)
    )
    """)
    op.execute("create index frame_facts_by_cik on frame_facts (cik, concept, period_end desc)")


def downgrade() -> None:
    op.execute("drop table frame_facts")
