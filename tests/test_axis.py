"""End-to-end tests against mock Ollama and llama.cpp servers.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import json
import os
import shutil
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from axis import agents, backend, cli, server

LAST_REQUEST: dict = {}


def reply_for(messages):
    user = messages[-1]["content"]
    return None if "FAIL" in user else f"echo: {user}"


class _Base(BaseHTTPRequestHandler):
    models: list = []

    def log_message(self, *args):
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))


class OllamaHandler(_Base):
    def do_GET(self):
        if self.path == "/api/version":
            self._json(200, {"version": "0.12.0"})
        elif self.path == "/api/ps":
            self._json(200, {"models": [{"name": m, "model": m} for m in self.models]})
        elif self.path == "/v1/models":
            self._json(200, {"object": "list", "data": [{"id": "should-not-be-used"}]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/chat":
            return self._json(404, {"error": "not found"})
        body = self._body()
        LAST_REQUEST.clear()
        LAST_REQUEST.update(body)
        text = reply_for(body["messages"])
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        if text is None:
            self.wfile.write(json.dumps({"error": "simulated failure"}).encode() + b"\n")
            return
        for word in text.split(" "):
            chunk = {"model": body["model"], "message": {"role": "assistant", "content": word + " "}, "done": False}
            self.wfile.write(json.dumps(chunk).encode() + b"\n")
        self.wfile.write(json.dumps({"message": {"content": ""}, "done": True}).encode() + b"\n")


class LlamaCppHandler(_Base):
    def do_GET(self):
        if self.path == "/v1/models":
            self._json(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in self.models]})
        else:
            self._json(404, {"error": {"code": 404, "message": "File Not Found"}})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            return self._json(404, {"error": {"message": "not found"}})
        body = self._body()
        LAST_REQUEST.clear()
        LAST_REQUEST.update(body)
        text = reply_for(body["messages"])
        if text is None:
            return self._json(500, {"error": {"code": 500, "message": "simulated failure"}})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for word in text.split(" "):
            chunk = {"choices": [{"index": 0, "delta": {"content": word + " "}}]}
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")


def start_server(handler, models):
    cls = type(handler.__name__, (handler,), {"models": list(models)})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"AXIS_HOME": self.home})
        self.env.start()
        os.environ.pop("AXIS_URL", None)
        self.servers = []

    def tearDown(self):
        for s in self.servers:
            s.shutdown()
            s.server_close()
        self.env.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    def model_server(self, handler, models):
        srv, url = start_server(handler, models)
        self.servers.append(srv)
        return url

    def axis_server(self, model_url):
        srv = server.make_server(0, model_url)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servers.append(srv)
        return srv.url


# ------------------------------------------------------------------ backend


class TestBackend(Base):
    def test_no_server(self):
        with self.assertRaises(backend.ServerUnavailable):
            backend.connect(f"http://127.0.0.1:{free_port()}")

    def test_server_without_model(self):
        for handler in (OllamaHandler, LlamaCppHandler):
            url = self.model_server(handler, [])
            with self.assertRaisesRegex(backend.BackendError, "no model is loaded"):
                backend.connect(url)

    def test_detects_ollama_before_openai_route(self):
        conn = backend.connect(self.model_server(OllamaHandler, ["llama3.2:3b"]))
        self.assertEqual((conn.kind, conn.models), ("ollama", ["llama3.2:3b"]))

    def test_detects_llamacpp(self):
        conn = backend.connect(self.model_server(LlamaCppHandler, ["x.gguf"]))
        self.assertEqual(conn.kind, "openai")

    def test_env_url_without_scheme(self):
        url = self.model_server(OllamaHandler, ["m"])
        with mock.patch.dict(os.environ, {"AXIS_URL": url.replace("http://", "")}):
            self.assertEqual(backend.connect(None).url, url)

    def test_resolve_model(self):
        conn = backend.Backend("ollama", "u", ["llama3.2:3b", "qwen:latest"])
        self.assertEqual(backend.resolve_model(conn, None), "llama3.2:3b")
        self.assertEqual(backend.resolve_model(conn, "qwen"), "qwen:latest")
        with self.assertRaisesRegex(backend.BackendError, "not loaded"):
            backend.resolve_model(conn, "mistral")

    def test_chat_streams_both_backends(self):
        for handler in (OllamaHandler, LlamaCppHandler):
            conn = backend.connect(self.model_server(handler, ["m"]))
            tokens = []
            reply = backend.chat(conn, "m", [{"role": "user", "content": "hi"}], tokens.append)
            self.assertEqual(reply.strip(), "echo: hi")
            self.assertGreater(len(tokens), 1)
            self.assertEqual("keep_alive" in LAST_REQUEST, handler is OllamaHandler)

    def test_chat_errors(self):
        for handler in (OllamaHandler, LlamaCppHandler):
            conn = backend.connect(self.model_server(handler, ["m"]))
            with self.assertRaisesRegex(backend.BackendError, "simulated failure"):
                backend.chat(conn, "m", [{"role": "user", "content": "FAIL"}])


# ------------------------------------------------------------------- agents


class TestAgents(Base):
    def test_create_load_update_delete(self):
        a = agents.create({
            "name": "editor", "system_prompt": "You edit.", "model": "m",
            "config": ["Be brief.", "  ", "Use US spelling."],
            "skills": [{"name": "Grammar", "instructions": "Fix grammar.", "enabled": True},
                       {"name": "", "instructions": ""}],
        })
        self.assertEqual(a.config, ["Be brief.", "Use US spelling."])
        self.assertEqual(len(a.skills), 1)
        loaded = agents.load("editor")
        self.assertEqual(loaded.to_dict(), a.to_dict())

        updated = agents.update("editor", {"name": "copyeditor", "config": ["One rule."]})
        self.assertEqual(updated.name, "copyeditor")
        self.assertEqual(updated.system_prompt, "You edit.")  # untouched fields are kept
        self.assertFalse((Path(self.home) / "agents" / "editor").exists())
        self.assertEqual([x.name for x in agents.list_all()], ["copyeditor"])

        agents.delete("copyeditor")
        self.assertEqual(agents.list_all(), [])

    def test_validation(self):
        for bad in ("../x", ".hidden", "a b", "", "x" * 65):
            with self.assertRaises(agents.AgentError):
                agents.create({"name": bad})
        agents.create({"name": "a"})
        with self.assertRaisesRegex(agents.AgentError, "already exists"):
            agents.create({"name": "a"})
        with self.assertRaisesRegex(agents.AgentError, "needs a name"):
            agents.create({"name": "b", "skills": [{"name": "", "instructions": "x"}]})
        with self.assertRaises(agents.AgentError):
            agents.create({"name": "c", "config": "not a list"})
        agents.create({"name": "d"})
        with self.assertRaisesRegex(agents.AgentError, "already exists"):
            agents.update("a", {"name": "d"})

    def test_reads_version_1_agent_files(self):
        folder = Path(self.home) / "agents" / "old"
        folder.mkdir(parents=True)
        (folder / "agent.json").write_text(json.dumps({"name": "old", "system_prompt": "Hi", "model": "", "created": "x"}))
        a = agents.load("old")
        self.assertEqual((a.system_prompt, a.config, a.skills), ("Hi", [], []))

    def test_build_messages(self):
        a = agents.create({
            "name": "x", "system_prompt": "Base.", "config": ["Rule one.", "Rule two."],
            "skills": [{"name": "On", "instructions": "use me"}, {"name": "Off", "instructions": "hidden", "enabled": False}],
        })
        history = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}, {"role": "user", "content": "q2"}]
        msgs = agents.build_messages(a, history)
        self.assertEqual(msgs[0]["role"], "system")
        self.assertIn("Base.", msgs[0]["content"])
        self.assertIn("## On\nuse me", msgs[0]["content"])
        self.assertNotIn("hidden", msgs[0]["content"])
        self.assertEqual(msgs[1]["content"], "q1")  # earlier turns untouched
        self.assertTrue(msgs[-1]["content"].startswith("q2"))
        self.assertIn("- Rule one.\n- Rule two.", msgs[-1]["content"])
        self.assertEqual(history[-1]["content"], "q2")  # caller's list is not modified

        plain = agents.build_messages(None, history)
        self.assertEqual(plain, history)

    def test_build_messages_rejects_bad_history(self):
        for bad in ([], None, [{"role": "assistant", "content": "x"}], [{"role": "system", "content": "x"}],
                    [{"role": "user", "content": 5}], [{"role": "user", "content": "  "}]):
            with self.assertRaises(agents.AgentError):
                agents.build_messages(None, bad)


# ------------------------------------------------------------------- server


def request(url, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def chat_events(text):
    return [json.loads(line) for line in text.splitlines() if line.strip()]


class TestServer(Base):
    def setUp(self):
        super().setUp()
        self.model_url = self.model_server(OllamaHandler, ["llama3.2:3b", "qwen3:8b"])
        self.url = self.axis_server(self.model_url)

    def test_page_and_ping(self):
        code, text = request(self.url + "/")
        self.assertEqual(code, 200)
        self.assertIn("<title>Axis</title>", text)
        code, text = request(self.url + "/api/ping")
        self.assertEqual(json.loads(text)["app"], "axis")

    def test_status_ready(self):
        code, text = request(self.url + "/api/status")
        st = json.loads(text)
        self.assertTrue(st["ready"])
        self.assertEqual(st["models"], ["llama3.2:3b", "qwen3:8b"])

    def test_status_not_ready(self):
        down = self.axis_server(f"http://127.0.0.1:{free_port()}")
        st = json.loads(request(down + "/api/status")[1])
        self.assertEqual((st["ready"], st["server_running"]), (False, False))
        empty = self.axis_server(self.model_server(LlamaCppHandler, []))
        st = json.loads(request(empty + "/api/status")[1])
        self.assertEqual((st["ready"], st["server_running"]), (False, True))
        self.assertIn("no model is loaded", st["message"])

    def test_agent_routes(self):
        body = {"name": "poet", "system_prompt": "Be poetic.", "config": ["Rhyme."],
                "skills": [{"name": "Haiku", "instructions": "5-7-5", "enabled": False}]}
        code, text = request(self.url + "/api/agents", "POST", body)
        self.assertEqual(code, 201, text)
        self.assertEqual(request(self.url + "/api/agents", "POST", body)[0], 400)
        self.assertEqual(len(json.loads(request(self.url + "/api/agents")[1])), 1)
        code, text = request(self.url + "/api/agents/poet", "PUT", {"name": "bard", "model": "qwen3:8b"})
        self.assertEqual(code, 200, text)
        self.assertEqual(json.loads(text)["config"], ["Rhyme."])
        self.assertEqual(request(self.url + "/api/agents/poet")[0], 404)
        self.assertEqual(request(self.url + "/api/agents/bard")[0], 200)
        self.assertEqual(request(self.url + "/api/agents/bard", "DELETE")[0], 200)
        self.assertEqual(request(self.url + "/api/agents/bard", "DELETE")[0], 404)
        self.assertEqual(request(self.url + "/api/nope")[0], 404)
        self.assertEqual(request(self.url + "/api/agents", "POST", None, {"Content-Length": "0"})[0], 400)

    def test_chat_direct(self):
        code, text = request(self.url + "/api/chat", "POST", {"messages": [{"role": "user", "content": "hello"}]})
        ev = chat_events(text)
        self.assertEqual(ev[0], {"model": "llama3.2:3b"})
        self.assertEqual("".join(e.get("t", "") for e in ev).strip(), "echo: hello")
        self.assertEqual(ev[-1], {"done": True})
        self.assertEqual(LAST_REQUEST["messages"], [{"role": "user", "content": "hello"}])

    def test_chat_with_agent_uses_config_skills_and_model(self):
        request(self.url + "/api/agents", "POST", {
            "name": "a", "system_prompt": "S.", "model": "qwen3:8b", "config": ["C1"],
            "skills": [{"name": "K", "instructions": "skill text"}]})
        request(self.url + "/api/chat", "POST", {"agent": "a", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(LAST_REQUEST["model"], "qwen3:8b")
        self.assertIn("skill text", LAST_REQUEST["messages"][0]["content"])
        self.assertIn("- C1", LAST_REQUEST["messages"][-1]["content"])
        # the dropdown choice overrides the agent's preferred model
        request(self.url + "/api/chat", "POST", {"agent": "a", "model": "llama3.2:3b", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(LAST_REQUEST["model"], "llama3.2:3b")

    def test_chat_errors(self):
        ev = chat_events(request(self.url + "/api/chat", "POST", {"messages": [{"role": "user", "content": "FAIL"}]})[1])
        self.assertIn("simulated failure", ev[-1]["error"])
        ev = chat_events(request(self.url + "/api/chat", "POST", {"model": "mistral", "messages": [{"role": "user", "content": "x"}]})[1])
        self.assertIn("not loaded", ev[-1]["error"])
        self.assertEqual(request(self.url + "/api/chat", "POST", {"agent": "ghost", "messages": [{"role": "user", "content": "x"}]})[0], 404)
        self.assertEqual(request(self.url + "/api/chat", "POST", {"messages": []})[0], 400)
        down = self.axis_server(f"http://127.0.0.1:{free_port()}")
        ev = chat_events(request(down + "/api/chat", "POST", {"messages": [{"role": "user", "content": "x"}]})[1])
        self.assertIn("No model server", ev[-1]["error"])

    def test_rejects_other_sites_and_hosts(self):
        self.assertEqual(request(self.url + "/api/agents", "POST", {"name": "evil"}, {"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(request(self.url + "/api/status", headers={"Host": "evil.example"})[0], 403)
        port = self.url.rsplit(":", 1)[1]
        self.assertEqual(request(self.url + "/api/agents", "POST", {"name": "ok"}, {"Origin": f"http://localhost:{port}"})[0], 201)

    def test_stop_mid_stream_does_not_break_server(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(self.url + "/api/chat", method="POST",
                                     data=json.dumps({"messages": [{"role": "user", "content": "a b c d e f"}]}).encode(),
                                     headers={"Content-Type": "application/json"})
        resp = opener.open(req, timeout=10)
        resp.readline()
        resp.close()  # like pressing Stop
        self.assertEqual(request(self.url + "/api/ping")[0], 200)


# ---------------------------------------------------------------------- cli


class TestCli(Base):
    def test_version(self):
        with self.assertRaises(SystemExit) as ctx, redirect_stdout(io.StringIO()) as out:
            cli.main(["--version"])
        self.assertEqual(ctx.exception.code, 0)

    def test_bad_port(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["--port", "70000", "--no-browser"]), 2)

    def test_detects_running_axis(self):
        url = self.axis_server(None)
        port = int(url.rsplit(":", 1)[1])
        self.assertTrue(server.find_running(port))
        self.assertFalse(server.find_running(free_port()))
        with redirect_stdout(io.StringIO()) as out, mock.patch("webbrowser.open") as wb:
            self.assertEqual(cli.main(["--port", str(port)]), 0)
        self.assertIn("already running", out.getvalue())
        wb.assert_called_once()

    def test_starts_and_stops(self):
        port = free_port()
        with redirect_stdout(io.StringIO()) as out, \
                mock.patch.object(server.AxisServer, "serve_forever", side_effect=KeyboardInterrupt), \
                mock.patch("threading.Timer") as timer:
            self.assertEqual(cli.main(["--port", str(port)]), 0)
        self.assertIn(f"http://127.0.0.1:{port}", out.getvalue())
        self.assertIn("Axis stopped", out.getvalue())
        timer.assert_called_once()

    def test_skips_busy_port(self):
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen()
        try:
            port = blocker.getsockname()[1]
            srv = server.make_server(port)
            self.assertNotEqual(srv.server_address[1], port)
            srv.server_close()
        finally:
            blocker.close()


if __name__ == "__main__":
    unittest.main()
