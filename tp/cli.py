"""tp command-line interface."""
from __future__ import annotations

import argparse
import sys

from . import __version__, config, hashchain, hooks, push as pushmod, session, store

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

def cmd_start(a):
    title = " ".join(a.title).strip()
    if not title:
        raise SystemExit("give the session a title:  tp start \"owning the k3s ingress\"")
    meta = session.start(title, a.project)
    print(_c("g", "● recording") + f"  {meta['id']}")
    print(f"  {_c('b', meta['title'])}" + (f"   project={meta['project']}" if meta['project'] else ""))
    print(_c("dim", "  open new terminals freely - every one of them feeds this session."))
    print(_c("dim", "  tp why \"...\"   tp milestone \"...\"   tp pause   tp stop --to writeups --push"))


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
    print(f"  {meta['event_count']} events   {_fmt_dur(meta['active_seconds'])} active")
    print(f"  digest {_c('c', meta['digest'][:16])}…   " + _c("dim", "(tp verify " + meta['id'] + ")"))
    if a.push:
        _do_push(meta["id"], bucket)
    elif bucket:
        print(_c("dim", f"  routed to {bucket}; push with:  tp push {meta['id']}"))


def cmd_status(a):
    sid = store.active_id()
    if not sid:
        print(_c("dim", "idle - no active session.  tp start \"<title>\" to begin."))
        return
    meta = session.load_meta(sid)
    raw = store.read_jsonl(store.session_dir(sid) / "events.raw.jsonl")
    state = _c("y", "∥ paused") if meta["status"] == "paused" else _c("g", "● recording")
    print(f"{state}   {meta['id']}")
    print(f"  {_c('b', meta['title'])}" + (f"   project={meta['project']}" if meta['project'] else ""))
    print(f"  {len(raw)} commands captured   {_fmt_dur(session.active_seconds(meta))} active")


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
    for ev in events[-a.n:]:
        ts = ev["ts"][11:19]
        if ev["type"] == "cmd":
            ok = _c("g", "ok") if ev.get("exit") == 0 else _c("r", f"e{ev.get('exit')}")
            print(f"    {ts} {ok:>4} {ev.get('command','')}")
        else:
            print(f"    {ts} {_c('c', ev['type'])}: {ev.get('text','')}")


def cmd_verify(a):
    sid = a.id
    meta = session.load_meta(sid)
    if not meta or not meta.get("digest"):
        raise SystemExit(f"{sid} is not finalized yet (stop it first)")
    events, art_hashes = session.load_finalized(sid)  # read as stored, do not rebuild
    ok, msg = hashchain.verify(events, art_hashes, meta["digest"])
    if ok:
        print(_c("g", "✓ verified") + f"  {msg}  ({len(events)} events)")
    else:
        print(_c("r", "✗ FAILED") + f"  {msg}")
        raise SystemExit(1)


def cmd_route(a):
    bucket = _bucket(a.to)
    pushmod.record_bucket(a.id, bucket)
    print(_c("c", "routed") + f"  {a.id} → {bucket}")
    if a.push:
        _do_push(a.id, bucket)


def cmd_push(a):
    _do_push(a.id, _bucket(a.to))


def _do_push(sid, bucket):
    try:
        out = pushmod.push(sid, bucket)
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
        session.record_cmd(a.cmd, a.cwd, a.exit_code, a.dur, a.term)
    except Exception:
        pass


def cmd_hook(a):
    src = hooks.for_shell(a.shell)
    if src is None:
        raise SystemExit("shell must be one of: bash | zsh | fish")
    print(src)


# ---- parser -----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tp", description="Deskflight recorder for your workstation and lab.")
    p.add_argument("-V", "--version", action="version", version=f"tp {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("start", help="begin recording a session")
    s.add_argument("title", nargs="+")
    s.add_argument("--project", "-p", default=None)
    s.set_defaults(func=cmd_start)

    sub.add_parser("pause", help="pause the clock and capture").set_defaults(func=cmd_pause)
    sub.add_parser("resume", help="resume recording").set_defaults(func=cmd_resume)
    sub.add_parser("status", help="show the active session").set_defaults(func=cmd_status)

    s = sub.add_parser("stop", help="finish, seal and optionally push")
    s.add_argument("--to", default=None, help="writeups | projects | tech-feed | archive")
    s.add_argument("--push", action="store_true")
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
    s.set_defaults(func=cmd_ls)

    s = sub.add_parser("show", help="show a session timeline")
    s.add_argument("id")
    s.add_argument("-n", type=int, default=40)
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("verify", help="recompute the hash chain")
    s.add_argument("id")
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("route", help="assign a session to a bucket")
    s.add_argument("id")
    s.add_argument("--to", required=True)
    s.add_argument("--push", action="store_true")
    s.set_defaults(func=cmd_route)

    s = sub.add_parser("push", help="push a session to your web app")
    s.add_argument("id")
    s.add_argument("--to", default=None)
    s.set_defaults(func=cmd_push)

    s = sub.add_parser("config", help="get/set config (token, api_url, ...)")
    s.add_argument("key", nargs="?", default=None)
    s.add_argument("value", nargs="?", default=None)
    s.set_defaults(func=cmd_config)

    s = sub.add_parser("hook", help="print the shell hook to source")
    s.add_argument("shell", choices=["bash", "zsh", "fish"])
    s.set_defaults(func=cmd_hook)

    # internal: the shell hook calls this once per command
    s = sub.add_parser("_record")
    s.add_argument("--cmd", required=True)
    s.add_argument("--cwd", default="")
    s.add_argument("--exit", type=int, default=0, dest="exit_code")
    s.add_argument("--dur", type=int, default=0)
    s.add_argument("--term", default="")
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
