# tp — CLI Reference & Integration Manual

`tp` (Deskflight Recorder) is a start/stop/pause recorder for your workstation
and lab. This manual is the full interface: every command, what a session looks
like, what "push" actually sends, how it lands in your site, and how to wire the
server side into **rev-engineering.vercel.app**.

If you just want to install and go, see `INSTALL.md`. This document is the
reference you keep open.

---

## 1. The mental model

There are two halves, and they live in two different places.

```
  YOUR MACHINE (local)                                   REV-ENGINEERING (web app)
  ──────────────────────────────                          ──────────────────────────
  tp CLI  +  shell hooks                                  POST /api/ingest/session
     │  record every terminal                                   │  (bearer token)
     ▼                                                           ▼
  ~/.tp/  vault  ──────────  tp push (HTTPS, on your command) ─► Postgres
  capture · redact · seal                                        tp_session / tp_event / tp_artifact
                                                                 │
                                                                 ▼
                                                            your UI: review, verify, publish
```

- **Local half:** the recorder runs entirely on your machine. It works offline.
  Nothing leaves until you run `tp push`.
- **Web half:** one API route + three Postgres tables inside your existing
  Next.js app. This is what receives a session and stores it so the UI can show
  it. Code is in `server/`.
- **The boundary is crossed once, by you**, with `tp push`. Before that moment
  the data is only on disk under `~/.tp/`.

A **session** is one recording: from `tp start` to `tp stop`. While it's active,
every terminal that has the hook loaded feeds commands into it. You annotate it
with reasoning as you go. On stop it's redacted, hash-chained, and sealed.

---

## 2. Command reference

General form: `tp <command> [args] [flags]`. Global: `tp --version`, `tp -h`,
`tp <command> -h`.

### `tp start "<title>" [--project|-p NAME]`
Begin recording. Fails if a session is already active.
```
$ tp start "prooflayer sidecar: mTLS between gateway and recorder" -p ProofLayer
● recording  20261002-211021-47d5
  prooflayer sidecar: mTLS between gateway and recorder   project=ProofLayer
```
The id (`20261002-211021-47d5`) is `YYYYMMDD-HHMMSS-<rand>`. It's the handle for
every later command.

### `tp pause` / `tp resume`
Stop and restart **both** the clock and capture. Commands you run while paused
are not recorded, and paused time is not counted toward `active_seconds`.
```
$ tp pause
∥ paused   12m03s on the clock
$ tp resume
● recording   resumed 20261002-211021-47d5
```

### `tp why "<text>"` · `tp note "<text>"` · `tp milestone "<text>"`
Attach reasoning to the active session, timestamped into the timeline.
- `why` — the reason behind a decision (the most valuable thing to capture).
- `note` — a plain observation.
- `milestone` — a checkpoint; **also triggers a git-diff + port snapshot now**.
```
$ tp why "gateway accepted plaintext on 8443; recorder must reject non-mTLS clients"
+ why  gateway accepted plaintext on 8443; recorder must reject non-mTLS clients
$ tp milestone "mTLS enforced end to end"
+ milestone  mTLS enforced end to end
```

### `tp open <url> [label...]`
Log a localhost / URL you're testing, with an optional label.
```
$ tp open https://localhost:8443/v1/events recorder ingest, now mTLS-gated
+ localhost  https://localhost:8443/v1/events  (recorder ingest, now mTLS-gated)
```

### `tp status`
Show the active session at a glance.
```
$ tp status
● recording   20261002-211021-47d5
  prooflayer sidecar: mTLS between gateway and recorder   project=ProofLayer
  5 commands captured   8m12s active
```

### `tp stop [--to BUCKET] [--push]`
Finish the session: close the clock, snapshot code+ports, redact, hash-chain,
compute the digest. `--to` sets the bucket; `--push` also sends it immediately.
```
$ tp stop --to writeups
■ stopped   20261002-211021-47d5
  8 events   8m12s active
  digest 1dca8249c0d16b85…   (tp verify 20261002-211021-47d5)
  routed to writeups; push with:  tp push 20261002-211021-47d5
```

### `tp ls [-n N]`
List recent sessions (newest first). `●` active, `∥` paused, `↑` pushed.
```
$ tp ls
  20261002-211021-47d5    8m12s  writeups   ↑ prooflayer sidecar: mTLS between gateway and recorder
```

### `tp show <id> [-n N]`
Print a session's sealed timeline. `ok` / `eN` is the command's exit code.
```
$ tp show 20261002-211021-47d5
prooflayer sidecar: mTLS between gateway and recorder   20261002-211021-47d5
  status=stopped  active=8m12s  bucket=writeups
  digest 1dca8249c0d16b85a6c8c2b7ec94e3f863f5479736ae6e59837d0d6da5bc10ef
  timeline:
    21:10:22 why: gateway accepted plaintext on 8443; recorder must reject non-mTLS clients
    21:10:22 open: https://localhost:8443/v1/events  recorder ingest, now mTLS-gated
    21:10:22 milestone: mTLS enforced end to end
    21:10:26   ok docker compose up -d recorder gateway
    21:11:01   ok openssl req -x509 -newkey rsa:4096 -keyout ca.key -out ca.crt -days 365 -nodes ...
    21:11:56   ok curl -sk https://localhost:8443/healthz
    21:12:21   ok curl -sk https://localhost:8443/v1/events --cert client.crt --key client.key
    21:15:21   ok git commit -am 'recorder: require client cert on /v1/events'
```

### `tp verify <id>`
Recompute the hash chain from the sealed file **as stored** and compare it to the
digest. This is the tamper-evidence check.
```
$ tp verify 20261002-211021-47d5
✓ verified  chain intact  (8 events)
#  if a stored event was edited after the fact:
✗ FAILED  event seq=5 hash mismatch (tampered?)
```

### `tp route <id> --to BUCKET [--push]`
Assign (or reassign) a session's bucket without re-recording. `--push` sends it.
```
$ tp route 20261002-211021-47d5 --to projects --push
routed  20261002-211021-47d5 → projects
↑ pushed  20261002-211021-47d5 → rev-engineering  /projects/20261002-211021-47d5
```

### `tp push <id> [--to BUCKET]`
Send a finished session to rev-engineering over HTTPS. Idempotent — re-pushing
the same id updates the record (and its bucket) rather than duplicating it.
```
$ tp push 20261002-211021-47d5 --to writeups
↑ pushed  20261002-211021-47d5 → rev-engineering  /writeups/20261002-211021-47d5
```

### `tp config [key] [value]`
No args: print config. One arg: print that key. Two args: set it.
```
$ tp config
  api_url = https://rev-engineering.vercel.app
  ingest_path = /api/ingest/session
  token = (set)
  host = zephyrus
  capture_ports = True
  capture_gitdiff = True
  max_patch_bytes = 200000
  redact_patterns = []
$ tp config token  rv_live_9f2c...        # set the bearer token
$ tp config capture_gitdiff false         # turn a toggle off
```

### `tp hook <bash|zsh|fish>`
Print the shell hook to source (see Install). This is the only thing that makes
capture happen automatically.

Buckets everywhere accept aliases: `writeups`=`w`, `projects`=`p`,
`tech-feed`=`t`/`feed`, `archive`=`a`/`raw`.

---

## 3. A full session, start to finish

```bash
# 1. start recording, tagged to a project
tp start "prooflayer sidecar: mTLS between gateway and recorder" -p ProofLayer

# 2. just... work. In any terminal with the hook loaded, every command is
#    captured automatically. Open as many panes as you want.
docker compose up -d recorder gateway
openssl req -x509 -newkey rsa:4096 -keyout ca.key -out ca.crt -days 365 -nodes -subj '/CN=prooflayer-ca'
curl -sk https://localhost:8443/healthz

# 3. capture the thinking as it happens (this is what a writeup is made of)
tp why "gateway accepted plaintext on 8443; recorder must reject non-mTLS clients"
tp open https://localhost:8443/v1/events "recorder ingest, now mTLS-gated"

# 4. mark a checkpoint — snapshots your git diff + listening services right now
tp milestone "mTLS enforced end to end; /v1/events requires client cert"

# 5. coffee. the clock stops.
tp pause
tp resume

# 6. finish, route to writeups, and push to rev-engineering in one go
tp stop --to writeups --push

# 7. anytime later: prove it wasn't edited
tp verify 20261002-211021-47d5
```

---

## 4. What "push" actually is

`tp push` serializes the sealed session and `POST`s it to
`{api_url}{ingest_path}` with `Authorization: Bearer <token>`. Here is the exact
JSON body for the session above (trimmed to 4 events for space):

```json
{
  "id": "20261002-211021-47d5",
  "title": "prooflayer sidecar: mTLS between gateway and recorder",
  "project": "ProofLayer",
  "bucket": "writeups",
  "host": "zephyrus",
  "started_at": "2026-10-02T21:10:21+00:00",
  "ended_at":   "2026-10-02T21:23:40+00:00",
  "active_seconds": 492,
  "digest": "1dca8249c0d16b85a6c8c2b7ec94e3f863f5479736ae6e59837d0d6da5bc10ef",
  "event_count": 8,
  "events": [
    { "seq": 1, "ts": "2026-10-02T21:10:22+00:00", "type": "why",
      "text": "gateway accepted plaintext on 8443; recorder must reject non-mTLS clients",
      "prev_hash": "0000...0000",
      "hash": "d844590c2ba0666cc1f06b1b23d4fae466eb6cd7b3e25e46f7219df090a3e8de" },
    { "seq": 2, "ts": "2026-10-02T21:10:22+00:00", "type": "open",
      "text": "https://localhost:8443/v1/events  recorder ingest, now mTLS-gated",
      "prev_hash": "d844...8de",
      "hash": "4f2b64ea55be71a7e87550575e015cbc7becfd104c8c278a11d4f211608be951" },
    { "seq": 3, "ts": "2026-10-02T21:10:22+00:00", "type": "milestone",
      "text": "mTLS enforced end to end; /v1/events requires client cert",
      "prev_hash": "4f2b...951",
      "hash": "75e790b1210cd26f65e6b946aea0a376246fd45c7ea38af6e359bf4637c6c132" },
    { "seq": 4, "ts": "2026-10-02T21:10:26+00:00", "type": "cmd",
      "cwd": "/home/rev/prooflayer", "command": "docker compose up -d recorder gateway",
      "exit": 0, "duration": 12, "term": "pane-left",
      "prev_hash": "75e7...132",
      "hash": "5436ab8986117cc1423dd8d4d5c5b76355110294e05e666478896131663b71ce" }
  ],
  "artifacts": [
    { "type": "ports",   "services": [ { "local": "127.0.0.1:8443", "proc": "gateway" } ] },
    { "type": "gitdiff", "repo": "/home/rev/prooflayer", "branch": "main",
      "head": "a1b9c0d", "stat": " recorder/tls.go | 34 +++++---", "patch": "diff --git ..." }
  ]
}
```

The server replies:
```json
{ "ok": true, "id": "20261002-211021-47d5", "bucket": "writeups",
  "url": "/writeups/20261002-211021-47d5" }
```

### Where it lands (Postgres)

The ingest route writes three tables (full DDL in `server/schema.sql`):

- **`tp_session`** — one row: id, title, project, bucket, host, start/end,
  `active_seconds`, `digest`, `event_count`, and a `published` flag you control.
- **`tp_event`** — one row per timeline entry (cmd / why / note / milestone /
  open), with `seq`, `hash`, `prev_hash` so the chain is reconstructable in SQL.
- **`tp_artifact`** — git diffs and port snapshots as `jsonb`.

Re-pushing replaces that session's events/artifacts, so routing to a new bucket
or fixing a title and pushing again just updates the one record.

### How it looks in the UI

The record is structured so a session maps cleanly onto a Writeup:

```
┌─ /writeups/20261002-211021-47d5 ─────────────────────────────┐
│  prooflayer sidecar: mTLS between gateway and recorder        │
│  ProofLayer · 8m12s at the desk · ✓ verified                  │
│                                                               │
│  Why                                                          │
│   gateway accepted plaintext on 8443; recorder must reject…   │
│                                                               │
│  ▸ mTLS enforced end to end           ← milestone = a heading │
│     $ docker compose up -d recorder gateway            ok     │
│     $ openssl req -x509 -newkey rsa:4096 …              ok     │
│     $ curl -sk https://localhost:8443/healthz          ok     │
│     diff recorder/tls.go  (+34/-11)        ▸ expand           │
│                                                               │
│  Services seen:  127.0.0.1:8443 (gateway)                     │
└───────────────────────────────────────────────────────────────┘
```

Milestones become section headers, `why`/`note` become prose, commands become
fenced blocks with their exit status, git diffs become collapsibles, and the
`✓ verified` badge is the server recomputing the same sha256 chain. That
rendering is **your call to build** in the app — ingest just gives it everything
it needs. (I can build that renderer next.)

---

## 5. Config reference (`~/.tp/config.json`)

| key | default | meaning |
|---|---|---|
| `api_url` | `https://rev-engineering.vercel.app` | your site's base URL |
| `ingest_path` | `/api/ingest/session` | the ingest route path |
| `token` | `""` | bearer token; must equal server `TP_INGEST_TOKEN` |
| `host` | hostname | stamped on every session |
| `capture_ports` | `true` | snapshot listening localhost services |
| `capture_gitdiff` | `true` | snapshot git diffs for repos you touched |
| `max_patch_bytes` | `200000` | cap a single repo's stored patch |
| `redact_patterns` | `[]` | extra regexes to redact (your own secret shapes) |

Override the whole vault location with `export TP_HOME=/path` (e.g. to keep it
inside a repo you back up).

---

## 6. Redaction & security

Before anything is written to the sealed timeline or pushed, command text runs
through redaction. Built-in: `--password/--token/--secret/--api-key/...`,
`NAME_TOKEN=value`, bare `token=/secret=/password=/apikey=` (any case),
`Bearer <...>`, AWS access keys (`AKIA…`), GitHub tokens (`ghp_…`), private-key
headers. Add your own shapes:
```bash
tp config redact_patterns '["corp_[A-Za-z0-9]{20}", "vault:[^ ]+"]'
```
Two more layers:
- **Space-prefix a command** (with `HISTCONTROL=ignorespace`) and it's never
  recorded at all — use this for anything you don't want on disk.
- **`tp pause`** before doing something you don't want captured.

Capture is **command-level**, not a PTY transcript: it records what you ran and
how it exited, never keystrokes or screen output. That keeps records small and
safe to publish. Treat the DB as sensitive anyway — it describes your lab; don't
expose those tables through any public route.

---

## 7. Multi-terminal & tmux

Every shell with the hook loaded feeds the **one** active session — that's the
design. To get readable terminal labels in the timeline instead of PIDs, export
`TP_TERM` per pane:
```bash
# in a tmux pane, a terminal profile, or an ssh rc
export TP_TERM=pane-left
export TP_TERM=t320-ai-core      # e.g. when ssh'd into a lab VM
```
Install the hook on lab VMs too (same `tp hook` line in their shell rc, same
`TP_HOME` if you NFS-share it, or a per-host vault you push from). A command run
over ssh is captured by the shell *on that host*, so run `tp` where the vault is.

---

## 8. Server integration (rev-engineering)

1. **Tables**
   ```bash
   psql "$DATABASE_URL" -f server/schema.sql
   ```
2. **Route** — copy `server/app/api/ingest/session/route.ts` to the same path in
   your app. It needs `pg`:
   ```bash
   npm i pg
   ```
   It pins `runtime = "nodejs"` (not Edge, because it uses a TCP Postgres pool).
3. **Env** (Vercel project settings)
   ```
   DATABASE_URL=postgres://…
   TP_INGEST_TOKEN=<long random string>
   ```
4. **Client**
   ```bash
   tp config api_url https://rev-engineering.vercel.app
   tp config token   <same string as TP_INGEST_TOKEN>
   ```
5. **Smoke test**
   ```bash
   tp push <id> --to writeups          # expect: ↑ pushed … /writeups/<id>
   curl -s -X POST https://rev-engineering.vercel.app/api/ingest/session \
     -H 'content-type: application/json' -d '{}'    # expect: 401 (no token)
   ```

**Prerequisite:** this assumes rev-engineering has a reachable Postgres and runs
server routes. If the site is currently static-only on Vercel with no database,
stand up a Postgres (Vercel Postgres / Neon / Supabase) and set `DATABASE_URL`
first — that's the one piece that must exist before `tp push` has a home.

---

## 9. Troubleshooting

| symptom | cause / fix |
|---|---|
| nothing captured | hook not loaded in that shell (`tp hook <shell>` in your rc), or no active session (`tp status`), or paused |
| a secret showed up in `tp show` | add a pattern to `redact_patterns`; re-run `tp stop` is not needed — finalize re-redacts on `show`/`verify` from source |
| `tp verify` fails | the sealed `events.jsonl` was edited after stop — that's the point; the record is no longer trustworthy |
| `push failed … 401` | client `token` ≠ server `TP_INGEST_TOKEN` |
| `push failed … could not reach` | wrong `api_url`, route not deployed, or offline |
| `push failed … 500 … TP_INGEST_TOKEN` | server env var not set |
| hook feels slow | it shells out to `python3` once per command; `tp pause` during heavy scripted loops |

---

## 10. Not in v1 (candidates next)

- The UI renderer described in §4 (session → Writeup draft) + server-side
  `verified` badge.
- `tp resume <id>` to reopen a stopped session across days.
- Dedup of identical consecutive port snapshots.
- Richer browser capture beyond `tp open` / localhost ports.
- A `tp sync` to push every un-pushed session in one shot.
