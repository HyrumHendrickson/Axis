"""Regression tests for the safety and robustness fixes.

Each test names the problem it guards against. Everything runs against local fakes: no network and no
model are needed.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import collections
import io
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from contextlib import redirect_stderr
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from axis import agents, cli, engines, hub, images, instances, paths, runner, server, shortcut, tools, updates, web_tools  # noqa: E402
from test_axis import Base, OllamaHandler, request  # noqa: E402
from test_manage import serve  # noqa: E402

POSIX = os.name != "nt"


# ------------------------------------------------------------ deleting folders


class TestRemoveTree(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: (paths._unlock(str(self.tmp)), shutil.rmtree(self.tmp, ignore_errors=True)))

    def test_read_only_files_and_folders_are_deleted(self):
        # Deleting an agent failed on Linux and macOS when its folder (or a folder inside) was read-only,
        # because deleting a file needs write permission on the folder that holds it.
        top = self.tmp / "tree"
        (top / "a" / "b").mkdir(parents=True)
        for f in (top / "locked.txt", top / "a" / "locked.txt", top / "a" / "b" / "deep.txt"):
            f.write_text("x")
            os.chmod(f, 0o444)
        if POSIX:
            os.chmod(top / "a" / "b", 0o555)
            os.chmod(top / "a", 0o500)
            os.chmod(top, 0o555)
        paths.remove_tree(top)
        self.assertFalse(top.exists())

    @unittest.skipUnless(POSIX, "folder permissions work differently on Windows")
    def test_unlistable_folder_is_deleted(self):
        top = self.tmp / "tree"
        (top / "closed").mkdir(parents=True)
        (top / "closed" / "f.txt").write_text("x")
        os.chmod(top / "closed", 0o000)
        paths.remove_tree(top)
        self.assertFalse(top.exists())

    @unittest.skipUnless(POSIX, "needs symbolic links")
    def test_links_are_never_followed(self):
        # The delete used to chmod whatever a link pointed at, even a file outside the folder.
        outside = self.tmp / "outside.txt"
        outside.write_text("keep me")
        os.chmod(outside, 0o444)
        top = self.tmp / "tree"
        top.mkdir()
        os.symlink(outside, top / "link")
        paths.remove_tree(top)
        self.assertFalse(top.exists())
        self.assertEqual(outside.read_text(), "keep me")
        self.assertEqual(outside.stat().st_mode & 0o777, 0o444)


class TestDeleteRoute(Base):
    def test_deleting_a_read_only_folder_in_the_files_tab(self):
        url = self.axis_server(self.model_server(OllamaHandler, ["m"]))
        request(url + "/api/agents", "POST", {"name": "a"})
        sub = Path(self.home) / "agents" / "a" / "workspace" / "sub"
        sub.mkdir()
        f = sub / "locked.txt"
        f.write_text("x")
        os.chmod(f, 0o444)
        if POSIX:
            os.chmod(sub, 0o555)
        self.addCleanup(lambda: sub.exists() and paths._unlock(str(sub)))
        status, body = request(url + "/api/files/a?path=sub", "DELETE")
        self.assertEqual(status, 200, body)
        self.assertFalse(sub.exists())


# ------------------------------------------------------------ the web server


class TestServerSafety(Base):
    def setUp(self):
        super().setUp()
        self.url = self.axis_server(self.model_server(OllamaHandler, ["m"]))
        request(self.url + "/api/agents", "POST", {"name": "a"})
        self.ws = Path(self.home) / "agents" / "a" / "workspace"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def upload(self, name, data, path=""):
        req = urllib.request.Request(
            f"{self.url}/api/files/a/upload?path={urllib.parse.quote(path)}&name={urllib.parse.quote(name)}",
            data=data, method="POST", headers={"Content-Type": "application/octet-stream"})
        try:
            with self.opener.open(req, timeout=20) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_the_page_cannot_be_framed_by_another_site(self):
        # Clickjacking: another website could otherwise show Axis in an invisible frame and steer clicks.
        with self.opener.open(self.url + "/", timeout=10) as resp:
            self.assertEqual(resp.headers["X-Frame-Options"], "DENY")
            self.assertIn("frame-ancestors 'none'", resp.headers["Content-Security-Policy"])
            self.assertEqual(resp.headers["Referrer-Policy"], "no-referrer")

    def test_simultaneous_uploads_of_one_name_never_overwrite_each_other(self):
        results = []

        def go(i):
            results.append(self.upload("report.txt", f"upload {i}".encode()))

        threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual([s for s, _ in results], [201] * 8)
        names = {b["path"] for _, b in results}
        self.assertEqual(len(names), 8)
        contents = sorted((self.ws / n).read_text() for n in names)
        self.assertEqual(contents, sorted(f"upload {i}" for i in range(8)))
        self.assertEqual([p.name for p in self.ws.iterdir() if p.name.startswith(".axis-")], [])

    @unittest.skipUnless(POSIX, "needs symbolic links")
    def test_upload_skips_a_name_taken_by_a_dangling_link(self):
        os.symlink(self.ws / "nowhere", self.ws / "x.txt")
        status, body = self.upload("x.txt", b"data")
        self.assertEqual((status, body["path"]), (201, "x (2).txt"))
        self.assertFalse((self.ws / "nowhere").exists())

    def test_failed_upload_leaves_nothing_behind(self):
        # Sends fewer bytes than promised, then closes the connection.
        host, port = self.url.replace("http://", "").split(":")
        with socket.create_connection((host, int(port)), timeout=10) as s:
            s.sendall((f"POST /api/files/a/upload?path=&name=half.bin HTTP/1.1\r\nHost: {host}:{port}\r\n"
                       "Content-Length: 1000\r\n\r\n" + "x" * 10).encode())
            s.shutdown(socket.SHUT_WR)
            s.recv(4096)
        time.sleep(0.3)
        self.assertEqual(list(self.ws.iterdir()), [])

    def test_file_names_that_cannot_be_used(self):
        bad = server.bad_file_name
        for name in ("", ".", "..", "a/b", "a\\b", "a:b", "x\x01y", "a" * 300, ".axis-tmp"):
            self.assertIsNotNone(bad(name, windows=False), name)
        for name in ("nul", "NUL.txt", "con.md", "COM1", "lpt9.log", "notes.", "Aux.tar.gz"):
            self.assertIsNotNone(bad(name, windows=True), name)
        for name in ("notes.txt", "console.txt", "com10.txt", "report (2).pdf", "ünï.txt"):
            self.assertIsNone(bad(name, windows=True), name)
        self.assertEqual(self.upload("nul.txt", b"x")[0], 201 if POSIX else 400)

    def test_saving_a_text_file_ignores_a_link_planted_at_the_old_temporary_name(self):
        if not POSIX:
            self.skipTest("needs symbolic links")
        target = self.ws / "note.txt"
        target.write_text("old")
        victim = Path(self.home) / "victim.txt"
        victim.write_text("untouched")
        os.symlink(victim, self.ws / "note.txt.axis-save")   # the name the previous version used
        req = urllib.request.Request(self.url + "/api/files/a/save?path=note.txt", data=b"new", method="PUT")
        with self.opener.open(req, timeout=10) as resp:
            self.assertEqual(resp.status, 200)
        self.assertEqual(target.read_text(), "new")
        self.assertEqual(victim.read_text(), "untouched")

    def test_a_browser_that_leaves_mid_reply_is_not_an_error(self):
        # Writing an answer to a closed connection used to print a traceback for each such request.
        handler = server.Handler.__new__(server.Handler)
        handler.wfile = mock.Mock(write=mock.Mock(side_effect=BrokenPipeError()))
        handler.close_connection = False
        with mock.patch.object(server.Handler, "send_response"), mock.patch.object(server.Handler, "send_header"), \
                mock.patch.object(server.Handler, "end_headers"):
            handler._send(200, b"x", "text/plain")
        self.assertTrue(handler.close_connection)

        srv = server.make_server(0, None)
        self.addCleanup(srv.server_close)
        err = io.StringIO()
        with redirect_stderr(err):
            try:
                raise ConnectionResetError()
            except ConnectionResetError:
                srv.handle_error(None, ("127.0.0.1", 1))
        self.assertEqual(err.getvalue(), "")
        with redirect_stderr(err):
            try:
                raise ValueError("a real bug")
            except ValueError:
                srv.handle_error(None, ("127.0.0.1", 1))
        self.assertIn("a real bug", err.getvalue())


# ------------------------------------------------------------ web actions


class TestFetchPageSafety(unittest.TestCase):
    def test_addresses_that_are_not_the_public_internet(self):
        blocked = ["127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "224.0.0.1", "0.0.0.0",
                   "100.64.1.1",            # Tailscale and carrier-grade NAT: is_private is False for these
                   "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1"]
        for text in blocked:
            self.assertTrue(web_tools._blocked(ipaddress.ip_address(text)), text)
        for text in ("8.8.8.8", "93.184.216.34", "2606:4700:4700::1111", "::ffff:8.8.8.8"):
            self.assertFalse(web_tools._blocked(ipaddress.ip_address(text)), text)

    def test_shared_address_space_is_refused(self):
        with self.assertRaises(web_tools.WebError):
            web_tools.fetch_page({"url": "http://100.64.1.1/"})

    def test_a_site_that_changes_its_address_after_the_check_is_refused(self):
        # DNS rebinding: the name answers with a public address for the safety check, then with 127.0.0.1
        # for the connection. The address actually connected to must be checked too.
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", "6")
                self.end_headers()
                self.wfile.write(b"secret")

        srv, url = serve(H)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        port = srv.server_address[1]
        real, lookups = socket.getaddrinfo, []

        def fake(host, *args, **kwargs):
            if host != "rebind.test":
                return real(host, *args, **kwargs)
            lookups.append(host)
            ip = "93.184.216.34" if len(lookups) == 1 else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

        with mock.patch("socket.getaddrinfo", fake):
            with self.assertRaises(web_tools.WebError) as ctx:
                web_tools.fetch_page({"url": f"http://rebind.test:{port}/"})
        self.assertIn("local network", str(ctx.exception))
        self.assertGreaterEqual(len(lookups), 2)

    def test_a_site_that_trickles_its_answer_is_given_up_on(self):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", "1000")
                self.end_headers()
                try:
                    for _ in range(100):
                        self.wfile.write(b"x")
                        self.wfile.flush()
                        time.sleep(0.1)
                except OSError:
                    pass

        srv, url = serve(H)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        started = time.monotonic()
        with mock.patch.object(web_tools, "TOTAL_TIMEOUT", 0.5):
            with self.assertRaises(web_tools.WebError) as ctx:
                web_tools._http(url + "/")
        self.assertIn("too slowly", str(ctx.exception))
        self.assertLess(time.monotonic() - started, 5)


# ------------------------------------------------------------ Run Python


class TestRunPythonSafety(Base):
    def setUp(self):
        super().setUp()
        self.ws = Path(self.home) / "ws"
        self.ws.mkdir()

    def test_output_is_capped(self):
        with mock.patch.object(tools, "MAX_CAPTURE", 5000):
            result = tools.run_code(self.ws, "print('x' * 400000)\nimport sys\nsys.stderr.write('e' * 400000)")
        self.assertEqual(result["exit"], 0)
        for stream in ("stdout", "stderr"):
            self.assertLess(len(result[stream]), 5200, stream)
            self.assertIn("output cut off", result[stream])

    def test_normal_output_and_exit_codes_are_unchanged(self):
        result = tools.run_code(self.ws, "print('héllo')\nraise SystemExit(3)")
        self.assertEqual((result["exit"], result["stdout"], result["timed_out"]), (3, "héllo\n", False))

    def test_runs_at_the_same_time_keep_their_own_scratch_files(self):
        # All runs shared one .axis-tmp folder, and a run that finished deleted it under the others.
        slow = ("import os, time\np = os.path.join(os.environ['TMPDIR'], 'scratch.txt')\n"
                "open(p, 'w').write('kept')\ntime.sleep(2)\nprint(open(p).read())")
        out = {}
        t = threading.Thread(target=lambda: out.update(tools.run_code(self.ws, slow)))
        t.start()
        time.sleep(0.8)
        for _ in range(3):
            self.assertEqual(tools.run_code(self.ws, "print(1)")["stdout"], "1\n")
        t.join(60)
        self.assertEqual((out["exit"], out["stdout"].strip()), (0, "kept"), out.get("stderr"))
        self.assertFalse((self.ws / ".axis-tmp").exists())


# ------------------------------------------------------------ the agent loop


class TestRepeatedActions(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.ws, True)
        self.events = []
        self.session = runner.Session(self.ws, ["list_files", "read_file", "write_file", "fetch_page"], self.events.append)

    def run_tool(self, name, args):
        return self.session.run(name, args, f"c{len(self.events)}")

    def test_a_listing_is_not_stale_after_a_file_is_written(self):
        # The loop guard used to answer "you already did this" with the old, now wrong, listing.
        self.assertIn("is empty", self.run_tool("list_files", {}))
        self.run_tool("write_file", {"path": "a.txt", "content": "hi"})
        self.assertIn("a.txt", self.run_tool("list_files", {}))

    def test_a_file_is_not_stale_after_it_is_rewritten(self):
        self.run_tool("write_file", {"path": "a.txt", "content": "one"})
        self.assertEqual(self.run_tool("read_file", {"path": "a.txt"}), "one")
        self.run_tool("write_file", {"path": "a.txt", "content": "two"})
        self.assertEqual(self.run_tool("read_file", {"path": "a.txt"}), "two")

    def test_writing_an_earlier_text_again_runs(self):
        # write x, write y, write x: the third call is not "exactly the same as before" for the folder.
        for text in ("x", "y", "x"):
            self.run_tool("write_file", {"path": "a.txt", "content": text})
        self.assertEqual((self.ws / "a.txt").read_text(), "x")

    def test_real_repeats_are_still_refused(self):
        self.run_tool("list_files", {})
        again = self.run_tool("list_files", {})
        self.assertIn("already did exactly this", again)
        self.assertEqual(self.session.repeats, 1)


# ------------------------------------------------------------ installing engines


def _zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


class TestEngineUnpackSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_link_to_a_sibling_folder_with_the_same_prefix_is_refused(self):
        # "startswith" accepted /x/engine-evil as being inside /x/engine. Without tarfile's own filter
        # (older Pythons) this was the only check.
        archive = self.tmp / "evil.tar.gz"
        with tarfile.open(archive, "w:gz") as t:
            info = tarfile.TarInfo("link")
            info.type, info.linkname = tarfile.SYMTYPE, "../engine-evil/target"
            t.addfile(info)
        dest = self.tmp / "engine"
        with mock.patch.dict(tarfile.__dict__):
            tarfile.__dict__.pop("data_filter", None)
            with self.assertRaises(engines.EngineError) as ctx:
                engines._unpack(archive, dest)
        self.assertIn("points outside", str(ctx.exception))
        self.assertFalse((dest / "link").exists() or (dest / "link").is_symlink())

    def test_links_inside_the_folder_are_accepted(self):
        top = self.tmp / "engine"
        top.mkdir()
        top = top.resolve()
        self.assertTrue(engines._within(top, top / "lib" / "x"))
        self.assertTrue(engines._within(top, top))
        self.assertFalse(engines._within(top, Path(str(top) + "-evil") / "x"))
        self.assertFalse(engines._within(top, top / ".." / "other"))

    def test_unpacking_checks_free_disk_space_first(self):
        archive = self.tmp / "big.zip"
        archive.write_bytes(_zip_bytes({"a.bin": b"x" * 1000}))
        usage = collections.namedtuple("usage", "total used free")
        with mock.patch.object(engines.shutil, "disk_usage", return_value=usage(10, 10, 1000)):
            with self.assertRaises(engines.EngineError) as ctx:
                engines._unpack(archive, self.tmp / "out")
        self.assertIn("Not enough disk space", str(ctx.exception))
        self.assertFalse((self.tmp / "out" / "a.bin").exists())

    def test_the_github_token_is_never_forwarded_by_a_redirect(self):
        seen = []
        env = {"GITHUB_TOKEN": "secret", "AXIS_GITHUB_API": "http://api.example.test"}
        with mock.patch.dict(os.environ, env), mock.patch("urllib.request.urlopen", side_effect=lambda r, timeout: seen.append(r)):
            engines._open("http://api.example.test/repos/x/y/releases")
            engines._open("http://downloads.example.test/file.zip")
        api, other = seen
        self.assertEqual(api.unredirected_hdrs.get("Authorization"), "Bearer secret")
        self.assertNotIn("Authorization", api.headers)   # headers (not "unredirected") are copied to redirects
        self.assertNotIn("Authorization", other.unredirected_hdrs)


# ------------------------------------------------------------ updates and downloads


class _Reply:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *a):
        return self.payload


class TestUpdateAddress(unittest.TestCase):
    def tree(self, sha):
        return {"sha": sha, "tree": [{"path": "axis/cli.py", "type": "blob", "sha": "a" * 40}]}

    def test_only_a_real_commit_id_is_put_into_a_download_address(self):
        with mock.patch("urllib.request.urlopen", return_value=_Reply(self.tree("../../evil/repo/zip/x"))):
            with self.assertRaises(ValueError):
                updates.fetch_tree()
        good = "0123456789abcdef0123456789abcdef01234567"
        with mock.patch("urllib.request.urlopen", return_value=_Reply(self.tree(good))):
            self.assertEqual(updates.fetch_tree()[0], good)


class TestHubRedirects(unittest.TestCase):
    def follow(self, start, new):
        req = urllib.request.Request(start, headers={"Authorization": "Bearer secret"})
        out = hub._Redirect().redirect_request(req, None, 302, "Found", {}, new)
        return out.get_header("Authorization")

    def test_the_token_stays_on_the_same_site_only(self):
        self.assertEqual(self.follow("https://huggingface.co/a", "https://huggingface.co/b"), "Bearer secret")
        self.assertIsNone(self.follow("https://huggingface.co/a", "https://cdn.example.test/b"))
        self.assertIsNone(self.follow("https://huggingface.co/a", "http://huggingface.co/b"))      # downgrade to plain http
        self.assertIsNone(self.follow("https://huggingface.co/a", "https://huggingface.co:8443/b"))


# ------------------------------------------------------------ stopping Axis


class TestQuitDoesNotTrustAnAnswer(Base):
    def test_a_process_id_claimed_by_whatever_answers_a_port_is_not_used(self):
        # `axis quit` ends a process that ignores the request to quit. The id must not come from the
        # answer of an unknown listener, or that listener could name any process of the user.
        class Liar(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = json.dumps({"app": "axis", "version": "0", "pid": 1}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv, _ = serve(Liar)
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        found = instances.find([srv.server_address[1]])
        self.assertEqual([(e["port"], e["pid"]) for e in found], [(srv.server_address[1], 0)])

        said, killed = [], []
        with mock.patch("axis.procs._kill_pid", side_effect=killed.append), mock.patch.object(instances, "ask_to_quit", return_value=False):
            instances.quit_all([srv.server_address[1]], timeout=0.5, say=said.append)
        self.assertEqual(killed, [])
        self.assertTrue(any("did not stop" in s for s in said), said)

    def test_a_recorded_copy_is_still_ended_when_it_will_not_quit(self):
        port = 59999
        record = instances._folder() / "4242.json"
        record.parent.mkdir(parents=True, exist_ok=True)
        record.write_text(json.dumps({"pid": 4242, "port": port}), encoding="utf-8")
        killed = []
        with mock.patch.object(instances, "ping", return_value={"app": "axis", "pid": 4242}), \
                mock.patch.object(instances, "ask_to_quit", return_value=True), \
                mock.patch("axis.procs._pid_alive", return_value=True), \
                mock.patch("axis.procs._kill_pid", side_effect=lambda pid, timeout=0: killed.append(pid)):
            instances.quit_all([], timeout=0.4, say=lambda *_: None)
        self.assertEqual(killed, [4242])


# ------------------------------------------------------------ small fixes


class TestSmallFixes(Base):
    def test_the_uninstall_helper_is_written_to_a_private_folder(self):
        # It used a fixed file name in the shared temp folder, which another program could plant or reuse.
        with mock.patch.object(cli, "installed_with_pipx", return_value=False), \
                mock.patch.object(cli, "is_windows", return_value=True), \
                mock.patch("subprocess.Popen") as popen, \
                mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(cli.main(["uninstall", "-y"]), 0)
        helper = Path(popen.call_args[0][0][1])
        self.addCleanup(shutil.rmtree, helper.parent, True)
        self.assertEqual(helper.name, "axis_uninstall_helper.py")
        self.assertTrue(helper.parent.name.startswith("axis-uninstall-"))
        self.assertNotEqual(helper.parent.resolve(), Path(tempfile.gettempdir()).resolve())
        self.assertTrue(helper.is_file())

    def test_powershell_with_no_output_is_not_a_crash(self):
        done = subprocess.CompletedProcess([], 0, stdout=None, stderr=None)
        with mock.patch.object(shortcut.shutil, "which", return_value="powershell"), mock.patch("subprocess.run", return_value=done):
            self.assertEqual(shortcut._powershell("x", {}), [])

    def test_the_picture_program_output_pipe_is_closed(self):
        # The pipe stayed open after every picture, holding handles that Windows will not let go of.
        started = []
        real = subprocess.Popen

        def spy(*args, **kwargs):
            proc = real(*args, **kwargs)
            started.append(proc)
            return proc

        with mock.patch.object(images.subprocess, "Popen", side_effect=spy):
            lines = images._run([sys.executable, "-c", "print('hello')"], None, 1, lambda event: None)
        self.assertEqual(lines, ["hello"])
        self.assertTrue(started[0].stdout.closed)


if __name__ == "__main__":
    unittest.main()
