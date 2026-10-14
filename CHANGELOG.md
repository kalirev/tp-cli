# Changelog

## 0.3.0 — 2026-10-14

**Record first, decide later.** Capture no longer depends on remembering
`tp start`.

- **Always-on journal** (`~/.tp/journal/`): every command in every hooked
  terminal, redacted and hash-chained as it is written. 7 days / 1 GB retention.
- **Output capture**: the hook re-launches each new interactive shell inside
  `tp pty`, which slices the terminal stream per command (start/end markers),
  strips escape codes, applies progress-bar overwrites, redacts, and keeps the
  first and last 32 KB. Full-screen programs are noted, not recorded.
- **Backfill**: `tp start --from 21:40` / `--since 1h`, and `tp extend --from`
  on a running session. Merged in timestamp order. Journal gaps fall back to
  fish history (commands only), labelled `history`.
- **`tp reopen <id>`**: unseal a stopped, unpushed session; earlier digests are
  kept (`prior_digests`, `events.sealed-N.json`).
- **Origins everywhere**: each command is `live`, `journal` or `history`;
  `stop`, `status`, `show` and `verify` report them. Backfilled active time is
  estimated and reported separately.
- **Verify** also checks output against its sealed hash and backfilled commands
  against their journal records.
- **Push** leaves output local unless `--with-output`; the chain still verifies
  because `output_sha256` is sealed. Server: new columns (`server/schema.sql`,
  idempotent) and route fields.
- **Archive path**: `tp archive` moves sealed sessions to `~/.tp/archive` (or
  `tp config archive_path …`, e.g. an external SSD — refused if not mounted)
  after a verified copy; `tp unarchive`; `tp ls --archived`; archived sessions
  still `show` / `verify` / `push`.
- **Purposeful disposal**: `tp dispose <id>` (type the id to confirm;
  `--journal` also wipes its journal records; `--remote` deletes it from Rev
  Vision first) and `tp dispose --journal-from … --until …`. Journal disposal
  leaves tombstones so the chain still verifies and the count is reported.
  Every disposal is logged (id/title/digest, no content). Server: `DELETE` on
  the ingest route.
- **Tests for every command** (`tests/`): ~90 purpose tests, including real
  fish/bash terminals through `tp pty`, plus a guard that fails when a command
  has no test. `python3 -m unittest discover -s tests -v`.
- New: `tp off|on [--window]`, `tp journal [verify|prune|clear]`, `tp doctor`,
  `tp pty`, `install.sh` (launcher in `~/.local/bin`, no pip needed).
- `tp start` with no title prompts for one.
- Fixes: `TP_TERM` is now honoured (the manual already promised it);
  `tp config redact_patterns '[...]'` is stored as a list (0.2 stored a string
  and applied it character by character — existing configs are read correctly);
  the bash hook no longer records a stale history entry at the first prompt.
- Compatible: sessions sealed by 0.2 verify unchanged; a 0.2 session's
  `events.raw.jsonl` is still read.
