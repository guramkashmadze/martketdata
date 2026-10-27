"""XBRL facts from companyfacts JSON (PLAN.md §7, E4a-S1).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-27
"""

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # The §7 table, with checks. Every us-gaap and dei fact of each loaded company, in every
    # unit and from every form; metrics read the 10-K and 10-Q family only (§9.4). A fact is
    # one value for one period in one filing, so its key includes the accession number.
    op.execute("""
    create table xbrl_facts (
      id            bigserial primary key,
      cik           bigint not null references companies(cik),
      taxonomy      text not null check (taxonomy in ('us-gaap','dei')),
      concept       text not null,
      unit          text not null,                -- USD, shares, USD/shares, pure …
      period_start  date,                         -- null for instant facts
      period_end    date not null,
      value         numeric not null,
      fy            int,                          -- the filing's fiscal year and period, as filed
      fp            text,
      form          text,
      frame         text,                         -- e.g. CY2024Q1, CY2024Q1I; moves to newer facts
      filed_at      date not null,
      accession_no  text not null check (accession_no ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
      unique nulls not distinct (cik, taxonomy, concept, unit, period_start, period_end, accession_no),
      check (period_start is null or period_start <= period_end)
    )
    """)
    op.execute("create index xbrl_facts_lookup on xbrl_facts (cik, concept, period_end)")


def downgrade() -> None:
    op.execute("drop table xbrl_facts")
