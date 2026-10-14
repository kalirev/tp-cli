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


# ---- terminal output ----------------------------------------------------------
#
# Output leaks secrets far more often than commands do (a `cat .env`, an
# `aws configure list`, a token printed by a CLI on login). Output therefore
# gets the command rules above PLUS:
#   - "key: value" forms, not just key=value
#   - whole PEM private-key blocks
#   - well-known token prefixes (OpenAI/Anthropic, Slack, Google, GitLab, HF, JWT)
#   - a high-entropy check for anything else that looks like a random secret.

_OUTPUT_EXTRA = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S),
     lambda m: REDACTED),
    (re.compile(r"\b((?:password|passwd|secret|token|api[-_ ]?key|access[-_ ]?key|client[-_ ]?secret)"
                r"[\"']?\s*[:=]\s*[\"']?)([^\s\"',;]{4,})", re.I),
     lambda m: m.group(1) + REDACTED),
    (re.compile(r"\b(?:sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}|xox[abposr]-[A-Za-z0-9\-]{10,}"
                r"|AIza[0-9A-Za-z_\-]{35}|glpat-[A-Za-z0-9_\-]{20,}|hf_[A-Za-z0-9]{30,})"),
     lambda m: REDACTED),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
     lambda m: REDACTED),
]

# Candidate random tokens: no '/', '.' or ':' so paths, hostnames and versions
# never match; mixed case + digits required so hex hashes and commit ids
# (useful in a writeup, and not secrets) are kept.
_TOKEN = re.compile(r"(?<![A-Za-z0-9+_\-])[A-Za-z0-9+_=\-]{24,}(?![A-Za-z0-9+_\-])")


def _entropy(s: str) -> float:
    from collections import Counter
    from math import log2
    n = len(s)
    return -sum(c / n * log2(c / n) for c in Counter(s).values())


def _looks_secret(tok: str) -> bool:
    if not (any(c.islower() for c in tok) and any(c.isupper() for c in tok)
            and any(c.isdigit() for c in tok)):
        return False
    return _entropy(tok) >= 4.2


def redact_output(text: str, extra_patterns: list[str] | None = None) -> str:
    if not text:
        return text
    for rx, repl in _OUTPUT_EXTRA:
        text = rx.sub(repl, text)
    text = redact(text, extra_patterns)
    return _TOKEN.sub(lambda m: REDACTED if _looks_secret(m.group(0)) else m.group(0), text)


# Escape sequences: CSI (colours, cursor moves), OSC (titles, hyperlinks,
# kitty/iTerm integration), and the remaining two-byte ESC forms.
_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ESC = re.compile(r"\x1b[PX^_][^\x1b]*\x1b\\|\x1b[@-Z\\-_]|\x1b[()][0-9A-Za-z]")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
ALT_SCREEN = re.compile(rb"\x1b\[\?(?:1049|1047|47)h")


def clean_terminal(raw: str) -> str:
    """Turn a captured terminal byte stream into the text a person saw.

    Strips escape sequences, applies carriage-return overwrites (progress bars
    keep only their final state) and backspaces, and drops other controls.
    """
    s = _OSC.sub("", raw)
    s = _CSI.sub("", s)
    s = _ESC.sub("", s)
    s = s.replace("\r\n", "\n")
    lines = []
    for line in s.split("\n"):
        if "\r" in line:
            # later segments overwrite earlier ones from column 0
            buf: list[str] = []
            for seg in line.split("\r"):
                buf[: len(seg)] = list(seg)
            line = "".join(buf)
        if "\b" in line:
            out: list[str] = []
            for ch in line:
                if ch == "\b":
                    if out:
                        out.pop()
                else:
                    out.append(ch)
            line = "".join(out)
        lines.append(_CTRL.sub("", line).rstrip())
    return "\n".join(lines).strip("\n")


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
