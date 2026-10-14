"""Unit tests: python3 -m unittest discover -s tests   (stdlib only)."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tp import capture, hashchain, history, journal, ptywrap, session, store  # noqa: E402


class Home(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["TP_HOME"] = self.tmp
        os.environ["XDG_DATA_HOME"] = os.path.join(self.tmp, "xdg")
        store.ensure_home()
        store.write_json(store.home() / "config.json",
                         {"capture_ports": False, "capture_gitdiff": False})

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def rec(self, cmd, ts, out=None):
        r = {"ts": ts, "ts_end": ts + 1, "type": "cmd", "term": "f1", "cwd": "/tmp",
             "command": cmd, "exit": 0, "duration": 1}
        if out is not None:
            r.update(output=out, output_sha256=hashchain.content_hash(out),
                     output_bytes=len(out), output_truncated=False, fullscreen=False)
        return journal.append(r)

    def fish_history(self, entries):
        p = Path(os.environ["XDG_DATA_HOME"]) / "fish" / "fish_history"
        p.parent.mkdir(parents=True)
        p.write_text("".join(f"- cmd: {c}\n  when: {t}\n" for t, c in entries))


class SplitterTest(unittest.TestCase):
    def test_markers_split_across_reads(self):
        got = []
        sp = ptywrap.StreamSplitter(1 << 16, got.append)
        stream = b"prompt$ \x1b]7777;tp;S;abc1\x07hello \x1b[31mred\x1b[0m\r\nline2\n\x1b]7777;tp;E;abc1\x07next$ "
        shown = b"".join(sp.feed(stream[i:i + 3]) for i in range(0, len(stream), 3)) + sp.flush()
        self.assertNotIn(b"7777", shown)
        self.assertEqual(shown, b"prompt$ hello \x1b[31mred\x1b[0m\r\nline2\nnext$ ")
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].result([])["output"], "hello red\nline2")

    def test_foreign_osc_passes_through(self):
        sp = ptywrap.StreamSplitter(1024, lambda c: None)
        data = b"\x1b]0;title\x07\x1b]7777;other\x07x"
        self.assertEqual(sp.feed(data) + sp.flush(), data)

    def test_cap_keeps_head_and_tail(self):
        got = []
        sp = ptywrap.StreamSplitter(2048, got.append)
        sp.feed(b"\x1b]7777;tp;S;q\x07" + b"".join(b"line %05d\n" % i for i in range(5000))
                + b"\x1b]7777;tp;E;q\x07")
        r = got[0].result([])
        self.assertTrue(r["output_truncated"])
        self.assertIn("line 00000", r["output"])
        self.assertIn("line 04999", r["output"])
        self.assertIn("bytes omitted", r["output"])

    def test_fullscreen_not_recorded(self):
        got = []
        sp = ptywrap.StreamSplitter(4096, got.append)
        sp.feed(b"\x1b]7777;tp;S;v\x07\x1b[?1049hSECRET SCREEN\x1b[?1049l\x1b]7777;tp;E;v\x07")
        r = got[0].result([])
        self.assertTrue(r["fullscreen"])
        self.assertNotIn("SECRET", r["output"])

    def test_discard_marker(self):
        got = []
        sp = ptywrap.StreamSplitter(4096, got.append)
        sp.feed(b"\x1b]7777;tp;S;d\x07secret\x1b]7777;tp;X;d\x07")
        self.assertEqual(got, [])


class RedactTest(unittest.TestCase):
    def test_output_secrets(self):
        t = ("password: hunter22\nAuthorization: Bearer abcdefghijkl123\n"
             "key Zx9Qp2LmT7vKw3RbN8yHc5DfJ1sGa6Ue\n"
             "sha 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08\n"
             "/home/zephyrus/.tp/sessions/20261012-054356-0d57/meta.json")
        r = capture.redact_output(t)
        for s in ("hunter22", "abcdefghijkl123", "Zx9Qp2LmT7vKw3RbN8yHc5DfJ1sGa6Ue"):
            self.assertNotIn(s, r)
        self.assertIn("9f86d081884c7d659a2feaa0c55ad015", r)  # hashes are kept
        self.assertIn("20261012-054356-0d57/meta.json", r)    # paths are kept

    def test_clean_progress_bar(self):
        self.assertEqual(capture.clean_terminal("10%\r50%\r100%\r\ndone"), "100%\ndone")


class JournalTest(Home):
    def test_chain_and_tamper(self):
        for i in range(5):
            self.rec(f"echo {i}", 1_800_000_000 + i)
        self.assertTrue(journal.verify()[0])
        f = journal.day_files()[0]
        lines = f.read_text().splitlines()
        r = json.loads(lines[2]); r["command"] = "echo EDITED"
        lines[2] = json.dumps(r)
        f.write_text("\n".join(lines) + "\n")
        ok, msg, _ = journal.verify()
        self.assertFalse(ok)
        self.assertIn("jseq=3", msg)


class SessionTest(Home):
    def test_backfill_from_journal_and_history(self):
        now = int(time.time())
        self.fish_history([(now - 3000, "lsblk"), (now - 1800, "systemctl daemon-reload"),
                           (now - 1200, "echo journaled")])
        self.rec("echo journaled", now - 1200, out="journaled")
        meta = session.start("t", None, from_ts=now - 3600)
        self.rec("echo live", int(time.time()), out="live")
        # a command from before the window must not be included
        self.rec("echo too-early", now - 7200)
        session.mark("why", "because")
        meta = session.stop()
        o = meta["origins"]
        self.assertEqual(o, {"live": 1, "journal": 1, "history": 2})
        events, arts = session.load_finalized(meta["id"])
        self.assertTrue(hashchain.verify(events, arts, meta["digest"])[0])
        cmds = [e["command"] for e in events if e["type"] == "cmd"]
        self.assertEqual(cmds, ["lsblk", "systemctl daemon-reload", "echo journaled", "echo live"])
        self.assertGreater(meta["active_breakdown"]["estimated"], 0)

    def test_extend_then_reopen_and_reseal(self):
        now = int(time.time())
        self.rec("before", now - 600)
        meta = session.start("t", None)
        session.extend(now - 900)
        meta = session.stop()
        first = meta["digest"]
        self.assertEqual(meta["origins"]["journal"], 1)
        session.reopen(meta["id"])
        self.rec("after reopen", int(time.time()))
        meta = session.stop()
        self.assertNotEqual(meta["digest"], first)
        self.assertEqual(meta["prior_digests"][0]["digest"], first)
        meta["pushed_at"] = "x"; session.save_meta(meta["id"], meta)
        with self.assertRaises(session.SessionError):
            session.reopen(meta["id"])

    def test_output_tamper_detected_but_push_without_output_verifies(self):
        now = int(time.time())
        meta = session.start("t", None)
        self.rec("cat notes", int(time.time()), out="real output")
        meta = session.stop()
        events, arts = session.load_finalized(meta["id"])
        stripped = [{k: v for k, v in e.items() if k != "output"} for e in events]
        self.assertTrue(hashchain.verify(stripped, arts, meta["digest"])[0])
        events[0]["output"] = "fake output"
        self.assertFalse(hashchain.verify(events, arts, meta["digest"])[0])

    def test_v02_session_still_verifies(self):
        # exact shape of a v0.2-sealed event (no origin / output fields)
        ev = [{"ts": "2026-10-04T21:40:13+00:00", "type": "cmd", "cwd": "/home/zephyrus",
               "command": "echo PROBE123", "exit": 0, "duration": 0, "term": "f15024"}]
        hashchain.seal(ev)
        self.assertEqual(ev[0]["hash"],
                         "927a8d9ff1fe824a877ded753f1f38777ffc24964080384e3638ac7cfce55150")

    def test_legacy_raw_session_reopened_and_backfilled(self):
        # the 2026-10-12 situation: a v0.2 session with a raw log, stopped, not pushed
        now = int(time.time())
        meta = session.start("legacy", None)
        sd = store.session_dir(meta["id"])
        store.append_jsonl(sd / "events.raw.jsonl", {"ts": now, "type": "cmd", "cwd": "/",
                           "command": "ls", "exit": 0, "duration": 0, "term": "f9"})
        meta = session.stop()
        self.fish_history([(now - 1000, "sudo systemctl daemon-reload"), (now - 900, "tp start x")])
        session.reopen(meta["id"])
        session.extend(now - 3600)
        meta = session.stop()
        self.assertEqual(meta["origins"], {"live": 1, "journal": 0, "history": 1})


class SwitchTest(Home):
    def test_off_global_and_window(self):
        self.assertTrue(session.record_cmd("echo a", "/", 0, 0, "f1", shell_pid=os.getpid()))
        journal.set_off(True, os.getpid())
        self.assertFalse(session.record_cmd("echo b", "/", 0, 0, "f1", shell_pid=os.getpid()))
        self.assertTrue(session.record_cmd("echo c", "/", 0, 0, "f2", shell_pid=os.getppid()))
        journal.set_off(False, os.getpid())
        journal.set_off(True)
        self.assertFalse(session.record_cmd("echo d", "/", 0, 0, "f2", shell_pid=os.getppid()))
        journal.set_off(False)
        self.assertEqual([r["command"] for r in journal.records()], ["echo a", "echo c"])

    def test_control_commands_and_redaction(self):
        session.record_cmd("tp status", "/", 0, 0, "f1")
        session.record_cmd("curl -H 'Authorization: Bearer abcdefgh12345678' x", "/", 0, 0, "f1")
        cmds = [r["command"] for r in journal.records()]
        self.assertEqual(len(cmds), 1)
        self.assertNotIn("abcdefgh12345678", cmds[0])


class WhenTest(unittest.TestCase):
    def test_parse(self):
        import datetime as dt
        now = dt.datetime(2026, 10, 12, 22, 45).astimezone()
        self.assertEqual(history.parse_when("1h", now), int(now.timestamp()) - 3600)
        self.assertEqual(history.parse_when("21:40", now),
                         int(now.replace(hour=21, minute=40).timestamp()))
        # a clock time later than now means yesterday
        self.assertEqual(history.parse_when("23:30", now),
                         int((now.replace(hour=23, minute=30) - dt.timedelta(days=1)).timestamp()))


if __name__ == "__main__":
    unittest.main()
