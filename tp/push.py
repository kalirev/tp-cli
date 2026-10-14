"""Push a finalized session to Rev Vision over HTTPS (stdlib only)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from . import config, session, store


def build_payload(sid: str, bucket: str | None, with_output: bool = False) -> dict:
    meta = session.load_meta(sid)
    if meta is None:
        raise session.SessionError(f"unknown session {sid}")
    if not meta.get("digest"):
        raise session.SessionError(f"{sid} is not sealed yet (tp stop first)")
    sd = store.session_dir(sid)
    events = store.read_json(sd / "events.jsonl", default=[]) or []
    if not with_output:
        # Output stays on this machine. Its sha256 is sealed in the chain, so
        # the pushed record still verifies without it.
        events = [{k: v for k, v in e.items() if k != "output"} for e in events]
    artifacts = store.read_jsonl(sd / "artifacts.jsonl")
    starts = [s["start"] for s in meta.get("segments", []) if s.get("start")]
    return {
        "id": meta["id"],
        "title": meta["title"],
        "project": meta.get("project"),
        "bucket": bucket or meta.get("bucket") or "archive",
        "host": meta.get("host"),
        "started_at": min(starts, key=session._epoch) if starts else meta.get("created_at"),
        "ended_at": meta.get("ended_at"),
        "active_seconds": meta.get("active_seconds", session.active_seconds(meta)),
        "active_estimated_seconds": (meta.get("active_breakdown") or {}).get("estimated", 0),
        "origins": meta.get("origins"),
        "prior_digests": meta.get("prior_digests", []),
        "output_included": bool(with_output),
        "digest": meta.get("digest"),
        "event_count": len(events),
        "events": events,
        "artifacts": artifacts,
    }


def push(sid: str, bucket: str | None, with_output: bool | None = None) -> dict:
    cfg = config.load()
    if with_output is None:
        with_output = bool(cfg.get("push_output", False))
    if not cfg.get("token"):
        raise session.SessionError(
            "no ingest token set - run:  tp config token <YOUR_TOKEN>")
    url = cfg["api_url"].rstrip("/") + cfg["ingest_path"]
    payload = build_payload(sid, bucket, with_output)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {cfg['token']}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            out = json.loads(body) if body else {}
            out.setdefault("status", resp.status)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise session.SessionError(f"server returned {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise session.SessionError(f"could not reach {url}: {e.reason}") from e
    meta = session.load_meta(sid)
    meta["pushed_at"] = session._now()
    meta["pushed_digest"] = meta.get("digest")
    session.save_meta(sid, meta)
    return out


def record_bucket(sid: str, bucket: str) -> None:
    meta = session.load_meta(sid)
    if meta is None:
        raise session.SessionError(f"unknown session {sid}")
    meta["bucket"] = bucket
    session.save_meta(sid, meta)


def delete_remote(sid: str) -> dict:
    """Ask Rev Vision to delete a pushed session (DELETE on the ingest route)."""
    cfg = config.load()
    if not cfg.get("token"):
        raise session.SessionError("no ingest token set - run:  tp config token <YOUR_TOKEN>")
    from urllib.parse import quote
    url = cfg["api_url"].rstrip("/") + cfg["ingest_path"] + "?id=" + quote(sid)
    req = urllib.request.Request(url, method="DELETE")
    req.add_header("Authorization", f"Bearer {cfg['token']}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {"ok": True}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"ok": True, "note": "not on the server"}
        detail = e.read().decode("utf-8", "replace")
        raise session.SessionError(f"server returned {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise session.SessionError(f"could not reach {url}: {e.reason}") from e
