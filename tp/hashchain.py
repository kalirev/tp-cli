"""Tamper-evident hash chaining for a session's timeline.

Each event is sealed with sha256 over its canonical fields plus the previous
event's hash. Change any field of any past event and every later hash - and
the session digest - stops matching. This mirrors the ProofLayer evidence
model: the record is verifiable proof, not just a log.
"""
from __future__ import annotations

import hashlib
import json

GENESIS = "0" * 64

# Fields that are sealed into the chain. Anything else (large diffs, port
# dumps) is stored as an artifact with its own content hash and folded into
# the final digest, so it is covered without bloating each link.
_SEALED = ("seq", "ts", "type", "cwd", "command", "exit", "duration", "text")


def _canon(ev: dict, prev_hash: str) -> bytes:
    payload = {k: ev.get(k) for k in _SEALED}
    payload["prev_hash"] = prev_hash
    return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")


def seal(events: list[dict]) -> list[dict]:
    """Assign seq, prev_hash and hash to events in place; return them."""
    prev = GENESIS
    for i, ev in enumerate(events, start=1):
        ev["seq"] = i
        ev["prev_hash"] = prev
        ev["hash"] = hashlib.sha256(_canon(ev, prev)).hexdigest()
        prev = ev["hash"]
    return events


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def digest(events: list[dict], artifact_hashes: list[str]) -> str:
    """Session digest = chain tip folded together with artifact hashes."""
    tip = events[-1]["hash"] if events else GENESIS
    h = hashlib.sha256()
    h.update(tip.encode("utf-8"))
    for ah in sorted(artifact_hashes):
        h.update(ah.encode("utf-8"))
    return h.hexdigest()


def verify(events: list[dict], artifact_hashes: list[str], expected_digest: str):
    """Recompute the chain. Return (ok, message)."""
    prev = GENESIS
    for ev in events:
        want = hashlib.sha256(_canon(ev, prev)).hexdigest()
        if ev.get("prev_hash") != prev:
            return False, f"event seq={ev.get('seq')} prev_hash mismatch"
        if ev.get("hash") != want:
            return False, f"event seq={ev.get('seq')} hash mismatch (tampered?)"
        prev = ev["hash"]
    if digest(events, artifact_hashes) != expected_digest:
        return False, "session digest mismatch"
    return True, "chain intact"
