# Installing tp

Requires Python 3.11+ (Fedora Zephyrus has this). Zero third-party deps for the
CLI itself.

## 1. Install the CLI

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

The hook is cheap and silent: when no session is active it does almost nothing.
It only records while a session is `recording` (not `paused`).

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
```

## Where state lives

```
~/.tp/
  config.json
  current                      # active session id ("" when idle)
  paused                       # exists only while paused
  sessions/<id>/
    meta.json                  # title, project, timer segments, digest
    events.raw.jsonl           # written by the hook (every terminal)
    notes.jsonl                # why / note / milestone / open
    artifacts.jsonl            # git diffs, port snapshots
    events.jsonl               # sealed, redacted, hash-chained timeline
```

Override the whole location with `export TP_HOME=/path/to/vault` (useful if you
want the vault inside a git repo you also back up).

## Uninstall

```bash
pipx uninstall tp-deskflight     # or pip uninstall tp-deskflight
# then remove the hook line from your shell rc
```
