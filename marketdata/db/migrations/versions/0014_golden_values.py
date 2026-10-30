"""The golden sets as data (E5b-S4): the values read by hand from earnings releases, so the
accuracy report and /v1/status compare them with what is served without reading files.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-30
"""

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # One row per hand-checked value: a release's slot (its quarter and the year before), or a
    # metric's period. `marketdata quality accuracy` replaces them all from tests/golden/.
    op.execute("""
    create table golden_values (
      golden_id     bigserial primary key,
      kind          text not null check (kind in ('earnings', 'metric')),
      cik           bigint not null,
      accession_no  text,
      slot          text,
      metric        text,
      period_type   text check (period_type in ('Q', 'FY', 'TTM')),
      period_end    date,
      value         numeric,
      prior_value   numeric,
      status        text not null
                    check (status in ('expected', 'known_difference', 'not_applicable')),
      note          text,
      source_url    text not null,
      source_file   text not null,
      loaded_at     timestamptz not null default now(),
      check ((kind = 'earnings') = (accession_no is not null and slot is not null)),
      check ((kind = 'metric')
             = (metric is not null and period_type is not null and period_end is not null)),
      check (status = 'not_applicable' or value is not null),
      unique nulls not distinct (kind, cik, accession_no, slot, metric, period_type, period_end)
    );
    """)


def downgrade() -> None:
    op.execute("drop table golden_values")
