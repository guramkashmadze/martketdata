"""Earnings documents, task priority and predecessor companies (PLAN.md §7, E7a-S1).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-26
"""

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # The §7 table, with checks. One row per exhibit an earnings task stored; the text is what
    # verification reads (§9.9).
    op.execute("""
    create table documents (
      accession_no  text not null check (accession_no ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
      doc_name      text not null,
      doc_type      text not null,                -- e.g. EX-99.1
      raw_key       text not null references raw_objects(key),
      text          text not null,                -- tables converted to markdown
      extracted_at  timestamptz not null default now(),
      primary key (accession_no, doc_name)
    )
    """)
    # D-067: live work runs before backfills. Workers take the highest priority first, then
    # the oldest; 10 live (routed filings), 0 periodic work, -10 backfills.
    op.execute("alter table tasks add column priority int not null default 0")
    op.execute("drop index tasks_ready")
    op.execute("create index tasks_ready on tasks (status, priority desc, run_after)")
    # D-068: a company's history filed under an earlier CIK (XOM's before 2026-07-01 is under
    # 34088), from data/predecessors.csv; the predecessor has its own companies row.
    op.execute("""
    create table company_predecessors (
      cik              bigint not null references companies(cik),
      predecessor_cik  bigint not null references companies(cik),
      source_url       text not null,              -- the SEC filing that shows the succession
      note             text not null,
      primary key (cik, predecessor_cik),
      check (cik <> predecessor_cik)
    )
    """)


def downgrade() -> None:
    op.execute("drop table company_predecessors")
    op.execute("drop index tasks_ready")
    op.execute("create index tasks_ready on tasks (status, run_after)")
    op.execute("alter table tasks drop column priority")
    op.execute("drop table documents")
