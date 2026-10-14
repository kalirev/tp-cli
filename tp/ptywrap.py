"""`tp pty` - capture what each command prints, not just what you typed.

The wrapper sits between your terminal and your shell:

    kitty  <->  tp pty  <->  fish

It relays every byte unchanged, except for two private escape sequences the
shell hook prints around each command:

    ESC ] 7777 ; tp ; S ; <id> BEL     command <id> is starting
    ESC ] 7777 ; tp ; E ; <id> BEL     command <id> finished
    ESC ] 7777 ; tp ; X ; <id> BEL     discard (command won't be recorded,
                                       e.g. space-prefixed in bash)

Those are stripped (your terminal never sees them). Everything printed in
between is the command's output. On E the wrapper cleans it (escape codes,
progress-bar overwrites), redacts it, caps it (head + tail), and drops it in
~/.tp/spool/<id>.json, where `tp _record` picks it up a few ms later and
journals it together with the command.

Full-screen programs (vim, htop, less, ...) switch to the alternate screen;
their screen contents are not recorded, only that they ran.

Safety: if anything goes wrong setting up the PTY, the wrapper exec()s the
shell directly, so a broken tp can never leave you without a terminal.
"""
from __future__ import annotations

import errno
import json
import os
import re
import select
import signal
import sys
import time

from . import capture, config, journal, store

MARK = b"\x1b]7777;tp;"
_MARK_RX = re.compile(rb"\x1b\]7777;tp;([SEX]);([A-Za-z0-9_\-]{1,64})\x07")
_MAX_MARK = 96


def spool_dir():
    d = store.home() / "spool"
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


class _Capture:
    """Head + tail of one command's raw output, bounded in memory."""

    def __init__(self, cid: str, cap: int):
        self.id = cid
        self.start = time.time()
        self.half = max(cap // 2, 1024)
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0
        self.fullscreen = False
        self._edge = b""

    def feed(self, data: bytes) -> None:
        self.total += len(data)
        probe = self._edge + data
        if capture.ALT_SCREEN.search(probe):
            self.fullscreen = True
        self._edge = probe[-12:]
        room = self.half - len(self.head)
        if room > 0:
            self.head += data[:room]
            data = data[room:]
        if data:
            self.tail += data
            if len(self.tail) > self.half:
                del self.tail[: len(self.tail) - self.half]

    def result(self, redact_extra: list[str]) -> dict:
        truncated = self.total > len(self.head) + len(self.tail)
        if self.fullscreen:
            text = "‹full-screen program - screen contents not recorded›"
        else:
            raw = bytes(self.head).decode("utf-8", "replace")
            if truncated:
                omitted = self.total - len(self.head) - len(self.tail)
                raw += f"\n‹… {omitted} bytes omitted …›\n"
            if self.tail:
                raw += bytes(self.tail).decode("utf-8", "replace")
            text = capture.redact_output(capture.clean_terminal(raw), redact_extra)
        return {
            "id": self.id,
            "ts_start": self.start,
            "ts_end": time.time(),
            "output": text,
            "output_bytes": self.total,
            "output_truncated": truncated,
            "fullscreen": self.fullscreen,
        }


class StreamSplitter:
    """Strips tp markers from the shell's output and slices it per command.

    Markers may arrive split across reads, so a possible marker prefix at the
    end of a chunk is held back until the next chunk decides it.
    """

    def __init__(self, cap: int, on_done):
        self.cap = cap
        self.on_done = on_done
        self.pending = b""
        self.cur: _Capture | None = None

    def _emit(self, data: bytes, out: list[bytes]) -> None:
        if not data:
            return
        out.append(data)
        if self.cur is not None:
            self.cur.feed(data)

    def feed(self, data: bytes) -> bytes:
        buf = self.pending + data
        self.pending = b""
        out: list[bytes] = []
        while buf:
            i = buf.find(MARK)
            if i == -1:
                keep = _partial_suffix(buf, MARK)
                self._emit(buf[: len(buf) - keep], out)
                self.pending = buf[len(buf) - keep:] if keep else b""
                break
            self._emit(buf[:i], out)
            buf = buf[i:]
            m = _MARK_RX.match(buf)
            if m:
                self._marker(m.group(1), m.group(2).decode())
                buf = buf[m.end():]
                continue
            if b"\x07" in buf[:_MAX_MARK] or len(buf) >= _MAX_MARK:
                # looked like ours but isn't: pass it through untouched
                self._emit(buf[:1], out)
                buf = buf[1:]
                continue
            self.pending = buf  # incomplete marker; wait for more bytes
            break
        return b"".join(out)

    def flush(self) -> bytes:
        data, self.pending = self.pending, b""
        if self.cur is not None and data:
            self.cur.feed(data)
        return data

    def _marker(self, kind: bytes, cid: str) -> None:
        if kind == b"S":
            self.cur = _Capture(cid, self.cap)
        elif kind == b"E" and self.cur is not None and self.cur.id == cid:
            done, self.cur = self.cur, None
            self.on_done(done)
        elif kind == b"X":
            self.cur = None


def _partial_suffix(buf: bytes, mark: bytes) -> int:
    """Length of the longest suffix of buf that is a proper prefix of mark."""
    for n in range(min(len(mark) - 1, len(buf)), 0, -1):
        if buf.endswith(mark[:n]):
            return n
    return 0


def _write_spool(res: dict, shell_pid: int) -> None:
    if journal.is_off(shell_pid):
        return
    d = spool_dir()
    tmp = d / f".{res['id']}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    os.replace(tmp, d / f"{res['id']}.json")


def _copy_winsize(src_fd: int, dst_fd: int) -> None:
    import fcntl
    import termios
    try:
        ws = fcntl.ioctl(src_fd, termios.TIOCGWINSZ, b"\0" * 8)
        fcntl.ioctl(dst_fd, termios.TIOCSWINSZ, ws)
    except OSError:
        pass


def run(argv: list[str]) -> int:
    if not argv:
        argv = [os.environ.get("SHELL") or "/bin/sh"]
    # Not a terminal, wrapping disabled, or already inside a tp pty: just run it.
    if (not os.isatty(0) or not os.isatty(1) or os.environ.get("TP_NO_PTY")
            or os.environ.get("TP_PTY") == _ttyname(0)):
        os.execvp(argv[0], argv)

    try:
        import pty
        import termios
        import tty
        cfg = config.load()
        cap = int(cfg.get("output_max_bytes", 65536))
        extra = cfg.get("redact_patterns", [])
        pid, master = pty.fork()
    except Exception:
        os.environ["TP_NO_PTY"] = "1"
        os.execvp(argv[0], argv)

    if pid == 0:  # child: become the shell
        os.environ["TP_PTY"] = _ttyname(0) or "1"
        os.environ.pop("TP_NO_PTY", None)
        try:
            os.execvp(argv[0], argv)
        except OSError as e:
            sys.stderr.write(f"tp pty: cannot run {argv[0]}: {e}\n")
            os._exit(127)

    _copy_winsize(0, master)
    old = termios.tcgetattr(0)

    def on_winch(*_):
        _copy_winsize(0, master)
        try:
            os.kill(pid, signal.SIGWINCH)
        except ProcessLookupError:
            pass

    signal.signal(signal.SIGWINCH, on_winch)

    def done(c: _Capture) -> None:
        try:
            _write_spool(c.result(extra), pid)
        except Exception:
            pass  # never let capture break the terminal

    split = StreamSplitter(cap, done)
    try:
        tty.setraw(0)
        _pump(master, split)
    finally:
        termios.tcsetattr(0, termios.TCSAFLUSH, old)
    os.close(master)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else status >> 8


def _pump(master: int, split: StreamSplitter) -> None:
    stdin_open = True
    while True:
        fds = [master] + ([0] if stdin_open else [])
        try:
            r, _, _ = select.select(fds, [], [])
        except InterruptedError:
            continue
        if master in r:
            try:
                data = os.read(master, 65536)
            except OSError as e:
                if e.errno in (errno.EIO, errno.EBADF):
                    data = b""
                else:
                    raise
            if not data:
                tail = split.flush()
                if tail:
                    _write_all(1, tail)
                return
            out = split.feed(data)
            if out:
                _write_all(1, out)
        if stdin_open and 0 in r:
            data = os.read(0, 65536)
            if not data:
                stdin_open = False
            else:
                _write_all(master, data)


def _write_all(fd: int, data: bytes) -> None:
    while data:
        try:
            n = os.write(fd, data)
        except InterruptedError:
            continue
        except BlockingIOError:
            select.select([], [fd], [])
            continue
        data = data[n:]


def _ttyname(fd: int) -> str:
    try:
        return os.ttyname(fd)
    except OSError:
        return ""


def take_spool(cid: str, wait: float) -> dict | None:
    """Collect (and delete) the wrapper's capture for command `cid`."""
    if not cid:
        return None
    p = store.home() / "spool" / f"{cid}.json"
    deadline = time.time() + wait
    while not p.exists():
        if time.time() >= deadline:
            return None
        time.sleep(0.01)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = None
    p.unlink(missing_ok=True)
    return data


def sweep_spool(max_age: float = 600) -> None:
    """Delete captures nobody claimed (space-prefixed commands, crashes)."""
    d = store.home() / "spool"
    if not d.exists():
        return
    now = time.time()
    for f in d.iterdir():
        try:
            if now - f.stat().st_mtime > max_age:
                f.unlink()
        except OSError:
            pass
