"""Foundation: task queue, API clients, API keys and the request log (PLAN.md §7, E0-S3).

Revision ID: 0001
Revises:
Create Date: 2026-10-24
"""

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # One op.execute per statement.
    op.execute("""
    create table tasks (
      task_id      bigserial primary key,
      kind         text not null,
      key          text not null,                -- dedupe key: accession_no, cik, event_id:dest_id ...
      payload      jsonb not null default '{}',
      status       text not null default 'queued'
                   check (status in ('queued','running','done','failed','dead')),
      attempts     int not null default 0,
      run_after    timestamptz not null default now(),
      locked_until timestamptz,                   -- reclaim if a worker dies
      last_error   text,
      created_at   timestamptz not null default now(),
      updated_at   timestamptz not null default now(),
      unique (kind, key)
    )
    """)
    op.execute("create index tasks_ready on tasks (status, run_after)")
    op.execute("""
    create table clients (                        -- an API client: a company, an app, or the operator's own test client
      client_id   bigserial primary key,
      name        text not null unique,
      created_at  timestamptz not null default now()
    )
    """)
    op.execute("""
    create table api_keys (
      key_id         bigserial primary key,
      client_id      bigint not null references clients(client_id),
      scope          text not null default 'api' check (scope in ('api','review')),  -- 'review' used from E7b
      key_hash       text not null unique,        -- sha256 of the key; the key itself is shown once
      rate_per_min   int not null default 120,
      created_at     timestamptz not null default now(),
      revoked_at     timestamptz
    )
    """)
    op.execute("""
    create table api_requests (
      id           bigserial primary key,
      key_id       bigint references api_keys(key_id),
      at           timestamptz not null default now(),
      method       text not null,
      path         text not null,
      status       int not null,
      duration_ms  int not null
    )
    """)


def downgrade() -> None:
    op.execute("drop table api_requests")
    op.execute("drop table api_keys")
    op.execute("drop table clients")
    op.execute("drop table tasks")
