"""Time parsing for --from/--since, and the fish-history fallback importer.

Fish history is the fallback for time the journal doesn't cover (before you
upgraded to v0.3, or a terminal that wasn't hooked). It is weaker evidence and
is labelled that way: commands only, no output / cwd / exit code, nothing was
hash-chained at the time, and fish keeps only the *latest* run of a repeated
command, so repeats inside the window can be missing.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path

_DUR = re.compile(r"^(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$")


def parse_when(text: str, now: dt.datetime | None = None) -> int:
    """Parse a point in time into a UTC epoch.

    Accepts:  21:40  |  21:40:15  |  2026-10-12 21:40  |  2026-10-12T21:40
              1h  45m  1h30m  90s  2d   (meaning "that long ago")
    A bare clock time is today, local time; if that is still in the future it
    means yesterday (so `--from 23:30` after midnight does the right thing).
    """
    now = now or dt.datetime.now().astimezone()
    t = text.strip().lower().removesuffix(" ago")
    m = _DUR.match(t)
    if m and any(m.groups()):
        d, h, mi, s = (int(x or 0) for x in m.groups())
        return int((now - dt.timedelta(days=d, hours=h, minutes=mi, seconds=s)).timestamp())
    m = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", t)
    if m:
        hh, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        cand = now.replace(hour=hh, minute=mm, second=ss, microsecond=0)
        if cand > now:
            cand -= dt.timedelta(days=1)
        return int(cand.timestamp())
    try:
        parsed = dt.datetime.fromisoformat(text.strip().replace(" ", "T"))
    except ValueError:
        raise ValueError(f"can't read time '{text}'. use 21:40, '2026-10-12 21:40', or 1h / 45m") from None
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()  # interpret as local time
    return int(parsed.timestamp())


def fmt_local(ts: int, with_date: bool = False) -> str:
    d = dt.datetime.fromtimestamp(ts).astimezone()
    return d.strftime("%Y-%m-%d %H:%M:%S" if with_date else "%H:%M:%S")


# ---- fish history ---------------------------------------------------------------

def fish_history_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "fish" / "fish_history"


def _decode(s: str) -> str:
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] in "n\\":
            out.append("\n" if s[i + 1] == "n" else "\\")
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def fish_entries(path: Path | None = None) -> list[tuple[int, str]]:
    p = path or fish_history_path()
    if not p.exists():
        return []
    out: list[tuple[int, str]] = []
    cmd = None
    with open(p, encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("- cmd: "):
                cmd = _decode(line[7:].rstrip("\n"))
            elif line.startswith("  when: ") and cmd is not None:
                try:
                    out.append((int(line.split(":", 1)[1]), cmd))
                except ValueError:
                    pass
                cmd = None
    out.sort()
    return out
