"""One purpose test per tp command.

Each test drives the real CLI (`tp.cli.main([...])`) against a throwaway
TP_HOME, exactly as you would from a terminal, and checks the thing the
command exists to do - not just that it ran.

    python3 -m unittest discover -s tests -v          # everything
    python3 -m unittest tests.test_commands.Dispose   # one command

Stdlib only; nothing here touches your real ~/.tp.
"""
import contextlib
import http.server
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tp import __version__, cli, hashchain, journal, store  # noqa: E402


class TP(unittest.TestCase):
    """A clean TP_HOME per test, and a helper that runs `tp ...`."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tp-test-")
        self.env = mock.patch.dict(os.environ, {
            "TP_HOME": os.path.join(self.tmp, "tp"),
            "XDG_DATA_HOME": os.path.join(self.tmp, "xdg"),
            "HOME": os.path.join(self.tmp, "home"),
        })
        self.env.start()
        os.makedirs(os.environ["HOME"])
        os.environ.pop("TP_PTY", None)
        self.tp("config", "capture_ports", "false")
        self.tp("config", "capture_gitdiff", "false")

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def tp(self, *args, stdin=None):
        """Run `tp args...`; return (exit_code, stdout, stderr)."""
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch("sys.stdin", io.StringIO(stdin or "")):
            try:
                code = cli.main([str(a) for a in args]) or 0
            except SystemExit as e:
                if isinstance(e.code, str):
                    err.write(e.code)
                    code = 1
                else:
                    code = e.code or 0
        return code, out.getvalue(), err.getvalue()

    def ok(self, *args, **kw):
        code, out, err = self.tp(*args, **kw)
        self.assertEqual(code, 0, f"tp {' '.join(map(str, args))} failed:\n{out}\n{err}")
        return out

    def fails(self, *args, **kw):
        code, out, err = self.tp(*args, **kw)
        self.assertNotEqual(code, 0, f"tp {' '.join(map(str, args))} should have failed:\n{out}")
        return out + err

    def run_cmd(self, cmd, start=None, exit=0, term="f1", output=None):
        """What the shell hook does after each command."""
        args = ["_record", "--cmd", cmd, "--cwd", self.tmp, "--exit", exit, "--term", term]
        if start:
            args += ["--start", int(start)]
        if output is not None:  # simulate tp pty having captured the output
            cid = f"t{time.time_ns()}"
            d = Path(os.environ["TP_HOME"]) / "spool"
            d.mkdir(parents=True, exist_ok=True)
            t0 = start or time.time()
            (d / f"{cid}.json").write_text(json.dumps({
                "id": cid, "ts_start": t0, "ts_end": t0 + 0.2, "output": output,
                "output_bytes": len(output), "output_truncated": False, "fullscreen": False}))
            args += ["--id", cid]
        self.ok(*args)

    def sid(self):
        """The most recently created session (ids made in the same second sort randomly)."""
        return max(store.list_sessions(),
                   key=lambda i: json.loads((store.session_dir(i) / "meta.json").read_text())["created_at"])

    def events(self, sid):
        return store.read_json(store.session_dir(sid) / "events.jsonl", default=[])

    def cmds(self, sid):
        return [e["command"] for e in self.events(sid) if e["type"] == "cmd"]

    def fish_history(self, entries):
        p = Path(os.environ["XDG_DATA_HOME"]) / "fish" / "fish_history"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(f"- cmd: {c}\n  when: {int(t)}\n" for t, c in entries))


class FakeRevVision:
    """Stands in for the ingest route: records POSTs and DELETEs."""

    def __init__(self, token="t0k"):
        self.posts, self.deletes, self.token = [], [], token
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def _auth(self):
                if self.headers.get("Authorization") != f"Bearer {outer.token}":
                    self.send_response(401); self.end_headers(); return False
                return True

            def do_POST(self):
                if not self._auth():
                    return
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.posts.append(body)
                self.send_response(200); self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "url": f"/{body['bucket']}/{body['id']}"}).encode())

            def do_DELETE(self):
                if not self._auth():
                    return
                outer.deletes.append(self.path)
                self.send_response(200); self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *a):
                pass

        self.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()


# ---- session lifecycle -----------------------------------------------------------

class Start(TP):
    def test_starts_a_recording_session(self):
        """start: creates an active session with title + project, and refuses a second one."""
        out = self.ok("start", "first", "real", "session", "-p", "CyberDen")
        self.assertIn("● recording", out)
        meta = json.loads((store.session_dir(self.sid()) / "meta.json").read_text())
        self.assertEqual((meta["title"], meta["project"], meta["status"]),
                         ("first real session", "CyberDen", "recording"))
        self.assertIn("already active", self.fails("start", "another"))

    def test_needs_a_title(self):
        """start: no title and no terminal to ask on -> refuses instead of making 'untitled'."""
        self.assertIn("title", self.fails("start"))

    def test_from_backfills_earlier_work(self):
        """start --from: work done BEFORE `tp start` ends up in the session, in time order, with output."""
        now = time.time()
        self.run_cmd("lsblk", start=now - 1500, output="sda 465G")
        self.run_cmd("sudo systemctl daemon-reload", start=now - 900)
        self.run_cmd("echo way-too-early", start=now - 7200)
        out = self.ok("start", "ssd mount", "--from", "30m")
        self.assertIn("2 from journal", out)
        self.run_cmd("findmnt /mnt/rev-ssd")
        self.ok("stop")
        sid = self.sid()
        self.assertEqual(self.cmds(sid), ["lsblk", "sudo systemctl daemon-reload", "findmnt /mnt/rev-ssd"])
        ev = self.events(sid)
        self.assertEqual([e["origin"] for e in ev], ["journal", "journal", "live"])
        self.assertEqual(ev[0]["output"], "sda 465G")

    def test_from_falls_back_to_fish_history(self):
        """start --from: time the journal never saw is filled from fish history, labelled 'history'."""
        now = time.time()
        self.fish_history([(now - 1200, "mount -a"), (now - 1100, "tp status")])
        self.ok("start", "x", "--from", "1h")
        self.ok("stop")
        ev = [e for e in self.events(self.sid()) if e["type"] == "cmd"]
        self.assertEqual([(e["command"], e["origin"]) for e in ev], [("mount -a", "history")])

    def test_no_history_flag(self):
        """start --no-history: fish history is not used."""
        self.fish_history([(time.time() - 600, "mount -a")])
        self.ok("start", "x", "--from", "1h", "--no-history")
        self.ok("stop")
        self.assertEqual(self.cmds(self.sid()), [])


class Extend(TP):
    def test_widens_running_session(self):
        """extend: mid-session, pulls earlier journal work in, ahead of live work."""
        now = time.time()
        self.run_cmd("early", start=now - 2400)
        self.ok("start", "x")
        self.run_cmd("live")
        out = self.ok("extend", "--from", "1h")
        self.assertIn("1 from journal", out)
        self.ok("stop")
        self.assertEqual(self.cmds(self.sid()), ["early", "live"])

    def test_rejects_time_after_start(self):
        """extend: a --from later than the session start is an error, not a silent no-op."""
        self.ok("start", "x")
        self.assertIn("pick an earlier time", self.fails("extend", "--from", "0s"))

    def test_needs_active_session(self):
        """extend: with nothing recording it says how to get one."""
        self.assertIn("no active session", self.fails("extend", "--from", "1h"))


class Reopen(TP):
    def test_reopen_reseal_keeps_old_digest(self):
        """reopen: unseals a stopped session; the new seal differs and the old digest is kept."""
        self.ok("start", "x"); self.run_cmd("one"); self.ok("stop")
        sid = self.sid()
        first = json.loads((store.session_dir(sid) / "meta.json").read_text())["digest"]
        self.assertIn("reopened", self.ok("reopen", sid))
        self.run_cmd("two"); self.ok("stop")
        meta = json.loads((store.session_dir(sid) / "meta.json").read_text())
        self.assertNotEqual(meta["digest"], first)
        self.assertEqual(meta["prior_digests"][0]["digest"], first)
        self.assertTrue((store.session_dir(sid) / "events.sealed-1.json").exists())
        self.assertIn("✓ verified", self.ok("verify", sid))

    def test_refuses_pushed_unless_forced(self):
        """reopen: a pushed session is a published record - needs --force."""
        self.ok("start", "x"); self.ok("stop")
        sid = self.sid()
        meta = json.loads((store.session_dir(sid) / "meta.json").read_text())
        meta["pushed_at"] = "2026-10-13T00:00:00+00:00"
        (store.session_dir(sid) / "meta.json").write_text(json.dumps(meta))
        self.assertIn("--force", self.fails("reopen", sid))
        self.ok("reopen", sid, "--force")

    def test_refuses_while_another_is_active(self):
        """reopen: only one session records at a time."""
        self.ok("start", "a"); self.ok("stop"); old = self.sid()
        self.ok("start", "b")
        self.assertIn("stop it first", self.fails("reopen", old))


class PauseResume(TP):
    def test_pause_excludes_work_and_stops_clock(self):
        """pause/resume: commands while paused are not in the session; resume brings capture back."""
        self.ok("start", "x")
        self.run_cmd("before")
        self.assertIn("paused", self.ok("pause"))
        time.sleep(1.1)
        self.run_cmd("while-paused")
        time.sleep(1.1)
        self.assertIn("recording", self.ok("resume"))
        self.run_cmd("after")
        self.ok("stop")
        self.assertEqual(self.cmds(self.sid()), ["before", "after"])

    def test_double_pause_and_resume_are_errors(self):
        """pause/resume: pausing twice or resuming while recording is reported."""
        self.ok("start", "x")
        self.fails("resume")
        self.ok("pause")
        self.fails("pause")


class Status(TP):
    def test_idle_and_active(self):
        """status: says idle, then shows the session with command counts by origin."""
        self.assertIn("idle", self.ok("status"))
        self.run_cmd("early", start=time.time() - 300)
        self.ok("start", "x", "--from", "10m")
        self.run_cmd("now", output="hi")
        out = self.ok("status")
        self.assertIn("● recording", out)
        self.assertIn("2 commands (1 with output)", out)
        self.assertIn("1 live", out)
        self.assertIn("1 backfilled from journal", out)

    def test_reports_capture_off(self):
        """status: makes it obvious when capture is switched off."""
        self.ok("off")
        self.assertIn("capture OFF", self.ok("status"))


class Stop(TP):
    def test_seals_and_reports(self):
        """stop: seals the timeline (verifiable digest), sets the bucket, clears the active session."""
        self.ok("start", "x"); self.run_cmd("echo hi")
        out = self.ok("stop", "--to", "writeups")
        self.assertIn("■ stopped", out)
        self.assertIn("digest", out)
        meta = json.loads((store.session_dir(self.sid()) / "meta.json").read_text())
        self.assertEqual((meta["status"], meta["bucket"]), ("stopped", "writeups"))
        self.assertIsNone(store.active_id())
        self.assertIn("✓ verified", self.ok("verify", self.sid()))

    def test_estimates_backfilled_time_separately(self):
        """stop: active time for backfill is estimated and shown apart from the live clock."""
        now = time.time()
        self.run_cmd("a", start=now - 600)
        self.run_cmd("b", start=now - 500)
        self.ok("start", "x", "--from", "15m")
        out = self.ok("stop")
        self.assertIn("estimated for backfill", out)
        meta = json.loads((store.session_dir(self.sid()) / "meta.json").read_text())
        self.assertGreater(meta["active_breakdown"]["estimated"], 0)

    def test_unknown_bucket(self):
        """stop: a typo'd bucket is rejected before anything is sealed."""
        self.ok("start", "x")
        self.assertIn("unknown bucket", self.fails("stop", "--to", "blog"))
        self.assertIsNotNone(store.active_id())


class Notes(TP):
    def test_why_note_milestone_open_land_in_timeline(self):
        """why / note / milestone / open: reasoning is timestamped into the sealed timeline."""
        self.ok("start", "x")
        self.ok("why", "ingress 404 was a missing host rule")
        self.ok("note", "cert expires in 30d")
        self.ok("milestone", "serving on :3000")
        self.ok("open", "http://localhost:3000", "dev", "server")
        self.ok("stop")
        got = [(e["type"], e["text"]) for e in self.events(self.sid()) if e["type"] != "cmd"]
        self.assertEqual(got, [("why", "ingress 404 was a missing host rule"),
                               ("note", "cert expires in 30d"),
                               ("milestone", "serving on :3000"),
                               ("open", "http://localhost:3000  dev server")])

    def test_notes_are_redacted(self):
        """note: secrets typed into a note are redacted like commands."""
        self.ok("start", "x")
        self.ok("note", "used", "token=supersecret123")
        self.ok("stop")
        text = [e["text"] for e in self.events(self.sid()) if e["type"] == "note"][0]
        self.assertNotIn("supersecret123", text)

    def test_need_a_session(self):
        """why: without an active session there is nowhere to put it."""
        self.fails("why", "x")


# ---- looking at recordings ----------------------------------------------------------

class Ls(TP):
    def test_lists_sessions_newest_first(self):
        """ls: lists sessions with the active one marked."""
        self.ok("start", "alpha"); self.ok("stop")
        time.sleep(1.1)
        self.ok("start", "beta")
        out = self.ok("ls").splitlines()
        self.assertIn("beta", out[0]); self.assertIn("●", out[0])
        self.assertIn("alpha", out[1])

    def test_empty(self):
        """ls: says so when there is nothing."""
        self.assertIn("no sessions", self.ok("ls"))


class Show(TP):
    def test_timeline_with_output_and_origin_marks(self):
        """show: prints the timeline; --output adds what each command printed; origins are marked."""
        self.run_cmd("early", start=time.time() - 300)
        self.ok("start", "x", "--from", "10m")
        self.run_cmd("ls /etc", output="fstab\nhosts")
        self.run_cmd("false", exit=1)
        self.ok("stop")
        out = self.ok("show", self.sid())
        self.assertIn("ls /etc", out)
        self.assertIn("e1", out)
        self.assertIn("↶", out)
        self.assertNotIn("fstab", out)
        self.assertIn("fstab", self.ok("show", self.sid(), "--output"))

    def test_unknown_id(self):
        """show: a wrong id is an error."""
        self.fails("show", "20990101-000000-dead")


class Verify(TP):
    def setUp(self):
        super().setUp()
        self.ok("start", "x")
        self.run_cmd("cat notes.txt", output="real output")
        self.run_cmd("echo two")
        self.ok("stop")
        self.path = store.session_dir(self.sid()) / "events.jsonl"

    def edit(self, fn):
        ev = json.loads(self.path.read_text())
        fn(ev)
        self.path.write_text(json.dumps(ev))

    def test_passes_untouched(self):
        """verify: an untouched session verifies, and its commands match the journal."""
        out = self.ok("verify", self.sid())
        self.assertIn("✓ verified", out)
        self.assertIn("match their journal records", out)

    def test_catches_edited_command(self):
        """verify: editing a sealed command afterwards is detected."""
        self.edit(lambda ev: ev[1].__setitem__("command", "echo innocent"))
        self.assertIn("FAILED", self.fails("verify", self.sid()))

    def test_catches_edited_output(self):
        """verify: editing a command's recorded output afterwards is detected."""
        self.edit(lambda ev: ev[0].__setitem__("output", "doctored output"))
        self.assertIn("output does not match", self.fails("verify", self.sid()))

    def test_catches_deleted_event(self):
        """verify: removing an event from the middle of the timeline is detected."""
        self.edit(lambda ev: ev.pop(0))
        self.fails("verify", self.sid())

    def test_needs_a_sealed_session(self):
        """verify: a running session has nothing sealed to check yet."""
        self.ok("start", "y")
        self.assertIn("not finalized", self.fails("verify", self.sid()))


# ---- getting it to Rev Vision ---------------------------------------------------------

class PushRoute(TP):
    def setUp(self):
        super().setUp()
        self.rv = FakeRevVision()
        self.ok("config", "api_url", self.rv.url)
        self.ok("start", "x")
        self.run_cmd("cat .env", output="API_URL=http://x")
        self.ok("stop")

    def tearDown(self):
        self.rv.close()
        super().tearDown()

    def test_push_leaves_output_local_by_default(self):
        """push: sends the sealed session; output stays on this machine; the chain still verifies."""
        self.ok("config", "token", "t0k")
        self.assertIn("↑ pushed", self.ok("push", self.sid(), "--to", "writeups"))
        body = self.rv.posts[-1]
        self.assertEqual(body["bucket"], "writeups")
        self.assertFalse(any("output" in e for e in body["events"]))
        self.assertTrue(all("output_sha256" in e for e in body["events"] if e["type"] == "cmd"))
        from tp import session
        ok, _ = hashchain.verify(body["events"], session.artifact_hashes(self.sid()), body["digest"])
        self.assertTrue(ok)
        meta = json.loads((store.session_dir(self.sid()) / "meta.json").read_text())
        self.assertIn("pushed_at", meta)

    def test_push_with_output(self):
        """push --with-output: includes what commands printed."""
        self.ok("config", "token", "t0k")
        self.ok("push", self.sid(), "--with-output")
        self.assertEqual(self.rv.posts[-1]["events"][0]["output"], "API_URL=http://x")

    def test_push_without_token_or_bad_token(self):
        """push: no token -> tells you how to set it; wrong token -> server's 401 is reported."""
        self.assertIn("tp config token", self.fails("push", self.sid()))
        self.ok("config", "token", "wrong")
        self.assertIn("401", self.fails("push", self.sid()))

    def test_route_sets_bucket_and_can_push(self):
        """route: re-files a session into another bucket, optionally pushing it."""
        self.ok("config", "token", "t0k")
        self.ok("route", self.sid(), "--to", "p", "--push")
        meta = json.loads((store.session_dir(self.sid()) / "meta.json").read_text())
        self.assertEqual(meta["bucket"], "projects")
        self.assertEqual(self.rv.posts[-1]["bucket"], "projects")


# ---- storage: archive and disposal ---------------------------------------------------

class Archive(TP):
    def make(self, title="x"):
        self.ok("start", title); self.run_cmd("echo hi", output="hi"); self.ok("stop")
        return self.sid()

    def test_default_path_and_moves_verified_copy(self):
        """archive: moves a sealed session to ~/.tp/archive by default; it still shows and verifies."""
        sid = self.make()
        self.assertTrue(self.ok("archive", "--where").split()[0].endswith("/tp/archive"))
        self.assertIn("archived", self.ok("archive", sid))
        self.assertFalse((Path(os.environ["TP_HOME"]) / "sessions" / sid).exists())
        self.assertTrue((Path(os.environ["TP_HOME"]) / "archive" / sid / "events.jsonl").exists())
        self.assertIn("✓ verified", self.ok("verify", sid))
        self.assertIn("echo hi", self.ok("show", sid))
        self.assertIn(sid, self.ok("ls", "--archived"))
        self.assertNotIn(sid, self.ok("ls"))
        idx = (Path(os.environ["TP_HOME"]) / "archive" / "index.jsonl").read_text()
        self.assertIn(sid, idx)

    def test_custom_archive_path(self):
        """archive: honours `tp config archive_path`."""
        target = Path(self.tmp) / "cold-storage"
        target.mkdir()
        self.ok("config", "archive_path", str(target))
        sid = self.make()
        self.ok("archive", sid)
        self.assertTrue((target / sid / "meta.json").exists())

    def test_refuses_unmounted_drive(self):
        """archive: an archive path on /mnt/<drive> that isn't mounted is refused (no filling the root disk)."""
        self.ok("config", "archive_path", "/mnt/tp-test-not-a-real-drive/tp-archive")
        sid = self.make()
        self.assertIn("isn't mounted", self.fails("archive", sid))
        self.assertTrue((Path(os.environ["TP_HOME"]) / "sessions" / sid).exists())

    def test_refuses_active_tampered_or_unknown(self):
        """archive: won't archive the running session, a damaged one, or an unknown id."""
        sid = self.make()
        p = store.session_dir(sid) / "events.jsonl"
        ev = json.loads(p.read_text()); ev[0]["command"] = "edited"; p.write_text(json.dumps(ev))
        self.assertIn("fails tp verify", self.fails("archive", sid))
        self.ok("start", "running")
        self.assertIn("stop it first", self.fails("archive", store.active_id()))
        self.fails("archive", "20990101-000000-dead")

    def test_bulk_filters(self):
        """archive --pushed / --older-than: picks sessions by filter; --dry-run moves nothing."""
        a = self.make("a")
        time.sleep(1.1)
        b = self.make("b")
        meta = json.loads((store.session_dir(a) / "meta.json").read_text())
        meta["pushed_at"] = "2026-10-13T00:00:00+00:00"
        (store.session_dir(a) / "meta.json").write_text(json.dumps(meta))
        out = self.ok("archive", "--pushed", "--dry-run")
        self.assertIn(a, out); self.assertNotIn(b, out)
        self.assertFalse(store.is_archived(a))
        self.ok("archive", "--pushed")
        self.assertTrue(store.is_archived(a)); self.assertFalse(store.is_archived(b))
        self.ok("archive", "--older-than", "0s")
        self.assertTrue(store.is_archived(b))

    def test_unarchive_and_reopen_rule(self):
        """unarchive: brings a session back; reopen refuses while it is archived."""
        sid = self.make()
        self.ok("archive", sid)
        self.assertIn("unarchive", self.fails("reopen", sid))
        self.ok("unarchive", sid)
        self.assertFalse(store.is_archived(sid))
        self.ok("reopen", sid)


class Dispose(TP):
    def make(self):
        self.ok("start", "secret thing")
        self.run_cmd("cat ~/.ssh/config", output="Host lab")
        self.ok("stop")
        return self.sid()

    def test_asks_for_the_id(self):
        """dispose: deletes only after you type the session id; a wrong answer deletes nothing."""
        sid = self.make()
        # no terminal and no --yes -> refuses
        self.assertIn("--yes", self.fails("dispose", sid))
        code, out, err = self._dispose_interactive(sid, "wrong-id\n")
        self.assertNotEqual(code, 0)
        self.assertTrue(store.session_dir(sid).exists())
        code, out, err = self._dispose_interactive(sid, sid + "\n")
        self.assertEqual(code, 0, err)
        self.assertFalse(store.session_dir(sid).exists())

    def _dispose_interactive(self, sid, typed):
        out, err = io.StringIO(), io.StringIO()
        class Tty(io.StringIO):
            def isatty(self):
                return True
        fake_in = Tty(typed)
        code = 0
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch("sys.stdin", fake_in), mock.patch("builtins.input",
                                                              lambda prompt="": fake_in.readline().rstrip("\n")):
            try:
                code = cli.main(["dispose", sid]) or 0
            except SystemExit as e:
                code = 1 if isinstance(e.code, str) else (e.code or 0)
        return code, out.getvalue(), err.getvalue()

    def test_logs_without_content(self):
        """dispose: the session is gone, and disposed.jsonl records id/title/digest but no content."""
        sid = self.make()
        self.ok("dispose", sid, "--yes")
        self.assertNotIn(sid, store.list_sessions())
        log = (Path(os.environ["TP_HOME"]) / "disposed.jsonl").read_text()
        self.assertIn(sid, log)
        self.assertIn("secret thing", log)
        self.assertNotIn("Host lab", log)

    def test_with_journal_removes_content_but_chain_stays_checkable(self):
        """dispose --journal: the session's journal records lose their content; journal verify still passes and counts them."""
        self.run_cmd("outside", start=time.time() - 3600)
        sid = self.make()
        self.ok("dispose", sid, "--journal", "--yes")
        raw = "".join(p.read_text() for p in journal.day_files())
        self.assertNotIn("Host lab", raw)
        self.assertNotIn(".ssh/config", raw)
        self.assertIn("outside", raw)
        out = self.ok("journal", "verify")
        self.assertIn("1 records disposed on purpose", out)

    def test_journal_range_without_session(self):
        """dispose --journal-from/--until: wipes a time range of the journal (e.g. you typed a secret)."""
        now = time.time()
        self.run_cmd("keep-before", start=now - 3000)
        self.run_cmd("oops-secret", start=now - 1800)
        self.run_cmd("keep-after", start=now - 600)
        self.ok("dispose", "--journal-from", "35m", "--until", "20m", "--yes")
        self.assertEqual([r["command"] for r in journal.records()], ["keep-before", "keep-after"])
        self.assertIn("1 records disposed", self.ok("journal", "verify"))

    def test_archived_and_active(self):
        """dispose: works on archived sessions, and on the running one (clears it)."""
        sid = self.make()
        self.ok("archive", sid)
        self.ok("dispose", sid, "--yes")
        self.assertFalse((Path(os.environ["TP_HOME"]) / "archive" / sid).exists())
        self.ok("start", "running")
        self.ok("dispose", store.active_id(), "--yes")
        self.assertIsNone(store.active_id())

    def test_remote(self):
        """dispose --remote: deletes it from Rev Vision first; if that fails, nothing is deleted locally."""
        rv = FakeRevVision()
        try:
            sid = self.make()
            self.ok("config", "api_url", rv.url)
            self.ok("config", "token", "wrong")
            self.assertIn("nothing deleted locally", self.fails("dispose", sid, "--remote", "--yes"))
            self.assertTrue(store.session_dir(sid).exists())
            self.ok("config", "token", "t0k")
            self.ok("dispose", sid, "--remote", "--yes")
            self.assertIn(f"id={sid}", rv.deletes[-1])
            self.assertFalse(store.session_dir(sid).exists())
        finally:
            rv.close()

    def test_warns_when_pushed_but_not_remote(self):
        """dispose: says plainly when a pushed copy will remain on Rev Vision."""
        sid = self.make()
        meta = json.loads((store.session_dir(sid) / "meta.json").read_text())
        meta["pushed_at"] = "2026-10-13T00:00:00+00:00"
        (store.session_dir(sid) / "meta.json").write_text(json.dumps(meta))
        self.assertIn("stays there", self.ok("dispose", sid, "--yes"))


# ---- the always-on journal and switches -------------------------------------------------

class Record(TP):
    def test_hook_entry_point(self):
        """_record: journals a command with cwd/exit/term, redacted, even with no session running."""
        self.run_cmd("curl -H 'Authorization: Bearer abcdef1234567890' x", exit=7, term="f42")
        r = journal.records()[-1]
        self.assertEqual((r["exit"], r["term"], r["cwd"]), (7, "f42", self.tmp))
        self.assertNotIn("abcdef1234567890", r["command"])

    def test_skips_tp_and_blank(self):
        """_record: tp's own commands and blank lines are never journaled."""
        self.run_cmd("tp status")
        self.run_cmd("   ")
        self.assertEqual(journal.records(), [])

    def test_attaches_pty_output(self):
        """_record --id: picks up the output tp pty captured and removes the spool file."""
        self.run_cmd("ls", output="a\nb")
        r = journal.records()[-1]
        self.assertEqual(r["output"], "a\nb")
        self.assertEqual(r["output_sha256"], hashchain.content_hash("a\nb"))
        self.assertEqual(list((Path(os.environ["TP_HOME"]) / "spool").glob("*.json")), [])


class OffOn(TP):
    def test_global_switch(self):
        """off/on: while off nothing is journaled, in any terminal."""
        self.ok("off")
        self.run_cmd("hidden")
        self.ok("on")
        self.run_cmd("visible")
        self.assertEqual([r["command"] for r in journal.records()], ["visible"])

    def test_window_switch(self):
        """off --window: only this terminal (this shell's pid) stops recording."""
        self.ok("off", "--window")                      # parent pid = this test process's parent
        self.ok("_record", "--cmd", "here", "--pid", os.getppid())
        self.ok("_record", "--cmd", "other-terminal", "--pid", os.getpid())
        self.ok("on", "--window")
        self.ok("_record", "--cmd", "here-again", "--pid", os.getppid())
        self.assertEqual([r["command"] for r in journal.records()], ["other-terminal", "here-again"])


class Journal(TP):
    def test_show_and_since(self):
        """journal: lists recent records with their first output line; --since narrows it."""
        self.run_cmd("old", start=time.time() - 7200)
        self.run_cmd("new", output="result line")
        out = self.ok("journal")
        self.assertIn("old", out); self.assertIn("→ result line", out)
        self.assertNotIn("old", self.ok("journal", "--since", "1h"))

    def test_verify_catches_edit_and_removal(self):
        """journal verify: catches an edited record and a removed one."""
        for i in range(4):
            self.run_cmd(f"echo {i}")
        self.assertIn("intact", self.ok("journal", "verify"))
        f = journal.day_files()[0]
        lines = f.read_text().splitlines()
        f.write_text("\n".join(lines[:1] + lines[2:]) + "\n")
        self.assertIn("prev mismatch", self.fails("journal", "verify"))

    def test_prune_respects_retention(self):
        """journal prune: drops day files older than journal_days, never today's."""
        self.run_cmd("ancient", start=time.time() - 10 * 86400)
        self.run_cmd("today")
        self.ok("config", "journal_days", "7")
        self.ok("journal", "prune")
        self.assertEqual([r["command"] for r in journal.records()], ["today"])

    def test_clear_needs_yes(self):
        """journal clear: refuses without --yes, empties the journal with it."""
        self.run_cmd("x")
        self.fails("journal", "clear")
        self.ok("journal", "clear", "--yes")
        self.assertEqual(journal.records(), [])


# ---- setup and plumbing ----------------------------------------------------------------

class Config(TP):
    def test_get_set_and_types(self):
        """config: sets/gets keys with the right types; the token is never printed."""
        self.ok("config", "journal_days", "14")
        self.assertEqual(self.ok("config", "journal_days").strip(), "14")
        self.ok("config", "push_output", "yes")
        self.assertEqual(self.ok("config", "push_output").strip(), "True")
        self.ok("config", "token", "s3cr3t-token")
        listing = self.ok("config")
        self.assertNotIn("s3cr3t-token", listing)
        self.assertIn("token = (set)", listing)

    def test_redact_patterns_is_a_list(self):
        """config redact_patterns: stored as a list and actually applied to commands."""
        self.ok("config", "redact_patterns", '["corp_[A-Za-z0-9]{8}"]')
        self.run_cmd("login corp_ABCD1234 now")
        self.assertNotIn("corp_ABCD1234", journal.records()[-1]["command"])
        self.assertIn("login", journal.records()[-1]["command"])


class Hook(TP):
    def test_each_shell(self):
        """hook: prints a hook for fish/bash/zsh that records via `tp _record` and marks commands."""
        for sh in ("fish", "bash", "zsh"):
            src = self.ok("hook", sh)
            self.assertIn("tp _record", src, sh)
            self.assertIn("7777;tp;E", src, sh)

    def test_pty_block_follows_config(self):
        """hook: includes the tp pty wrapper only when pty is on and a tp executable exists."""
        with mock.patch("shutil.which", return_value="/usr/local/bin/tp"):
            self.assertIn("pty (status fish-path)", self.ok("hook", "fish"))
            self.ok("config", "pty", "off")
            self.assertNotIn("pty (status fish-path)", self.ok("hook", "fish"))
        self.ok("config", "pty", "on")
        with mock.patch("shutil.which", return_value=None):
            self.assertIn("no `tp` executable", self.ok("hook", "fish"))

    def test_rejects_unknown_shell(self):
        """hook: unknown shells are refused."""
        self.fails("hook", "tcsh")


class Doctor(TP):
    def test_reports_wiring(self):
        """doctor: reports the executable, pty status, hook files and the journal chain."""
        Path(os.environ["HOME"], ".config/fish").mkdir(parents=True)
        Path(os.environ["HOME"], ".config/fish/config.fish").write_text("tp hook fish | source\n")
        self.run_cmd("echo hi")
        out = self.ok("doctor")
        self.assertIn("shell hook in: fish", out)
        self.assertIn("journal chain intact", out)
        self.assertIn("inside tp pty: no", out)


class Version(TP):
    def test_version(self):
        """--version: prints the installed version."""
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stdout(io.StringIO()) as out:
                cli.main(["--version"])
        self.assertIn(__version__, out.getvalue())


class EveryCommandIsTested(unittest.TestCase):
    """Guard: adding a tp command without a purpose test fails the suite."""

    COVERED = {
        "start": Start, "extend": Extend, "reopen": Reopen, "pause": PauseResume,
        "resume": PauseResume, "status": Status, "stop": Stop, "note": Notes, "why": Notes,
        "milestone": Notes, "open": Notes, "ls": Ls, "show": Show, "verify": Verify,
        "route": PushRoute, "push": PushRoute, "archive": Archive, "unarchive": Archive,
        "dispose": Dispose, "_record": Record, "off": OffOn, "on": OffOn,
        "journal": Journal, "config": Config, "hook": Hook, "doctor": Doctor,
        "pty": "tests/test_terminals.py (Pty, Fish, Bash)",
    }

    def test_all_commands_have_tests(self):
        import argparse
        parser = cli.build_parser()
        sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        missing = sorted(set(sub.choices) - set(self.COVERED))
        self.assertEqual(missing, [], f"commands without a purpose test: {missing}")


if __name__ == "__main__":
    unittest.main()
