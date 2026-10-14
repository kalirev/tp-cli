-- tp deskflight -> Rev Vision ingest schema (Postgres)
-- Run once against your Rev Vision database.

create table if not exists tp_session (
  id             text primary key,            -- tp session id (timestamp-rand)
  title          text not null,
  project        text,
  bucket         text not null default 'archive'
                 check (bucket in ('writeups','projects','tech-feed','archive')),
  host           text,
  started_at     timestamptz,
  ended_at       timestamptz,
  active_seconds integer not null default 0,
  digest         text,                        -- hash-chain tip (tamper-evident)
  event_count    integer not null default 0,
  published      boolean not null default false,  -- you flip this in the UI
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now()
);

create table if not exists tp_event (
  session_id  text not null references tp_session(id) on delete cascade,
  seq         integer not null,
  ts          timestamptz,
  type        text not null,                  -- cmd | note | why | milestone | open
  cwd         text,
  command     text,
  exit        integer,
  duration    integer,
  text        text,
  hash        text,
  prev_hash   text,
  primary key (session_id, seq)
);

create table if not exists tp_artifact (
  id          bigserial primary key,
  session_id  text not null references tp_session(id) on delete cascade,
  ts          timestamptz,
  type        text not null,                  -- gitdiff | ports
  data        jsonb not null
);

create index if not exists tp_session_bucket_idx  on tp_session (bucket);
create index if not exists tp_session_project_idx on tp_session (project);
create index if not exists tp_event_type_idx      on tp_event (session_id, type);

-- ---------------------------------------------------------------------------
-- v0.3: backfill origins, command output, re-seal history.
-- Safe to run on an existing v0.2 database (idempotent).
alter table tp_session add column if not exists active_estimated_seconds integer not null default 0;
alter table tp_session add column if not exists origins         jsonb;   -- {"live":n,"journal":n,"history":n}
alter table tp_session add column if not exists prior_digests   jsonb;   -- earlier seals of a reopened session
alter table tp_session add column if not exists output_included boolean not null default false;

alter table tp_event add column if not exists term             text;
alter table tp_event add column if not exists origin           text;     -- live | journal | history
alter table tp_event add column if not exists ts_end           timestamptz;
alter table tp_event add column if not exists jhash            text;     -- journal record hash
alter table tp_event add column if not exists output           text;     -- null unless pushed --with-output
alter table tp_event add column if not exists output_sha256    text;     -- always sealed, even when output stays local
alter table tp_event add column if not exists output_bytes     integer;
alter table tp_event add column if not exists output_truncated boolean;
alter table tp_event add column if not exists fullscreen       boolean;
