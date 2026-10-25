"""Security master: companies and their securities (PLAN.md §7, E2-S2).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-25
"""

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # The §7 tables, with three changes: `name` may be null, because company_tickers_mf.json
    # has no names (D-053); fiscal_year_end and ticker are checked for shape; and a partial
    # unique index allows one current row per ticker (D-053).
    op.execute("""
    create table companies (
      cik              bigint primary key,
      name             text,                       -- EDGAR's name; null = listed only in the fund list
      sic_code         text,
      sic_description  text,
      fiscal_year_end  text                        -- 'MMDD' as the SEC gives it
                       check (fiscal_year_end ~ '^(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])$'),
      state_of_inc     text,
      filer_type       text not null default 'unknown'
                       check (filer_type in ('domestic','foreign_private_issuer','fund','other','unknown')),
      in_universe      boolean not null default false,
      updated_at       timestamptz not null default now()
    )
    """)
    op.execute("""
    create table securities (
      security_id    bigserial primary key,
      cik            bigint not null references companies(cik),
      ticker         text not null                -- as the SEC lists it, e.g. 'BRK-B'
                     check (ticker ~ '^[A-Z0-9]+(-[A-Z0-9]+)*$'),
      security_type  text not null default 'common'
                     check (security_type in ('common','preferred','fund','adr','other')),
      exchange       text,
      share_class    text,
      cusip          text,
      figi           text,
      valid_from     date not null,
      valid_to       date,                        -- null = current
      unique (ticker, valid_from),
      check (valid_to is null or valid_to > valid_from)
    )
    """)
    op.execute(
        "create unique index securities_current_ticker on securities (ticker) where valid_to is null"
    )
    op.execute("create index securities_by_cik on securities (cik)")


def downgrade() -> None:
    op.execute("drop table securities")
    op.execute("drop table companies")
