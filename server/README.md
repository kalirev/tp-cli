# Rev Vision ingest for tp

Two files wire the recorder into your Next.js + Postgres app:

- `schema.sql` — three tables: `tp_session`, `tp_event`, `tp_artifact`.
- `app/api/ingest/session/route.ts` — the `POST /api/ingest/session` endpoint.

## Setup

1. **Create the tables**
   ```bash
   psql "$DATABASE_URL" -f schema.sql
   ```

2. **Add the route** — copy `app/api/ingest/session/route.ts` into your app at
   the same path. It uses `pg`:
   ```bash
   npm i pg
   ```

3. **Set env** (e.g. Vercel project env vars)
   ```
   DATABASE_URL=postgres://...
   TP_INGEST_TOKEN=<a long random string>
   ```
   Then on your workstation: `tp config token <same string>`.

4. **Test**
   ```bash
   tp push <session-id> --to writeups
   ```
   You should get `↑ pushed ... /writeups/<id>`. Re-pushing the same id is
   idempotent (children are replaced), so routing to a new bucket just updates.

## The wire contract

`POST /api/ingest/session`, `Authorization: Bearer <TP_INGEST_TOKEN>`:

```jsonc
{
  "id": "20261002-205257-a5d7",
  "title": "owning the k3s ingress",
  "project": "CyberDen",
  "bucket": "writeups",            // writeups | projects | tech-feed | archive
  "host": "zephyrus",
  "started_at": "2026-10-02T20:52:00+00:00",
  "ended_at":   "2026-10-02T21:40:00+00:00",
  "active_seconds": 2880,
  "digest": "e2c6fbd5a0cc...",     // hash-chain tip
  "event_count": 42,
  "events": [
    { "seq": 1, "ts": "...", "type": "why",  "text": "...", "hash": "...", "prev_hash": "..." },
    { "seq": 2, "ts": "...", "type": "cmd",  "cwd": "...", "command": "...", "exit": 0,
      "duration": 4, "hash": "...", "prev_hash": "..." }
  ],
  "artifacts": [
    { "type": "gitdiff", "repo": "...", "branch": "...", "head": "...", "stat": "...", "patch": "..." },
    { "type": "ports",   "services": [ { "local": "127.0.0.1:3000", "proc": "..." } ] }
  ]
}
```

Response: `{ "ok": true, "id": "...", "bucket": "...", "url": "/writeups/<id>" }`.

## Rendering ideas (your call)

- A **Writeups** draft seeded from the timeline: milestones as section headers,
  `why` entries as prose, commands as fenced blocks, git diffs as collapsibles.
- A **verified** badge on any session whose stored events still match `digest`
  (re-run the same sha256 chain server-side).
- The **active_seconds** across sessions is your real "time at the desk" metric
  for the per-pillar timers.
- `published` stays `false` until you flip it in the UI — ingest is capture,
  publishing is a separate, deliberate step.

## Security

- The endpoint only trusts the bearer token. Keep `TP_INGEST_TOKEN` secret and
  rotate it if it leaks (`tp config token <new>` on the client).
- Commands are already redacted client-side, but treat the DB as sensitive: it
  describes your lab. Don't expose these tables through any public API route.
