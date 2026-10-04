"""Session lifecycle: start / pause / resume / stop, plus notes and finalize."""
from __future__ import annotations

import datetime as dt
import secrets
from pathlib import Path

from . import capture, config, hashchain, store


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _parse(ts: str) -> dt.datetime:
    return dt.datetime.fromisoformat(ts)


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


def active_seconds(meta: dict) -> int:
    total = 0.0
    for seg in meta.get("segments", []):
        start = _parse(seg["start"])
        end = _parse(seg["end"]) if seg.get("end") else dt.datetime.now(dt.timezone.utc)
        total += (end - start).total_seconds()
    return int(total)


# ---- lifecycle --------------------------------------------------------------

class SessionError(RuntimeError):
    pass


def start(title: str, project: str | None) -> dict:
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
    }
    save_meta(sid, meta)
    store.set_paused(False)
    store.set_active(sid)
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


def record_cmd(command: str, cwd: str, exit_code: int, duration: int, term: str) -> bool:
    """Append one command event to the active session (called by the shell hook).

    Silent no-op when nothing is recording, when paused, or for the recorder's
    own `tp ...` control commands (so the timeline isn't full of tp noise).
    """
    sid = store.active_id()
    if not sid or store.is_paused():
        return False
    # A pasted multi-line command arrives as one string; record each line so
    # pasting a block of work is captured, not collapsed into one event.
    lines = (command or "").split("\n") if "\n" in (command or "") else [command or ""]
    sd = store.session_dir(sid)
    wrote = False
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.split(" ", 1)[0] == "tp":   # skip the recorder's own control commands
            continue
        rec = {
            "ts": int(dt.datetime.now(dt.timezone.utc).timestamp()),
            "type": "cmd", "cwd": cwd, "command": line,
            "exit": exit_code, "duration": duration, "term": term,
        }
        store.append_jsonl(sd / "events.raw.jsonl", rec)
        wrote = True
    return wrote


def mark(kind: str, text: str) -> dict:
    """Record reasoning/intent: note | why | milestone | open."""
    sid = _require_active()
    sd = store.session_dir(sid)
    rec = {"ts": int(dt.datetime.now(dt.timezone.utc).timestamp()), "type": kind, "text": text}
    store.append_jsonl(sd / "notes.jsonl", rec)
    # A milestone is a natural checkpoint: snapshot code + services now.
    if kind == "milestone":
        _snapshot(sid)
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
    meta["active_seconds"] = active_seconds(meta)
    if push_to:
        meta["bucket"] = push_to
    save_meta(sid, meta)

    _snapshot(sid)                      # final code + port snapshot
    events, artifact_hashes = finalize(sid)
    meta = load_meta(sid)
    meta["event_count"] = len(events)
    meta["digest"] = hashchain.digest(events, artifact_hashes)
    save_meta(sid, meta)

    store.clear_active()
    store.set_paused(False)
    return meta


# ---- internals --------------------------------------------------------------

def _require_active() -> str:
    sid = store.active_id()
    if not sid:
        raise SessionError("no active session")
    return sid


def _snapshot(sid: str) -> None:
    cfg = config.load()
    sd = store.session_dir(sid)
    events = store.read_jsonl(sd / "events.raw.jsonl")
    cwds = {e.get("cwd") for e in events if e.get("cwd")}
    if cfg.get("capture_gitdiff", True):
        seen_repos: set[str] = set()
        for cwd in cwds:
            root = capture.git_root(cwd)
            if root and root not in seen_repos:
                seen_repos.add(root)
                snap = capture.git_snapshot(root, cfg["max_patch_bytes"])
                store.append_jsonl(sd / "artifacts.jsonl",
                                   {"ts": int(dt.datetime.now(dt.timezone.utc).timestamp()),
                                    "type": "gitdiff", **snap})
    if cfg.get("capture_ports", True):
        ports = capture.localhost_ports()
        if ports:
            store.append_jsonl(sd / "artifacts.jsonl",
                               {"ts": int(dt.datetime.now(dt.timezone.utc).timestamp()),
                                "type": "ports", "services": ports})


def finalize(sid: str):
    """Merge, redact, sort and hash-chain the whole timeline. Idempotent."""
    cfg = config.load()
    sd = store.session_dir(sid)
    extra = cfg.get("redact_patterns", [])

    events: list[dict] = []
    for e in store.read_jsonl(sd / "events.raw.jsonl"):
        events.append({
            "ts": _iso(e.get("ts")),
            "type": "cmd",
            "cwd": e.get("cwd"),
            "command": capture.redact(e.get("command", ""), extra),
            "exit": e.get("exit"),
            "duration": e.get("duration"),
            "term": e.get("term"),
        })
    for n in store.read_jsonl(sd / "notes.jsonl"):
        events.append({
            "ts": _iso(n.get("ts")),
            "type": n.get("type", "note"),
            "text": capture.redact(n.get("text", ""), extra),
        })

    events.sort(key=lambda e: e["ts"])
    hashchain.seal(events)
    store.write_json(sd / "events.jsonl", events)
    return events, artifact_hashes(sid)


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


def _iso(ts) -> str:
    if isinstance(ts, str):
        return ts
    try:
        return dt.datetime.fromtimestamp(int(ts), dt.timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return _now()
