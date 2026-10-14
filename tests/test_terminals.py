"""Real-terminal tests: hooks + `tp pty`, driven like kitty drives a shell.

Each test opens a pseudo-terminal, starts fish or bash with the tp hook in its
rc file, types commands, and then checks the journal. Skipped automatically
for a shell that isn't installed. Takes ~30 s.

    python3 -m unittest tests.test_terminals -v
"""
import json
import os
import pty
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Terminal(unittest.TestCase):
    shell = ""

    def setUp(self):
        if not shutil.which(self.shell):
            self.skipTest(f"{self.shell} not installed")
        self.tmp = Path(tempfile.mkdtemp(prefix="tp-term-"))
        self.home = self.tmp / "home"
        (self.home / "work").mkdir(parents=True)
        bindir = self.home / ".local" / "bin"
        subprocess.run(["sh", str(ROOT / "install.sh")], check=True, capture_output=True,
                       env={**os.environ, "HOME": str(self.home), "TP_BIN_DIR": str(bindir)})
        self.tp_bin = bindir / "tp"
        self.env = {"HOME": str(self.home), "PATH": f"{bindir}:/usr/local/bin:/usr/bin:/bin",
                    "TERM": "xterm-256color", "LANG": "C.UTF-8",
                    "TP_HOME": str(self.home / ".tp")}
        self.write_rc()
        self.tpc("config", "capture_ports", "false")
        self.tpc("config", "capture_gitdiff", "false")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def tpc(self, *args):
        r = subprocess.run([str(self.tp_bin), *args], env=self.env, capture_output=True, text=True)
        return r.stdout + r.stderr

    def type_into(self, lines, settle=1.5):
        """Start the shell in a pty, type each line, return everything the screen got."""
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(self.home / "work")
            os.execvpe(self.shell, self.argv, self.env)
        screen = b""

        def drain(t):
            nonlocal screen
            end = time.time() + t
            while time.time() < end:
                r, _, _ = select.select([fd], [], [], 0.05)
                if r:
                    try:
                        d = os.read(fd, 65536)
                    except OSError:
                        return
                    if not d:
                        return
                    screen += d

        drain(3)
        for line in lines:
            os.write(fd, line.encode() + b"\r")
            drain(settle)
        os.write(fd, b"exit\r")
        drain(1.5)
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        os.close(fd)
        return screen

    def journal(self):
        recs = []
        for f in sorted((self.home / ".tp" / "journal").glob("*.jsonl")):
            recs += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        return {r["command"]: r for r in recs if not r.get("disposed")}

    # shared checks ------------------------------------------------------------
    def check_capture(self):
        screen = self.type_into([
            "echo hello-output",
            "printf 'password: hunter22\\n'",
            " echo SPACE-PREFIXED-SECRET",
            "ls /definitely-missing",
        ])
        self.assertNotIn(b"7777;tp", screen, "markers must never reach the terminal")
        self.assertIn(b"hello-output", screen, "the terminal must still show output")
        j = self.journal()
        self.assertEqual(j["echo hello-output"]["output"], "hello-output")
        self.assertNotIn("hunter22", j["printf 'password: hunter22\\n'"]["output"])
        self.assertNotEqual(j["ls /definitely-missing"]["exit"], 0)
        self.assertIn("No such file", j["ls /definitely-missing"]["output"])
        self.assertFalse(any("SPACE-PREFIXED" in c for c in j))
        self.assertFalse(any("SPACE-PREFIXED" in json.dumps(r) for r in j.values()))
        self.assertEqual(list((self.home / ".tp" / "spool").glob("*.json")), [])

    def check_backfill_flow(self):
        self.type_into([
            "echo before-start",
            "tp start --from 10m 'terminal flow'",
            "echo after-start",
            "tp stop --to archive",
        ])
        sid = sorted(p.name for p in (self.home / ".tp" / "sessions").iterdir())[-1]
        out = self.tpc("verify", sid)
        self.assertIn("✓ verified", out)
        events = json.loads((self.home / ".tp" / "sessions" / sid / "events.jsonl").read_text())
        got = [(e["command"], e["origin"], e.get("output")) for e in events if e["type"] == "cmd"]
        self.assertIn(("echo before-start", "journal", "before-start"), got)
        self.assertIn(("echo after-start", "live", "after-start"), got)
        self.assertLess(got.index(("echo before-start", "journal", "before-start")),
                        got.index(("echo after-start", "live", "after-start")))


class Fish(Terminal):
    shell = "fish"
    argv = ["fish"]

    def write_rc(self):
        rc = self.home / ".config" / "fish" / "config.fish"
        rc.parent.mkdir(parents=True, exist_ok=True)
        rc.write_text("tp hook fish | source\n")

    def test_capture(self):
        """fish hook + tp pty: commands and output journaled, redacted; markers hidden; space-prefix skipped."""
        self.check_capture()

    def test_backfill_flow(self):
        """fish: work, then `tp start --from`, then stop -> earlier work is in the session before live work."""
        self.check_backfill_flow()

    def test_greets_once(self):
        """fish: re-launching inside tp pty doesn't print the greeting twice."""
        self.assertEqual(self.type_into(["echo x"]).count(b"Welcome to fish"), 1)

    def test_no_pty_escape_hatch(self):
        """fish: TP_NO_PTY=1 gives a plain shell that still journals commands (without output)."""
        self.env["TP_NO_PTY"] = "1"
        self.type_into(["echo plain"])
        r = self.journal()["echo plain"]
        self.assertNotIn("output", r)


class Bash(Terminal):
    shell = "bash"
    argv = ["bash", "-i"]

    def write_rc(self):
        (self.home / ".bashrc").write_text(
            "export HISTCONTROL=ignorespace\n"
            'eval "$(tp hook bash)"\n')

    def test_capture(self):
        """bash hook + tp pty: same guarantees as fish (PS0 start marker)."""
        self.check_capture()

    def test_no_stale_history_at_first_prompt(self):
        """bash: the last command of a previous session is not re-recorded on startup."""
        (self.home / ".bash_history").write_text("echo from-yesterday\n")
        self.type_into(["echo today"])
        self.assertNotIn("echo from-yesterday", self.journal())


class Pty(Terminal):
    shell = "bash"
    argv = []

    def write_rc(self):
        pass

    def test_resize_reaches_shell(self):
        """pty: window resizes are passed through to the shell inside the wrapper."""
        import fcntl
        import struct
        import termios
        pid, fd = pty.fork()
        if pid == 0:
            os.execvpe(str(self.tp_bin), [str(self.tp_bin), "pty", "bash", "--norc"], self.env)

        def drain(t):
            out, end = b"", time.time() + t
            while time.time() < end:
                r, _, _ = select.select([fd], [], [], 0.05)
                if r:
                    try:
                        out += os.read(fd, 65536)
                    except OSError:
                        break
            return out

        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 100, 0, 0))
        drain(1.5)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 132, 0, 0))
        drain(0.5)
        os.write(fd, b"stty size\r")
        out = drain(1)
        os.write(fd, b"exit\r")
        drain(1)
        os.waitpid(pid, 0)
        self.assertIn(b"50 132", out)

    def test_not_a_terminal_runs_plain(self):
        """pty: without a terminal it just runs the program (scripts and cron are unaffected)."""
        r = subprocess.run([str(self.tp_bin), "pty", "sh", "-c", "echo plain; exit 3"],
                           env=self.env, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual((r.stdout.strip(), r.returncode), ("plain", 3))


if __name__ == "__main__":
    unittest.main()
