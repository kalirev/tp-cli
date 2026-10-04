# tp — Deskflight Recorder

A **start / stop / pause** recorder for everything you do at the workstation and
in the lab. Hit record, work across as many terminals as you like, and `tp`
captures the commands you run, the reasoning you attach, the git diffs you
produce, and the localhost services you bring up — then seals the whole
session into a **tamper-evident hash chain** and pushes it to **Rev Vision**,
where you route it to Writeups, Projects, or the Tech feed.

It's ProofLayer pointed at *you*: proof-of-work for every hour you sit at the
desk, not a log you have to trust.

```
tp start "owning the k3s ingress" --project CyberDen
#  ... work in terminal 1, terminal 2, a browser on localhost:3000 ...
tp why "ingress 404 was a missing host rule, not TLS"
tp milestone "auth path fixed, serving on :3000"
tp pause              # grab coffee — the clock stops
tp resume
tp stop --to writeups --push
tp verify 20261002-...   # ✓ chain intact
```

## Why it's built this way

- **One recorder, every terminal.** A tiny shell hook in each shell appends to
  whichever session is active. Open ten terminals; they all feed the same take.
- **Honor-system timer.** `pause`/`resume` are real clock controls — the active
  time is summed from recording segments, which is exactly the per-session
  timer Rev Vision wants.
- **Tamper-evident.** Every event is sha256-chained to the one before it, and
  the session gets a digest. Change a past command and `tp verify` fails. Your
  proof-of-work is verifiable, not just asserted.
- **Secrets stay out.** Commands are redacted before anything is written or
  pushed (tokens, `--password`, `key=value` secrets, AWS/GitHub keys, private
  keys). A space-prefixed command (with `HISTCONTROL=ignorespace`) is never
  recorded at all.
- **Direct to Rev Vision.** `tp push` sends straight to your app's ingest API;
  you decide in the UI where each session is published.

## Commands

| Command | What it does |
|---|---|
| `tp start "<title>" [-p PROJECT]` | begin recording |
| `tp pause` / `tp resume` | stop / restart the clock + capture |
| `tp why "<text>"` | record reasoning (the *why* behind a move) |
| `tp note "<text>"` | record a plain note |
| `tp milestone "<text>"` | checkpoint + snapshot code & services now |
| `tp open <url> [label]` | log a localhost / URL you're testing |
| `tp status` | show the active session |
| `tp stop [--to BUCKET] [--push]` | finish, seal, optionally route + push |
| `tp ls` | list recent sessions |
| `tp show <id>` | print a session's timeline |
| `tp verify <id>` | recompute the hash chain |
| `tp route <id> --to BUCKET [--push]` | assign a bucket |
| `tp push <id> [--to BUCKET]` | push to Rev Vision |
| `tp config [key] [value]` | get/set config |
| `tp hook <bash\|zsh\|fish>` | print the shell hook to source |

Buckets: `writeups` · `projects` · `tech-feed` · `archive` (aliases: `w`,`p`,`t`,`a`,`raw`,`feed`).

## What a session records

- **Commands** — command text (redacted), cwd, exit code, timing, which terminal.
- **Reasoning** — your `why` / `note` / `milestone` entries, interleaved in time.
- **Code** — on every milestone and at `stop`, a `git diff` snapshot (branch,
  HEAD, stat, patch) for each repo you touched.
- **Services** — a snapshot of listening localhost ports (your dev servers).
- **Integrity** — per-event hashes, a chain, and a session digest.

Everything lives under `~/.tp/` (override with `$TP_HOME`). Nothing leaves your
machine until you `tp push`.

## Install

See [INSTALL.md](INSTALL.md). Short version:

```bash
pipx install ./tp-deskflight        # or: pip install --user ./tp-deskflight
echo 'eval "$(tp hook bash)"' >> ~/.bashrc   # zsh/fish: see INSTALL.md
echo 'export HISTCONTROL=ignorespace' >> ~/.bashrc
tp config api_url  https://rev-engineering.vercel.app
tp config token    <YOUR_INGEST_TOKEN>
```

The Rev Vision side (Postgres schema + Next.js ingest route) is in
[`server/`](server/README.md).

## Notes & limits (v1)

- Capture is **command-level**, not a full PTY transcript; it records *what you
  ran and how it ended*, not keystrokes or screen output. That keeps records
  small, readable, and safe to publish.
- The hook shells out to `python3` once per command. Fine interactively; if you
  script thousands of commands, `tp pause` first.
- Redaction is good, not magic. Prefix sensitive one-off commands with a space,
  and add your own patterns with `tp config` → `redact_patterns`.
- Browser/app activity beyond localhost ports isn't auto-captured; use
  `tp open` / `tp note` to mark those moments.
