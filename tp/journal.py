"""The always-on journal: every command, in every hooked terminal, all the time.

v0.3 changes the model. A session no longer *is* the recording; it is a time
window cut from this journal. That is what makes backfill possible: if you
forgot to `tp start`, the work was still journaled, with its output, and
`tp start --from 21:40` (or `tp extend --from ...`) simply widens the window.

Layout under $TP_HOME/journal/:

    YYYY-MM-DD.jsonl   one file per UTC day, one record per command
    tip.json           {"jseq": n, "hash": "<last record hash>"}
    .lock              flock target, so concurrent terminals append in order
    .pruned            mtime = last retention pass

Every record is redacted *before* it is written (secrets never reach disk) and
hash-chained *as it is written*: record.prev = previous record's hash, and
record.hash = sha256 over the canonical record. Sessions that include journal
records seal those hashes (`jhash`) into their own chain, so a record can't be
inserted or edited after the fact without breaking both chains - including
the digests of other sessions that were already sealed or pushed.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

from . import store

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None

GENESIS = "0" * 64


def jdir() -> Path:
    return store.home() / "journal"


def _day_file(ts: int) -> Path:
    day = dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")
    return jdir() / f"{day}.jsonl"


def _canon(rec: dict) -> bytes:
    body = {k: v for k, v in rec.items() if k != "hash"}
    return json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")


def record_hash(rec: dict) -> str:
    return hashlib.sha256(_canon(rec)).hexdigest()


@contextmanager
def _lock():
    d = jdir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    f = open(d / ".lock", "a")
    try:
        if fcntl is not None:
            fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def _read_tip() -> tuple[int, str]:
    tip = store.read_json(jdir() / "tip.json", default=None)
    if isinstance(tip, dict) and "hash" in tip:
        return int(tip.get("jseq", 0)), tip["hash"]
    # tip missing or damaged: recover from the newest record on disk
    for f in reversed(day_files()):
        recs = store.read_jsonl(f)
        if recs:
            return int(recs[-1].get("jseq", 0)), recs[-1].get("hash", GENESIS)
    return 0, GENESIS


def append(rec: dict) -> dict:
    """Chain and append one record. `rec` must already be redacted."""
    with _lock():
        seq, prev = _read_tip()
        rec = dict(rec)
        rec["jseq"] = seq + 1
        rec["prev"] = prev
        rec["hash"] = record_hash(rec)
        path = _day_file(int(rec["ts"]))
        new = not path.exists()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if new:
            os.chmod(path, 0o600)
        store.write_json(jdir() / "tip.json", {"jseq": rec["jseq"], "hash": rec["hash"]})
    return rec


def day_files() -> list[Path]:
    d = jdir()
    if not d.exists():
        return []
    return sorted(p for p in d.glob("????-??-??.jsonl"))


def records(since: int | None = None, until: int | None = None) -> list[dict]:
    """Journal records with since <= ts < until, in time order."""
    out: list[dict] = []
    lo_day = dt.datetime.fromtimestamp(since, dt.timezone.utc).strftime("%Y-%m-%d") if since else None
    hi_day = dt.datetime.fromtimestamp(until, dt.timezone.utc).strftime("%Y-%m-%d") if until else None
    for f in day_files():
        day = f.stem
        # records are filed by start ts, but a long command can be written a
        # day later; include the neighbouring day files to be safe
        if lo_day and day < _shift(lo_day, -1):
            continue
        if hi_day and day > _shift(hi_day, 1):
            continue
        for r in store.read_jsonl(f):
            ts = int(r.get("ts", 0))
            if r.get("disposed"):
                continue
            if since is not None and ts < since:
                continue
            if until is not None and ts >= until:
                continue
            out.append(r)
    out.sort(key=lambda r: (int(r.get("ts", 0)), int(r.get("jseq", 0))))
    return out


def _shift(day: str, n: int) -> str:
    d = dt.date.fromisoformat(day) + dt.timedelta(days=n)
    return d.isoformat()


def tip_hash() -> str:
    return _read_tip()[1]


def verify() -> tuple[bool, str, int]:
    """Walk the whole journal in write order. Return (ok, message, count)."""
    recs: list[dict] = []
    for f in day_files():
        recs.extend(store.read_jsonl(f))
    recs.sort(key=lambda r: int(r.get("jseq", 0)))
    if not recs:
        return True, "journal empty", 0
    prev = recs[0].get("prev")
    anchored = prev != GENESIS
    disposed = 0
    for r in recs:
        if r.get("prev") != prev:
            return False, f"jseq={r.get('jseq')} prev mismatch (record removed or reordered)", len(recs)
        if r.get("disposed"):
            disposed += 1   # body deliberately deleted; its hash still links the chain
        elif record_hash(r) != r.get("hash"):
            return False, f"jseq={r.get('jseq')} hash mismatch (edited)", len(recs)
        prev = r["hash"]
    note = " (older records pruned; chain anchored at the oldest kept record)" if anchored else ""
    if disposed:
        note += f" ({disposed} records disposed on purpose)"
    return True, "journal chain intact" + note, len(recs)


def dispose_range(since: int, until: int, reason: str = "") -> int:
    """Delete the content of every record with since <= ts < until.

    Each record becomes a tombstone that keeps only jseq / ts / prev / hash, so
    the chain still links end to end and `tp journal verify` reports exactly
    how many records were disposed - deletion is possible, hiding it is not.
    """
    n = 0
    with _lock():
        for f in day_files():
            recs = store.read_jsonl(f)
            changed = False
            for i, r in enumerate(recs):
                ts = int(r.get("ts", 0))
                if since <= ts < until and not r.get("disposed"):
                    recs[i] = {"jseq": r.get("jseq"), "ts": ts, "prev": r.get("prev"),
                               "hash": r.get("hash"), "disposed": True,
                               "disposed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                               "reason": reason}
                    changed = True
                    n += 1
            if changed:
                tmp = f.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as out:
                    for r in recs:
                        out.write(json.dumps(r, ensure_ascii=False) + "\n")
                os.chmod(tmp, 0o600)
                tmp.replace(f)
    return n


def index_by_hash() -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for f in day_files():
        for r in store.read_jsonl(f):
            if r.get("hash"):
                idx[r["hash"]] = r
    return idx


# ---- retention ----------------------------------------------------------------

def prune(days: int, max_mb: int, protect_from: int | None = None, force: bool = False) -> list[str]:
    """Drop whole day files older than `days`, then oldest-first until under
    `max_mb`. Never drops a file that could hold records of the active session
    (anything on or after `protect_from`). Runs at most hourly unless forced."""
    d = jdir()
    stamp = d / ".pruned"
    if not force and stamp.exists() and time.time() - stamp.stat().st_mtime < 3600:
        return []
    removed: list[str] = []
    with _lock():
        files = day_files()
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).strftime("%Y-%m-%d")
        guard = (dt.datetime.fromtimestamp(protect_from, dt.timezone.utc) - dt.timedelta(days=1)).strftime("%Y-%m-%d") \
            if protect_from else None

        def droppable(f: Path) -> bool:
            return not (guard and f.stem >= guard) and f != files[-1]

        for f in list(files):
            if f.stem < cutoff and droppable(f):
                f.unlink()
                removed.append(f.name)
                files.remove(f)
        total = sum(f.stat().st_size for f in files)
        for f in list(files):
            if total <= max_mb * 1024 * 1024:
                break
            if droppable(f):
                total -= f.stat().st_size
                f.unlink()
                removed.append(f.name)
                files.remove(f)
        stamp.touch()
    return removed


def clear() -> int:
    n = 0
    with _lock():
        for f in day_files():
            f.unlink()
            n += 1
        tip = jdir() / "tip.json"
        if tip.exists():
            tip.unlink()
    return n


# ---- on/off switches ------------------------------------------------------------

def _off_global() -> Path:
    return store.home() / "off"


def _off_window(pid: int) -> Path:
    return store.home() / "off.d" / str(pid)


def is_off(shell_pid: int | None = None) -> bool:
    if _off_global().exists():
        return True
    if shell_pid and _off_window(shell_pid).exists():
        return True
    return False


def set_off(flag: bool, shell_pid: int | None = None) -> None:
    p = _off_window(shell_pid) if shell_pid else _off_global()
    if flag:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("1", encoding="utf-8")
    elif p.exists():
        p.unlink()
    # forget window switches for shells that no longer exist
    od = store.home() / "off.d"
    if od.exists():
        for f in od.iterdir():
            try:
                os.kill(int(f.name), 0)
            except (ValueError, ProcessLookupError):
                f.unlink(missing_ok=True)
            except PermissionError:
                pass
