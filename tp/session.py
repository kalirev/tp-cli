"""Session lifecycle: start / pause / resume / extend / stop / reopen, plus
notes and finalize.

v0.3 model
----------
Recording happens all the time, into the journal (tp/journal.py). A session is
a set of time *segments* cut from it:

    {"start": ..., "end": ...}                    live segment: you were in
                                                  `tp start`..`tp pause`/`stop`
    {"start": ..., "end": ..., "kind": "backfill"} backfilled segment: time
                                                  before `tp start` that you
                                                  pulled in with --from / extend

At `tp stop` the journal records inside the segments, any fish-history imports
for gaps the journal doesn't cover, legacy v0.2 raw events, and your notes are
merged in timestamp order and sealed into the session's hash chain. Every
command event carries an `origin`:

    live      captured while the session was recording
    journal   captured live by the journal before `tp start`, pulled in later
              (same fidelity as live: output, cwd, exit code, chained at the time)
    history   imported from fish history (command text + time only)
"""
from __future__ import annotations

import datetime as dt
import secrets
import shutil
import time
from pathlib import Path

from . import capture, config, hashchain, history, journal, store

ORIGINS = ("live", "journal", "history")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _parse(ts: str) -> dt.datetime:
    return dt.datetime.fromisoformat(ts)


def _epoch(ts: str | None) -> int:
    return int(_parse(ts).timestamp()) if ts else int(time.time())


def _iso_epoch(ts: int) -> str:
    return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).isoformat()


def new_id(title: str) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(2)}"


# ---- metadata / timer -------------------------------------------------------

def _meta_path(sid: str) -> Path:
    return store.session_dir(sid) / "meta.json"


def load_meta(sid: str) -> dict | None:
    return store.read_json(_meta_path(sid))


def save_meta(sid: str, meta: dict) -> None:
    store.write_json(_meta_path(sid), meta)


def _is_backfill(seg: dict) -> bool:
    return seg.get("kind") == "backfill"


def active_seconds(meta: dict) -> int:
    """Clock time of the live segments (honour-system pause/resume)."""
    total = 0.0
    for seg in meta.get("segments", []):
        if _is_backfill(seg):
            continue
        start = _parse(seg["start"])
        end = _parse(seg["end"]) if seg.get("end") else dt.datetime.now(dt.timezone.utc)
        total += (end - start).total_seconds()
    return int(total)


def window_start(meta: dict) -> int:
    return min(_epoch(s["start"]) for s in meta["segments"])


def first_live_start(meta: dict) -> int:
    live = [_epoch(s["start"]) for s in meta["segments"] if not _is_backfill(s)]
    return min(live) if live else _epoch(meta["created_at"])


def _segment_for(ts: int, segs: list[dict]) -> dict | None:
    """The segment holding ts. Records are whole seconds and segment edges are
    not, so both edges are inclusive at second resolution; live wins a tie."""
    now = int(time.time())
    for seg in sorted(segs, key=_is_backfill):
        lo = _epoch(seg["start"])
        hi = _epoch(seg["end"]) if seg.get("end") else now + 1
        if lo <= ts <= hi:
            return seg
    return None


# ---- lifecycle --------------------------------------------------------------

class SessionError(RuntimeError):
    pass


def start(title: str, project: str | None, from_ts: int | None = None,
          use_history: bool = True) -> dict:
    if store.active_id():
        raise SessionError("a session is already active; stop or pause it first")
    cfg = config.load()
    sid = new_id(title)
    store.ensure_session_dir(sid)
    meta = {
        "id": sid,
        "title": title,
        "project": project,
        "host": cfg["host"],
        "created_at": _now(),
        "ended_at": None,
        "status": "recording",
        "bucket": None,
        "segments": [{"start": _now(), "end": None}],
        "digest": None,
        "event_count": 0,
        "tp_version": _version(),
    }
    save_meta(sid, meta)
    store.set_paused(False)
    store.set_active(sid)
    if from_ts is not None:
        meta = extend(from_ts, use_history=use_history)
    return meta


def extend(from_ts: int, use_history: bool = True) -> dict:
    """Backfill: widen the active session so it starts at `from_ts`."""
    sid = _require_active()
    meta = load_meta(sid)
    first = first_live_start(meta)
    if from_ts >= first:
        raise SessionError(
            f"the session already covers {history.fmt_local(from_ts)} "
            f"(it starts at {history.fmt_local(first)}); pick an earlier time")
    if from_ts < first - 7 * 86400:
        raise SessionError("backfill is limited to 7 days before the session start")
    meta["segments"] = [s for s in meta["segments"] if not _is_backfill(s)]
    meta["segments"].insert(0, {"start": _iso_epoch(from_ts), "end": _iso_epoch(first),
                                "kind": "backfill"})
    save_meta(sid, meta)
    _import_history(sid, from_ts, first, use_history)
    return meta


def pause() -> dict:
    sid = _require_active()
    meta = load_meta(sid)
    if meta["status"] == "paused":
        raise SessionError("already paused")
    for seg in reversed(meta["segments"]):
        if seg.get("end") is None:
            seg["end"] = _now()
            break
    meta["status"] = "paused"
    save_meta(sid, meta)
    store.set_paused(True)
    return meta


def resume() -> dict:
    sid = _require_active()
    meta = load_meta(sid)
    if meta["status"] == "recording":
        raise SessionError("not paused")
    meta["segments"].append({"start": _now(), "end": None})
    meta["status"] = "recording"
    save_meta(sid, meta)
    store.set_paused(False)
    return meta


def reopen(sid: str, force: bool = False) -> dict:
    """Unseal a stopped session so it can be extended / keep recording.

    The previous digest is kept in meta["prior_digests"], and the previous
    sealed timeline in events.sealed-N.json, so re-sealing is visible.
    """
    if store.active_id():
        raise SessionError(f"session {store.active_id()} is active; stop it first")
    meta = load_meta(sid)
    if not meta:
        raise SessionError(f"unknown session {sid}")
    if store.is_archived(sid):
        raise SessionError(f"{sid} is archived; tp unarchive {sid} first")
    if meta.get("status") != "stopped":
        raise SessionError(f"{sid} is '{meta.get('status')}', not stopped")
    if meta.get("pushed_at") and not force:
        raise SessionError(
            f"{sid} was pushed at {meta['pushed_at']}. Reopening changes a published record; "
            "rerun with --force (the old digest stays on the record)")
    sd = store.session_dir(sid)
    prior = meta.setdefault("prior_digests", [])
    prior.append({"digest": meta.get("digest"), "sealed_at": meta.get("ended_at"),
                  "pushed_at": meta.get("pushed_at")})
    if (sd / "events.jsonl").exists():
        shutil.copy2(sd / "events.jsonl", sd / f"events.sealed-{len(prior)}.json")
    meta.update(status="recording", ended_at=None, digest=None)
    meta["segments"].append({"start": _now(), "end": None})
    save_meta(sid, meta)
    store.set_paused(False)
    store.set_active(sid)
    return meta


# ---- capture (called by the shell hook) -----------------------------------------

def _is_control(cmd: str) -> bool:
    s = cmd.strip()
    if "\n" in s:
        return False
    first = s.split(None, 1)[0] if s else ""
    return first == "tp" or first.endswith("/tp") or s.startswith("exec tp ")


def record_cmd(command: str, cwd: str, exit_code: int, duration: int, term: str,
               cid: str = "", start_ts: int | None = None, shell_pid: int | None = None,
               wait: float = 0.0) -> bool:
    """Journal one finished command (and its output, when `tp pty` caught it)."""
    from . import ptywrap

    cfg = config.load()
    spool = ptywrap.take_spool(cid, wait) if cid else None
    if not (command or "").strip() or _is_control(command):
        return False
    if not cfg.get("journal", True):
        return _record_legacy(command, cwd, exit_code, duration, term)
    if journal.is_off(shell_pid):
        return False

    extra = cfg.get("redact_patterns", [])
    now = int(time.time())
    rec: dict = {
        "ts": int(start_ts) if start_ts else now - int(duration or 0),
        "ts_end": now,
        "type": "cmd",
        "host": cfg.get("host"),
        "term": term,
        "cwd": cwd,
        "command": capture.redact(command, extra),
        "exit": exit_code,
        "duration": duration,
    }
    if spool:
        rec["ts"] = int(spool["ts_start"])
        rec["ts_end"] = int(spool["ts_end"])
        rec["duration"] = int(round(spool["ts_end"] - spool["ts_start"]))
        out = spool.get("output") or ""
        rec.update(output=out, output_sha256=hashchain.content_hash(out),
                   output_bytes=spool.get("output_bytes", 0),
                   output_truncated=bool(spool.get("output_truncated")),
                   fullscreen=bool(spool.get("fullscreen")))
    journal.append(rec)

    try:  # housekeeping, at most hourly; never allowed to fail a record
        active = store.active_id()
        meta = load_meta(active) if active else None
        journal.prune(int(cfg.get("journal_days", 7)), int(cfg.get("journal_max_mb", 1024)),
                      protect_from=window_start(meta) if meta else None)
        ptywrap.sweep_spool()
    except Exception:
        pass
    return True


def _record_legacy(command, cwd, exit_code, duration, term) -> bool:
    """v0.2 behaviour (config journal=false): only while a session records."""
    sid = store.active_id()
    if not sid or store.is_paused():
        return False
    sd = store.session_dir(sid)
    wrote = False
    for line in command.split("\n"):
        if not line.strip() or _is_control(line):
            continue
        store.append_jsonl(sd / "events.raw.jsonl", {
            "ts": int(time.time()), "type": "cmd", "cwd": cwd, "command": line,
            "exit": exit_code, "duration": duration, "term": term})
        wrote = True
    return wrote


def mark(kind: str, text: str) -> dict:
    """Record reasoning/intent: note | why | milestone | open."""
    sid = _require_active()
    sd = store.session_dir(sid)
    rec = {"ts": int(time.time()), "type": kind, "text": text}
    store.append_jsonl(sd / "notes.jsonl", rec)
    if kind == "milestone":  # a natural checkpoint: snapshot code + services now
        _snapshot(sid, _cwds(collect(sid)))
    return rec


def stop(push_to: str | None = None) -> dict:
    sid = _require_active()
    meta = load_meta(sid)
    if meta["status"] != "paused":
        for seg in reversed(meta["segments"]):
            if seg.get("end") is None:
                seg["end"] = _now()
                break
    meta["status"] = "stopped"
    meta["ended_at"] = _now()
    if push_to:
        meta["bucket"] = push_to
    save_meta(sid, meta)

    _snapshot(sid, _cwds(collect(sid)))   # final code + port snapshot
    events, artifact_hashes = finalize(sid)
    meta = load_meta(sid)
    live, est = active_breakdown(meta, events)
    meta["active_seconds"] = live + est
    meta["active_breakdown"] = {"live": live, "estimated": est}
    meta["origins"] = origin_counts(events)
    meta["event_count"] = len(events)
    meta["journal_tip"] = journal.tip_hash()
    meta["digest"] = hashchain.digest(events, artifact_hashes)
    save_meta(sid, meta)

    store.clear_active()
    store.set_paused(False)
    return meta


# ---- building the timeline ------------------------------------------------------

def collect(sid: str) -> list[dict]:
    """Every event of the session, merged and time-ordered, not yet sealed."""
    cfg = config.load()
    extra = cfg.get("redact_patterns", [])
    meta = load_meta(sid)
    sd = store.session_dir(sid)
    segs = meta.get("segments", [])
    events: list[dict] = []

    # 1. journal records inside the session's segments
    if segs:
        lo = window_start(meta)
        hi = max((_epoch(s["end"]) if s.get("end") else int(time.time()) + 1) for s in segs)
        for r in journal.records(lo, hi + 1):
            seg = _segment_for(int(r["ts"]), segs)
            if seg is None:
                continue
            ev = {
                "ts": _iso_epoch(r["ts"]), "type": "cmd", "cwd": r.get("cwd"),
                "command": r.get("command", ""), "exit": r.get("exit"),
                "duration": r.get("duration"), "term": r.get("term"),
                "origin": "journal" if _is_backfill(seg) else "live",
                "ts_end": _iso_epoch(r.get("ts_end", r["ts"])), "jhash": r.get("hash"),
            }
            if "output_sha256" in r:
                for k in ("output", "output_sha256", "output_bytes", "output_truncated", "fullscreen"):
                    ev[k] = r.get(k)
            events.append(ev)

    # 2. v0.2 raw events (sessions started before the upgrade, or journal=false)
    jkeys = [(e["term"], e["command"], _epoch(e["ts_end"])) for e in events]
    for e in store.read_jsonl(sd / "events.raw.jsonl"):
        cmd = capture.redact(e.get("command", ""), extra)
        ts = int(e.get("ts", 0))
        if any(t == e.get("term") and c == cmd and abs(te - ts) <= 2 for t, c, te in jkeys):
            continue  # also journaled; keep the richer journal copy
        events.append({
            "ts": _iso_epoch(ts), "type": "cmd", "cwd": e.get("cwd") or None,
            "command": cmd, "exit": e.get("exit"), "duration": e.get("duration"),
            "term": e.get("term"),
            # the hand-rolled backfill script from 2026-10-12 tagged rows "backfill"
            "origin": "history" if e.get("origin") in ("backfill", "history") else "live",
        })

    # 3. fish-history imports for the parts of backfill windows the journal missed
    for h in store.read_jsonl(sd / "history.jsonl"):
        events.append({
            "ts": _iso_epoch(h["ts"]), "type": "cmd", "cwd": None,
            "command": h["command"], "exit": None, "duration": None,
            "term": "history", "origin": "history",
        })

    # 4. notes / why / milestones / opens
    for n in store.read_jsonl(sd / "notes.jsonl"):
        events.append({"ts": _iso_epoch(n.get("ts", 0)), "type": n.get("type", "note"),
                       "text": capture.redact(n.get("text", ""), extra)})

    events.sort(key=lambda e: e["ts"])  # stable: same-second events keep order
    return events


def finalize(sid: str):
    """Merge, sort and hash-chain the whole timeline."""
    events = hashchain.seal(collect(sid))
    store.write_json(store.session_dir(sid) / "events.jsonl", events)
    return events, artifact_hashes(sid)


def _import_history(sid: str, lo: int, hi: int, use_history: bool) -> None:
    """Fill [lo, hi) from fish history where the journal has nothing."""
    path = store.session_dir(sid) / "history.jsonl"
    if path.exists():
        path.unlink()
    if not use_history:
        return
    extra = config.load().get("redact_patterns", [])
    known = [(int(r["ts"]), r.get("command", "")) for r in journal.records(lo - 5, hi + 5)]
    known += [(int(e.get("ts", 0)) - int(e.get("duration") or 0), capture.redact(e.get("command", ""), extra))
              for e in store.read_jsonl(store.session_dir(sid) / "events.raw.jsonl")]
    entries = history.fish_entries()
    # fish stamps a bulk import (e.g. of ~/.bash_history on first start) with
    # one shared time; those times are meaningless, so skip them
    from collections import Counter
    bulk = {t for t, n in Counter(t for t, _ in entries).items() if n > 3}
    for ts, cmd in entries:
        if not (lo <= ts < hi) or ts in bulk or _is_control(cmd):
            continue
        red = capture.redact(cmd, extra)
        if any(c == red and abs(t - ts) <= 3 for t, c in known):
            continue
        store.append_jsonl(path, {"ts": ts, "command": red})


def active_breakdown(meta: dict, events: list[dict]) -> tuple[int, int]:
    """(live clock seconds, estimated seconds for backfilled segments)."""
    idle = int(config.load().get("idle_gap_seconds", 300))
    est = 0
    for seg in meta.get("segments", []):
        if not _is_backfill(seg):
            continue
        lo, hi = _epoch(seg["start"]), _epoch(seg["end"])
        items = sorted(
            (_epoch(e["ts"]), _epoch(e.get("ts_end") or e["ts"]))
            for e in events if e.get("type") == "cmd" and lo <= _epoch(e["ts"]) < hi)
        for i, (s, e) in enumerate(items):
            nxt = items[i + 1][0] if i + 1 < len(items) else hi
            est += max(0, min(max(e, s) + idle, nxt, hi) - s)
    return active_seconds(meta), est


def origin_counts(events: list[dict]) -> dict:
    out = {k: 0 for k in ORIGINS}
    for e in events:
        if e.get("type") == "cmd":
            o = e.get("origin", "live")
            out[o] = out.get(o, 0) + 1
    return out


# ---- internals --------------------------------------------------------------

def _require_active() -> str:
    sid = store.active_id()
    if not sid:
        raise SessionError("no active session")
    return sid


def _cwds(events: list[dict]) -> set[str]:
    return {e["cwd"] for e in events if e.get("cwd")}


def _snapshot(sid: str, cwds: set[str]) -> None:
    cfg = config.load()
    sd = store.session_dir(sid)
    if cfg.get("capture_gitdiff", True):
        seen_repos: set[str] = set()
        for cwd in cwds:
            root = capture.git_root(cwd)
            if root and root not in seen_repos:
                seen_repos.add(root)
                snap = capture.git_snapshot(root, cfg["max_patch_bytes"])
                store.append_jsonl(sd / "artifacts.jsonl",
                                   {"ts": int(time.time()), "type": "gitdiff", **snap})
    if cfg.get("capture_ports", True):
        ports = capture.localhost_ports()
        if ports:
            store.append_jsonl(sd / "artifacts.jsonl",
                               {"ts": int(time.time()), "type": "ports", "services": ports})


def artifact_hashes(sid: str) -> list[str]:
    """Content hashes of this session's artifacts (git diffs, port dumps)."""
    cfg = config.load()
    extra = cfg.get("redact_patterns", [])
    sd = store.session_dir(sid)
    hs: list[str] = []
    for a in store.read_jsonl(sd / "artifacts.jsonl"):
        blob = capture.redact(str(a.get("patch", "")) + str(a.get("services", "")), extra)
        hs.append(hashchain.content_hash(blob + str(a.get("ts"))))
    return hs


def load_finalized(sid: str):
    """Read the sealed timeline AS STORED (no rebuild) for verify/push."""
    sd = store.session_dir(sid)
    events = store.read_json(sd / "events.jsonl", default=[]) or []
    return events, artifact_hashes(sid)


def _iso(ts) -> str:  # kept for callers of the v0.2 API
    if isinstance(ts, str):
        return ts
    try:
        return _iso_epoch(int(ts))
    except (TypeError, ValueError, OSError):
        return _now()


def _version() -> str:
    from . import __version__
    return __version__
