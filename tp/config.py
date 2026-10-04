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
}


def load() -> dict:
    cfg = dict(DEFAULTS)
    saved = store.read_json(store.home() / "config.json", default={})
    if isinstance(saved, dict):
        cfg.update(saved)
    return cfg


def save(cfg: dict) -> None:
    store.ensure_home()
    store.write_json(store.home() / "config.json", cfg)


def set_key(key: str, value: str) -> dict:
    cfg = load()
    if key in ("capture_ports", "capture_gitdiff"):
        cfg[key] = value.lower() in ("1", "true", "yes", "on")
    elif key == "max_patch_bytes":
        cfg[key] = int(value)
    else:
        cfg[key] = value
    save(cfg)
    return cfg
