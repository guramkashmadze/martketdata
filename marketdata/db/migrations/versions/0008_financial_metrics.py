"""Financial metrics from XBRL facts (PLAN.md §7, E4a-S4).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-28
"""

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # The §7 table, with checks. One row per company, metric and period; a value is null only
    # with a quality flag that says why (§9.4 rule 10: a bank's revenue). Rows stay under the
    # CIK whose filings they come from; the service layer merges predecessors (D-084).
    op.execute("""
    create table financial_metrics (
      cik                bigint not null references companies(cik),
      metric             text not null,
      period_type        text not null check (period_type in ('Q','FY','TTM')),
      fiscal_year        int not null,                -- D-008, D-074
      fiscal_period      text not null check (fiscal_period in ('Q1','Q2','Q3','Q4','FY')),
      period_start       date,                        -- null for instants
      period_end         date not null,
      value              numeric,                     -- null allowed, with a quality flag
      unit               text not null check (unit in ('USD','shares','USD/shares')),
      method             text not null check (method in
                         ('reported','derived_ytd','derived_q4','derived_ttm','computed')),
      concept            text,                        -- XBRL concept used; null for computed
      accession_no       text check (accession_no ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
      source_accessions  text[] not null,             -- all contributing filings
      filed_at           date,                        -- filing date of accession_no
      quality_flags      text[] not null default '{}',
      calc_version       text not null,               -- catalog and period rules versions
      updated_at         timestamptz not null default now(),
      primary key (cik, metric, period_type, period_end),
      check ((period_type = 'FY') = (fiscal_period = 'FY')),
      check (period_start is null or period_start <= period_end),
      check (value is not null or cardinality(quality_flags) > 0)
    )
    """)


def downgrade() -> None:
    op.execute("drop table financial_metrics")
