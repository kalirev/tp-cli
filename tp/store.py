"""On-disk state: paths, locking, and append-only JSONL.

Everything lives under $TP_HOME (default ~/.tp):

    config.json              global config (api url, token, redaction)
    current                  id of the active session ("" when idle)
    paused                   present on disk only while paused
    archive/<id>/            sessions moved out by `tp archive` (path configurable)
    disposed.jsonl           what `tp dispose` deleted (ids, digests - no content)
    sessions/<id>/
        meta.json            session metadata + timer segments
        events.raw.jsonl     raw command events (written by the shell hook)
        notes.jsonl          notes / why / milestones / localhost marks
        artifacts.jsonl      git diffs and port snapshots
        events.jsonl         finalized, redacted, hash-chained timeline
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl  # POSIX only; the hook and finalizer run on Linux/macOS.
except ImportError:  # pragma: no cover
    fcntl = None


def home() -> Path:
    return Path(os.environ.get("TP_HOME", str(Path.home() / ".tp")))


def ensure_home() -> Path:
    h = home()
    (h / "sessions").mkdir(parents=True, exist_ok=True)
    return h


def session_dir(sid: str) -> Path:
    """Where a session lives: the working set, or the archive if it was moved."""
    d = home() / "sessions" / sid
    if not d.exists():
        a = archive_root() / sid
        if a.exists():
            return a
    return d


def archive_root() -> Path:
    """Default: $TP_HOME/archive. Set elsewhere with `tp config archive_path PATH`."""
    cfg = read_json(home() / "config.json", default={}) or {}
    p = cfg.get("archive_path") if isinstance(cfg, dict) else None
    return Path(os.path.expanduser(p)) if p else home() / "archive"


def is_archived(sid: str) -> bool:
    return not (home() / "sessions" / sid).exists() and (archive_root() / sid).exists()


def ensure_session_dir(sid: str) -> Path:
    d = session_dir(sid)
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---- active-session pointer -------------------------------------------------

def active_id() -> str | None:
    p = home() / "current"
    if not p.exists():
        return None
    sid = p.read_text(encoding="utf-8").strip()
    return sid or None


def set_active(sid: str) -> None:
    (home() / "current").write_text(sid, encoding="utf-8")


def clear_active() -> None:
    (home() / "current").write_text("", encoding="utf-8")


def is_paused() -> bool:
    return (home() / "paused").exists()


def set_paused(flag: bool) -> None:
    p = home() / "paused"
    if flag:
        p.write_text("1", encoding="utf-8")
    elif p.exists():
        p.unlink()


# ---- json helpers -----------------------------------------------------------

@contextmanager
def _locked(path: Path, mode: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, mode, encoding="utf-8")
    try:
        if fcntl is not None:
            fcntl.flock(f, fcntl.LOCK_EX)
        yield f
    finally:
        if fcntl is not None:
            fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def append_jsonl(path: Path, obj: dict) -> None:
    with _locked(path, "a") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # tolerate a half-written line from a crashed shell
    return out


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def list_sessions() -> list[str]:
    root = home() / "sessions"
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())
