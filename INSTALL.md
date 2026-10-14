# Installing tp

Requires Python 3.11+ (Fedora Zephyrus has this). Zero third-party deps for the
CLI itself.

## 1. Install the CLI

**Recommended (v0.3): the launcher.** Output capture needs `tp` to be a real
program your terminals can exec — a fish function or alias isn't enough.

```bash
sh ~/tp-deskflight/install.sh     # writes ~/.local/bin/tp -> this checkout
```

No pip, no venv: tp is stdlib-only, so the launcher just runs this folder with
`python3`. `git pull` in the folder upgrades it. Python packaging also works:

```bash
# from the unzipped folder
pipx install ./tp-deskflight          # recommended (isolated)
# or
pip install --user ./tp-deskflight    # puts `tp` on your PATH
# or, to hack on it
pip install --user -e ./tp-deskflight
```

Check: `tp --version`

**No-install fallback.** If you'd rather not install, run it straight from the
folder — everything is stdlib:
```bash
alias tp='python3 -m tp.cli'      # run from inside tp-deskflight/, or set PYTHONPATH
# e.g.  PYTHONPATH=/opt/tp-deskflight python3 -m tp.cli status
```

## 2. Add the shell hook

The hook is what captures commands in every terminal. Add the line for your
shell, then open a new terminal.

**bash** (`~/.bashrc`)
```bash
eval "$(tp hook bash)"
export HISTCONTROL=ignorespace   # so a space-prefixed command is never recorded
```

**zsh** (`~/.zshrc`)
```zsh
eval "$(tp hook zsh)"
```

**fish** (`~/.config/fish/config.fish`)
```fish
tp hook fish | source
```

Open a **new** terminal afterwards and run `tp doctor`.

What the hook does (v0.3):
- At the first prompt it re-launches the shell inside `tp pty`, once, so each
  command's output can be captured. Set `TP_NO_PTY=1` to skip that in one
  shell, or `tp config pty off` to never do it. If the PTY can't be set up,
  you simply get the plain shell.
- After every command it journals the command, cwd, exit code, timing and
  output to `~/.tp/journal/` — whether or not a session is running. That's
  what lets `tp start --from` backfill. `tp off` stops it.

Nothing is written for space-prefixed commands, and `tp ...` commands are
filtered out.

### Naming your terminals (optional)

Each event stores a terminal id. By default that's the shell PID. To get
readable labels in the timeline, export `TP_TERM` per terminal:

```bash
export TP_TERM=pane-left     # e.g. in a tmux pane or terminal profile
```

## 3. Point it at Rev Vision

```bash
tp config api_url      https://rev-engineering.vercel.app
tp config ingest_path  /api/ingest/session
tp config token        <YOUR_INGEST_TOKEN>     # must match server TP_INGEST_TOKEN
```

Config is stored at `~/.tp/config.json` (the token is shown as `(set)` in
`tp config`). Set up the server first — see [`server/README.md`](server/README.md).

## 4. Record

```bash
tp start "first real session" -p CyberDen
# ... work ...
tp stop --to writeups --push

# forgot to start? pull the earlier work in:
tp start "first real session" -p CyberDen --from 21:40
tp extend --from 20:30          # mid-session, go back further
```

## Upgrading from 0.2

1. `git pull` (or unzip over) `~/tp-deskflight`, then `sh ~/tp-deskflight/install.sh`.
2. Keep `tp hook fish | source` in `config.fish`; it now prints the v0.3 hook.
3. Open new terminals (already-open ones keep the old hook, commands only).
4. If you run Rev Vision ingest: `psql "$DATABASE_URL" -f server/schema.sql`
   (adds the v0.3 columns; safe on an existing database) and redeploy the route.

Sessions sealed by 0.2 verify unchanged. A stopped 0.2 session that you haven't
pushed can be backfilled: `tp reopen <id>`, `tp extend --from <time>`,
`tp stop`. Its earlier hour comes from fish history (the journal didn't exist
yet), tagged `history`.

## Where state lives

```
~/.tp/
  config.json
  current                      # active session id ("" when idle)
  paused                       # exists only while paused
  off, off.d/<pid>             # exist only while capture is off
  journal/                     # always-on, chained as written (0700)
    YYYY-MM-DD.jsonl           # one record per command, output included
    tip.json                   # last record's hash
  spool/                       # tp pty hands output to the hook here (ms-lived)
  sessions/<id>/
    meta.json                  # title, project, segments (live + backfill), digest
    notes.jsonl                # why / note / milestone / open
    history.jsonl              # fish-history imports for backfill gaps
    events.raw.jsonl           # 0.2 sessions only (old hook)
    artifacts.jsonl            # git diffs, port snapshots
    events.jsonl               # sealed, hash-chained timeline
    events.sealed-N.json       # earlier seals, if the session was reopened
```

Override the whole location with `export TP_HOME=/path/to/vault` (useful if you
want the vault inside a git repo you also back up).

## Uninstall

```bash
rm ~/.local/bin/tp               # launcher install; or: pipx uninstall tp-deskflight
# then remove the hook line from your shell rc, and  rm -rf ~/.tp/journal  if you like
```
