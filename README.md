# tp — Deskflight Recorder

A **start / stop / pause** recorder for everything you do at the workstation and
in the lab. Work across as many terminals as you like, and `tp` captures the
commands you run **and what they printed**, the reasoning you attach, the git
diffs you produce, and the localhost services you bring up — then seals the
session into a **tamper-evident hash chain** and pushes it to **Rev Vision**,
where you route it to Writeups, Projects, or the Tech feed.

**Forgot to hit record?** Since v0.3 every hooked terminal journals all the
time, so `tp start --from 21:40 "..."` (or `tp extend --from 1h` mid-session)
pulls the earlier work in, in timestamp order, output included.

It's ProofLayer pointed at *you*: proof-of-work for every hour you sit at the
desk, not a log you have to trust.

```
tp start "owning the k3s ingress" --project CyberDen --from 45m   # include the last 45 min
#  ... work in terminal 1, terminal 2, a browser on localhost:3000 ...
tp why "ingress 404 was a missing host rule, not TLS"
tp milestone "auth path fixed, serving on :3000"
tp pause              # grab coffee — the clock stops
tp resume
tp stop --to writeups --push
tp verify 20261002-...   # ✓ chain intact
```

## Why it's built this way

- **Always recording, sessions are windows.** A tiny shell hook journals every
  command (and, through `tp pty`, its output) to `~/.tp/journal/`, chained as
  it's written. A session is a time window cut from that journal, so it can
  start before you typed `tp start`.
- **One recorder, every terminal.** Open ten terminals; they all feed the same
  journal, and the active session takes from all of them.
- **Honor-system timer.** `pause`/`resume` are real clock controls — the active
  time is summed from recording segments, which is exactly the per-session
  timer Rev Vision wants.
- **Tamper-evident.** Every event is sha256-chained to the one before it, and
  the session gets a digest. Change a past command and `tp verify` fails. Your
  proof-of-work is verifiable, not just asserted.
- **Secrets stay out.** Commands *and output* are redacted before they touch
  disk (tokens, `--password`, `key=value` / `key: value` secrets, AWS/GitHub/
  OpenAI/Slack/JWT shapes, private-key blocks, random-looking high-entropy
  strings). A space-prefixed command is never recorded, output included.
  `tp off` stops all capture; output is **not pushed** unless you ask.
- **Direct to Rev Vision.** `tp push` sends straight to your app's ingest API;
  you decide in the UI where each session is published.

## Commands

| Command | What it does |
|---|---|
| `tp start "<title>" [-p PROJECT] [--from WHEN]` | begin a session, optionally backfilled (`21:40`, `1h`, `'2026-10-12 21:40'`) |
| `tp extend --from WHEN` | backfill the active session further back |
| `tp reopen <id>` | unseal a stopped (unpushed) session to extend / continue it |
| `tp pause` / `tp resume` | stop / restart the clock + capture |
| `tp why "<text>"` | record reasoning (the *why* behind a move) |
| `tp note "<text>"` | record a plain note |
| `tp milestone "<text>"` | checkpoint + snapshot code & services now |
| `tp open <url> [label]` | log a localhost / URL you're testing |
| `tp status` | show the active session |
| `tp stop [--to BUCKET] [--push]` | finish, seal, optionally route + push |
| `tp ls` | list recent sessions |
| `tp show <id> [--output]` | print a session's timeline (with what each command printed) |
| `tp verify <id>` | recompute the hash chain; reports live / backfilled / history counts |
| `tp route <id> --to BUCKET [--push]` | assign a bucket |
| `tp push <id> [--to BUCKET] [--with-output]` | push to Rev Vision (output stays local unless asked) |
| `tp archive <id>… \| --pushed \| --older-than 30d` | move sealed sessions to the archive path (default `~/.tp/archive`), verified copy first |
| `tp unarchive <id>` | bring an archived session back |
| `tp dispose <id> [--journal] [--remote]` | delete a recording on purpose (type the id to confirm; logged, no content) |
| `tp dispose --journal-from WHEN [--until WHEN]` | wipe a stretch of the journal (e.g. you printed a secret) |
| `tp journal [--since WHEN] \| verify \| prune \| clear` | look at / check the always-on journal |
| `tp off` / `tp on` `[--window]` | stop / restart capture everywhere, or just this terminal |
| `tp doctor` | check the hook, the PTY wrapper and the journal are wired up |
| `tp config [key] [value]` | get/set config |
| `tp hook <bash\|zsh\|fish>` | print the shell hook to source |

Buckets: `writeups` · `projects` · `tech-feed` · `archive` (aliases: `w`,`p`,`t`,`a`,`raw`,`feed`).

## What a session records

- **Commands** — command text (redacted), cwd, exit code, timing, which terminal.
- **Output** — what each command printed (cleaned, redacted, capped at 64 KB as
  head + tail). Full-screen programs (vim, htop, less) are recorded as having
  run, not their screens.
- **Origin** — every command is tagged `live`, `journal` (captured before
  `tp start`, pulled in by `--from`/`extend`) or `history` (imported from fish
  history for time the journal didn't cover: command text only).
- **Reasoning** — your `why` / `note` / `milestone` entries, interleaved in time.
- **Code** — on every milestone and at `stop`, a `git diff` snapshot (branch,
  HEAD, stat, patch) for each repo you touched.
- **Services** — a snapshot of listening localhost ports (your dev servers).
- **Integrity** — per-event hashes, a chain, and a session digest.

Everything lives under `~/.tp/` (override with `$TP_HOME`). Nothing leaves your
machine until you `tp push`.

## Where recordings live

| | path | how they get there |
|---|---|---|
| journal (rolling) | `~/.tp/journal/` | automatically; 7 days / 1 GB |
| sessions (working set) | `~/.tp/sessions/<id>/` | `tp start` … `tp stop` |
| archive (cold) | `~/.tp/archive/<id>/` — or `tp config archive_path /mnt/rev-ssd/tp-archive` | `tp archive` |
| Rev Vision | your Postgres | `tp push` |
| gone | — | `tp dispose` (logged in `~/.tp/disposed.jsonl`, id/title/digest only) |

The `archive` *bucket* (`tp stop --to archive`) is separate: it's where a
session is filed on Rev Vision, not where its files sit on disk.

## Tests

Every command has a purpose test — each one checks the thing the command is
for, against a throwaway `TP_HOME` (your `~/.tp` is never touched):

```bash
cd ~/tp-deskflight
python3 -m unittest discover -s tests -v       # ~90 tests, about a minute
python3 -m unittest tests.test_commands.Dispose  # one command
```

`tests/test_terminals.py` drives real fish and bash sessions through the hook
and `tp pty` (skipped for a shell that isn't installed). A guard test fails if
a command is added without a test.

## Install

See [INSTALL.md](INSTALL.md). Short version:

```bash
sh tp-deskflight/install.sh                  # puts a real `tp` in ~/.local/bin
echo 'tp hook fish | source' >> ~/.config/fish/config.fish   # bash/zsh: see INSTALL.md
# open a NEW terminal, then:
tp doctor
tp config api_url  https://rev-engineering.vercel.app
tp config token    <YOUR_INGEST_TOKEN>
```

The Rev Vision side (Postgres schema + Next.js ingest route) is in
[`server/`](server/README.md).

## Notes & limits (v0.3)

- Output capture works per command, through the `tp pty` wrapper the hook
  starts in each new interactive shell. Keystrokes are never recorded. Inside
  an `ssh` session the remote output is captured as the output of the `ssh`
  command (one block); commands you type on the remote host are not split out
  unless that host runs tp too.
- Backfill reaches as far as the journal: 7 days / 1 GB by default. Before the
  journal existed, fish history fills in commands only (fish keeps only the
  latest run of a repeated command, so repeats can be missing).
- The hook shells out to `python3` once per command (~80 ms). Fine
  interactively; for scripted loops, `tp off --window` first.
- Redaction is good, not magic. Prefix sensitive one-off commands with a space,
  and add your own patterns with `tp config` → `redact_patterns`.
- Browser/app activity beyond localhost ports isn't auto-captured; use
  `tp open` / `tp note` to mark those moments.
