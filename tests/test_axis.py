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
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from axis import cli

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


def run_cli(*argv, stdin=None):
    out, err = io.StringIO(), io.StringIO()
    patches = []
    if stdin is not None:
        patches.append(mock.patch("sys.stdin", io.StringIO(stdin)))
    for p in patches:
        p.start()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
    finally:
        for p in patches:
            p.stop()
    return code, out.getvalue(), err.getvalue()


class AxisTestCase(unittest.TestCase):
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

    def server(self, handler, models):
        srv, url = start_server(handler, models)
        self.servers.append(srv)
        return url


class TestGate(AxisTestCase):
    def test_no_server(self):
        url = f"http://127.0.0.1:{free_port()}"
        for argv in (["status"], ["ask", "hi"], ["chat"]):
            code, _, err = run_cli("--url", url, *argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("No model server is running", err)

    def test_ollama_running_but_no_model(self):
        url = self.server(OllamaHandler, [])
        code, _, err = run_cli("--url", url, "ask", "hi")
        self.assertEqual(code, 1)
        self.assertIn("no model is loaded", err)
        self.assertIn("ollama run", err)

    def test_llamacpp_running_but_no_model(self):
        url = self.server(LlamaCppHandler, [])
        code, _, err = run_cli("--url", url, "status")
        self.assertEqual(code, 1)
        self.assertIn("no model is loaded", err)

    def test_env_url_and_bare_host(self):
        url = self.server(OllamaHandler, ["llama3.2:3b"])
        with mock.patch.dict(os.environ, {"AXIS_URL": url.replace("http://", "")}):
            code, out, _ = run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("Ollama", out)


class TestOllama(AxisTestCase):
    def setUp(self):
        super().setUp()
        self.url = self.server(OllamaHandler, ["llama3.2:3b", "qwen3:8b"])

    def test_status(self):
        code, out, _ = run_cli("--url", self.url, "status")
        self.assertEqual(code, 0)
        self.assertIn("llama3.2:3b   (default)", out)
        self.assertIn("qwen3:8b", out)

    def test_ask_streams_and_keeps_model_loaded(self):
        code, out, _ = run_cli("--url", self.url, "ask", "hello", "world")
        self.assertEqual(code, 0)
        self.assertIn("echo: hello world", out)
        self.assertEqual(LAST_REQUEST["model"], "llama3.2:3b")
        self.assertEqual(LAST_REQUEST["keep_alive"], -1)

    def test_ask_from_stdin(self):
        code, out, _ = run_cli("--url", self.url, "ask", stdin="piped text\n")
        self.assertEqual(code, 0)
        self.assertIn("echo: piped text", out)

    def test_ask_specific_model_and_latest_alias(self):
        code, _, _ = run_cli("--url", self.url, "ask", "-m", "qwen3:8b", "x")
        self.assertEqual(code, 0)
        self.assertEqual(LAST_REQUEST["model"], "qwen3:8b")

    def test_ask_model_not_loaded(self):
        code, _, err = run_cli("--url", self.url, "ask", "-m", "mistral", "x")
        self.assertEqual(code, 1)
        self.assertIn("not loaded", err)

    def test_server_error_reported(self):
        code, _, err = run_cli("--url", self.url, "ask", "FAIL")
        self.assertEqual(code, 1)
        self.assertIn("simulated failure", err)

    def test_chat_session(self):
        script = "first\n/model\n/clear\nsecond\n/help\n/exit\n"
        with mock.patch("builtins.input", side_effect=script.splitlines()):
            code, out, _ = run_cli("--url", self.url, "chat")
        self.assertEqual(code, 0)
        self.assertIn("echo: first", out)
        self.assertIn("echo: second", out)
        self.assertIn("Conversation cleared.", out)
        # after /clear only system + "second" are sent
        self.assertEqual([m["role"] for m in LAST_REQUEST["messages"]], ["system", "user"])

    def test_chat_keeps_history(self):
        with mock.patch("builtins.input", side_effect=["one", "two", EOFError()]):
            code, _, _ = run_cli("--url", self.url, "chat")
        self.assertEqual(code, 0)
        roles = [m["role"] for m in LAST_REQUEST["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])

    def test_chat_error_does_not_end_session(self):
        with mock.patch("builtins.input", side_effect=["FAIL", "ok", "/quit"]):
            code, out, err = run_cli("--url", self.url, "chat")
        self.assertEqual(code, 0)
        self.assertIn("simulated failure", err)
        self.assertIn("echo: ok", out)


class TestLlamaCpp(AxisTestCase):
    def setUp(self):
        super().setUp()
        self.url = self.server(LlamaCppHandler, ["Llama-3.2-3B-Instruct-Q4_K_M.gguf"])

    def test_status(self):
        code, out, _ = run_cli("--url", self.url, "status")
        self.assertEqual(code, 0)
        self.assertIn("llama.cpp", out)

    def test_ask(self):
        code, out, _ = run_cli("--url", self.url, "ask", "hi there")
        self.assertEqual(code, 0)
        self.assertIn("echo: hi there", out)
        self.assertNotIn("keep_alive", LAST_REQUEST)

    def test_http_error(self):
        code, _, err = run_cli("--url", self.url, "ask", "FAIL")
        self.assertEqual(code, 1)
        self.assertIn("simulated failure", err)


class TestAgents(AxisTestCase):
    def test_crud(self):
        code, out, _ = run_cli("agent", "create", "writer", "-p", "You write haiku.")
        self.assertEqual(code, 0, out)
        code, _, err = run_cli("agent", "create", "writer")
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

        code, out, _ = run_cli("agent", "list")
        self.assertIn("writer", out)
        code, out, _ = run_cli("agent", "show", "writer")
        self.assertIn("You write haiku.", out)

        code, out, _ = run_cli("agent", "edit", "writer", "-m", "qwen3:8b", "-p", "New prompt")
        self.assertEqual(code, 0)
        code, out, _ = run_cli("agent", "show", "writer")
        self.assertIn("qwen3:8b", out)
        self.assertIn("New prompt", out)
        run_cli("agent", "edit", "writer", "--clear-model")
        code, out, _ = run_cli("agent", "show", "writer")
        self.assertIn("whichever model is loaded", out)

        code, _, _ = run_cli("agent", "edit", "writer")
        self.assertEqual(code, 1)

        with mock.patch("builtins.input", return_value="n"):
            code, _, _ = run_cli("agent", "delete", "writer")
        self.assertEqual(code, 1)
        code, _, _ = run_cli("agent", "delete", "writer", "-y")
        self.assertEqual(code, 0)
        code, out, _ = run_cli("agent", "list")
        self.assertIn("No agents yet", out)

    def test_invalid_and_missing_names(self):
        for bad in ("../x", ".hidden", "a b", ""):
            code, _, err = run_cli("agent", "create", bad)
            self.assertEqual(code, 1, bad)
            self.assertIn("Invalid agent name", err)
        code, _, err = run_cli("agent", "show", "ghost")
        self.assertEqual(code, 1)
        self.assertIn("does not exist", err)

    def test_agent_management_needs_no_server(self):
        with mock.patch.dict(os.environ, {"AXIS_URL": f"http://127.0.0.1:{free_port()}"}):
            code, _, _ = run_cli("agent", "create", "offline")
        self.assertEqual(code, 0)

    def test_ask_and_chat_as_agent(self):
        url = self.server(OllamaHandler, ["llama3.2:3b", "qwen3:8b"])
        run_cli("agent", "create", "poet", "-p", "Be poetic.", "-m", "qwen3:8b")
        code, out, _ = run_cli("--url", url, "ask", "-a", "poet", "moon")
        self.assertEqual(code, 0)
        self.assertEqual(LAST_REQUEST["model"], "qwen3:8b")
        self.assertEqual(LAST_REQUEST["messages"][0]["content"], "Be poetic.")
        # -m overrides the agent's model
        run_cli("--url", url, "ask", "-a", "poet", "-m", "llama3.2:3b", "x")
        self.assertEqual(LAST_REQUEST["model"], "llama3.2:3b")
        with mock.patch("builtins.input", side_effect=["hi", "/exit"]):
            code, out, _ = run_cli("--url", url, "chat", "-a", "poet")
        self.assertIn("agent 'poet'", out)

    def test_agent_model_not_loaded(self):
        url = self.server(OllamaHandler, ["llama3.2:3b"])
        run_cli("agent", "create", "picky", "-m", "mistral")
        code, _, err = run_cli("--url", url, "ask", "-a", "picky", "x")
        self.assertEqual(code, 1)
        self.assertIn("'mistral' is not loaded", err)


class TestRun(AxisTestCase):
    def test_run_once(self):
        for handler in (OllamaHandler, LlamaCppHandler):
            with self.subTest(handler=handler.__name__):
                url = self.server(handler, ["model-a"])
                name = "w" + handler.__name__.lower()
                run_cli("agent", "create", name)
                tasks = Path(self.home) / "agents" / name / "tasks"
                results = Path(self.home) / "agents" / name / "results"
                (tasks / "one.txt").write_text("summarize this", encoding="utf-8")
                (tasks / "two.md").write_text("FAIL please", encoding="utf-8")
                (tasks / "empty.txt").write_text("   ", encoding="utf-8")
                (tasks / "ignore.json").write_text("{}", encoding="utf-8")

                code, out, err = run_cli("--url", url, "run", name, "--once")
                self.assertEqual(code, 0, err)
                self.assertIn("echo: summarize this", (results / "one.md").read_text(encoding="utf-8"))
                self.assertTrue((tasks / "done" / "one.txt").exists())
                self.assertTrue((tasks / "failed" / "two.md").exists())
                self.assertTrue((results / "two.error.txt").exists())
                self.assertTrue((tasks / "failed" / "empty.txt").exists())
                self.assertTrue((tasks / "ignore.json").exists())

                # a second task with the same name does not overwrite the first result
                (tasks / "one.txt").write_text("again", encoding="utf-8")
                run_cli("--url", url, "run", name, "--once")
                self.assertEqual(len(list(results.glob("one*.md"))), 2)
                self.assertEqual(len(list((tasks / "done").glob("one*.txt"))), 2)

    def test_run_refuses_without_model(self):
        run_cli("agent", "create", "idle")
        url = self.server(OllamaHandler, [])
        code, _, err = run_cli("--url", url, "run", "idle", "--once")
        self.assertEqual(code, 1)
        self.assertIn("no model is loaded", err)

    def test_run_missing_agent(self):
        url = self.server(OllamaHandler, ["m"])
        code, _, err = run_cli("--url", url, "run", "ghost", "--once")
        self.assertEqual(code, 1)
        self.assertIn("does not exist", err)

    def test_run_loop_stops_on_ctrl_c(self):
        url = self.server(OllamaHandler, ["m"])
        run_cli("agent", "create", "loop")
        with mock.patch("axis.worker.time.sleep", side_effect=KeyboardInterrupt):
            code, out, _ = run_cli("--url", url, "run", "loop")
        self.assertEqual(code, 0)
        self.assertIn("Stopped.", out)


class TestParser(AxisTestCase):
    def test_version_and_help(self):
        for argv in (["--version"], ["--help"], ["agent", "--help"]):
            with self.assertRaises(SystemExit) as ctx, redirect_stdout(io.StringIO()):
                cli.main(argv)
            self.assertEqual(ctx.exception.code, 0)

    def test_missing_command(self):
        with self.assertRaises(SystemExit) as ctx, redirect_stderr(io.StringIO()):
            cli.main([])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
