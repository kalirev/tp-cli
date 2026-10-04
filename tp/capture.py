"""Redaction, git-diff snapshots, and localhost port snapshots."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

REDACTED = "‹redacted›"  # ‹redacted›

# Built-in redaction. These run on every captured command string before it is
# ever written to the finalized timeline or pushed anywhere.
_BUILTIN = [
    # --password foo / --token=foo / --api-key foo
    (re.compile(r"(--?(?:password|passwd|pass|token|secret|api[-_]?key|apikey|auth|bearer)[=\s]+)(\S+)", re.I),
     lambda m: m.group(1) + REDACTED),
    # NAME_TOKEN=value  /  AWS_SECRET_ACCESS_KEY=value
    (re.compile(r"\b([A-Za-z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|ACCESS_KEY)[A-Za-z0-9_]*=)(\S+)"),
     lambda m: m.group(1) + REDACTED),
    # bare key=value, any case:  token=... secret=... password=... apikey=...
    (re.compile(r"\b(token|secret|passwd|password|api[-_]?key|apikey|access[-_]?key)=(\S+)", re.I),
     lambda m: m.group(1) + "=" + REDACTED),
    # Authorization: Bearer xxxxx
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}", re.I),
     lambda m: m.group(1) + REDACTED),
    # AWS access key ids
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), lambda m: REDACTED),
    # GitHub / common PATs
    (re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}\b"), lambda m: REDACTED),
    # Private-key headers accidentally echoed
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), lambda m: REDACTED),
]


def redact(text: str, extra_patterns: list[str] | None = None) -> str:
    if not text:
        return text
    for rx, repl in _BUILTIN:
        text = rx.sub(repl, text)
    for pat in extra_patterns or []:
        try:
            text = re.sub(pat, REDACTED, text)
        except re.error:
            continue
    return text


def _run(cmd: list[str], cwd: str | None = None) -> str:
    try:
        out = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=15)
        return out.stdout
    except (subprocess.SubprocessError, OSError):
        return ""


def git_root(path: str) -> str | None:
    if not shutil.which("git"):
        return None
    out = _run(["git", "-C", path, "rev-parse", "--show-toplevel"])
    root = out.strip()
    return root or None


def git_snapshot(repo: str, max_patch_bytes: int) -> dict:
    """A compact, reproducible record of the repo's current working state."""
    branch = _run(["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"]).strip()
    head = _run(["git", "-C", repo, "rev-parse", "--short", "HEAD"]).strip()
    stat = _run(["git", "-C", repo, "diff", "--stat"]).strip()
    staged = _run(["git", "-C", repo, "diff", "--stat", "--cached"]).strip()
    patch = _run(["git", "-C", repo, "diff", "HEAD"])
    truncated = False
    if len(patch) > max_patch_bytes:
        patch = patch[:max_patch_bytes]
        truncated = True
    return {
        "repo": repo,
        "branch": branch,
        "head": head,
        "stat": stat,
        "staged_stat": staged,
        "patch": patch,
        "patch_truncated": truncated,
    }


def localhost_ports() -> list[dict]:
    """Snapshot listening local services (the web localhosts you test)."""
    services: list[dict] = []
    if shutil.which("ss"):
        out = _run(["ss", "-tlnpH"])
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            local = parts[3]
            proc = parts[-1] if "users:" in line else ""
            services.append({"local": local, "proc": proc})
    elif shutil.which("lsof"):
        out = _run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"])
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 9:
                services.append({"local": parts[8], "proc": parts[0]})
    return services
