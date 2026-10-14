"""tp command-line interface."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import os

from . import __version__, config, hashchain, history, hooks, journal, push as pushmod, session, store, vault

BUCKETS = {
    "writeups": "writeups", "writeup": "writeups", "w": "writeups",
    "projects": "projects", "project": "projects", "p": "projects",
    "tech-feed": "tech-feed", "techfeed": "tech-feed", "feed": "tech-feed", "t": "tech-feed",
    "archive": "archive", "raw": "archive", "a": "archive",
}

C = {"dim": "\033[2m", "b": "\033[1m", "g": "\033[32m", "y": "\033[33m",
     "r": "\033[31m", "c": "\033[36m", "x": "\033[0m"}


def _c(key: str, text: str) -> str:
    if not sys.stdout.isatty():
        return text
    return f"{C[key]}{text}{C['x']}"


def _fmt_dur(secs: int) -> str:
    h, rem = divmod(int(secs), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def _bucket(name: str | None) -> str | None:
    if name is None:
        return None
    key = BUCKETS.get(name.lower())
    if key is None:
        raise SystemExit(f"unknown bucket '{name}'. choose: writeups | projects | tech-feed | archive")
    return key


# ---- commands ---------------------------------------------------------------

def _when(text, ref=None):
    """Parse --from/--since. Durations and bare clock times count back from
    `ref` (default now) - for `tp extend` that is the session's start, so
    `tp extend --from 21:40` on a session reopened days later still means
    21:40 on the day the session began."""
    if text is None:
        return None
    try:
        return history.parse_when(text, ref)
    except ValueError as e:
        raise SystemExit(str(e))


def cmd_start(a):
    title = " ".join(a.title).strip()
    if not title and sys.stdin.isatty():
        try:
            title = input("session title: ").strip()
        except EOFError:
            title = ""
    if not title:
        raise SystemExit('give the session a title:  tp start "owning the k3s ingress"')
    meta = session.start(title, a.project, from_ts=_when(a.from_), use_history=not a.no_history)
    print(_c("g", "● recording") + f"  {meta['id']}")
    print(f"  {_c('b', meta['title'])}" + (f"   project={meta['project']}" if meta['project'] else ""))
    if a.from_:
        _print_backfill(meta)
    print(_c("dim", "  open new terminals freely - every one of them feeds this session."))
    print(_c("dim", "  tp why \"...\"   tp milestone \"...\"   tp pause   tp stop --to writeups --push"))


def _print_backfill(meta):
    events = session.collect(meta["id"])
    seg = next(s for s in meta["segments"] if s.get("kind") == "backfill")
    lo, hi = session._epoch(seg["start"]), session._epoch(seg["end"])
    inside = [e for e in events if e.get("type") == "cmd" and lo <= session._epoch(e["ts"]) < hi]
    j = sum(1 for e in inside if e.get("origin") == "journal")
    h = sum(1 for e in inside if e.get("origin") == "history")
    print(_c("c", "  ↶ backfilled") + f"  {history.fmt_local(lo)} → {history.fmt_local(hi)}"
          f"   {j} from journal" + (f" (with output)" if any("output" in e for e in inside) else "")
          + (f", {h} from fish history (commands only)" if h else ""))


def cmd_extend(a):
    import datetime as dt
    sid = store.active_id()
    if not sid:
        raise SystemExit("no active session (tp start, or tp reopen <id> first)")
    ref = dt.datetime.fromtimestamp(session.first_live_start(session.load_meta(sid))).astimezone()
    meta = session.extend(_when(a.from_, ref), use_history=not a.no_history)
    _print_backfill(meta)


def cmd_reopen(a):
    meta = session.reopen(a.id, force=a.force)
    print(_c("g", "● recording") + f"  reopened {meta['id']}  {_c('b', meta['title'])}")
    print(_c("dim", f"  previous digest kept in the record. backfill with:  tp extend --from 21:40"))


def cmd_pause(a):
    meta = session.pause()
    print(_c("y", "∥ paused") + f"   {_fmt_dur(session.active_seconds(meta))} on the clock")


def cmd_resume(a):
    meta = session.resume()
    print(_c("g", "● recording") + f"   resumed {meta['id']}")


def cmd_stop(a):
    bucket = _bucket(a.to)
    meta = session.stop(push_to=bucket)
    print(_c("r", "■ stopped") + f"   {meta['id']}")
    br = meta.get("active_breakdown", {})
    est = f" (incl. ~{_fmt_dur(br['estimated'])} estimated for backfill)" if br.get("estimated") else ""
    print(f"  {meta['event_count']} events   {_fmt_dur(meta['active_seconds'])} active{est}")
    print(f"  {_origins(meta.get('origins'))}")
    print(f"  digest {_c('c', meta['digest'][:16])}…   " + _c("dim", "(tp verify " + meta['id'] + ")"))
    if a.push:
        _do_push(meta["id"], bucket, a.with_output)
    elif bucket:
        print(_c("dim", f"  routed to {bucket}; push with:  tp push {meta['id']}"))


def _origins(o):
    if not o:
        return ""
    parts = [f"{o.get('live', 0)} live"]
    if o.get("journal"):
        parts.append(f"{o['journal']} backfilled from journal")
    if o.get("history"):
        parts.append(f"{o['history']} from fish history (commands only)")
    return _c("dim", " · ".join(parts))


def _capture_line():
    cfg = config.load()
    if not cfg.get("journal", True):
        return _c("y", "journal off (config)") + _c("dim", " - only records inside a session, commands only")
    if journal.is_off(os.getppid()):
        return _c("y", "capture OFF") + _c("dim", "  (tp on" + (" --window" if not (store.home() / "off").exists() else "") + ")")
    try:
        here = os.ttyname(0)
    except OSError:
        here = ""
    out = _c("g", "commands + output") if here and os.environ.get("TP_PTY") == here \
        else _c("y", "commands only") + _c("dim", " (this terminal isn't in tp pty - see tp doctor)")
    return "journal on · this terminal: " + out


def cmd_status(a):
    print(_capture_line())
    sid = store.active_id()
    if not sid:
        print(_c("dim", 'idle - no active session.  tp start "<title>"  (add --from 21:40 to backfill)'))
        return
    meta = session.load_meta(sid)
    events = session.collect(sid)
    cmds = [e for e in events if e.get("type") == "cmd"]
    state = _c("y", "∥ paused") if meta["status"] == "paused" else _c("g", "● recording")
    print(f"{state}   {meta['id']}")
    print(f"  {_c('b', meta['title'])}" + (f"   project={meta['project']}" if meta['project'] else ""))
    print(f"  {len(cmds)} commands ({sum(1 for e in cmds if 'output' in e)} with output)"
          f"   {_fmt_dur(session.active_seconds(meta))} live on the clock")
    print(f"  {_origins(session.origin_counts(events))}")
    print(_c("dim", f"  window starts {history.fmt_local(session.window_start(meta), True)}"))


def cmd_mark(a, kind):
    text = " ".join(a.text).strip()
    if not text:
        raise SystemExit(f"tp {kind} needs some text")
    session.mark(kind, text)
    label = {"why": "why", "note": "note", "milestone": "milestone"}[kind]
    print(_c("c", f"+ {label}") + f"  {text}")


def cmd_open(a):
    url = a.url
    label = " ".join(a.label).strip()
    session.mark("open", f"{url}  {label}".strip())
    print(_c("c", "+ localhost") + f"  {url}" + (f"  ({label})" if label else ""))


def cmd_ls(a):
    if a.archived:
        ids = vault.archived_ids()
        print(_c("dim", f"archive: {store.archive_root()}"))
        if not ids:
            print(_c("dim", "  nothing archived yet.  tp archive <id>"))
            return
        for sid in reversed(ids[-a.n:]):
            meta = session.load_meta(sid) or {}
            print(f"  {sid}  {_fmt_dur(meta.get('active_seconds', 0)):>7}  "
                  f"{(meta.get('bucket') or '-'):<10}   {meta.get('title', '')}")
        return
    ids = store.list_sessions()
    if not ids:
        print(_c("dim", "no sessions yet."))
        return
    active = store.active_id()
    for sid in reversed(ids[-a.n:]):
        meta = session.load_meta(sid)
        if not meta:
            continue
        flag = _c("g", "●") if sid == active else (
            _c("y", "∥") if meta["status"] == "paused" else " ")
        bucket = meta.get("bucket") or "-"
        pushed = _c("g", "↑") if meta.get("digest") and bucket != "-" else " "
        print(f"{flag} {sid}  {_fmt_dur(meta.get('active_seconds', session.active_seconds(meta))):>7}"
              f"  {bucket:<10} {pushed} {meta['title']}")


def cmd_show(a):
    sid = a.id
    meta = session.load_meta(sid)
    if not meta:
        raise SystemExit(f"unknown session {sid}")
    events = store.read_json(store.session_dir(sid) / "events.jsonl", default=[]) or []
    if not events:  # not finalized yet
        session.finalize(sid)
        events = store.read_json(store.session_dir(sid) / "events.jsonl", default=[]) or []
    print(_c("b", meta["title"]) + f"   {sid}")
    print(f"  status={meta['status']}  active={_fmt_dur(meta.get('active_seconds', 0))}"
          f"  bucket={meta.get('bucket') or '-'}")
    if meta.get("digest"):
        print(f"  digest {_c('c', meta['digest'])}")
    print(_c("dim", "  timeline:"))
    tag = {"journal": _c("c", "↶"), "history": _c("y", "h")}
    for ev in events[-a.n:]:
        ts = history.fmt_local(session._epoch(ev["ts"]))
        if ev["type"] == "cmd":
            if ev.get("exit") is None:
                ok = _c("dim", " ?")
            else:
                ok = _c("g", "ok") if ev.get("exit") == 0 else _c("r", f"e{ev.get('exit')}")
            cmd = ev.get("command", "")
            lines = cmd.split("\n")
            more = _c("dim", f"  (+{len(lines) - 1} lines)") if len(lines) > 1 else ""
            print(f"    {ts} {tag.get(ev.get('origin'), ' ')} {ok:>4} {lines[0]}{more}")
            if a.output and ev.get("output"):
                out = ev["output"].split("\n")
                for line in out[: a.lines]:
                    print(_c("dim", "             │ ") + line)
                if len(out) > a.lines:
                    print(_c("dim", f"             │ … {len(out) - a.lines} more lines"))
        else:
            print(f"    {ts}   {_c('c', ev['type'])}: {ev.get('text','')}")
    print(_c("dim", "  ↶ = backfilled from journal   h = fish history   ? = exit unknown"
             + ("" if a.output else "   (--output to show what commands printed)")))


def cmd_verify(a):
    sid = a.id
    meta = session.load_meta(sid)
    if not meta or not meta.get("digest"):
        raise SystemExit(f"{sid} is not finalized yet (stop it first)")
    events, art_hashes = session.load_finalized(sid)  # read as stored, do not rebuild
    ok, msg = hashchain.verify(events, art_hashes, meta["digest"])
    if not ok:
        print(_c("r", "✗ FAILED") + f"  {msg}")
        raise SystemExit(1)
    print(_c("g", "✓ verified") + f"  {msg}  ({len(events)} events)")
    o = session.origin_counts(events)
    if o.get("journal") or o.get("history"):
        print(f"  {_origins(o)}")
    anchored = [e for e in events if e.get("jhash")]
    if anchored:
        idx = journal.index_by_hash()
        gone = [e for e in anchored if idx.get(e["jhash"], {}).get("disposed")]
        found = [e for e in anchored if e["jhash"] in idx and not idx[e["jhash"]].get("disposed")]
        bad = [e for e in found if idx[e["jhash"]].get("output_sha256") != e.get("output_sha256")
               or idx[e["jhash"]].get("command") != e.get("command")]
        if bad:
            print(_c("r", "✗ journal mismatch") + f"  {len(bad)} events differ from the journal record they cite")
            raise SystemExit(1)
        if gone:
            print(_c("dim", f"  journal: {len(gone)} cited records were disposed on purpose (tp dispose)"))
        if found and len(found) == len(anchored):
            print(_c("g", "✓ journal") + f"  all {len(anchored)} commands match their journal records")
        elif len(found) + len(gone) < len(anchored):
            print(_c("dim", f"  journal: {len(found)}/{len(anchored)} records still on disk "
                            "(the rest were pruned by retention; the session chain still covers them)"))
    if o.get("history"):
        print(_c("dim", "  note: fish-history events were imported, not captured live - "
                        "their text is sealed from import time, but there was no chain before that"))
    if meta.get("prior_digests"):
        print(_c("dim", f"  re-sealed {len(meta['prior_digests'])}x; earlier digests are kept in meta.json"))


def cmd_route(a):
    bucket = _bucket(a.to)
    pushmod.record_bucket(a.id, bucket)
    print(_c("c", "routed") + f"  {a.id} → {bucket}")
    if a.push:
        _do_push(a.id, bucket, a.with_output)


def cmd_push(a):
    _do_push(a.id, _bucket(a.to), a.with_output)


def _do_push(sid, bucket, with_output=None):
    try:
        out = pushmod.push(sid, bucket, with_output)
    except session.SessionError as e:
        print(_c("r", "push failed") + f"  {e}")
        raise SystemExit(1)
    where = out.get("url", "")
    print(_c("g", "↑ pushed") + f"  {sid}" + (f"  {where}" if where else ""))


def cmd_config(a):
    if not a.key:
        cfg = config.load()
        for k, v in cfg.items():
            shown = (_c("dim", "(set)") if k == "token" and v else v)
            print(f"  {k} = {shown}")
        return
    if a.value is None:
        print(config.load().get(a.key))
        return
    config.set_key(a.key, a.value)
    print(_c("g", "set") + f"  {a.key}")


def cmd_record(a):
    # Called by the shell hook once per command. Must never be noisy or fail hard.
    try:
        session.record_cmd(a.cmd, a.cwd, a.exit_code, a.dur, a.term, cid=a.id,
                           start_ts=a.start or None, shell_pid=a.pid or os.getppid(),
                           wait=0.5 if a.id else 0.0)
    except Exception:
        pass


def cmd_hook(a):
    src = hooks.for_shell(a.shell, pty=bool(config.load().get("pty", True)))
    if src is None:
        raise SystemExit("shell must be one of: bash | zsh | fish")
    print(src)


def cmd_pty(a):
    from . import ptywrap
    argv = list(a.argv)
    if argv and argv[0] == "--":
        argv = argv[1:]
    raise SystemExit(ptywrap.run(argv))


def cmd_off(a, flag):
    pid = os.getppid() if a.window else None
    journal.set_off(flag, pid)
    scope = "this terminal" if a.window else "all terminals"
    if flag:
        print(_c("y", "○ capture off") + f"  for {scope}. nothing is journaled until  tp on" + (" --window" if a.window else ""))
    else:
        print(_c("g", "● capture on") + f"  for {scope}")
        od = store.home() / "off.d"
        if not a.window and od.exists() and any(od.iterdir()):
            print(_c("dim", "  some terminals are still off individually (tp on --window in them)"))


def cmd_journal(a):
    cfg = config.load()
    if a.action == "verify":
        ok, msg, n = journal.verify()
        print((_c("g", "✓ ") if ok else _c("r", "✗ ")) + f"{msg}  ({n} records)")
        if not ok:
            raise SystemExit(1)
        return
    if a.action == "clear":
        if not a.yes:
            raise SystemExit("this deletes every journal record (sealed sessions keep their copies). add --yes")
        print(_c("y", "cleared") + f"  {journal.clear()} day files")
        return
    if a.action == "prune":
        sid = store.active_id()
        meta = session.load_meta(sid) if sid else None
        gone = journal.prune(int(cfg["journal_days"]), int(cfg["journal_max_mb"]),
                             protect_from=session.window_start(meta) if meta else None, force=True)
        print(_c("c", "pruned") + f"  {', '.join(gone) or 'nothing'}")
        return
    since = _when(a.since) if a.since else None
    recs = journal.records(since=since)
    if not a.since:
        recs = recs[-a.n:]
    if not recs:
        print(_c("dim", "journal is empty for that range."))
        return
    for r in recs:
        ok = _c("g", "ok") if r.get("exit") == 0 else _c("r", f"e{r.get('exit')}")
        out = ""
        if r.get("output"):
            first = next((l for l in r["output"].split("\n") if l.strip()), "")
            out = _c("dim", f"  → {first[:60]}")
        print(f"  {history.fmt_local(int(r['ts']), True)} {ok:>4} {r.get('command','').split(chr(10))[0]}{out}")


def cmd_archive(a):
    if a.where:
        root = store.archive_root()
        print(f"{root}" + ("" if root.exists() else _c("dim", "   (created on first archive)")))
        return
    ids = list(a.ids)
    if a.older_than or a.pushed:
        cutoff = None
        if a.older_than:
            cutoff = _when(a.older_than)
        for sid in store.list_sessions():
            m = session.load_meta(sid) or {}
            if m.get("status") != "stopped" or not m.get("digest") or sid == store.active_id():
                continue
            if a.pushed and not m.get("pushed_at"):
                continue
            if cutoff and session._epoch(m.get("ended_at")) > cutoff:
                continue
            ids.append(sid)
    if not ids:
        raise SystemExit("name sessions to archive, or use --older-than 30d / --pushed")
    failed = 0
    for sid in dict.fromkeys(ids):
        if a.dry_run:
            print(_c("dim", "would archive") + f"  {sid}")
            continue
        try:
            dst = vault.archive(sid)
            print(_c("c", "⇣ archived") + f"  {sid}  → {dst}")
        except vault.VaultError as e:
            failed += 1
            print(_c("r", "✗ ") + f"{sid}: {e}", file=sys.stderr)
    if failed:
        raise SystemExit(1)


def cmd_unarchive(a):
    try:
        dst = vault.unarchive(a.id)
    except vault.VaultError as e:
        raise SystemExit(str(e))
    print(_c("c", "⇡ restored") + f"  {a.id}  → {dst}")


def _confirm(sid: str, what: str, yes: bool) -> None:
    if yes:
        return
    if not sys.stdin.isatty():
        raise SystemExit(f"refusing to {what} without confirmation; add --yes")
    try:
        typed = input(f"type the session id to confirm ({sid}): ").strip()
    except EOFError:
        typed = ""
    if typed != sid:
        raise SystemExit("not confirmed - nothing was deleted")


def cmd_dispose(a):
    if a.journal_from:
        lo = _when(a.journal_from)
        hi = _when(a.until) if a.until else int(__import__("time").time()) + 1
        if not a.yes:
            if not sys.stdin.isatty():
                raise SystemExit("refusing to dispose journal records without confirmation; add --yes")
            n = len(journal.records(lo, hi))
            ans = input(f"dispose {n} journal records {history.fmt_local(lo, True)} → "
                        f"{history.fmt_local(hi, True)}? type 'dispose': ").strip()
            if ans != "dispose":
                raise SystemExit("not confirmed - nothing was deleted")
        n = journal.dispose_range(lo, hi, reason="tp dispose --journal-from")
        store.append_jsonl(store.home() / "disposed.jsonl", {
            "journal_range": [lo, hi], "journal_records": n, "disposed_at": session._now()})
        print(_c("r", "✗ disposed") + f"  {n} journal records (tombstones keep the chain checkable)")
        return
    if not a.id:
        raise SystemExit("tp dispose <id>   or   tp dispose --journal-from 21:40 [--until 22:00]")
    sid = a.id
    meta = session.load_meta(sid)
    if not meta:
        raise SystemExit(f"unknown session {sid}")
    where = "archive" if store.is_archived(sid) else "working set"
    print(f"{_c('b', meta.get('title', ''))}   {sid}   ({where}, {meta.get('status')}, "
          f"content {vault.fingerprint(sid)})")
    if meta.get("pushed_at") and not a.remote:
        print(_c("y", "  ! this session was pushed to Rev Vision - it stays there unless you add --remote"))
    if a.journal:
        print(_c("dim", "  its journal records (commands + output in the window) will be disposed too"))
    _confirm(sid, "dispose", a.yes)
    if a.remote:
        try:
            pushmod.delete_remote(sid)
            print(_c("r", "✗ removed") + "  from Rev Vision")
        except session.SessionError as e:
            raise SystemExit(f"remote delete failed ({e}); nothing deleted locally")
    entry = vault.dispose(sid, with_journal=a.journal)
    print(_c("r", "✗ disposed") + f"  {sid}" +
          (f"  (+{entry['journal_records']} journal records)" if a.journal else ""))
    print(_c("dim", "  logged in ~/.tp/disposed.jsonl (id, title, digest - no content)"))


def cmd_doctor(a):
    import shutil
    cfg = config.load()
    good, warn = _c("g", "✓"), _c("y", "!")
    exe = shutil.which("tp")
    print(f"{good if exe else warn} tp executable on PATH: {exe or 'no - run install.sh (needed for output capture)'}")
    try:
        here = os.ttyname(0)
    except OSError:
        here = ""
    in_pty = bool(here) and os.environ.get("TP_PTY") == here
    print(f"{good if in_pty else warn} this terminal is inside tp pty: "
          + ("yes - output is captured" if in_pty else "no - open a NEW terminal after the hook update"))
    rc = {"fish": Path.home() / ".config/fish/config.fish", "bash": Path.home() / ".bashrc",
          "zsh": Path.home() / ".zshrc"}
    hooked = [n for n, p in rc.items() if p.exists() and "tp hook" in p.read_text(errors="replace")]
    print(f"{good if hooked else warn} shell hook in: {', '.join(hooked) or 'none - see INSTALL.md'}")
    print(f"{good if cfg.get('journal', True) else warn} journal: {'on' if cfg.get('journal', True) else 'off (config)'}"
          + (_c("y", "  (paused: tp on)") if journal.is_off() else ""))
    recs = journal.records(since=int(__import__('time').time()) - 86400)
    if recs:
        last = recs[-1]
        print(f"{good} last journal record: {history.fmt_local(int(last['ts']), True)}  "
              f"{last.get('command','')[:50]}" + ("  [+output]" if "output" in last else ""))
    else:
        print(f"{warn} no journal records in the last 24h")
    ok, msg, n = journal.verify()
    print(f"{good if ok else _c('r', '✗')} {msg} ({n} records)")
    print(f"{good if history.fish_history_path().exists() else warn} fish history (backfill fallback): "
          f"{history.fish_history_path()}")


# ---- parser -----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tp", description="Deskflight recorder for your workstation and lab.")
    p.add_argument("-V", "--version", action="version", version=f"tp {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start", help="begin a session (optionally backfilled)")
    s.add_argument("title", nargs="*")
    s.add_argument("--project", "-p", default=None)
    s.add_argument("--from", "--since", dest="from_", metavar="WHEN", default=None,
                   help="also include earlier work: 21:40 | '2026-10-12 21:40' | 1h | 45m")
    s.add_argument("--no-history", action="store_true",
                   help="don't fill journal gaps from fish history")
    s.set_defaults(func=cmd_start)

    s = sub.add_parser("extend", help="backfill the active session further back")
    s.add_argument("--from", "--since", dest="from_", metavar="WHEN", required=True)
    s.add_argument("--no-history", action="store_true")
    s.set_defaults(func=cmd_extend)

    s = sub.add_parser("reopen", help="unseal a stopped session to extend / continue it")
    s.add_argument("id")
    s.add_argument("--force", action="store_true", help="even if it was already pushed")
    s.set_defaults(func=cmd_reopen)

    sub.add_parser("pause", help="pause the clock and capture").set_defaults(func=cmd_pause)
    sub.add_parser("resume", help="resume recording").set_defaults(func=cmd_resume)
    sub.add_parser("status", help="show the active session").set_defaults(func=cmd_status)

    s = sub.add_parser("stop", help="finish, seal and optionally push")
    s.add_argument("--to", default=None, help="writeups | projects | tech-feed | archive")
    s.add_argument("--push", action="store_true")
    s.add_argument("--with-output", action="store_true", default=None,
                   help="include command output in the push (default: config push_output)")
    s.set_defaults(func=cmd_stop)

    for kind in ("note", "why", "milestone"):
        s = sub.add_parser(kind, help=f"record a {kind}")
        s.add_argument("text", nargs="+")
        s.set_defaults(func=lambda a, k=kind: cmd_mark(a, k))

    s = sub.add_parser("open", help="log a localhost / URL you are testing")
    s.add_argument("url")
    s.add_argument("label", nargs="*")
    s.set_defaults(func=cmd_open)

    s = sub.add_parser("ls", help="list sessions")
    s.add_argument("-n", type=int, default=20)
    s.add_argument("--archived", "-a", action="store_true", help="list the archive instead")
    s.set_defaults(func=cmd_ls)

    s = sub.add_parser("show", help="show a session timeline")
    s.add_argument("id")
    s.add_argument("-n", type=int, default=40)
    s.add_argument("--output", "-o", action="store_true", help="show what each command printed")
    s.add_argument("--lines", type=int, default=12, help="output lines per command with --output")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("verify", help="recompute the hash chain")
    s.add_argument("id")
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("route", help="assign a session to a bucket")
    s.add_argument("id")
    s.add_argument("--to", required=True)
    s.add_argument("--push", action="store_true")
    s.add_argument("--with-output", action="store_true", default=None)
    s.set_defaults(func=cmd_route)

    s = sub.add_parser("push", help="push a session to your web app")
    s.add_argument("id")
    s.add_argument("--to", default=None)
    s.add_argument("--with-output", action="store_true", default=None,
                   help="include command output (default: config push_output)")
    s.set_defaults(func=cmd_push)

    s = sub.add_parser("config", help="get/set config (token, api_url, ...)")
    s.add_argument("key", nargs="?", default=None)
    s.add_argument("value", nargs="?", default=None)
    s.set_defaults(func=cmd_config)

    s = sub.add_parser("hook", help="print the shell hook to source")
    s.add_argument("shell", choices=["bash", "zsh", "fish"])
    s.set_defaults(func=cmd_hook)

    for name, flag in (("off", True), ("on", False)):
        s = sub.add_parser(name, help=f"turn capture {name} (all terminals, or --window)")
        s.add_argument("--window", "-w", action="store_true", help="only this terminal")
        s.set_defaults(func=lambda a, f=flag: cmd_off(a, f))

    s = sub.add_parser("journal", help="the always-on journal: show | verify | prune | clear")
    s.add_argument("action", nargs="?", default="show", choices=["show", "verify", "prune", "clear"])
    s.add_argument("--since", default=None, help="21:40 | 1h | '2026-10-12 21:40'")
    s.add_argument("-n", type=int, default=30)
    s.add_argument("--yes", action="store_true")
    s.set_defaults(func=cmd_journal)

    s = sub.add_parser("archive", help="move sealed sessions to the archive path")
    s.add_argument("ids", nargs="*")
    s.add_argument("--older-than", default=None, metavar="WHEN", help="e.g. 30d, or a date")
    s.add_argument("--pushed", action="store_true", help="only sessions already pushed")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--where", action="store_true", help="print the archive path and exit")
    s.set_defaults(func=cmd_archive)

    s = sub.add_parser("unarchive", help="move an archived session back")
    s.add_argument("id")
    s.set_defaults(func=cmd_unarchive)

    s = sub.add_parser("dispose", help="delete a recording on purpose (asks first)")
    s.add_argument("id", nargs="?")
    s.add_argument("--journal", action="store_true",
                   help="also dispose the journal records in the session's window")
    s.add_argument("--remote", action="store_true", help="also delete it from Rev Vision")
    s.add_argument("--journal-from", default=None, metavar="WHEN",
                   help="dispose journal records from WHEN (no session needed)")
    s.add_argument("--until", default=None, metavar="WHEN")
    s.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    s.set_defaults(func=cmd_dispose)

    s = sub.add_parser("pty", help="run a shell inside the output-capturing wrapper")
    s.add_argument("argv", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_pty)

    sub.add_parser("doctor", help="check that capture is wired up").set_defaults(func=cmd_doctor)

    # internal: the shell hook calls this once per command
    s = sub.add_parser("_record")
    s.add_argument("--cmd", required=True)
    s.add_argument("--cwd", default="")
    s.add_argument("--exit", type=int, default=0, dest="exit_code")
    s.add_argument("--dur", type=int, default=0)
    s.add_argument("--term", default="")
    s.add_argument("--id", default="")
    s.add_argument("--start", type=int, default=0)
    s.add_argument("--pid", type=int, default=0)
    s.set_defaults(func=cmd_record)

    return p


def main(argv=None) -> int:
    store.ensure_home()
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except session.SessionError as e:
        print(_c("r", "error") + f"  {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
