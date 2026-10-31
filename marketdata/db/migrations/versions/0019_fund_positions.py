"""Fund positions (E9-S4): each curated manager's holdings at a quarter end, after its
amendments, its lines merged by CUSIP and put or call, with weights (PLAN.md §9.8). Value
changes can now be about a position, when an amendment or a correction changes its shares or
value (D-027).

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-01
"""

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # One row per manager, quarter, CUSIP and put or call (null for shares held outright):
    # shares or principal and value added up from the lines in force, the weight among the
    # long equity positions (none for options and principal amounts), and the reports the lines
    # come from. Change types are decided when read, from the quarter before (D-124).
    op.execute("""
    create table fund_positions (
      manager_cik        bigint not null references managers(manager_cik),
      report_period      date not null,
      cusip              text not null,
      put_call           text check (put_call in ('PUT', 'CALL')),
      share_type         text not null check (share_type in ('SH', 'PRN')),
      name_of_issuer     text,
      title_of_class     text,
      figi               text,
      shares             numeric not null,
      value_usd          numeric not null,
      weight_pct         numeric,
      lines              int not null check (lines > 0),
      source_accessions  text[] not null check (cardinality(source_accessions) > 0),
      quality_flags      text[] not null default '{}',
      updated_at         timestamptz not null default now(),
      constraint fund_positions_key
        unique nulls not distinct (manager_cik, report_period, cusip, put_call)
    );
    create index fund_positions_by_cusip on fund_positions (cusip, report_period);
    """)
    op.execute("""
    alter table value_changes drop constraint value_changes_entity_check;
    alter table value_changes add constraint value_changes_entity_check
      check (entity in ('earnings_fact', 'financial_metric', 'insider_transaction',
                        'fund_position'));
    """)


def downgrade() -> None:
    op.execute("""
    delete from value_changes where entity = 'fund_position';
    alter table value_changes drop constraint value_changes_entity_check;
    alter table value_changes add constraint value_changes_entity_check
      check (entity in ('earnings_fact', 'financial_metric', 'insider_transaction'));
    """)
    op.execute("drop table fund_positions")
