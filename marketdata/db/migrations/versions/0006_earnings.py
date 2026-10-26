"""Earnings releases, their facts, and value changes (PLAN.md §7, E7a-S4).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-26
"""

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # The §7 table with checks, and one more status (D-071): no_statement, an 8-K with item
    # 2.02 that holds no income statement lines at all (a deliveries report, an announcement).
    op.execute("""
    create table earnings_releases (
      accession_no       text primary key check (accession_no ~ '^[0-9]{10}-[0-9]{2}-[0-9]{6}$'),
      cik                bigint not null references companies(cik),
      accepted_at        timestamptz,              -- the index page's time (D-057)
      doc_name           text,                     -- the press release exhibit used
      fiscal_year        int,                      -- D-008, D-074 labels
      fiscal_period      text check (fiscal_period in ('Q1','Q2','Q3','Q4')),
      period_end         date,                     -- current-quarter column date from the table header
      period_label       text,                     -- as the company states it
      status             text not null check (status in ('complete','partial','failed','no_statement')),
      extractor          text not null,            -- 'rules' (E7a), 'llm' (E12)
      extractor_version  text not null,
      check_report       jsonb not null,
      verified_at        timestamptz,              -- first verification that served a value
      updated_at         timestamptz not null default now()
    )
    """)
    op.execute(
        "create index earnings_releases_by_company on earnings_releases (cik, period_end desc)"
    )
    op.execute("""
    create table earnings_facts (
      accession_no       text not null references earnings_releases(accession_no),
      slot               text not null
                         check (slot in ('revenue','operating_income','net_income','eps_diluted')),
      value              numeric,                  -- base units (USD, USD/shares); null if not found or failed
      unit               text not null check (unit in ('USD','USD/shares')),
      prior_value        numeric,                  -- same quarter one year earlier, from the same table
      change_pct         numeric,                  -- computed by code (§9.9)
      basis              text not null default 'GAAP',
      source_row_label   text,                     -- row label as printed
      source_quote       text,                     -- row label and cells as printed
      scale_text         text,                     -- e.g. 'in millions, except per share amounts'
      method             text not null check (method in ('rule','llm')),
      extractor_version  text not null,
      check_status       text not null check (check_status in ('passed','failed','not_found','not_applicable')),
      cross_check_status     text not null default 'pending'          -- E4b, D-028
                             check (cross_check_status in ('pending','matched','mismatch','not_comparable')),
      cross_check_value      numeric,              -- XBRL value from the later 10-Q/10-K
      cross_check_accession  text,                 -- that filing
      quality_flags      text[] not null default '{}',
      primary key (accession_no, slot),
      check (check_status = 'passed' or (value is null and prior_value is null and change_pct is null))
    )
    """)
    # D-027: stored values never change silently.
    op.execute("""
    create table value_changes (
      change_id   bigserial primary key,
      entity      text not null check (entity in ('earnings_fact','financial_metric')),
      entity_key  jsonb not null,                 -- e.g. {"accession_no": "...", "slot": "revenue", "field": "value"}
      cik         bigint not null,
      old_value   numeric,
      new_value   numeric,
      reason      text not null check (reason in ('correction','restatement','amendment')),
      detail      text,                           -- e.g. rules version change, override added, the amending filing
      changed_at  timestamptz not null default now()
    )
    """)
    op.execute("create index value_changes_by_time on value_changes (changed_at)")


def downgrade() -> None:
    op.execute("drop table value_changes")
    op.execute("drop table earnings_facts")
    op.execute("drop table earnings_releases")
