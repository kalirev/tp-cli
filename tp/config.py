"""Global config (~/.tp/config.json)."""
from __future__ import annotations

import socket

from . import store

DEFAULTS = {
    # Rev Vision ingest endpoint and bearer token (see server/ for the route).
    "api_url": "https://rev-engineering.vercel.app",
    "ingest_path": "/api/ingest/session",
    "token": "",
    # Identity stamped on every session.
    "host": socket.gethostname(),
    # Capture toggles.
    "capture_ports": True,     # snapshot listening localhost services
    "capture_gitdiff": True,   # snapshot git diffs for repos you touched
    "max_patch_bytes": 200000, # cap a single repo's stored patch
    # Extra redaction regexes (applied on top of the built-in set).
    "redact_patterns": [],
    # v0.3 always-on journal (see tp/journal.py).
    "journal": True,            # false = v0.2 behaviour: record only inside a session
    "journal_days": 7,          # keep this many days of journal...
    "journal_max_mb": 1024,     # ...or this much, whichever is hit first
    "pty": True,                # hook wraps interactive shells in `tp pty` (output capture)
    "output_max_bytes": 65536,  # per command: first half + last half are kept
    "push_output": False,       # include command output in pushes by default?
    "idle_gap_seconds": 300,
    "archive_path": "",         # where `tp archive` moves sealed sessions ("" = ~/.tp/archive)    # backfilled active time: each command counts until
                                # the next one, at most this long after it ends
}

_BOOL = ("capture_ports", "capture_gitdiff", "journal", "pty", "push_output")
_INT = ("max_patch_bytes", "journal_days", "journal_max_mb", "output_max_bytes", "idle_gap_seconds")


def load() -> dict:
    cfg = dict(DEFAULTS)
    saved = store.read_json(store.home() / "config.json", default={})
    if isinstance(saved, dict):
        cfg.update(saved)
    # v0.2 stored `tp config redact_patterns '[...]'` as one string, which was
    # then applied character by character. Read it as the list it was meant to be.
    rp = cfg.get("redact_patterns")
    if isinstance(rp, str):
        import json
        try:
            rp = json.loads(rp)
        except json.JSONDecodeError:
            rp = [rp]
        cfg["redact_patterns"] = rp if isinstance(rp, list) else [str(rp)]
    return cfg


def save(cfg: dict) -> None:
    store.ensure_home()
    store.write_json(store.home() / "config.json", cfg)


def set_key(key: str, value: str) -> dict:
    cfg = load()
    if key == "redact_patterns":
        import json
        try:
            val = json.loads(value)
        except json.JSONDecodeError:
            val = [value]           # a single bare regex
        if isinstance(val, str):
            val = [val]
        if not isinstance(val, list):
            raise SystemExit('redact_patterns must be a JSON list, e.g. \'["corp_[A-Za-z0-9]{20}"]\'')
        cfg[key] = val
    elif key in _BOOL:
        cfg[key] = value.lower() in ("1", "true", "yes", "on")
    elif key in _INT:
        cfg[key] = int(value)
    else:
        cfg[key] = value
    save(cfg)
    return cfg
