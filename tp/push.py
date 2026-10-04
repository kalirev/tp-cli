"""Push a finalized session to Rev Vision over HTTPS (stdlib only)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import config, session, store


def build_payload(sid: str, bucket: str | None) -> dict:
    meta = session.load_meta(sid)
    if meta is None:
        raise session.SessionError(f"unknown session {sid}")
    sd = store.session_dir(sid)
    events = store.read_json(sd / "events.jsonl", default=[]) or []
    artifacts = store.read_jsonl(sd / "artifacts.jsonl")
    return {
        "id": meta["id"],
        "title": meta["title"],
        "project": meta.get("project"),
        "bucket": bucket or meta.get("bucket") or "archive",
        "host": meta.get("host"),
        "started_at": meta.get("created_at"),
        "ended_at": meta.get("ended_at"),
        "active_seconds": meta.get("active_seconds", session.active_seconds(meta)),
        "digest": meta.get("digest"),
        "event_count": len(events),
        "events": events,
        "artifacts": artifacts,
    }


def push(sid: str, bucket: str | None) -> dict:
    cfg = config.load()
    if not cfg.get("token"):
        raise session.SessionError(
            "no ingest token set - run:  tp config token <YOUR_TOKEN>")
    url = cfg["api_url"].rstrip("/") + cfg["ingest_path"]
    payload = build_payload(sid, bucket)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {cfg['token']}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            out = json.loads(body) if body else {}
            out.setdefault("status", resp.status)
            return out
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise session.SessionError(f"server returned {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise session.SessionError(f"could not reach {url}: {e.reason}") from e


def record_bucket(sid: str, bucket: str) -> None:
    meta = session.load_meta(sid)
    if meta is None:
        raise session.SessionError(f"unknown session {sid}")
    meta["bucket"] = bucket
    session.save_meta(sid, meta)
