"""Tests for Axis 4.3.12: OCR models on the OCR page, and sharing models between computers (Connect to a host,
Host models).

Everything runs against fakes in one process: a fake Ollama or llama.cpp as the host's engine, a small function in
place of the host's stable-diffusion.cpp, and a real sharing port on 127.0.0.1. The host and the computer that
connects to it share this process, so the tests tell them apart by what each side was asked.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from test_axis import Base, LlamaCppHandler, OllamaHandler, REQUESTS, free_port, request
from axis import backend, images, manager, netshare, tools

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "axis" / "web" / "index.html").read_text(encoding="utf-8")
PNG = b"\x89PNG\r\n\x1a\n" + b"fake picture"
HEAD = {"X-Axis-Share": "1"}


def share(port, route, body=None, headers=HEAD):
    """One request to a sharing port: (status, text)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{route}", data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=30) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def events(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def wait_for(test, check, seconds=8.0, what="the condition"):
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return
        time.sleep(0.05)
    test.fail(f"timed out waiting for {what}")


class SharingBase(Base):
    def setUp(self):
        super().setUp()
        netshare.shutdown()
        self.addCleanup(netshare.shutdown)

    def host(self, model_url, **kw):
        """Open the sharing port with the fake engine behind it; returns its port."""
        status = netshare.start_hosting(0, model_url, **kw)
        return status["port"]

    def connect(self, port):
        netshare.connect(f"127.0.0.1:{port}")
        return f"127.0.0.1:{port}"


# ------------------------------------------------------------------ version, docs and the page


class TestRelease(unittest.TestCase):
    def test_version_and_changelog(self):
        import axis
        self.assertGreaterEqual(tuple(int(x) for x in axis.__version__.split(".")), (4, 3, 12))
        self.assertEqual((ROOT / "axis" / "VERSION").read_text(encoding="utf-8").strip(), axis.__version__)
        self.assertIn("## [4.3.12]", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))

    def test_routes_are_documented(self):
        api = (ROOT / "docs" / "api.md").read_text(encoding="utf-8")
        for route in ("/api/net", "/api/net/host/start", "/api/net/host/stop", "/api/net/connect", "/api/net/disconnect"):
            self.assertIn(route, api)
        for variable in ("AXIS_SHARE_CHATS", "AXIS_SHARE_QUEUE"):
            self.assertIn(variable, (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8"))


class TestPage(unittest.TestCase):
    def test_script_parses(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is not installed")
        script = max(re.findall(r"<script[^>]*>(.*?)</script>", PAGE, re.S), key=len)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "page.js"
            path.write_text(script, encoding="utf-8")
            done = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_models_page_tabs(self):
        tabs = PAGE[PAGE.index("const MODEL_TABS"):PAGE.index("const OCR_ENGINES")]
        self.assertIn('["connect", "Connect to a host"]', tabs)
        self.assertIn('["host", "Host models"]', tabs)
        self.assertNotIn('"ocr"', tabs)
        models = PAGE[PAGE.index("function renderModels()"):PAGE.index("function engineOverview")]
        self.assertNotIn("ocrInstallPanel", models)
        self.assertNotIn("ocrModelsSection", models)
        self.assertIn("renderNetTab(tab, bar)", models)

    def test_ocr_models_are_on_the_ocr_page(self):
        ocr = PAGE[PAGE.index("function renderOcrModels("):PAGE.index("function drawOcr()")]
        for part in ("ocrInstallPanel(m)", "ocrModelsSection(m)", 'activity(m, "ocr")', "ocrTabs()"):
            self.assertIn(part, ocr)
        self.assertNotIn('focus: "ocr"', PAGE)
        self.assertEqual(len(re.findall(r"ocrSettingsSection\(", PAGE)), 3)   # still only on the Read text tab

    def test_images_page_lists_remote_models(self):
        self.assertIn("remote_models", PAGE)
        self.assertIn("imageModelList()", PAGE)


# ------------------------------------------------------------------------ what a host answers


class TestHostSurface(SharingBase):
    def test_only_loaded_models_and_no_paths(self):
        port = self.host(self.model_server(OllamaHandler, ["llama3.2:3b", "qwen3:8b"]))
        code, text = share(port, "/share/v1/models")
        self.assertEqual(code, 200, text)
        data = json.loads(text)
        self.assertEqual([m["name"] for m in data["chat"]], ["llama3.2:3b", "qwen3:8b"])
        self.assertEqual({m["kind"] for m in data["chat"]}, {"ollama"})
        self.assertEqual(data["image"], [])
        self.assertNotIn(self.home, text)

    def test_nothing_else_is_reachable(self):
        port = self.host(self.model_server(OllamaHandler, ["llama3.2:3b"]))
        for route in ("/", "/api/agents", "/api/status", "/api/files/_shared", "/api/manage", "/api/chats", "/api/skills",
                      "/api/terminal", "/share/v1/", "/share/v2/models", "/../etc/passwd"):
            self.assertEqual(share(port, route)[0], 404, route)
        for route in ("/api/chat", "/api/manage/install", "/api/agents", "/api/quit", "/api/terminal", "/share/v1/models"):
            self.assertEqual(share(port, route, {})[0], 404, route)
        self.assertEqual(share(port, "/share/v1/ping")[0], 200)

    def test_requests_without_the_header_are_refused(self):
        """A web page open in a browser cannot add a custom header to a request to another site."""
        port = self.host(self.model_server(OllamaHandler, ["llama3.2:3b"]))
        self.assertEqual(share(port, "/share/v1/models", headers={})[0], 403)
        self.assertEqual(share(port, "/share/v1/chat", {"model": "llama3.2:3b", "messages": [{"role": "user", "content": "hi"}]},
                               headers={})[0], 403)
        self.assertEqual(REQUESTS, [])

    def test_bad_requests(self):
        port = self.host(self.model_server(OllamaHandler, ["llama3.2:3b"]))
        self.assertEqual(share(port, "/share/v1/chat", {"model": "llama3.2:3b"})[0], 400)
        self.assertEqual(share(port, "/share/v1/chat", {"model": "llama3.2:3b", "messages": ["x"]})[0], 400)
        self.assertEqual(share(port, "/share/v1/chat", {"messages": [{"role": "user", "content": "x"}]})[0], 400)
        self.assertEqual(share(port, "/share/v1/image", {"model": "not-running", "prompt": "x"})[0], 400)
        self.assertEqual(REQUESTS, [])

    def test_a_chat_for_a_model_that_is_not_loaded(self):
        port = self.host(self.model_server(OllamaHandler, ["llama3.2:3b"]))
        code, text = share(port, "/share/v1/chat", {"model": "other:1b", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(code, 200)
        last = events(text)[-1]
        self.assertIn("not loaded", last["error"])
        self.assertEqual(REQUESTS, [])

    def test_port_choices(self):
        with self.assertRaises(netshare.ShareError):
            netshare.start_hosting(80, None)
        with self.assertRaises(netshare.ShareError):
            netshare.start_hosting(8765, None, avoid=8765)
        port = free_port()
        netshare.start_hosting(port, None)
        with self.assertRaises(netshare.ShareError) as why:
            netshare.start_hosting(port, None)
        self.assertIn("already on", str(why.exception))
        netshare.stop_hosting()
        busy = netshare._ShareServer(("0.0.0.0", 0), netshare.ShareHandler)
        self.addCleanup(busy.server_close)
        with self.assertRaises(netshare.ShareError) as why:
            netshare.start_hosting(busy.server_address[1], None)
        self.assertIn("Choose another port", str(why.exception))


# -------------------------------------------------------------------------------------- chat


class TestChatSharing(SharingBase):
    def test_chat_runs_on_the_host_with_the_visitors_messages(self):
        port = self.host(self.model_server(OllamaHandler, ["llama3.2:3b"]))
        tag = self.connect(port)
        conns = backend.connect_all(f"http://127.0.0.1:{free_port()}")
        self.assertEqual(backend.loaded_models(conns), [f"llama3.2:3b{backend.REMOTE_SEP}{tag}"])
        conn, model = backend.pick(None, f"llama3.2:3b{backend.REMOTE_SEP}{tag}")
        self.assertTrue(conn.remote)
        self.assertEqual((conn.kind, conn.tag, model), ("ollama", tag, "llama3.2:3b"))
        chunks = []
        turn = backend.complete(conn, model, [{"role": "system", "content": "You are a pirate."},
                                              {"role": "user", "content": "hello there"}], chunks.append)
        self.assertEqual(turn.text, "echo: hello there ")
        self.assertEqual("".join(chunks), turn.text)
        sent = REQUESTS[-1]
        self.assertEqual(sent["messages"][0], {"role": "system", "content": "You are a pirate."})   # the visitor's own prompt
        self.assertEqual(sent["options"]["num_ctx"], backend.NUM_CTX)

    def test_the_visitors_context_size_is_used(self):
        port = self.host(self.model_server(OllamaHandler, ["llama3.2:3b"]))
        code, text = share(port, "/share/v1/chat", {"model": "llama3.2:3b", "num_ctx": 4321,
                                                    "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(code, 200, text)
        self.assertEqual(REQUESTS[-1]["options"]["num_ctx"], 4321)
        share(port, "/share/v1/chat", {"model": "llama3.2:3b", "num_ctx": "huge", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(REQUESTS[-1]["options"]["num_ctx"], backend.NUM_CTX)   # nonsense falls back to the host's default

    def test_tool_calls_come_back_to_the_visitor(self):
        port = self.host(self.model_server(OllamaHandler, ["llama3.2:3b"]))
        tag = self.connect(port)
        conn, model = backend.pick(None, f"llama3.2:3b{backend.REMOTE_SEP}{tag}")
        tool = [{"type": "function", "function": {"name": "write_file", "parameters": {"type": "object", "properties": {}}}}]
        turn = backend.complete(conn, model, [{"role": "user", "content": 'TOOL write_file {"path": "a.txt", "content": "x"}'}],
                                tools=tool)
        self.assertEqual([c.name for c in turn.tool_calls], ["write_file"])
        self.assertEqual(turn.tool_calls[0].arguments, {"path": "a.txt", "content": "x"})
        self.assertTrue(REQUESTS[-1]["tools"])

    def test_a_model_without_tools_is_reported_as_such(self):
        port = self.host(self.model_server(OllamaHandler, ["llama3.2:3b"], no_tools=True))
        tag = self.connect(port)
        conn, model = backend.pick(None, f"llama3.2:3b{backend.REMOTE_SEP}{tag}")
        with self.assertRaises(backend.ToolsUnsupported):   # so the visitor's runner falls back to text actions
            backend.complete(conn, model, [{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {"name": "x"}}])

    def test_pictures_in_a_chat_are_passed_on_as_sent(self):
        port = self.host(self.model_server(OllamaHandler, ["llama3.2:3b"]))
        code, text = share(port, "/share/v1/chat", {"model": "llama3.2:3b",
                                                    "messages": [{"role": "user", "content": "what is this", "images": ["QUJD"]}]})
        self.assertEqual(code, 200, text)
        self.assertEqual(REQUESTS[-1]["messages"][0]["images"], ["QUJD"])   # the host never looks for the file

    def test_visitor_encodes_its_own_pictures(self):
        port = self.host(self.model_server(OllamaHandler, ["llama3.2:3b"]))
        tag = self.connect(port)
        picture = Path(self.home) / "p.png"
        picture.write_bytes(PNG)
        conn, model = backend.pick(None, f"llama3.2:3b{backend.REMOTE_SEP}{tag}")
        backend.complete(conn, model, [{"role": "user", "content": "look", "images": [str(picture)]}])
        self.assertEqual(REQUESTS[-1]["messages"][0]["images"], [base64.b64encode(PNG).decode()])

    def test_a_llama_cpp_host(self):
        port = self.host(self.model_server(LlamaCppHandler, ["tiny"]))
        tag = self.connect(port)
        conn, model = backend.pick(None, f"tiny{backend.REMOTE_SEP}{tag}")
        self.assertEqual(conn.kind, "openai")
        self.assertEqual(backend.complete(conn, model, [{"role": "user", "content": "hi"}]).text, "echo: hi ")

    def test_same_name_here_and_there(self):
        local = self.model_server(OllamaHandler, ["llama3.2:3b"])
        port = self.host(self.model_server(OllamaHandler, ["llama3.2:3b"]))
        tag = self.connect(port)
        self.assertEqual(backend.loaded_models(backend.connect_all(local)), ["llama3.2:3b", f"llama3.2:3b{backend.REMOTE_SEP}{tag}"])
        self.assertFalse(backend.pick(local, "llama3.2:3b")[0].remote)
        self.assertTrue(backend.pick(local, f"llama3.2:3b{backend.REMOTE_SEP}{tag}")[0].remote)
        with self.assertRaises(backend.BackendError):
            backend.pick(local, f"llama3.2:3b{backend.REMOTE_SEP}127.0.0.1:1")

    def test_a_host_never_passes_on_someone_elses_models(self):
        port = self.host(self.model_server(OllamaHandler, ["llama3.2:3b"]))
        self.connect(port)   # this process is both host and visitor
        names = [m["name"] for m in json.loads(share(port, "/share/v1/models")[1])["chat"]]
        self.assertEqual(names, ["llama3.2:3b"])

    def test_the_status_route_and_the_chat_route(self):
        port = self.host(self.model_server(OllamaHandler, ["llama3.2:3b"]))
        tag = self.connect(port)
        url = self.axis_server(f"http://127.0.0.1:{free_port()}")   # no model of its own: only the host's
        st = json.loads(request(url + "/api/status")[1])
        self.assertTrue(st["ready"])
        self.assertEqual(st["models"], [f"llama3.2:3b{backend.REMOTE_SEP}{tag}"])
        self.assertIn(tag, st["server"])
        code, text = request(url + "/api/chat", "POST", {"messages": [{"role": "user", "content": "hello"}],
                                                         "model": st["models"][0]})
        self.assertEqual(code, 200, text)
        evs = events(text)
        self.assertEqual(evs[0]["model"], st["models"][0])
        self.assertEqual("".join(e.get("t", "") for e in evs), "echo: hello ")
        netshare.disconnect()
        self.assertFalse(json.loads(request(url + "/api/status")[1])["ready"])

    def test_many_computers_at_once(self):
        """Four chats must be in the engine at the same moment: a host that took turns would never fill the barrier."""
        barrier = threading.Barrier(4)

        class Together(OllamaHandler):
            def do_POST(self):
                barrier.wait(timeout=15)
                super().do_POST()

        port = self.host(self.model_server(Together, ["llama3.2:3b"]))
        results, errors = {}, []

        def visitor(n):
            try:
                code, text = share(port, "/share/v1/chat", {"model": "llama3.2:3b", "messages": [{"role": "user", "content": f"from {n}"}]})
                results[n] = "".join(e.get("t", "") for e in events(text))
            except Exception as exc:   # pragma: no cover - reported below
                errors.append(exc)

        threads = [threading.Thread(target=visitor, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(results, {n: f"echo: from {n} " for n in range(4)})
        st = netshare.host_status()
        self.assertEqual(st["served"]["chat"], 4)
        self.assertEqual(st["active_chats"], 0)
        self.assertEqual([c["requests"] for c in st["clients"]], [4])

    def test_too_many_chats_are_told_to_wait(self):
        hold = threading.Event()

        class Slow(OllamaHandler):
            def do_POST(self):
                hold.wait(15)
                super().do_POST()

        with mock.patch.object(netshare, "CHAT_LIMIT", 1):
            port = self.host(self.model_server(Slow, ["llama3.2:3b"]))
        first = threading.Thread(target=share, args=(port, "/share/v1/chat", {"model": "llama3.2:3b", "messages": [{"role": "user", "content": "a"}]}))
        first.start()
        wait_for(self, lambda: netshare.host_status()["active_chats"] == 1, what="the first chat to start")
        code, text = share(port, "/share/v1/chat", {"model": "llama3.2:3b", "messages": [{"role": "user", "content": "b"}]})
        self.assertEqual(code, 503)
        self.assertIn("Try again", json.loads(text)["error"])
        hold.set()
        first.join(20)
        code, _ = share(port, "/share/v1/chat", {"model": "llama3.2:3b", "messages": [{"role": "user", "content": "c"}]})
        self.assertEqual(code, 200)

    def test_stopping_closes_the_chats_in_progress(self):
        hold = threading.Event()

        class Slow(OllamaHandler):
            def do_POST(self):
                hold.wait(15)
                super().do_POST()

        port = self.host(self.model_server(Slow, ["llama3.2:3b"]))
        out = {}
        visitor = threading.Thread(target=lambda: out.update(zip(("code", "text"), share(
            port, "/share/v1/chat", {"model": "llama3.2:3b", "messages": [{"role": "user", "content": "a"}]}))))
        visitor.start()
        wait_for(self, lambda: netshare.host_status()["active_chats"] == 1, what="the chat to start")
        netshare.stop_hosting()
        hold.set()
        visitor.join(20)
        self.assertFalse(visitor.is_alive())
        self.assertFalse(netshare.host_status()["hosting"])
        with self.assertRaises(OSError):
            share(port, "/share/v1/ping")


# --------------------------------------------------------------------------------- connecting


class TestConnecting(SharingBase):
    def test_addresses(self):
        self.assertEqual(netshare.parse_address("192.168.1.5:8800"), ("192.168.1.5", 8800, "192.168.1.5:8800"))
        self.assertEqual(netshare.parse_address("http://pc.local:9000/x")[2], "pc.local:9000")
        self.assertEqual(netshare.parse_address("10.0.0.2")[2], f"10.0.0.2:{netshare.DEFAULT_PORT}")
        self.assertEqual(netshare.parse_address("[::1]:8800")[2], "[::1]:8800")
        for bad in ("", "  ", "a b", "host:notaport", "host:99999", "user@host"):
            with self.assertRaises(netshare.ShareError, msg=bad):
                netshare.parse_address(bad)

    def test_unreachable_and_not_axis(self):
        with self.assertRaises(netshare.ShareError) as why:
            netshare.connect(f"127.0.0.1:{free_port()}")
        self.assertIn("Could not reach", str(why.exception))
        self.assertIn("Start sharing", str(why.exception))
        other = self.model_server(OllamaHandler, [])   # something answers, but it is not Axis
        with self.assertRaises(netshare.ShareError) as why:
            netshare.connect(other.replace("http://", ""))
        self.assertIn("not Axis", str(why.exception))
        self.assertFalse(netshare.client_status()["wanted"])

    def test_the_connection_is_remembered_and_resumed(self):
        port = self.host(self.model_server(OllamaHandler, ["llama3.2:3b"]))
        tag = self.connect(port)
        saved = json.loads((Path(self.home) / "net-settings.json").read_text(encoding="utf-8"))
        self.assertEqual((saved["address"], saved["connected"]), (tag, True))
        with netshare._LOCK:   # Axis starts again on this computer (the host keeps running)
            netshare._stop_polling()
            netshare._CLIENT.update(tag="", state="off", message="", chat=[], image=[], checked=0.0)
        self.assertEqual(netshare.client_status()["state"], "off")
        netshare.resume()
        self.assertEqual(netshare.client_status()["state"], "connecting")
        wait_for(self, lambda: netshare.client_status()["state"] == "connected", what="the connection to come back")
        netshare.disconnect()
        self.assertEqual(netshare.client_status()["state"], "off")
        self.assertFalse(json.loads((Path(self.home) / "net-settings.json").read_text(encoding="utf-8"))["connected"])
        netshare.resume()
        self.assertEqual(netshare.client_status()["state"], "off")   # a disconnect stays a disconnect

    def test_a_host_that_goes_away_and_comes_back(self):
        engine = self.model_server(OllamaHandler, ["llama3.2:3b"])
        with mock.patch.object(netshare, "POLL_SECONDS", 0.2):
            port = self.host(engine)
            self.connect(port)
            self.assertEqual(len(netshare.remote_backends()), 1)
            netshare.stop_hosting()
            wait_for(self, lambda: netshare.client_status()["state"] == "unreachable", what="the host to show as unreachable")
            self.assertEqual(netshare.remote_backends(), [])           # its models disappear, nothing blocks
            self.assertIn("Could not reach", netshare.client_status()["message"])
            netshare.start_hosting(port, engine)
            wait_for(self, lambda: netshare.client_status()["state"] == "connected", what="the host to come back")
            self.assertEqual(len(netshare.remote_backends()), 1)

    def test_models_that_start_later_appear(self):
        handler = type("Later", (OllamaHandler,), {"models": []})
        engine = self.model_server(handler, [])
        with mock.patch.object(netshare, "POLL_SECONDS", 0.2):
            port = self.host(engine)
            self.connect(port)
            self.assertEqual(netshare.client_status()["chat"], [])
            self.assertEqual(netshare.remote_backends(), [])
            self.assertEqual(json.loads(request(self.axis_server(engine) + "/api/status")[1])["ready"], False)
            self.servers[0].RequestHandlerClass.models.append("qwen3:8b")
            wait_for(self, lambda: [m["name"] for m in netshare.client_status()["chat"]] == ["qwen3:8b"], what="the new model")

    def test_routes(self):
        url = self.axis_server(self.model_server(OllamaHandler, ["llama3.2:3b"]))
        st = json.loads(request(url + "/api/net")[1])
        self.assertFalse(st["host"]["hosting"])
        self.assertEqual(st["client"]["state"], "off")
        self.assertEqual(st["default_port"], 8800)
        code, text = request(url + "/api/net/host/start", "POST", {"port": 80})
        self.assertEqual(code, 400)
        self.assertIn("1024", text)
        code, text = request(url + "/api/net/host/start", "POST", {"port": 0})
        self.assertEqual(code, 200, text)
        st = json.loads(text)
        self.assertTrue(st["host"]["hosting"])
        self.assertEqual([m["name"] for m in st["host"]["offering"]["chat"]], ["llama3.2:3b"])
        self.assertEqual(request(url + "/api/net/host/start", "POST", {"port": 0})[0], 400)   # already on
        port = st["host"]["port"]
        code, text = request(url + "/api/net/connect", "POST", {"address": f"127.0.0.1:{port}"})
        self.assertEqual(code, 200, text)
        self.assertEqual(json.loads(text)["client"]["state"], "connected")
        self.assertEqual(request(url + "/api/net/connect", "POST", {"address": "nonsense host"})[0], 400)
        self.assertEqual(json.loads(request(url + "/api/net/disconnect", "POST", {})[1])["client"]["state"], "off")
        self.assertFalse(json.loads(request(url + "/api/net/host/stop", "POST", {})[1])["host"]["hosting"])
        self.assertEqual(request(url + "/api/net", "DELETE")[0], 404)

    def test_the_page_port_is_not_offered_for_sharing(self):
        url = self.axis_server(self.model_server(OllamaHandler, []))
        own = int(url.rsplit(":", 1)[1])
        code, text = request(url + "/api/net/host/start", "POST", {"port": own})
        self.assertEqual(code, 400)
        self.assertIn("page uses", text)

    def test_the_page_still_refuses_other_hosts(self):
        """The new listener does not change the page's own check."""
        url = self.axis_server(self.model_server(OllamaHandler, []))
        self.assertEqual(request(url + "/api/net", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(request(url + "/api/net", headers={"Origin": "http://evil.example"})[0], 403)


# ------------------------------------------------------------------------------------ pictures


class TestPictureSharing(SharingBase):
    def setUp(self):
        super().setUp()
        files = Path(self.home) / "files"
        files.mkdir()
        (files / "z.gguf").write_bytes(b"x")
        (files / "ae.safetensors").write_bytes(b"y")
        images.create_model({"name": "Z-Image Turbo", "diffusion_model": str(files / "z.gguf"), "vae": str(files / "ae.safetensors"),
                             "steps": 4, "cfg_scale": 1, "width": 384, "height": 384, "description": "fast"})
        self.made = []          # what the host's engine was asked for
        self.delay = 0.0
        self.running = mock.patch.object(manager, "image_server_url", lambda model_id: "http://engine.invalid")
        self.engine = mock.patch.object(images, "_generate_with_server", self.fake_engine)
        self.running.start()
        self.engine.start()
        self.addCleanup(self.running.stop)
        self.addCleanup(self.engine.stop)
        self.folder = Path(self.home) / "visitor-images"

    def fake_engine(self, url, params, out_file, emit, preview=False):
        self.made.append(dict(params))
        emit({"phase": "Sampling"})
        for step in range(1, 3):
            emit({"progress": {"step": step, "steps": 2}})
            time.sleep(self.delay)
        out_file.write_bytes(PNG)

    def remote_id(self, tag):
        return f"net:{tag}/z-image-turbo"

    def test_the_host_offers_models_that_are_on(self):
        port = self.host(self.model_server(OllamaHandler, []))
        data = json.loads(share(port, "/share/v1/models")[1])
        self.assertEqual([(m["id"], m["name"], m["width"], m["steps"]) for m in data["image"]], [("z-image-turbo", "Z-Image Turbo", 384, 4)])
        self.assertNotIn(self.home, json.dumps(data))   # no file paths
        with mock.patch.object(manager, "image_server_url", lambda model_id: None):   # off: nothing to offer
            self.assertEqual(json.loads(share(port, "/share/v1/models")[1])["image"], [])

    def test_a_picture_is_made_on_the_host_and_kept_here(self):
        port = self.host(self.model_server(OllamaHandler, []))
        tag = self.connect(port)
        st = images.status()
        self.assertEqual([m["id"] for m in st["remote_models"]], [self.remote_id(tag)])
        self.assertEqual(st["remote_models"][0]["name"], f"Z-Image Turbo{backend.REMOTE_SEP}{tag}")
        got = []
        rec = images.generate({"model": self.remote_id(tag), "prompt": "a lighthouse", "width": 512, "height": 256, "seed": 7},
                              self.folder, source="images tab", on_event=got.append)
        self.assertEqual((self.made[0]["prompt"], self.made[0]["width"], self.made[0]["seed"]), ("a lighthouse", 512, 7))
        self.assertEqual((rec["width"], rec["height"], rec["steps"], rec["seed"]), (512, 256, 4, 7))
        self.assertEqual((self.folder / rec["path"]).read_bytes(), PNG)
        self.assertEqual(rec["host"], tag)
        self.assertIn(tag, rec["model"])
        meta = json.loads((self.folder / rec["folder"] / f"{rec['folder']}.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["prompt"], "a lighthouse")
        self.assertIn({"phase": "Sampling"}, got)
        self.assertIn({"progress": {"step": 2, "steps": 2}}, got)
        self.assertEqual([g["prompt"] for g in images.gallery(self.folder)], ["a lighthouse"])
        # Nothing is kept at the host: no gallery, no temporary folder.
        host_gallery = Path(self.home) / "images"
        self.assertEqual(list(host_gallery.glob("*")) if host_gallery.exists() else [], [])
        wait_for(self, lambda: not list(Path(tempfile.gettempdir()).glob("axis-share-*")), what="the host's temporary folder to be deleted")
        wait_for(self, lambda: netshare.host_status()["served"]["image"] == 1, what="the host to count the picture")

    def test_a_picture_can_be_edited_on_the_host(self):
        port = self.host(self.model_server(OllamaHandler, []))
        tag = self.connect(port)
        source = Path(self.home) / "cat.png"
        source.write_bytes(PNG)
        seen = {}

        def engine(url, params, out_file, emit, preview=False):
            seen.update(params)
            out_file.write_bytes(PNG)

        with mock.patch.object(images, "_generate_with_server", engine), \
                mock.patch.object(images, "check_init_image", lambda p: p), \
                mock.patch.object(images, "edit_params", lambda model, raw, init: {**images.resolve_params(model, raw), "init_image": str(init)}):
            rec = images.generate({"model": self.remote_id(tag), "prompt": "a dog", "strength": 0.5}, self.folder, init_image=source,
                                  init_name="shared:cat.png")
        self.assertTrue(Path(seen["init_image"]).name.startswith("source"))
        self.assertEqual(rec["edited_from"], "shared:cat.png")

    def test_pictures_wait_in_line_and_are_all_made(self):
        self.delay = 0.4
        port = self.host(self.model_server(OllamaHandler, []))
        tag = self.connect(port)
        results, texts = {}, {}

        def visitor(n):
            got = []
            results[n] = images.generate({"model": self.remote_id(tag), "prompt": f"picture {n}", "seed": n}, self.folder, on_event=got.append)
            texts[n] = [e["phase"] for e in got if "phase" in e]

        threads = [threading.Thread(target=visitor, args=(n,)) for n in range(3)]
        for t in threads:
            t.start()
            time.sleep(0.15)   # a clear order of arrival
        for t in threads:
            t.join(60)
        self.assertEqual(sorted(results), [0, 1, 2])
        self.assertEqual([m["prompt"] for m in self.made], ["picture 0", "picture 1", "picture 2"])   # first come, first served
        self.assertTrue(any(p.startswith("Waiting for") for p in texts[2]), texts[2])
        self.assertTrue(any("ahead" in p for p in texts[2]), texts[2])
        self.assertEqual(len({r["folder"] for r in results.values()}), 3)
        wait_for(self, lambda: netshare.host_status()["active_images"] == 0, what="the host to finish")

    def test_cancelling_frees_the_line(self):
        self.delay = 0.3
        port = self.host(self.model_server(OllamaHandler, []))
        tag = self.connect(port)

        class Stop(Exception):
            pass

        def on_event(e):
            if "progress" in e:
                raise Stop()

        with self.assertRaises(Stop):
            images.generate({"model": self.remote_id(tag), "prompt": "never finished"}, self.folder, on_event=on_event)
        self.assertEqual(list(self.folder.glob("*")) if self.folder.exists() else [], [])
        wait_for(self, lambda: netshare.host_status()["active_images"] == 0 and netshare.host_status()["waiting_images"] == 0,
                 what="the host to notice")
        self.delay = 0
        rec = images.generate({"model": self.remote_id(tag), "prompt": "after"}, self.folder)   # the line still works
        self.assertTrue((self.folder / rec["path"]).is_file())

    def test_a_full_line_is_refused(self):
        with mock.patch.object(netshare, "QUEUE_LIMIT", 0):
            port = self.host(self.model_server(OllamaHandler, []))
        code, text = share(port, "/share/v1/image", {"model": "z-image-turbo", "prompt": "x"})
        self.assertEqual(code, 503)
        self.assertEqual(self.made, [])

    def test_errors_on_the_host_reach_the_visitor_without_paths(self):
        port = self.host(self.model_server(OllamaHandler, []))
        tag = self.connect(port)

        def broken(url, params, out_file, emit, preview=False):
            raise RuntimeError(f"cannot open {self.home}/secret")

        with mock.patch.object(images, "_generate_with_server", broken):
            with self.assertRaises(images.ImageError) as why:
                images.generate({"model": self.remote_id(tag), "prompt": "x"}, self.folder)
        self.assertNotIn(self.home, str(why.exception))
        self.assertEqual(list(self.folder.glob("*")) if self.folder.exists() else [], [])
        nothing = images.generate  # and a bad prompt is the visitor's own mistake, explained as for a local picture
        with self.assertRaises(images.ImageError) as why:
            nothing({"model": self.remote_id(tag), "prompt": " "}, self.folder)
        self.assertIn("prompt", str(why.exception).lower())

    def test_not_connected(self):
        port = self.host(self.model_server(OllamaHandler, []))
        tag = self.connect(port)
        remote = netshare.remote_image_models()[0]
        netshare.disconnect()
        with self.assertRaises(images.ImageError) as why:
            netshare.generate_remote(remote, {"prompt": "x"}, self.folder, "", lambda e: None, False, None, "")
        self.assertIn("not connected", str(why.exception))
        self.assertIsNone(netshare.find_remote_image(self.remote_id(tag)))

    def test_agents_are_told_about_remote_models(self):
        port = self.host(self.model_server(OllamaHandler, []))
        tag = self.connect(port)
        spec = tools.tool_spec("generate_image")
        self.assertIn(f"Z-Image Turbo{backend.REMOTE_SEP}{tag}", spec["description"])
        rec = images.generate({"model": f"Z-Image Turbo{backend.REMOTE_SEP}{tag}", "prompt": "by name"}, self.folder)   # by its shown name
        self.assertEqual(rec["host"], tag)

    def test_only_remote_models_make_images_ready(self):
        for m in images.list_models():
            images.delete_model(m["id"])
        self.assertFalse(images.status()["ready"])
        # A host that has an image model on, seen by a computer with none of its own.
        self.engine.stop()
        hosted = [{"id": "far", "name": "Far", "description": "", "width": 512, "height": 512, "steps": 8, "cfg_scale": 2.0, "sampler": ""}]
        # The host keeps running, and the connected side asks it again every few seconds, so the stand-in must stay
        # in place for the whole test: if it ended after connecting, the next look would find the real (empty) list.
        with mock.patch.object(netshare, "image_models", lambda: hosted):
            port = self.host(self.model_server(OllamaHandler, []))
            tag = self.connect(port)
            self.assertTrue(images.status()["ready"])
            self.assertEqual(images.status()["models"], [])
            self.assertEqual(netshare.find_remote_image(None)["id"], f"net:{tag}/far")   # no local model: the remote one is the default
        self.engine.start()


if __name__ == "__main__":
    unittest.main()
