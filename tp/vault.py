"""Where finished recordings go: archive them, bring them back, or dispose of them.

Archive
-------
`tp archive` moves sealed sessions out of the working set (~/.tp/sessions) to
the archive path: ~/.tp/archive by default, or anywhere you set with
`tp config archive_path /mnt/rev-ssd/tp-archive`. The move is verified: the
copy's hash chain must verify against the session digest and every file must
match byte-for-byte before the original is removed. `tp show` / `tp verify` /
`tp push` still find archived sessions. `tp unarchive` moves one back.

Note: this is local storage. The `archive` *bucket* (`tp stop --to archive`)
is a different thing - where a session is filed on Rev Vision.

Dispose
-------
`tp dispose` deletes a recording on purpose. It always asks you to type the
session id (or --yes), refuses nothing it can't name, and leaves a line in
~/.tp/disposed.jsonl saying what was deleted and when (id, title, digest - no
content), so a disposal is deliberate and visible, never silent.
"""
from __future__ import annotations

import datetime as dt
import filecmp
import hashlib
import os
import shutil
from pathlib import Path

from . import hashchain, journal, store


class VaultError(RuntimeError):
    pass


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


# ---- archive ------------------------------------------------------------------

_MOUNT_PREFIXES = ("/mnt/", "/media/", "/run/media/")


def check_archive_root() -> Path:
    """Make sure the archive path is usable - and, for removable drives, that
    the drive is actually mounted (otherwise we'd fill the empty mountpoint on
    your root disk)."""
    root = store.archive_root()
    s = str(root)
    for pre in _MOUNT_PREFIXES:
        if s.startswith(pre):
            depth = 4 if pre == "/run/media/" else 3   # /run/media/<user>/<disk>, /mnt/<disk>
            parts = Path(s).parts[:depth]
            mount = Path(*parts)
            if not os.path.ismount(mount):
                raise VaultError(f"archive path {root} is on {mount}, which isn't mounted. "
                                 "mount the drive (or change: tp config archive_path PATH)")
    if not root.exists():
        if not root.parent.exists():
            raise VaultError(f"archive path {root} doesn't exist and neither does {root.parent}")
        root.mkdir()
    if not os.access(root, os.W_OK):
        raise VaultError(f"archive path {root} isn't writable")
    return root


def _same_tree(a: Path, b: Path) -> bool:
    for f in a.rglob("*"):
        if f.is_file():
            g = b / f.relative_to(a)
            if not g.is_file() or not filecmp.cmp(f, g, shallow=False):
                return False
    return True


def _verifies(sdir: Path, meta: dict) -> bool:
    """tp verify, run against an arbitrary folder (e.g. the archive copy)."""
    from . import capture, config
    extra = config.load().get("redact_patterns", [])
    events = store.read_json(sdir / "events.jsonl", default=[]) or []
    arts = []
    for a in store.read_jsonl(sdir / "artifacts.jsonl"):
        blob = capture.redact(str(a.get("patch", "")) + str(a.get("services", "")), extra)
        arts.append(hashchain.content_hash(blob + str(a.get("ts"))))
    ok, _ = hashchain.verify(events, arts, meta.get("digest") or "")
    return ok


def archive(sid: str) -> Path:
    src = store.home() / "sessions" / sid
    if not src.exists():
        if store.is_archived(sid):
            raise VaultError(f"{sid} is already archived")
        raise VaultError(f"unknown session {sid}")
    if store.active_id() == sid:
        raise VaultError(f"{sid} is the active session; stop it first")
    meta = store.read_json(src / "meta.json", default={}) or {}
    if meta.get("status") != "stopped" or not meta.get("digest"):
        raise VaultError(f"{sid} isn't sealed (tp stop first)")
    if not _verifies(src, meta):
        raise VaultError(f"{sid} fails tp verify - not archiving a damaged record")
    root = check_archive_root()
    dst = root / sid
    if dst.exists():
        raise VaultError(f"{dst} already exists in the archive")
    tmp = root / f".{sid}.partial"
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(src, tmp)
    if not (_same_tree(src, tmp) and _verifies(tmp, meta)):
        shutil.rmtree(tmp)
        raise VaultError(f"copy of {sid} didn't verify; original left in place")
    tmp.rename(dst)
    shutil.rmtree(src)
    store.append_jsonl(root / "index.jsonl", {
        "id": sid, "title": meta.get("title"), "project": meta.get("project"),
        "digest": meta.get("digest"), "archived_at": _now(), "from": str(src)})
    return dst


def unarchive(sid: str) -> Path:
    if (store.home() / "sessions" / sid).exists():
        raise VaultError(f"{sid} is already in the working set")
    src = store.archive_root() / sid
    if not src.exists():
        raise VaultError(f"{sid} isn't in the archive ({store.archive_root()})")
    dst = store.home() / "sessions" / sid
    tmp = dst.parent / f".{sid}.partial"
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(src, tmp)
    if not _same_tree(src, tmp):
        shutil.rmtree(tmp)
        raise VaultError(f"copy of {sid} didn't match; archive copy left in place")
    tmp.rename(dst)
    shutil.rmtree(src)
    return dst


def archived_ids() -> list[str]:
    root = store.archive_root()
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir()
                  if p.is_dir() and not p.name.startswith(".") and (p / "meta.json").exists())


# ---- dispose -----------------------------------------------------------------

def dispose(sid: str, with_journal: bool = False) -> dict:
    """Delete a session's folder (working set or archive). Returns the log entry."""
    sdir = store.session_dir(sid)
    if not sdir.exists():
        raise VaultError(f"unknown session {sid}")
    meta = store.read_json(sdir / "meta.json", default={}) or {}
    entry = {
        "id": sid, "title": meta.get("title"), "project": meta.get("project"),
        "digest": meta.get("digest"), "status": meta.get("status"),
        "pushed_at": meta.get("pushed_at"), "archived": store.is_archived(sid),
        "disposed_at": _now(), "journal_records": 0,
    }
    if with_journal and meta.get("segments"):
        from . import session
        lo = session.window_start(meta)
        ends = [session._epoch(s["end"]) if s.get("end") else int(dt.datetime.now().timestamp())
                for s in meta["segments"]]
        entry["journal_records"] = journal.dispose_range(lo, max(ends) + 1, reason=f"session {sid}")
    if store.active_id() == sid:
        store.clear_active()
        store.set_paused(False)
    shutil.rmtree(sdir)
    store.append_jsonl(store.home() / "disposed.jsonl", entry)
    return entry


def fingerprint(sid: str) -> str:
    """Short content hash shown before a disposal, so you know what goes."""
    sdir = store.session_dir(sid)
    h = hashlib.sha256()
    for f in sorted(sdir.rglob("*")):
        if f.is_file():
            h.update(f.read_bytes())
    return h.hexdigest()[:12]
