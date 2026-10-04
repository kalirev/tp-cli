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
