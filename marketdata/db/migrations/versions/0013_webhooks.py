"""Webhooks and the change feed (E5b): destinations, events written by the code that changes
the data (an outbox), deliveries, which releases came in live, and the feed's index.

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-30
"""

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | None = None
depends_on: str | None = None

EVENT_TYPES = "'earnings.verified', 'financials.updated', 'data.changed', 'filing.event'"


def upgrade() -> None:
    # A client's endpoint (PLAN.md §9.11). The signing secret is stored encrypted with
    # WEBHOOK_ENCRYPTION_KEY; a destination is switched off by the operator or after 50 failed
    # deliveries in a row, and says when and why.
    op.execute(f"""
    create table webhook_destinations (
      dest_id               bigserial primary key,
      client_id             bigint not null references clients(client_id),
      url                   text not null check (url ~ '^https?://'),
      secret_enc            text not null,
      event_types           text[] not null
                            check (cardinality(event_types) > 0
                                   and event_types <@ array[{EVENT_TYPES}]),
      active                boolean not null default true,
      consecutive_failures  int not null default 0 check (consecutive_failures >= 0),
      created_at            timestamptz not null default now(),
      disabled_at           timestamptz,
      disabled_reason       text,
      last_test_at          timestamptz,
      check (active = (disabled_at is null))
    );
    create index webhook_destinations_by_client on webhook_destinations (client_id);
    """)
    # An event is written in the transaction that changes the data, with what it is about
    # (subject) and what the writer knew (detail); the worker then writes its body, the §9.11
    # envelope exactly as it is delivered (json keeps the text), and queues its deliveries.
    # A live event is written once per type and subject; a test event goes to one destination.
    op.execute(f"""
    create table webhook_events (
      event_id      uuid primary key,
      type          text not null check (type in ({EVENT_TYPES})),
      subject       text not null,
      detail        jsonb not null default '{{}}',
      client_id     bigint references clients(client_id),
      dest_id       bigint references webhook_destinations(dest_id),
      livemode      boolean not null default true,
      payload       json,
      created_at    timestamptz not null default now(),
      published_at  timestamptz,
      check ((payload is null) = (published_at is null)),
      check (livemode = (dest_id is null)),
      check (dest_id is null or client_id is not null)
    );
    create unique index webhook_events_once on webhook_events (type, subject) where livemode;
    create index webhook_events_by_time on webhook_events (created_at);
    """)
    op.execute("""
    create table webhook_deliveries (
      event_id      uuid not null references webhook_events(event_id),
      dest_id       bigint not null references webhook_destinations(dest_id),
      attempt       int not null check (attempt >= 1),
      status_code   int,
      error         text,
      duration_ms   int not null check (duration_ms >= 0),
      attempted_at  timestamptz not null default now(),
      primary key (event_id, dest_id, attempt)
    );
    """)
    # A release the watcher routed when it was filed, rather than one a backfill found later:
    # only these announce earnings.verified and count toward the time to verified (§9.10).
    op.execute("alter table earnings_releases add column live boolean not null default false")
    # The change feed by company (GET /v1/changes?ticker=).
    op.execute("create index value_changes_by_cik on value_changes (cik, change_id)")


def downgrade() -> None:
    op.execute("drop index value_changes_by_cik")
    op.execute("alter table earnings_releases drop column live")
    op.execute("drop table webhook_deliveries")
    op.execute("drop table webhook_events")
    op.execute("drop table webhook_destinations")
