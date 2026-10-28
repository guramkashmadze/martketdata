"""Computed metrics' unit (E4b-S2): margins and growth are percentages.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-29
"""

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # §9.3's margins and year-over-year growth are percentages rounded to one decimal, like an
    # earnings release's change (§9.9).
    op.execute("""
    alter table financial_metrics drop constraint financial_metrics_unit_check;
    alter table financial_metrics add constraint financial_metrics_unit_check
      check (unit in ('USD','shares','USD/shares','percent'));
    """)


def downgrade() -> None:
    # Percent rows are computed from the other rows; `marketdata normalize --all` makes them
    # again after an upgrade.
    op.execute("""
    delete from financial_metrics where unit = 'percent';
    alter table financial_metrics drop constraint financial_metrics_unit_check;
    alter table financial_metrics add constraint financial_metrics_unit_check
      check (unit in ('USD','shares','USD/shares'));
    """)
