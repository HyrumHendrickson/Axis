"""End-to-end tests against mock Ollama and llama.cpp servers.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from axis import agents, appwindow, backend, cli, images, runner, server, shortcut, skills, tools

LAST_REQUEST: dict = {}
REQUESTS: list = []


def scripted_reply(body):
    """Decide what the fake model says.

    A user message "TOOL name {json}" makes the model call that tool (natively
    when tools were sent, or as a ```tool block when the system prompt asks for
    that format). After a tool result arrives, the model answers "done: <result>".
    Returns ("text", str) | ("call", name, args) | ("error", msg).
    """
    msgs = body["messages"]
    last = msgs[-1]
    users = [m.get("content") or "" for m in msgs if m["role"] == "user"]
    if any("LOOP" in u for u in users) or any("COUNT" in u for u in users):
        if last["content"].startswith("Stop using tools"):
            return ("text", "final answer")
        if any("LOOP" in u for u in users):
            return ("call", "list_files", {}) if body.get("tools") else ("text", '{"name": "list_files", "parameters": {}}')
        n = sum(1 for m in msgs if m["role"] == "tool")
        return ("call", "write_file", {"path": f"f{n}.txt", "content": str(n)})
    if last["role"] == "user" and last["content"].startswith("TEXTCALL "):
        return ("text", last["content"][len("TEXTCALL "):])
    if last["role"] == "tool" or (last["role"] == "user" and last["content"].startswith("Result of")):
        return ("text", "done: " + last["content"].splitlines()[-1 if last["role"] == "tool" else 1])
    user = last["content"]
    if "FAIL" in user:
        return ("error", "simulated failure")
    if user.startswith("TOOL "):
        _, name, args = user.split("\n")[0].split(" ", 2)
        if body.get("tools"):
            return ("call", name, json.loads(args))
        if "```tool" in msgs[0].get("content", ""):
            block = json.dumps({"name": name, "arguments": json.loads(args)})
            return ("text", "Let me do that.\n```tool\n" + block + "\n```")
    return ("text", f"echo: {user.splitlines()[0]}")


def reply_for(messages):
    kind = scripted_reply({"messages": messages})
    return None if kind[0] == "error" else kind[1]


class _Base(BaseHTTPRequestHandler):
    models: list = []
    no_tools = False

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
        REQUESTS.append(body)
        if body.get("tools") and self.no_tools:
            return self._json(400, {"error": f"registry.ollama.ai/library/{body['model']} does not support tools"})
        kind = scripted_reply(body)
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        if kind[0] == "error":
            self.wfile.write(json.dumps({"error": kind[1]}).encode() + b"\n")
            return
        if kind[0] == "call":
            msg = {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": kind[1], "arguments": kind[2]}}]}
            self.wfile.write(json.dumps({"message": msg, "done": False}).encode() + b"\n")
        else:
            for word in kind[1].split(" "):
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
        REQUESTS.append(body)
        if body.get("tools") and self.no_tools:
            return self._json(500, {"error": {"code": 500, "message": "tools param requires --jinja flag"}})
        kind = scripted_reply(body)
        if kind[0] == "error":
            return self._json(500, {"error": {"code": 500, "message": kind[1]}})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        if kind[0] == "call":
            args = json.dumps(kind[2])
            pieces = [{"index": 0, "id": "call_abc", "type": "function", "function": {"name": kind[1], "arguments": ""}}]
            pieces += [{"index": 0, "function": {"arguments": args[i:i + 7]}} for i in range(0, len(args), 7)]
            for p in pieces:
                self.wfile.write(b"data: " + json.dumps({"choices": [{"index": 0, "delta": {"tool_calls": [p]}}]}).encode() + b"\n\n")
        else:
            for word in kind[1].split(" "):
                chunk = {"choices": [{"index": 0, "delta": {"content": word + " "}}]}
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")


def start_server(handler, models, no_tools=False):
    cls = type(handler.__name__, (handler,), {"models": list(models), "no_tools": no_tools})
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
        self.env = mock.patch.dict(os.environ, {"AXIS_HOME": self.home, "AXIS_NO_ENGINE_INSTALL": "1",
                                                "AXIS_GITHUB": "http://127.0.0.1:9", "AXIS_GITHUB_API": "http://127.0.0.1:9",
                                                "AXIS_NO_SHORTCUT": "1", "XDG_DATA_HOME": os.path.join(self.home, "xdg")})
        self.env.start()
        os.environ.pop("AXIS_URL", None)
        self.servers = []
        REQUESTS.clear()
        runner._TEXT_MODE.clear()

    def tearDown(self):
        for s in self.servers:
            s.shutdown()
            s.server_close()
        self.env.stop()
        shutil.rmtree(self.home, ignore_errors=True)

    def model_server(self, handler, models, no_tools=False):
        srv, url = start_server(handler, models, no_tools)
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


def tool_msg(name, **args):
    return [{"role": "user", "content": f"TOOL {name} {json.dumps(args)}"}]


class TestSkillsAndAgents(Base):
    def test_skill_library(self):
        ids = [s["id"] for s in skills.list_all()]
        self.assertEqual(ids, ["run-python", "list-files", "read-files", "write-files", "delete-files",
                               "web-search", "wikipedia", "image-generation", "shared-folder"])
        s = skills.create({"name": "Plain English", "description": "Simple words", "instructions": "Avoid jargon."})
        self.assertEqual(s["id"], "plain-english")
        self.assertFalse(s["builtin"])
        with self.assertRaisesRegex(skills.SkillError, "already exists"):
            skills.create({"name": "plain english", "instructions": "x"})
        with self.assertRaisesRegex(skills.SkillError, "name"):
            skills.create({"name": "", "instructions": "x"})
        with self.assertRaisesRegex(skills.SkillError, "instructions"):
            skills.create({"name": "Empty"})
        skills.update("plain-english", {"instructions": "Use short words."})
        self.assertEqual(skills.get("plain-english")["instructions"], "Use short words.")
        self.assertEqual(skills.get("plain-english")["name"], "Plain English")
        for bad in (lambda: skills.update("run-python", {"name": "x"}), lambda: skills.delete("run-python")):
            with self.assertRaisesRegex(skills.SkillError, "Built-in"):
                bad()
        with self.assertRaises(skills.SkillError):
            skills.get("../../etc/passwd")
        skills.delete("plain-english")
        self.assertFalse(skills.exists("plain-english"))

    def test_agent_crud_and_workspace(self):
        skills.create({"name": "Brief", "instructions": "Be brief."})
        a = agents.create({"name": "editor", "system_prompt": "You edit.", "config": ["Rule.", " "],
                           "skills": ["brief", "write-files", "brief"]})
        self.assertEqual(a.skills, ["brief", "write-files"])
        self.assertEqual(a.config, ["Rule."])
        self.assertTrue(a.workspace.is_dir())
        self.assertEqual(a.workspace, Path(self.home) / "agents" / "editor" / "workspace")
        with self.assertRaisesRegex(agents.AgentError, "Unknown skill"):
            agents.update("editor", {"skills": ["nope"]})
        (a.workspace / "made.txt").write_text("x")

        b = agents.update("editor", {"name": "copyeditor"})
        self.assertTrue((b.workspace / "made.txt").is_file())  # folder moves with a rename
        self.assertEqual(agents.list_workspace(b), [{"path": "made.txt", "size": 1}])

        agents.delete("copyeditor")
        self.assertFalse((Path(self.home) / "agents" / "copyeditor").exists())  # workspace removed too
        self.assertEqual(agents.list_all(), [])

    def test_names_validated(self):
        for bad in ("../x", ".hidden", "a b", "", "x" * 65):
            with self.assertRaises(agents.AgentError):
                agents.create({"name": bad})

    def test_deleting_a_skill_removes_it_from_agents(self):
        skills.create({"name": "Tmp", "instructions": "x"})
        agents.create({"name": "a", "skills": ["tmp", "run-python"]})
        skills.delete("tmp")
        agents.remove_skill_everywhere("tmp")
        self.assertEqual(agents.load("a").skills, ["run-python"])

    def test_upgrades_older_agent_files(self):
        v1 = Path(self.home) / "agents" / "old"
        v1.mkdir(parents=True)
        (v1 / "agent.json").write_text(json.dumps({"name": "old", "system_prompt": "Hi", "model": ""}))
        self.assertEqual(agents.load("old").skills, [])
        v2 = Path(self.home) / "agents" / "mid"
        v2.mkdir(parents=True)
        (v2 / "agent.json").write_text(json.dumps({"name": "mid", "system_prompt": "Hi", "config": ["c"], "skills": [
            {"name": "Haiku", "instructions": "5-7-5", "enabled": True},
            {"name": "Off one", "instructions": "x", "enabled": False}]}))
        a = agents.load("mid")
        self.assertEqual(a.skills, ["haiku"])
        self.assertTrue(skills.exists("off-one"))  # kept in the library, just not chosen
        self.assertEqual(json.loads((v2 / "agent.json").read_text())["skills"], ["haiku"])

    def test_build_messages(self):
        skills.create({"name": "Grammar", "instructions": "Fix grammar."})
        skills.create({"name": "Unused", "instructions": "hidden"})
        a = agents.create({"name": "x", "system_prompt": "Base.", "config": ["One.", "Two."], "skills": ["grammar"]})
        history = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}, {"role": "user", "content": "q2"}]
        msgs = agents.build_messages(a, history)
        self.assertIn("## Grammar\nFix grammar.", msgs[0]["content"])
        self.assertNotIn("hidden", msgs[0]["content"])
        self.assertNotIn("own folder", msgs[0]["content"])  # no actions, no folder note
        self.assertEqual(msgs[1]["content"], "q1")
        self.assertIn("- One.\n- Two.", msgs[-1]["content"])
        self.assertEqual(history[-1]["content"], "q2")
        self.assertEqual(agents.build_messages(None, history), history)
        self.assertEqual(agents.tool_names(a), [])

        b = agents.create({"name": "y", "skills": ["write-files", "run-python"]})
        self.assertEqual(agents.tool_names(b), ["write_file", "make_folder", "run_python"])
        self.assertIn("own folder", agents.build_messages(b, history)[0]["content"])

    def test_long_history_is_trimmed_to_fit(self):
        history = []
        for i in range(60):
            history += [{"role": "user", "content": f"q{i} " + "x" * 500}, {"role": "assistant", "content": "a" * 500}]
        history.append({"role": "user", "content": "last"})
        a = agents.create({"name": "t", "system_prompt": "SYS"})
        msgs = agents.build_messages(a, history)
        self.assertEqual(msgs[0]["content"], "SYS")
        self.assertEqual(msgs[1]["role"], "user")
        self.assertEqual(msgs[-1]["content"], "last")
        self.assertLessEqual(sum(len(m["content"]) for m in msgs[1:]), agents.HISTORY_CHARS + 600)
        self.assertLess(len(msgs), len(history))

    def test_delete_removes_read_only_files(self):
        a = agents.create({"name": "ro"})
        f = a.workspace / "locked.txt"
        f.write_text("x")
        os.chmod(f, 0o444)
        os.chmod(a.workspace, 0o555)
        try:
            agents.delete("ro")
        finally:
            if a.workspace.exists():
                os.chmod(a.workspace, 0o755)
        self.assertFalse(a.folder.exists())

    def test_build_messages_rejects_bad_history(self):
        for bad in ([], None, [{"role": "assistant", "content": "x"}], [{"role": "system", "content": "x"}],
                    [{"role": "user", "content": 5}], [{"role": "user", "content": "  "}]):
            with self.assertRaises(agents.AgentError):
                agents.build_messages(None, bad)


class TestTools(Base):
    def setUp(self):
        super().setUp()
        self.ws = Path(self.home) / "ws"
        self.ws.mkdir()
        self.outside = Path(self.home) / "outside"
        self.outside.mkdir()
        self.all = list(tools.HANDLERS)

    def run_tool(self, name, **args):
        return tools.execute(self.ws, name, args, self.all)

    def test_file_tools(self):
        self.assertEqual(self.run_tool("write_file", path="notes/a.md", content="hi")[0], True)
        self.assertEqual(self.run_tool("read_file", path="notes/a.md"), (True, "hi"))
        self.assertIn("notes/", self.run_tool("list_files")[1])
        self.assertIn("a.md", self.run_tool("list_files", path="notes")[1])
        self.assertTrue(self.run_tool("make_folder", path="x/y")[0])
        self.assertFalse(self.run_tool("delete_file", path="notes")[0])  # not empty
        self.assertTrue(self.run_tool("delete_file", path="notes/a.md")[0])
        self.assertFalse(self.run_tool("read_file", path="missing.txt")[0])
        self.assertFalse(self.run_tool("read_file")[0])

    def test_paths_cannot_leave_the_folder(self):
        os.symlink(self.outside, self.ws / "link")
        for path in ("../outside/x.txt", str(self.outside / "x.txt"), "/etc/passwd", "C:/x.txt", "C:x.txt",
                     "link/x.txt", "notes/../../x.txt", "\\\\server\\share\\x"):
            ok, msg = self.run_tool("write_file", path=path, content="x")
            self.assertFalse(ok, path)
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_llama_name_variants(self):
        self.assertEqual(tools.normalize_call("python", {"script": "x"}, ["run_python"]), ("run_python", {"code": "x"}))
        self.assertEqual(tools.normalize_call("functions.create_file", {"filename": "a", "text": "b"}, ["write_file"]),
                         ("write_file", {"path": "a", "content": "b"}))
        self.assertEqual(tools.normalize_call("ls", {}, ["read_file"])[0], "ls")  # not allowed, not mapped
        self.assertEqual(tools.normalize_call("read_file", {"path": {"type": "string", "value": "a"}}, ["read_file"]),
                         ("read_file", {"path": "a"}))
        ok, out = tools.execute(self.ws, "mkdir", {"folder": "made"}, self.all)
        self.assertTrue(ok, out)
        self.assertTrue((self.ws / "made").is_dir())

    def test_escaped_newlines_in_code(self):
        self.assertEqual(tools.fix_escaped_code("a = 1\\nprint(a)"), "a = 1\nprint(a)")
        self.assertEqual(tools.fix_escaped_code("print('x\\ny')"), "print('x\\ny')")  # valid as is
        ok, out = self.run_tool("run_python", code="total = 0\\nfor i in range(4):\\n    total += i\\nprint(total)")
        self.assertIn("Output:\n6", out)

    def test_long_files_are_shortened_for_the_model(self):
        (self.ws / "big.txt").write_text("x" * 50000)
        ok, out = self.run_tool("read_file", path="big.txt")
        self.assertLess(len(out), tools.MAX_READ + 300)
        self.assertIn("file continues", out)

    def test_only_allowed_tools(self):
        ok, msg = tools.execute(self.ws, "run_python", {"code": "print(1)"}, ["read_file"])
        self.assertFalse(ok)
        self.assertIn("Unknown tool", msg)
        self.assertFalse(tools.execute(self.ws, "read_file", "notadict", self.all)[0])

    def test_run_python_stays_in_folder(self):
        ok, out = self.run_tool("run_python", code="open('in.txt','w').write('ok'); import os; os.makedirs('d', exist_ok=True); print('hello', 6*7)")
        self.assertTrue(ok)
        self.assertIn("hello 42", out)
        self.assertTrue((self.ws / "in.txt").is_file())
        escapes = [
            f"open(r'{self.outside / 'a.txt'}', 'w')",
            "import os; os.chdir('..'); open('b.txt', 'w')",
            f"import shutil; shutil.copy('in.txt', r'{self.outside / 'c.txt'}')",
            f"import os; os.rename('in.txt', r'{self.outside / 'd.txt'}')",
            f"import pathlib; pathlib.Path(r'{self.outside / 'e.txt'}').write_text('x')",
            "import subprocess; subprocess.run(['echo', 'hi'])",
            "import os; os.system('echo hi > /tmp/axis_escape.txt')",
        ]
        for code in escapes:
            ok, out = self.run_tool("run_python", code=code)
            self.assertIn("Exit code: 1", out, code)
            self.assertIn("Axis:", out, code)
        self.assertEqual(list(self.outside.iterdir()), [])
        self.assertTrue((self.ws / "in.txt").is_file())
        self.assertFalse((self.ws / ".axis-tmp").exists())

    def test_run_python_errors_and_timeouts(self):
        ok, out = self.run_tool("run_python", code="raise ValueError('boom')")
        self.assertIn("ValueError: boom", out)
        self.assertNotIn("<string>", out.split("agent_code.py")[0])
        with mock.patch.object(tools, "PYTHON_TIMEOUT", 1):
            ok, out = self.run_tool("run_python", code="import time; time.sleep(5)")
        self.assertIn("longer than 1 seconds", out)
        self.assertIn("printed nothing", self.run_tool("run_python", code="x = 1")[1])


class TestRunner(Base):
    def setUp(self):
        super().setUp()
        self.ws = Path(self.home) / "ws"
        self.ws.mkdir()

    def run_reply(self, url, messages, names=("write_file", "run_python")):
        conn = backend.connect(url)
        events = []
        runner.reply(conn, conn.models[0], [{"role": "system", "content": "S"}] + messages, events.append,
                     list(names), self.ws)
        return events

    def check_write(self, events):
        tool = [e for e in events if "tool" in e]
        result = [e for e in events if "tool_result" in e]
        self.assertEqual(tool[0]["tool"]["name"], "write_file")
        self.assertTrue(result[0]["tool_result"]["ok"])
        self.assertEqual((self.ws / "out.txt").read_text(), "line1\nline2")
        text = "".join(e.get("t", "") for e in events)
        self.assertIn("done: Wrote out.txt", text)
        self.assertNotIn("```tool", text)  # the raw block is never shown

    def test_native_ollama(self):
        url = self.model_server(OllamaHandler, ["m"])
        self.check_write(self.run_reply(url, tool_msg("write_file", path="out.txt", content="line1\nline2")))
        second = REQUESTS[-1]["messages"]
        self.assertEqual(second[-2]["tool_calls"][0]["function"]["name"], "write_file")
        self.assertEqual(second[-1], {"role": "tool", "content": "Wrote out.txt (11 bytes).", "tool_name": "write_file"})
        self.assertIn("tools", REQUESTS[0])

    def test_native_llamacpp(self):
        url = self.model_server(LlamaCppHandler, ["m"])
        self.check_write(self.run_reply(url, tool_msg("write_file", path="out.txt", content="line1\nline2")))
        second = REQUESTS[-1]["messages"]
        self.assertEqual(second[-2]["tool_calls"][0]["id"], "call_abc")
        self.assertEqual(json.loads(second[-2]["tool_calls"][0]["function"]["arguments"])["path"], "out.txt")
        self.assertEqual(second[-1]["tool_call_id"], "call_abc")

    def test_falls_back_to_text_tools(self):
        for handler in (OllamaHandler, LlamaCppHandler):
            with self.subTest(handler=handler.__name__):
                REQUESTS.clear()
                runner._TEXT_MODE.clear()
                if (self.ws / "out.txt").exists():
                    (self.ws / "out.txt").unlink()
                url = self.model_server(handler, ["m"], no_tools=True)
                events = self.run_reply(url, tool_msg("write_file", path="out.txt", content="line1\nline2"))
                self.check_write(events)
                self.assertIn("Let me do that.", "".join(e.get("t", "") for e in events))
                self.assertNotIn("tools", REQUESTS[-1])
                self.assertIn("```tool", REQUESTS[-1]["messages"][0]["content"])
                # remembered: the next reply skips the failed native attempt
                n = len(REQUESTS)
                self.run_reply(url, [{"role": "user", "content": "hi"}])
                self.assertEqual(len(REQUESTS), n + 1)

    def test_plain_reply_and_tool_errors(self):
        url = self.model_server(OllamaHandler, ["m"])
        events = self.run_reply(url, [{"role": "user", "content": "hello"}])
        self.assertEqual("".join(e.get("t", "") for e in events).strip(), "echo: hello")
        events = self.run_reply(url, tool_msg("read_file", path="../secret"), names=["read_file"])
        self.assertFalse([e for e in events if "tool_result" in e][0]["tool_result"]["ok"])
        events = self.run_reply(url, tool_msg("run_python", code="x"), names=["read_file"])
        self.assertIn("Unknown tool", [e for e in events if "tool_result" in e][0]["tool_result"]["output"])

    def test_repeated_action_is_not_rerun_and_ends_with_an_answer(self):
        url = self.model_server(OllamaHandler, ["m"])
        events = self.run_reply(url, [{"role": "user", "content": "LOOP"}], names=["list_files"])
        steps = [e for e in events if "tool_result" in e]
        self.assertEqual(len(steps), 3)  # one real run, two refused repeats
        self.assertIn("already did exactly this", steps[1]["tool_result"]["output"])
        self.assertEqual("".join(e.get("t", "") for e in events).strip(), "final answer")
        self.assertNotIn("tools", REQUESTS[-1])
        self.assertEqual(REQUESTS[-1]["messages"][-1]["content"], runner.FINAL_REQUEST)

    def test_step_limit_ends_with_an_answer(self):
        url = self.model_server(LlamaCppHandler, ["m"])
        events = self.run_reply(url, [{"role": "user", "content": "COUNT"}], names=["write_file"])
        self.assertEqual(len([e for e in events if "tool" in e]), runner.MAX_STEPS)
        self.assertEqual(len(list(self.ws.glob("f*.txt"))), runner.MAX_STEPS)
        self.assertEqual("".join(e.get("t", "") for e in events).strip(), "final answer")
        self.assertNotIn("tools", REQUESTS[-1])

    def test_llama_style_text_tool_calls_are_run(self):
        url = self.model_server(OllamaHandler, ["llama3.2:3b"])
        call = {"name": "python", "parameters": {"script": "open('t.txt','w').write('ok')\\nprint('made')"}}
        events = self.run_reply(url, [{"role": "user", "content": "TEXTCALL Sure. <|python_tag|>" + json.dumps(call)}])
        step = [e for e in events if "tool" in e][0]["tool"]
        self.assertEqual(step["name"], "run_python")
        result = [e for e in events if "tool_result" in e][0]["tool_result"]
        self.assertTrue(result["ok"])
        self.assertIn("made", result["output"])
        self.assertEqual((self.ws / "t.txt").read_text(), "ok")
        self.assertIn({"rewrite": "Sure."}, events)  # the raw JSON is replaced on the page
        self.assertIn("done:", "".join(e.get("t", "") for e in events))

    def test_text_mode_also_accepts_bare_json(self):
        url = self.model_server(OllamaHandler, ["llama3"], no_tools=True)
        call = {"name": "write_file", "parameters": {"filename": "a.md", "text": "# A"}}
        events = self.run_reply(url, [{"role": "user", "content": "TEXTCALL " + json.dumps(call)}])
        self.assertEqual((self.ws / "a.md").read_text(), "# A")
        self.assertIn({"rewrite": ""}, events)

    def test_agent_turns_use_low_temperature_and_bigger_context(self):
        url = self.model_server(OllamaHandler, ["m"])
        self.run_reply(url, [{"role": "user", "content": "hi"}])
        self.assertEqual(REQUESTS[-1]["options"], {"num_ctx": backend.NUM_CTX, "temperature": runner.TOOL_TEMPERATURE})
        conn = backend.connect(url)
        runner.reply(conn, "m", [{"role": "user", "content": "hi"}], lambda e: None)
        self.assertEqual(REQUESTS[-1]["options"], {"num_ctx": backend.NUM_CTX})
        url2 = self.model_server(LlamaCppHandler, ["m"])
        self.run_reply(url2, [{"role": "user", "content": "hi"}])
        self.assertEqual(REQUESTS[-1]["temperature"], runner.TOOL_TEMPERATURE)

    def test_hide_tool_blocks(self):
        for chunks in (["Hi ", "there\n```", "tool\n{}", "\n```"], ["Hi there\n`", "``tool {}"], ["Hi there\n``` code"]):
            out = []
            hider = runner.HideToolBlocks(out.append)
            for c in chunks:
                hider.feed(c)
            hider.flush()
            text = "".join(out)
            if "tool" in "".join(chunks):
                self.assertEqual(text, "Hi there\n")
            else:
                self.assertEqual(text, "Hi there\n``` code")

    def test_parse_tool_block(self):
        self.assertEqual(runner.parse_tool_block('```tool\n{"name": "a", "arguments": {"x": 1}}\n```'), ("a", {"x": 1}))
        self.assertEqual(runner.parse_tool_block('```tool\n{"name": "a", "arguments": "{\\"x\\": 1}"}'), ("a", {"x": 1}))
        self.assertEqual(runner.parse_tool_block("no block"), None)
        self.assertEqual(runner.parse_tool_block("```tool\nnot json\n```")[0], "")


# ------------------------------------------------------------------- server


def request(url, method="GET", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=20) as resp:
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
        self.assertEqual(json.loads(request(self.url + "/api/ping")[1])["app"], "axis")

    def test_status(self):
        st = json.loads(request(self.url + "/api/status")[1])
        self.assertTrue(st["ready"])
        self.assertEqual(st["models"], ["llama3.2:3b", "qwen3:8b"])
        down = self.axis_server(f"http://127.0.0.1:{free_port()}")
        st = json.loads(request(down + "/api/status")[1])
        self.assertEqual((st["ready"], st["server_running"]), (False, False))
        empty = self.axis_server(self.model_server(LlamaCppHandler, []))
        st = json.loads(request(empty + "/api/status")[1])
        self.assertEqual((st["ready"], st["server_running"]), (False, True))

    def test_skill_routes(self):
        self.assertEqual(len(json.loads(request(self.url + "/api/skills")[1])), len(skills.BUILTIN))
        code, text = request(self.url + "/api/skills", "POST", {"name": "Haiku", "instructions": "5-7-5"})
        self.assertEqual(code, 201, text)
        self.assertEqual(request(self.url + "/api/skills", "POST", {"name": "Haiku", "instructions": "x"})[0], 400)
        self.assertEqual(request(self.url + "/api/skills/haiku", "PUT", {"description": "Short poems"})[0], 200)
        self.assertEqual(json.loads(request(self.url + "/api/skills/haiku")[1])["description"], "Short poems")
        self.assertEqual(request(self.url + "/api/skills/run-python", "PUT", {"name": "x"})[0], 400)
        self.assertEqual(request(self.url + "/api/skills/run-python", "DELETE")[0], 400)
        request(self.url + "/api/agents", "POST", {"name": "poet", "skills": ["haiku"]})
        self.assertEqual(request(self.url + "/api/skills/haiku", "DELETE")[0], 200)
        self.assertEqual(json.loads(request(self.url + "/api/agents/poet")[1])["skills"], [])
        self.assertEqual(request(self.url + "/api/skills/haiku", "DELETE")[0], 404)

    def test_agent_routes(self):
        body = {"name": "poet", "system_prompt": "Be poetic.", "config": ["Rhyme."], "skills": ["list-files"]}
        code, text = request(self.url + "/api/agents", "POST", body)
        self.assertEqual(code, 201, text)
        self.assertTrue(json.loads(text)["workspace"].endswith("workspace"))
        self.assertEqual(request(self.url + "/api/agents", "POST", body)[0], 400)
        self.assertEqual(request(self.url + "/api/agents", "POST", {"name": "x", "skills": ["nope"]})[0], 400)
        code, text = request(self.url + "/api/agents/poet", "PUT", {"name": "bard"})
        self.assertEqual(code, 200, text)
        self.assertEqual(request(self.url + "/api/agents/poet")[0], 404)
        files = json.loads(request(self.url + "/api/agents/bard/files")[1])
        self.assertEqual(files["files"], [])
        with mock.patch.object(server, "open_folder") as opener:
            self.assertEqual(request(self.url + "/api/agents/bard/open-folder", "POST", {})[0], 200)
        opener.assert_called_once()
        self.assertEqual(request(self.url + "/api/agents/bard", "DELETE")[0], 200)
        self.assertFalse((Path(self.home) / "agents" / "bard").exists())
        self.assertEqual(request(self.url + "/api/agents/bard", "DELETE")[0], 404)
        self.assertEqual(request(self.url + "/api/nope")[0], 404)

    def test_files_routes(self):
        request(self.url + "/api/agents", "POST", {"name": "files"})  # a name that used to clash with a route
        self.assertEqual(request(self.url + "/api/agents/files")[0], 200)
        ws = Path(self.home) / "agents" / "files" / "workspace"
        (ws / "notes").mkdir()
        (ws / "notes" / "a.md").write_text("# Hello")
        (ws / "page.html").write_text("<script>alert(1)</script>")
        (ws / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 20)
        (ws / "blob.bin").write_bytes(b"\0\1\2" * 10)
        (ws / ".axis-tmp").mkdir()
        base = self.url + "/api/files/files"
        top = json.loads(request(base)[1])
        self.assertEqual([e["name"] for e in top["entries"]], ["notes", "blob.bin", "page.html", "pic.png"])
        self.assertEqual(top["entries"][0], {**top["entries"][0], "type": "dir", "items": 1, "path": "notes"})
        sub = json.loads(request(base + "?path=notes")[1])
        self.assertEqual(sub["path"], "notes")
        self.assertEqual(sub["entries"][0]["path"], "notes/a.md")
        view = json.loads(request(base + "/view?path=notes/a.md")[1])
        self.assertEqual((view["kind"], view["text"]), ("text", "# Hello"))
        self.assertEqual(json.loads(request(base + "/view?path=pic.png")[1])["kind"], "image")
        self.assertEqual(json.loads(request(base + "/view?path=blob.bin")[1])["kind"], "binary")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(base + "/raw?path=page.html") as resp:
            self.assertTrue(resp.headers["Content-Type"].startswith("text/plain"))
            self.assertIn("sandbox", resp.headers["Content-Security-Policy"])
        with opener.open(base + "/raw?path=pic.png&download=1") as resp:
            self.assertEqual(resp.headers["Content-Type"], "image/png")
            self.assertTrue(resp.headers["Content-Disposition"].startswith("attachment"))
        for bad in ("../agent.json", "../../skills", "/etc/passwd", "notes/../../agent.json"):
            self.assertEqual(request(base + "/view?path=" + urllib.parse.quote(bad))[0], 400, bad)
            self.assertIn(request(base + "?path=" + urllib.parse.quote(bad))[0], (400, 404), bad)
        self.assertEqual(request(base + "/view?path=missing.txt")[0], 404)
        self.assertEqual(request(self.url + "/api/files/ghost")[0], 404)
        with mock.patch.object(server, "reveal") as rv:
            self.assertEqual(request(base + "/reveal?path=notes/a.md", "POST", {})[0], 200)
            self.assertEqual(request(base + "/reveal?path=notes", "POST", {})[0], 200)
            self.assertEqual(request(base + "/reveal?path=", "POST", {})[0], 200)
        self.assertEqual(rv.call_count, 3)

    def test_chat_direct(self):
        ev = chat_events(request(self.url + "/api/chat", "POST", {"messages": [{"role": "user", "content": "hello"}]})[1])
        self.assertEqual(ev[0], {"model": "llama3.2:3b"})
        self.assertEqual("".join(e.get("t", "") for e in ev).strip(), "echo: hello")
        self.assertEqual(ev[-1], {"done": True})

    def test_chat_with_pictures(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
        base = self.url + "/api/files/_shared/upload?path=chat-images&mkdirs=1&name="
        code, body = request(base + "a.png", "POST")    # an empty upload makes the folder
        self.assertEqual(code, 201, body)
        req = urllib.request.Request(base + "pic.png", data=png, method="POST", headers={"Content-Type": "application/octet-stream"})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req) as resp:
            path = json.loads(resp.read())["path"]
        self.assertEqual(path, "chat-images/pic.png")
        pic = {"place": "_shared", "path": path}
        ev = chat_events(request(self.url + "/api/chat", "POST", {"messages": [
            {"role": "user", "content": "what is this?", "images": [pic]}]})[1])
        self.assertEqual(ev[-1], {"done": True}, ev)
        last = LAST_REQUEST["messages"][-1]
        self.assertEqual(last["images"], [base64.b64encode(png).decode()])
        # A message with only a picture is allowed; missing and unreadable pictures become notes.
        (Path(self.home) / "shared" / "chat-images" / "x.gif").write_bytes(b"GIF89a")
        ev = chat_events(request(self.url + "/api/chat", "POST", {"messages": [
            {"role": "user", "content": "", "images": [pic, {"place": "_shared", "path": "chat-images/gone.png"},
                                                       {"place": "_shared", "path": "chat-images/x.gif"}]}]})[1])
        self.assertEqual(ev[-1], {"done": True}, ev)
        last = LAST_REQUEST["messages"][-1]
        self.assertEqual(len(last["images"]), 1)
        self.assertIn("no longer available: chat-images/gone.png", last["content"])
        self.assertIn("could not be sent: chat-images/x.gif", last["content"])
        bad = request(self.url + "/api/chat", "POST", {"messages": [{"role": "user", "content": "x", "images": [{"path": ""}]}]})
        self.assertEqual(bad[0], 400)
        too_many = request(self.url + "/api/chat", "POST", {"messages": [{"role": "user", "content": "x", "images": [pic] * 5}]})
        self.assertEqual(too_many[0], 400)

    def test_only_newest_pictures_are_sent(self):
        shared = Path(self.home) / "shared"
        (shared / "p").mkdir(parents=True)
        history = []
        for i in range(3):
            for j in range(2):
                (shared / "p" / f"{i}{j}.png").write_bytes(b"\x89PNG\r\n\x1a\n%d%d" % (i, j))
            history += [{"role": "user", "content": f"q{i}", "images": [{"place": "_shared", "path": f"p/{i}{j}.png"} for j in range(2)]},
                        {"role": "assistant", "content": "ok"}]
        msgs = server.attach_images(agents.build_messages(None, history[:-1]))
        self.assertEqual([len(m.get("images") or []) for m in msgs if m["role"] == "user"], [0, 2, 2])
        self.assertIn("[earlier picture: p/00.png]", msgs[0]["content"])
        wired = backend.wire_messages("openai", msgs)
        parts = wired[-1]["content"]
        self.assertEqual(parts[0], {"type": "text", "text": "q2"})
        self.assertTrue(parts[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(backend.wire_messages("ollama", msgs)[-1]["content"], "q2")
        # An agent's standing instructions are added without losing the pictures.
        a = agents.create({"name": "seer", "config": ["Be brief."]})
        built = agents.build_messages(a, history[-2:-1])
        self.assertEqual(built[-1]["images"], history[-2]["images"])
        self.assertIn("Be brief.", built[-1]["content"])
        self.assertNotIn("tools", LAST_REQUEST)

    def test_chat_agent_with_tools_writes_into_its_folder(self):
        skills.create({"name": "Brief", "instructions": "Be brief."})
        request(self.url + "/api/agents", "POST", {"name": "maker", "model": "qwen3:8b", "config": ["C1"],
                                                    "skills": ["brief", "write-files", "run-python"]})
        msg = tool_msg("run_python", code="open('result.txt','w').write(str(6*7)); print('saved')")
        ev = chat_events(request(self.url + "/api/chat", "POST", {"agent": "maker", "messages": msg})[1])
        self.assertEqual(ev[0], {"model": "qwen3:8b"})
        self.assertEqual([e["tool"]["name"] for e in ev if "tool" in e], ["run_python"])
        self.assertIn("saved", [e for e in ev if "tool_result" in e][0]["tool_result"]["output"])
        self.assertEqual(ev[-1], {"done": True})
        ws = Path(self.home) / "agents" / "maker" / "workspace"
        self.assertEqual((ws / "result.txt").read_text(), "42")
        first = REQUESTS[0]
        self.assertEqual([t["function"]["name"] for t in first["tools"]], ["write_file", "make_folder", "run_python"])
        self.assertIn("Be brief.", first["messages"][0]["content"])
        self.assertIn("- C1", first["messages"][1]["content"])
        files = json.loads(request(self.url + "/api/agents/maker/files")[1])["files"]
        self.assertEqual(files, [{"path": "result.txt", "size": 2}])

    def test_chat_errors(self):
        ev = chat_events(request(self.url + "/api/chat", "POST", {"messages": [{"role": "user", "content": "FAIL"}]})[1])
        self.assertIn("simulated failure", ev[-1]["error"])
        ev = chat_events(request(self.url + "/api/chat", "POST", {"model": "mistral", "messages": [{"role": "user", "content": "x"}]})[1])
        self.assertIn("not loaded", ev[-1]["error"])
        self.assertEqual(request(self.url + "/api/chat", "POST", {"agent": "ghost", "messages": [{"role": "user", "content": "x"}]})[0], 404)
        self.assertEqual(request(self.url + "/api/chat", "POST", {"messages": []})[0], 400)

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
        resp.close()
        self.assertEqual(request(self.url + "/api/ping")[0], 200)


class TestModelsAndStats(Base):
    def test_describe_degrades_gracefully(self):
        info = backend.describe(self.model_server(OllamaHandler, ["llama3.2:3b"]))
        self.assertTrue(info["ready"])
        self.assertEqual([m["name"] for m in info["models"]], ["llama3.2:3b"])
        self.assertTrue(info["models"][0]["running"])
        info = backend.describe(self.model_server(LlamaCppHandler, ["m.gguf"]))
        self.assertEqual((info["kind"], info["models"][0]["name"]), ("openai", "m.gguf"))
        info = backend.describe(f"http://127.0.0.1:{free_port()}")
        self.assertEqual((info["ready"], info["models"]), (False, []))

    def test_ollama_metadata_and_memory_estimate(self):
        class Rich(OllamaHandler):
            def do_GET(self):
                if self.path == "/api/ps":
                    return self._json(200, {"models": [{"name": "a:1b", "size": 2000, "size_vram": 1000, "context_length": 4096}]})
                if self.path == "/api/tags":
                    return self._json(200, {"models": [{"name": "a:1b", "size": 1500, "modified_at": "x"},
                                                       {"name": "b:7b", "size": 4000, "modified_at": "y"}]})
                return super().do_GET()

            def do_POST(self):
                if self.path != "/api/show":
                    return super().do_POST()
                self._body()
                return self._json(200, {"capabilities": ["completion"], "model_info": {
                    "general.architecture": "llama", "llama.context_length": 131072, "llama.block_count": 2,
                    "llama.embedding_length": 64, "llama.attention.head_count": 4, "llama.attention.head_count_kv": 2}})
        info = backend.describe(self.model_server(Rich, ["a:1b"]))
        a, b = info["models"]
        self.assertEqual((a["name"], a["running"], a["gpu_percent"], a["context"], a["max_context"]), ("a:1b", True, 50, 4096, 131072))
        self.assertEqual((b["name"], b["running"]), ("b:7b", False))
        # 2 layers * 2 kv heads * (16 + 16) * 2 bytes * context
        self.assertEqual(b["estimated_cache"], 2 * 2 * 32 * 2 * b["axis_context"])
        self.assertEqual(b["estimated_memory"], 4000 + b["estimated_cache"])
        self.assertEqual(b["capabilities"], ["completion"])

    def test_llamacpp_metadata(self):
        class Rich(LlamaCppHandler):
            def do_GET(self):
                if self.path == "/v1/models":
                    return self._json(200, {"data": [{"id": "m", "meta": {"n_ctx_train": 8192, "n_params": 3e9, "size": 2e9}}]})
                if self.path == "/props":
                    return self._json(200, {"default_generation_settings": {"n_ctx": 4096}, "total_slots": 2, "modalities": {"vision": True}})
                return super().do_GET()
        m = backend.describe(self.model_server(Rich, ["m"]))["models"][0]
        self.assertEqual((m["context"], m["max_context"], m["slots"], m["memory"]), (4096, 8192, 2, 2e9))
        self.assertIn("vision", m["capabilities"])

    def test_token_stats(self):
        turn = backend.Turn("")
        backend._ollama_stats({"prompt_eval_count": 10, "eval_count": 5, "eval_duration": 2_000_000_000}, turn)
        self.assertEqual(turn.stats, {"prompt_tokens": 10, "output_tokens": 5, "seconds": 2.0})
        turn = backend.Turn("")
        backend._openai_stats({"timings": {"prompt_n": 3, "cache_n": 7, "predicted_n": 4, "predicted_ms": 500}}, turn)
        self.assertEqual(turn.stats, {"prompt_tokens": 10, "output_tokens": 4, "seconds": 0.5})

    def test_runner_emits_stats(self):
        class Counting(OllamaHandler):
            def do_POST(self):
                body = self._body()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"message": {"content": "hi"}, "done": False}).encode() + b"\n")
                self.wfile.write(json.dumps({"message": {"content": ""}, "done": True, "prompt_eval_count": 7,
                                             "eval_count": 1, "eval_duration": 10**9}).encode() + b"\n")
        conn = backend.connect(self.model_server(Counting, ["m"]))
        events = []
        runner.reply(conn, "m", [{"role": "user", "content": "x"}], events.append)
        self.assertIn({"stats": {"prompt_tokens": 7, "output_tokens": 1, "seconds": 1.0}}, events)


class TestFileManagement(Base):
    def setUp(self):
        super().setUp()
        self.url = self.axis_server(self.model_server(OllamaHandler, ["m"]))
        request(self.url + "/api/agents", "POST", {"name": "a"})
        self.ws = Path(self.home) / "agents" / "a" / "workspace"
        self.base = self.url + "/api/files/a"

    def upload(self, name, data, path=""):
        req = urllib.request.Request(f"{self.base}/upload?path={urllib.parse.quote(path)}&name={urllib.parse.quote(name)}",
                                     data=data, method="POST", headers={"Content-Type": "application/octet-stream"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_browse_shared_folder(self):
        shared = Path(self.home) / "shared"
        self.base = self.url + "/api/files/_shared"
        status, body = request(self.base + "?path=")
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body)["agent"], "_shared")
        self.assertEqual(self.upload("note.txt", b"for everyone")[0], 201)
        self.assertEqual((shared / "note.txt").read_bytes(), b"for everyone")
        status, body = request(self.base + "/view?path=missing.txt")
        self.assertEqual(status, 404)
        self.assertIn("the shared folder", body)
        self.assertEqual(request(self.base + "?path=../agents")[0], 400)

    def test_upload_never_overwrites(self):
        self.assertEqual(self.upload("data.csv", b"a,b\n1,2\n"), (201, {"path": "data.csv", "size": 8}))
        self.assertEqual(self.upload("data.csv", b"x")[1]["path"], "data (2).csv")
        self.assertEqual(self.upload(".env", b"x")[1]["path"], ".env")
        self.assertEqual(self.upload(".env", b"y")[1]["path"], ".env (2)")
        self.assertEqual((self.ws / "data.csv").read_bytes(), b"a,b\n1,2\n")
        self.assertFalse(any(p.name.endswith(".axis-upload") for p in self.ws.iterdir()))

    def test_upload_rejects_bad_names_and_places(self):
        for name in ("../x", "a/b", "..", "", ".axis-tmp", "c:x"):
            self.assertEqual(self.upload(name, b"x")[0], 400, name)
        self.assertEqual(self.upload("x", b"x", "../..")[0], 400)
        self.assertEqual(self.upload("x", b"x", "missing")[0], 404)

    def test_mkdir_and_delete(self):
        self.assertEqual(request(self.base + "/mkdir?path=reports/2026", "POST", {})[0], 201)
        self.assertTrue((self.ws / "reports" / "2026").is_dir())
        self.assertEqual(request(self.base + "/mkdir?path=reports", "POST", {})[0], 400)  # exists
        self.assertEqual(self.upload("r.txt", b"x", "reports/2026")[1]["path"], "reports/2026/r.txt")
        self.assertEqual(request(self.base + "?path=reports/2026/r.txt", "DELETE")[0], 200)
        self.assertFalse((self.ws / "reports" / "2026" / "r.txt").exists())
        self.assertEqual(request(self.base + "?path=reports", "DELETE")[0], 200)  # folder with contents
        self.assertFalse((self.ws / "reports").exists())
        for bad in ("", ".", "..", "../agent.json"):
            self.assertIn(request(self.base + "?path=" + urllib.parse.quote(bad), "DELETE")[0], (400, 404), bad)
        self.assertTrue((Path(self.home) / "agents" / "a" / "agent.json").is_file())
        self.assertTrue(self.ws.is_dir())

    def test_bad_content_length(self):
        conn = socket.create_connection(("127.0.0.1", int(self.url.rsplit(":", 1)[1])))
        host = self.url.split("//")[1]
        conn.sendall(f"POST /api/agents HTTP/1.0\r\nHost: {host}\r\nContent-Length: nope\r\n\r\n".encode())
        self.assertIn(b" 400 ", conn.recv(200))
        conn.close()

    def test_models_route(self):
        code, text = request(self.url + "/api/models")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(text)["models"][0]["name"], "m")


class TestChats(Base):
    def setUp(self):
        super().setUp()
        self.url = self.axis_server(self.model_server(OllamaHandler, ["m"]))
        request(self.url + "/api/agents", "POST", {"name": "poet"})
        self.msgs = [{"role": "user", "content": "Write   a poem\nabout rain"}, {"role": "assistant", "content": "Drip.", "parts": []}]

    def put(self, chat_id, agent, msgs=None, title=None):
        body = {"agent": agent, "messages": self.msgs if msgs is None else msgs}
        if title is not None:
            body["title"] = title
        return request(f"{self.url}/api/chats/{chat_id}", "PUT", body)

    def test_save_list_get_rename_delete(self):
        code, text = self.put("chat-0001", "poet")
        self.assertEqual(code, 200, text)
        self.assertEqual(json.loads(text)["title"], "Write a poem about rain")
        self.assertEqual(self.put("chat-0002", "")[0], 200)   # Direct chat is saved too
        listing = json.loads(request(self.url + "/api/chats")[1])
        self.assertEqual({(c["id"], c["agent"], c["messages"]) for c in listing}, {("chat-0001", "poet", 2), ("chat-0002", "", 2)})
        chat = json.loads(request(self.url + "/api/chats/chat-0001")[1])
        self.assertEqual(chat["messages"][1]["content"], "Drip.")
        created = chat["created"]
        self.put("chat-0001", "poet", self.msgs + [{"role": "user", "content": "more"}])
        chat = json.loads(request(self.url + "/api/chats/chat-0001")[1])
        self.assertEqual((len(chat["messages"]), chat["created"]), (3, created))
        code, text = request(self.url + "/api/chats/chat-0001", "PATCH", {"title": "Rain"})
        self.assertEqual(json.loads(text)["title"], "Rain")
        self.put("chat-0001", "poet", title="Rain")
        self.assertEqual(json.loads(request(self.url + "/api/chats/chat-0001")[1])["title"], "Rain")
        self.assertEqual(request(self.url + "/api/chats/chat-0001", "DELETE")[0], 200)
        self.assertEqual(request(self.url + "/api/chats/chat-0001")[0], 404)
        self.assertEqual(request(self.url + "/api/chats/chat-0001", "DELETE")[0], 404)

    def test_validation(self):
        self.assertEqual(self.put("chat-0001", "ghost")[0], 404)          # unknown agent
        self.assertEqual(self.put("../../x", "")[0], 404)
        self.assertEqual(self.put("short", "")[0], 404)
        self.assertEqual(self.put("chat-0001", "", [{"role": "system", "content": "x"}])[0], 400)
        self.assertEqual(self.put("chat-0001", "", "nope")[0], 400)
        self.assertEqual(request(self.url + "/api/chats/chat-9999")[0], 404)

    def test_chats_follow_their_agent(self):
        self.put("chat-0001", "poet")
        self.put("chat-0002", "")
        request(self.url + "/api/agents/poet", "PUT", {"name": "bard"})
        self.assertEqual(json.loads(request(self.url + "/api/chats/chat-0001")[1])["agent"], "bard")
        request(self.url + "/api/agents/bard", "DELETE")
        self.assertEqual(request(self.url + "/api/chats/chat-0001")[0], 404)   # deleted with its agent
        self.assertEqual(request(self.url + "/api/chats/chat-0002")[0], 200)   # other chats are kept


class TestToolRobustness(Base):
    def test_list_files_with_broken_link(self):
        ws = Path(self.home) / "ws"
        ws.mkdir()
        os.symlink(ws / "gone", ws / "dangling")
        ok, out = tools.execute(ws, "list_files", {}, ["list_files"])
        self.assertTrue(ok, out)
        self.assertIn("dangling", out)

    def test_huge_file_read_is_bounded(self):
        ws = Path(self.home) / "ws"
        ws.mkdir()
        (ws / "big.txt").write_text("y" * 2_000_000)
        ok, out = tools.execute(ws, "read_file", {"path": "big.txt"}, ["read_file"])
        self.assertTrue(ok)
        self.assertLess(len(out), tools.MAX_READ + 300)
        self.assertIn("file continues", out)


# ---------------------------------------------------------------------- cli


class TestSharedFolder(Base):
    def setUp(self):
        super().setUp()
        self.a = Path(self.home) / "a"
        self.b = Path(self.home) / "b"
        self.shared = Path(self.home) / "shared"
        for d in (self.a, self.b):
            d.mkdir()
        self.all = list(tools.FILE_TOOLS) + ["run_python"]

    def test_shared_paths_need_the_skill(self):
        ok, out = tools.execute(self.a, "write_file", {"path": "shared/x.txt", "content": "hi"}, self.all)
        self.assertTrue(ok)  # without the skill, shared/ is just a subfolder of the agent's own folder
        self.assertTrue((self.a / "shared" / "x.txt").is_file())
        self.assertFalse(self.shared.exists())

    def test_agents_exchange_files(self):
        ok, out = tools.execute(self.a, "write_file", {"path": "shared/inbox/b.md", "content": "hello b"}, self.all,
                                shared=self.shared)
        self.assertTrue(ok, out)
        self.assertIn("shared/inbox/b.md", out)
        self.assertEqual(tools.execute(self.b, "read_file", {"path": "shared/inbox/b.md"}, self.all, shared=self.shared),
                         (True, "hello b"))
        ok, listing = tools.execute(self.b, "list_files", {}, self.all, shared=self.shared)
        self.assertIn("shared/", listing)
        self.assertIn("inbox/", tools.execute(self.b, "list_files", {"path": "shared"}, self.all, shared=self.shared)[1])
        # personal files stay personal
        tools.execute(self.a, "write_file", {"path": "mine.txt", "content": "x"}, self.all, shared=self.shared)
        self.assertFalse(tools.execute(self.b, "read_file", {"path": "mine.txt"}, self.all, shared=self.shared)[0])

    def test_shared_folder_is_contained(self):
        for bad in ("shared/../a/secret.txt", "shared/../../x.txt", "shared"):
            ok, out = tools.execute(self.b, "write_file", {"path": bad, "content": "x"}, self.all, shared=self.shared)
            self.assertFalse(ok, bad)
        self.assertFalse(tools.execute(self.b, "delete_file", {"path": "shared"}, self.all, shared=self.shared)[0])
        self.assertTrue(self.shared.is_dir())

    def test_python_can_write_shared_only_when_enabled(self):
        code = "import os\nopen(os.path.join(os.environ['AXIS_SHARED_DIR'], 'p.txt'), 'w').write('py')\nprint('ok')"
        ok, out = tools.execute(self.a, "run_python", {"code": code}, self.all, shared=self.shared)
        self.assertIn("ok", out)
        self.assertEqual((self.shared / "p.txt").read_text(), "py")
        target = str(self.shared / "q.txt")
        ok, out = tools.execute(self.a, "run_python", {"code": f"open({target!r}, 'w').write('x')"}, self.all)
        self.assertIn("not allowed", out)
        self.assertFalse((self.shared / "q.txt").exists())

    def test_prompt_mentions_shared_folder_only_with_file_actions(self):
        a = agents.create({"name": "talker", "skills": ["shared-folder"]})
        system = agents.build_messages(a, [{"role": "user", "content": "hi"}])[0]["content"]
        self.assertNotIn("shared", system.lower())
        self.assertIsNone(agents.shared_folder(None))
        b = agents.create({"name": "filer", "skills": ["write-files", "shared-folder"]})
        system = agents.build_messages(b, [{"role": "user", "content": "hi"}])[0]["content"]
        self.assertIn("shared/", system)
        self.assertEqual(agents.shared_folder(b), Path(self.home) / "shared")
        c = agents.create({"name": "searcher", "skills": ["web-search"]})
        system = agents.build_messages(c, [{"role": "user", "content": "hi"}])[0]["content"]
        self.assertNotIn("own folder", system)
        self.assertEqual(agents.tool_names(c), ["web_search", "fetch_page"])


class TestWebTools(Base):
    DDG_HTML = (
        '<div class="result"><h2><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&amp;rut=1">'
        'Example <b>A</b></a></h2><a class="result__snippet" href="#">First <b>snippet</b> text.</a></div>'
        '<div class="result result--ad"><a class="result__a" href="https://duckduckgo.com/y.js?ad=1">Ad</a></div>'
        '<div class="result"><a class="result__a" href="https://example.org/b">Example B</a>'
        '<div class="result__snippet">Second snippet.</div></div>'
    )

    def run_tool(self, name, **args):
        return tools.execute(Path(self.home), name, args, list(tools.HANDLERS))

    def test_web_search_parses_and_respects_sizes(self):
        with mock.patch.object(tools.web_tools, "_http", return_value=(200, "text/html", self.DDG_HTML)):
            ok, out = self.run_tool("web_search", query="example")
            self.assertTrue(ok, out)
            self.assertIn("1. Example A\n   https://example.com/a\n   First snippet text.", out)
            self.assertIn("2. Example B", out)
            self.assertNotIn("y.js", out)
            ok, out = self.run_tool("web_search", query="example", max_results="1")
            self.assertNotIn("Example B", out)
            ok, out = self.run_tool("search", q="example", max_chars=200, count=10)  # aliases
            self.assertTrue(ok, out)
            self.assertLessEqual(len(out), 400)

    def test_web_search_rate_limited(self):
        with mock.patch.object(tools.web_tools, "_http", return_value=(202, "text/html", "<p>anomaly</p>")):
            ok, out = self.run_tool("web_search", query="x")
        self.assertFalse(ok)
        self.assertIn("limiting", out)

    def test_searxng(self):
        data = {"results": [{"title": "S", "url": "https://s.example", "content": "c"}]}
        with mock.patch.dict(os.environ, {"AXIS_SEARCH_URL": "http://127.0.0.1:9"}), \
                mock.patch.object(tools.web_tools, "_get_json", return_value=data) as got:
            ok, out = self.run_tool("web_search", query="q")
        self.assertIn("https://s.example", out)
        self.assertEqual(got.call_args[0][0], "http://127.0.0.1:9/search")

    def test_fetch_page_reads_in_pieces(self):
        html = ("<html><head><title>T</title><style>x{}</style></head><body><script>bad()</script>"
                "<h1>Head</h1><p>" + "word " * 200 + "</p></body></html>")
        with mock.patch.object(tools.web_tools, "_http", return_value=(200, "text/html", html)):
            ok, out = self.run_tool("fetch_page", url="https://example.com", max_chars=300)
            self.assertTrue(ok, out)
            self.assertIn("# Head", out)
            self.assertNotIn("bad()", out)
            self.assertIn("start=", out)
            nxt = int(out.rsplit("start=", 1)[1].split()[0])
            ok, out2 = self.run_tool("fetch_page", url="https://example.com", start=nxt)
            self.assertIn(f"Showing characters {nxt}-", out2)

    def test_fetch_page_refuses_local_addresses(self):
        for url in ("http://127.0.0.1:8765/api/agents", "http://localhost/", "file:///etc/passwd", "http://10.0.0.1/"):
            ok, out = self.run_tool("fetch_page", url=url)
            self.assertFalse(ok, url)

    def test_wikipedia(self):
        search = {"query": {"search": [{"title": "Cicero", "snippet": '<span class="searchmatch">Cicero</span> was', "wordcount": 9000}]}}
        article = {"query": {"pages": [{"title": "Cicero", "fullurl": "https://en.wikipedia.org/wiki/Cicero",
                                        "extract": "Marcus Tullius Cicero was a Roman statesman. " * 100}]}}
        with mock.patch.object(tools.web_tools, "_get_json", return_value=search) as got:
            ok, out = self.run_tool("wikipedia_search", query="Cicero", max_results=3, lang="la")
        self.assertIn("1. Cicero  (9000 words)\n   Cicero was", out)
        self.assertEqual(got.call_args[0][0], "https://la.wikipedia.org/w/api.php")
        self.assertEqual(got.call_args[0][1]["srlimit"], 3)
        with mock.patch.object(tools.web_tools, "_get_json", return_value=article) as got:
            ok, out = self.run_tool("wikipedia_article", title="Cicero", intro_only="true", max_chars=500)
        self.assertTrue(ok, out)
        self.assertEqual(got.call_args[0][1]["exintro"], 1)
        self.assertIn("https://en.wikipedia.org/wiki/Cicero", out)
        self.assertIn("more characters", out)
        with mock.patch.object(tools.web_tools, "_get_json", return_value={"query": {"pages": [{"title": "Nope", "missing": True}]}}):
            self.assertIn("no Wikipedia article", self.run_tool("wikipedia_article", title="Nope")[1])
        self.assertFalse(self.run_tool("wikipedia_search", query="x", lang="en.evil.com/")[0])


# ------------------------------------------------------------------ images

FAKE_SD = r'''
import os, sys
a = sys.argv[1:]
get = lambda k: a[a.index(k) + 1]
steps = int(get("--steps"))
for key in ("--diffusion-model", "--vae"):
    if key in a and not os.path.isfile(get(key)):
        print("missing", get(key)); sys.exit(2)
if "FAIL" in get("--prompt"):
    print("error: out of memory"); sys.exit(1)
print("loading tensors")
sys.stdout.write("  |=====| 300/300 - 1.0it/s\r")
print("\nsampling using Euler method")
if "--preview" in a and os.environ.get("FAKE_OLD_SD"):
    print("error: unknown argument: --preview"); sys.exit(1)
for i in range(1, steps + 1):
    if "--preview" in a:
        with open(get("--preview-path"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\nstep%d" % i + b"IEND\xaeB`\x82")
    sys.stdout.write("  |==| %d/%d - 0.10s/it\r" % (i, steps))
    sys.stdout.flush()
print("\ndecoding 1 latents")
with open(get("--output"), "wb") as f:
    f.write(b"\x89PNG\r\n\x1a\n" + " ".join(a).encode())
'''


class TestImages(Base):
    def setUp(self):
        super().setUp()
        root = Path(self.home) / "sd"
        (root / "bin").mkdir(parents=True)
        (root / "models").mkdir()
        (root / "models" / "z.gguf").write_bytes(b"x" * 10)
        (root / "models" / "ae.safetensors").write_bytes(b"y" * 5)
        script = root / "bin" / "fake_sd.py"
        script.write_text(FAKE_SD)
        if os.name == "nt":
            self.cli = root / "bin" / "sd-cli.cmd"
            self.cli.write_text(f'@"{sys.executable}" "{script}" %*\n')
        else:
            self.cli = root / "bin" / "sd-cli"
            self.cli.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
            self.cli.chmod(0o755)
        self.root = root

    def add_model(self, **extra):
        images.save_settings({"sd_cli": str(self.cli)})
        data = {"name": "Z-Image Turbo", "diffusion_model": "models/z.gguf", "vae": "models\\ae.safetensors".replace("\\", os.sep),
                "steps": 4, "cfg_scale": 1, "width": 384, "height": 384, "extra_args": "--flash-attn off,diffusion=on"}
        data.update(extra)
        return images.create_model(data)

    def test_settings_and_models(self):
        st = images.status()
        self.assertFalse(st["ready"])
        m = self.add_model()
        self.assertEqual(m["id"], "z-image-turbo")
        self.assertEqual(Path(images.workdir()), self.root)  # the folder above bin/
        self.assertEqual(m["missing"], [])
        self.assertEqual(m["disk_size"], 15)
        st = images.status()
        self.assertTrue(st["ready"])
        self.assertEqual(st["default_model"], "z-image-turbo")
        with self.assertRaises(images.ImageError):
            images.create_model({"name": "z-image turbo", "diffusion_model": "a"})  # same name
        with self.assertRaises(images.ImageError):
            images.create_model({"name": "Nothing"})  # no model file
        with self.assertRaises(images.ImageError):
            images.create_model({"name": "Bad", "model": "a.safetensors", "steps": 0})
        changed = images.update_model(m["id"], {"llm": "models/missing.gguf", "width": 500})
        self.assertEqual(changed["missing"], ["models/missing.gguf"])
        self.assertEqual(changed["width"], 496)  # rounded down to a multiple of 8
        with self.assertRaises(images.ImageError):
            images.generate({"prompt": "x"}, Path(self.home) / "shared")
        images.delete_model(m["id"])
        self.assertEqual(images.list_models(), [])

    def test_split_args_and_command(self):
        self.assertEqual(images.split_args('--flash-attn off,diffusion=on --x "C:\\a b\\c.gguf"'),
                         ["--flash-attn", "off,diffusion=on", "--x", "C:\\a b\\c.gguf"])
        m = self.add_model(sampler="euler")
        params = images.resolve_params(m, {"prompt": "hi", "seed": 7})
        cmd = images.build_command(self.cli, m, params, Path("out.png"), self.root)
        self.assertIn("--diffusion-model", cmd)
        self.assertEqual(cmd[cmd.index("--steps") + 1], "4")
        self.assertEqual(cmd[cmd.index("--cfg-scale") + 1], "1")
        self.assertEqual(cmd[cmd.index("--seed") + 1], "7")
        self.assertEqual(cmd[cmd.index("--sampling-method") + 1], "euler")
        self.assertIn("off,diffusion=on", cmd)
        self.assertGreaterEqual(images.resolve_params(m, {"prompt": "hi"})["seed"], 0)  # random seed chosen by Axis
        with self.assertRaises(images.ImageError):
            images.resolve_params(m, {"prompt": " "})
        with self.assertRaises(images.ImageError):
            images.resolve_params(m, {"prompt": "x", "sampler": "nope"})

    def test_generate_and_gallery(self):
        self.add_model()
        shared = Path(self.home) / "shared"
        events = []
        rec = images.generate({"prompt": "a lighthouse on a cliff", "width": 512, "height": 512}, shared, on_event=events.append)
        self.assertTrue((shared / rec["path"]).is_file())
        self.assertEqual((rec["width"], rec["steps"]), (512, 4))
        progress = [e["progress"] for e in events if "progress" in e]
        self.assertEqual(progress[-1], {"step": 4, "steps": 4})
        self.assertNotIn({"step": 300, "steps": 300}, progress)  # loading bars are not steps
        self.assertIn({"phase": "Sampling"}, events)
        written = (shared / rec["path"]).read_bytes().decode(errors="replace")
        self.assertIn(str(self.root / "models" / "z.gguf"), written)
        gallery = images.gallery(shared)
        self.assertEqual([g["prompt"] for g in gallery], ["a lighthouse on a cliff"])
        listing = tools.list_files(shared, {"path": "images"})
        self.assertNotIn(images.INDEX_NAME, listing)
        with self.assertRaises(images.ImageError) as ctx:
            images.generate({"prompt": "FAIL please"}, shared)
        self.assertIn("out of memory", str(ctx.exception))
        self.assertEqual(len(list((shared / "images").glob("*.png"))), 1)

    def test_step_previews(self):
        self.add_model()
        shared = Path(self.home) / "shared"
        events = []
        images.generate({"prompt": "a fox"}, shared, on_event=events.append)
        self.assertFalse([e for e in events if "preview" in e])   # off unless asked for
        events = []
        rec = images.generate({"prompt": "a fox"}, shared, on_event=events.append, preview=True)
        shots = [e["preview"] for e in events if "preview" in e]
        self.assertTrue(shots)
        self.assertTrue(all(s["image"].startswith("data:image/png;base64,") and s["steps"] == 4 for s in shots))
        self.assertEqual(base64.b64decode(shots[-1]["image"].split(",", 1)[1])[8:13], b"step4")
        self.assertEqual(len(list((shared / "images").glob("*.png"))), 2)   # the preview file is not kept
        self.assertTrue((shared / rec["path"]).is_file())
        cmd = images.build_command(self.cli, images.find_model(None), images.resolve_params(images.find_model(None), {"prompt": "x"}),
                                   Path("o.png"), self.root, Path("p.png"))
        self.assertEqual(cmd[cmd.index("--preview") + 1], "proj")
        with mock.patch.dict(os.environ, {"FAKE_OLD_SD": "1"}):          # an sd-cli without --preview
            events = []
            rec = images.generate({"prompt": "old"}, shared, on_event=events.append, preview=True)
        self.assertTrue((shared / rec["path"]).is_file())
        self.assertFalse([e for e in events if "preview" in e])

    def test_agent_step_previews(self):
        self.add_model()
        agent = agents.create({"name": "painter", "skills": ["image-generation"]})
        for preview in (False, True):
            events = []
            session = runner.Session(agent.workspace, ["generate_image"], events.append, image_preview=preview)
            out = session.run("generate_image", {"prompt": "a fox %s" % preview}, "0-0")
            self.assertTrue(out.startswith("Saved images/"), out)
            prog = [e["tool_progress"] for e in events if "tool_progress" in e]
            self.assertTrue(all(p["id"] == "0-0" for p in prog))
            self.assertIn({"id": "0-0", "progress": {"step": 4, "steps": 4}}, prog)
            self.assertEqual(any("preview" in p for p in prog), preview)

    def test_image_size_and_fit(self):
        d = Path(self.home)
        png = d / "a.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + (1000).to_bytes(4, "big") + (500).to_bytes(4, "big") + b"\0" * 20)
        self.assertEqual(images.image_size(png), (1000, 500))
        jpg = d / "a.jpg"
        jpg.write_bytes(b"\xff\xd8\xff\xe0\0\x10" + b"J" * 14 + b"\xff\xc0\0\x11\x08" + (300).to_bytes(2, "big")
                        + (640).to_bytes(2, "big") + b"\0" * 12)
        self.assertEqual(images.image_size(jpg), (640, 300))
        gif = d / "a.gif"
        gif.write_bytes(b"GIF89a" + (20).to_bytes(2, "little") + (10).to_bytes(2, "little"))
        self.assertEqual(images.image_size(gif), (20, 10))
        junk = d / "a.bin"
        junk.write_bytes(b"nothing")
        self.assertIsNone(images.image_size(junk))
        self.assertEqual(images.fit_size(1000, 500, 512 * 512), (704, 384))
        self.assertEqual(images.fit_size(512, 512, 1024 * 1024), (1024, 1024))
        self.assertEqual(images.fit_size(8000, 100, 1024 * 1024), (2048, 64))   # capped and never below 64

    def test_edit_picture(self):
        self.add_model()
        shared = Path(self.home) / "shared"
        (shared / "uploads").mkdir(parents=True)
        src = shared / "uploads" / "cat.png"
        src.write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + (800).to_bytes(4, "big") + (400).to_bytes(4, "big") + b"\0" * 20)
        rec = images.generate({"prompt": "a cat in snow", "strength": 0.4}, shared, init_image=src, init_name="_shared:uploads/cat.png")
        written = (shared / rec["path"]).read_bytes().decode(errors="replace")
        self.assertIn("--init-img " + str(src), written)
        self.assertIn("--strength 0.4", written)
        self.assertEqual((rec["width"], rec["height"]), (512, 256))     # the source's shape at about 384 x 384
        self.assertEqual((rec["strength"], rec["edited_from"]), (0.4, "_shared:uploads/cat.png"))
        self.assertNotIn("init_image", rec)
        self.assertEqual(images.gallery(shared)[0]["edited_from"], "_shared:uploads/cat.png")
        rec = images.generate({"prompt": "x", "width": 320, "height": 320}, shared, init_image=src)
        self.assertEqual((rec["width"], rec["strength"]), (320, images.DEFAULT_STRENGTH))   # a size given is used
        with self.assertRaises(images.ImageError):
            images.generate({"prompt": "x", "strength": 2}, shared, init_image=src)
        with self.assertRaises(images.ImageError):
            images.generate({"prompt": "x"}, shared, init_image=shared / "uploads" / "nope.png")
        (shared / "uploads" / "a.txt").write_text("hi")
        with self.assertRaisesRegex(images.ImageError, "cannot be edited"):
            images.generate({"prompt": "x"}, shared, init_image=shared / "uploads" / "a.txt")
        # Through the server, by {place, path}.
        url = self.axis_server(None)
        events = chat_events(request(url + "/api/images/generate", "POST", {"prompt": "a dog", "init_image": {"place": "_shared", "path": "uploads/cat.png"}})[1])
        image = next(e["image"] for e in events if "image" in e)
        self.assertEqual((image["edited_from"], image["source"]), ("_shared:uploads/cat.png", "image edit"))
        code, _ = request(url + "/api/images/generate", "POST", {"prompt": "a dog", "init_image": {"place": "_shared", "path": "../escape.png"}})
        self.assertEqual(code, 400)

    def test_edit_through_sd_server(self):
        src = Path(self.home) / "s.png"
        src.write_bytes(b"\x89PNG\r\n\x1a\nsource")
        params = {"prompt": "p", "negative_prompt": "", "width": 64, "height": 64, "steps": 2, "cfg_scale": 1.0,
                  "seed": 1, "sampler": "", "init_image": str(src), "strength": 0.5}
        sent = {}

        def fake(url, body=None, timeout=30.0):
            if body is not None and url.endswith("/img_gen"):
                sent.update(body)
                return {"id": "job1"}
            return {"status": "completed", "result": {"images": [{"b64_json": base64.b64encode(b"\x89PNGdone").decode()}]}}
        with mock.patch.object(images, "_server_json", fake):
            images._generate_with_server("http://x", params, Path(self.home) / "out.png", lambda e: None)
        self.assertEqual(sent["strength"], 0.5)
        self.assertEqual(sent["init_image"], "data:image/png;base64," + base64.b64encode(src.read_bytes()).decode())

    def test_cancel_kills_sd_cli(self):
        self.add_model()

        def stop(event):
            if "progress" in event:
                raise RuntimeError("browser left")

        with self.assertRaises(RuntimeError):
            images.generate({"prompt": "x"}, Path(self.home) / "shared", on_event=stop)
        self.assertFalse(images._LOCK.locked())
        self.assertEqual(list((Path(self.home) / "shared" / "images").glob("*.png")), [])

    def test_agent_tool(self):
        self.add_model()
        self.assertIn("image-generation", skills.BUILTIN_IDS)
        agent = agents.create({"name": "artist", "skills": ["image-generation"]})
        self.assertEqual(agents.tool_names(agent), ["generate_image"])
        ok, out = tools.execute(agent.workspace, "create_image", {"description": "a red fox"}, ["generate_image"])
        self.assertTrue(ok, out)
        self.assertTrue(out.startswith("Saved images/"), out)
        self.assertEqual(len(list((agent.workspace / "images").glob("*.png"))), 1)
        self.assertEqual([f["path"].startswith("images/") for f in agents.list_workspace(agent)], [True])
        ok, out = tools.execute(agent.workspace, "generate_image", {"prompt": "x", "model": "nope"}, ["generate_image"])
        self.assertFalse(ok)
        self.assertIn("Unknown image model", out)

    def test_routes(self):
        url = self.axis_server(self.model_server(OllamaHandler, ["llama3.1:8b"]))
        st = json.loads(request(url + "/api/images")[1])
        self.assertFalse(st["ready"])
        self.assertEqual(request(url + "/api/images/settings", "PUT", {"sd_cli": str(self.cli)})[0], 200)
        body = {"name": "Z-Image Turbo", "diffusion_model": "models/z.gguf", "steps": 3, "cfg_scale": 1}
        code, text = request(url + "/api/images/models", "POST", body)
        self.assertEqual(code, 201, text)
        self.assertEqual(request(url + "/api/images/models", "POST", body)[0], 400)
        self.assertEqual(request(url + "/api/images/models/z-image-turbo", "PUT", {"steps": 2})[0], 200)
        code, text = request(url + "/api/images/generate", "POST", {"prompt": "a cat", "width": 256, "height": 256})
        events = chat_events(text)
        self.assertEqual(events[-1], {"done": True}, events)
        image = next(e["image"] for e in events if "image" in e)
        self.assertEqual((image["place"], image["steps"], image["width"]), ("_shared", 2, 256))
        gallery = json.loads(request(url + "/api/images/gallery?place=_shared")[1])["images"]
        self.assertEqual(gallery[0]["path"], image["path"])
        listing = json.loads(request(url + "/api/files/_shared?path=images")[1])
        self.assertEqual([e["name"] for e in listing["entries"]], [image["file"]])
        view = json.loads(request(url + "/api/files/_shared/view?path=" + urllib.parse.quote(image["path"]))[1])
        self.assertEqual(view["kind"], "image")
        events = chat_events(request(url + "/api/images/generate", "POST", {"prompt": " "})[1])
        self.assertIn("error", events[-1])
        self.assertEqual(request(url + "/api/images/models/z-image-turbo", "DELETE")[0], 200)
        self.assertEqual(request(url + "/api/images/models/z-image-turbo", "DELETE")[0], 404)

    def test_agent_chat_uses_tool(self):
        self.add_model()
        url = self.axis_server(self.model_server(OllamaHandler, ["llama3.1:8b"]))
        request(url + "/api/agents", "POST", {"name": "artist", "skills": ["image-generation"]})
        msg = 'TOOL generate_image {"prompt": "a red fox in snow"}'
        events = chat_events(request(url + "/api/chat", "POST", {"agent": "artist", "messages": [{"role": "user", "content": msg}]})[1])
        result = next(e["tool_result"] for e in events if "tool_result" in e)
        self.assertTrue(result["ok"], result)
        self.assertRegex(result["output"], r"^Saved images/\S+\.png ")


class TestCli(Base):
    def test_version_and_help(self):
        for argv in (["--version"], ["--help"], ["update", "--help"], ["uninstall", "--help"]):
            with self.assertRaises(SystemExit) as ctx, redirect_stdout(io.StringIO()):
                cli.main(argv)
            self.assertEqual(ctx.exception.code, 0)

    def test_bad_port(self):
        with redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["--port", "70000", "--no-browser"]), 2)

    def test_detects_running_axis(self):
        url = self.axis_server(None)
        port = int(url.rsplit(":", 1)[1])
        self.assertTrue(server.find_running(port))
        self.assertFalse(server.find_running(free_port()))
        with redirect_stdout(io.StringIO()) as out, mock.patch.object(appwindow, "open_app") as opened:
            self.assertEqual(cli.main(["--port", str(port)]), 0)
        self.assertIn("already running", out.getvalue())
        opened.assert_called_once_with(f"http://127.0.0.1:{port}", tab=False)
        with redirect_stdout(io.StringIO()), mock.patch.object(appwindow, "open_app") as opened:
            self.assertEqual(cli.main(["--port", str(port), "--tab"]), 0)
        opened.assert_called_once_with(f"http://127.0.0.1:{port}", tab=True)

    def test_starts_and_stops(self):
        port = free_port()
        with redirect_stdout(io.StringIO()) as out, \
                mock.patch.object(server.AxisServer, "serve_forever", side_effect=KeyboardInterrupt), \
                mock.patch("threading.Timer") as timer:
            self.assertEqual(cli.main(["--port", str(port)]), 0)
        self.assertIn(f"http://127.0.0.1:{port}", out.getvalue())
        self.assertIn("Axis stopped", out.getvalue())
        timer.assert_called_once()

    def test_update_runs_pip_with_github_zip(self):
        calls = []
        def fake_run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="9.9.9\n")
        with mock.patch("subprocess.run", side_effect=fake_run), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["update"]), 0)
        self.assertEqual(calls[0][:4], [sys.executable, "-m", "pip", "install"])
        self.assertIn("--force-reinstall", calls[0])
        self.assertEqual(calls[0][-1], cli.REPO_ZIP)
        self.assertIn("-> 9.9.9", out.getvalue())
        with mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 1)), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(["update", "--source", "x.zip"]), 1)
        self.assertIn("pipx install --force", err.getvalue())

    def test_uninstall(self):
        data = Path(self.home)
        (data / "agents").mkdir(exist_ok=True)
        with mock.patch("builtins.input", return_value="n"), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["uninstall"]), 1)  # cancelled
        with mock.patch.object(cli, "installed_with_pipx", return_value=False), \
                mock.patch.object(cli, "is_windows", return_value=False), \
                mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run, \
                mock.patch("builtins.input", side_effect=["y", "n"]), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["uninstall"]), 0)
        self.assertEqual(run.call_args[0][0][:4], [sys.executable, "-m", "pip", "uninstall"])
        self.assertEqual(run.call_args[0][0][-1], "axis-agent-manager")
        self.assertTrue(data.exists())
        self.assertIn("Kept your data", out.getvalue())
        with mock.patch.object(cli, "installed_with_pipx", return_value=True), \
                mock.patch.object(cli, "find_pipx", return_value=["pipx"]), \
                mock.patch.object(cli, "is_windows", return_value=False), \
                mock.patch("subprocess.run", return_value=subprocess.CompletedProcess([], 0)) as run, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["uninstall", "-y", "--delete-data"]), 0)
        self.assertEqual(run.call_args[0][0], ["pipx", "uninstall", "axis-agent-manager"])
        self.assertFalse(data.exists())

    def test_uninstall_on_windows_uses_helper(self):
        with mock.patch.object(cli, "installed_with_pipx", return_value=False), \
                mock.patch.object(cli, "is_windows", return_value=True), \
                mock.patch("subprocess.Popen") as popen, redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["uninstall", "-y"]), 0)
        cmd = popen.call_args[0][0]
        self.assertTrue(cmd[1].endswith("axis_uninstall_helper.py"))
        self.assertEqual(cmd[2], str(os.getpid()))
        self.assertIn("finish uninstalling", out.getvalue())


class TestShortcut(Base):
    def test_quit_endpoint_stops_server(self):
        srv = server.make_server(0, None)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        status, _ = request(srv.url + "/api/quit", "POST", {})
        self.assertEqual(status, 200)
        t.join(10)
        self.assertFalse(t.is_alive())
        srv.server_close()

    def test_axis_quit_stops_every_copy(self):
        from axis import instances
        servers = []
        for _ in range(2):
            srv = server.make_server(0, None)
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            port = srv.server_address[1]
            rec = instances._folder() / f"{os.getpid()}-{port}.json"   # one process here, so name by port too
            rec.parent.mkdir(parents=True, exist_ok=True)
            rec.write_text(json.dumps({"pid": os.getpid(), "port": port}), encoding="utf-8")
            servers.append((srv, t, rec))
        # Both records name this test process, so the pid check passes for both.
        self.assertEqual(len(instances.find([])), 2)
        with mock.patch.object(cli.manager, "shutdown", return_value=["m:1b"]) as stop, \
                redirect_stdout(io.StringIO()) as out:
            code = cli.main(["quit", "--timeout", "10"])
        self.assertEqual(code, 0)
        for srv, t, _ in servers:
            t.join(10)
            self.assertFalse(t.is_alive())
            srv.server_close()
        stop.assert_called_once()
        self.assertIn("Stopped 2 copies of Axis", out.getvalue())
        self.assertIn("Unloaded from Ollama: m:1b", out.getvalue())

    def test_axis_quit_with_nothing_running(self):
        with mock.patch.object(cli.manager, "shutdown", return_value=[]), \
                mock.patch.object(cli.instances, "ping", return_value=None), \
                redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["quit"]), 0)
        self.assertIn("No Axis was running", out.getvalue())

    def test_quit_refuses_other_sites(self):
        url = self.axis_server(None)
        status, _ = request(url + "/api/quit", "POST", {}, {"Origin": "http://evil.example"})
        self.assertEqual(status, 403)

    def test_icons_are_packaged(self):
        for name in ("axis.ico", "axis.icns", "axis.png"):
            self.assertTrue((shortcut.ICONS / name).is_file(), name)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux desktop entry")
    def test_linux_entry_create_and_remove(self):
        made = shortcut.create()
        entry = Path(made[0])
        text = entry.read_text()
        self.assertIn(f'Exec="{sys.executable}" -m axis --app', text)
        self.assertIn("Terminal=false", text)
        self.assertIn(str(shortcut.ICONS / "axis.png"), text)
        self.assertEqual(shortcut.remove(), [str(entry)])
        self.assertFalse(entry.exists())

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux desktop entry")
    def test_first_run_only(self):
        with mock.patch.dict(os.environ, {"AXIS_NO_SHORTCUT": "0"}):
            self.assertEqual(len(shortcut.ensure_once()), 1)
            shortcut.remove()                          # the user removed it on purpose
            self.assertEqual(shortcut.ensure_once(), [])  # so it is not put back
            shortcut.remove(forget=True)               # uninstall forgets it
            self.assertEqual(len(shortcut.ensure_once()), 1)

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux desktop entry")
    def test_shortcut_command(self):
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["shortcut"]), 0)
            self.assertEqual(cli.main(["shortcut", "--remove"]), 0)
        self.assertIn("axis command still works", out.getvalue())
        self.assertIn("Removed", out.getvalue())

    def test_app_mode_logs_to_file(self):
        port = free_port()
        stdout, stderr = sys.stdout, sys.stderr
        try:
            with mock.patch.object(server.AxisServer, "serve_forever", side_effect=KeyboardInterrupt), \
                    mock.patch("threading.Timer"):
                self.assertEqual(cli.main(["--app", "--port", str(port)]), 0)
            sys.stdout.flush()
        finally:
            log, sys.stdout, sys.stderr = sys.stdout, stdout, stderr
            log.close()
        text = (Path(self.home) / "axis.log").read_text()
        self.assertIn("Quit Axis", text)
        self.assertIn("Axis stopped", text)


class TestAppWindow(Base):
    def setUp(self):
        super().setUp()
        self._env = mock.patch.dict(os.environ, {"AXIS_BROWSER": ""})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        super().tearDown()

    def fake_browser(self) -> str:
        path = Path(self.home) / "fake-chrome"
        path.write_text("")
        return str(path)

    def test_browser_override_and_tab(self):
        browser = self.fake_browser()
        with mock.patch.dict(os.environ, {"AXIS_BROWSER": browser}):
            self.assertEqual(appwindow.find_browser(), browser)
        with mock.patch.dict(os.environ, {"AXIS_BROWSER": "tab"}):
            self.assertIsNone(appwindow.find_browser())

    def test_finds_installed_browser(self):
        browser = self.fake_browser()
        with mock.patch.object(appwindow, "_candidates", return_value=["/nowhere/chrome", browser]):
            self.assertEqual(appwindow.find_browser(), browser)
        with mock.patch.object(appwindow, "_candidates", return_value=["/nowhere/chrome"]):
            self.assertIsNone(appwindow.find_browser())

    def test_opens_app_window(self):
        with mock.patch.object(appwindow, "find_browser", return_value="/x/chrome"), \
                mock.patch("subprocess.Popen") as popen, mock.patch("webbrowser.open") as wb:
            self.assertEqual(appwindow.open_app("http://127.0.0.1:8765"), "window")
        self.assertEqual(popen.call_args[0][0], ["/x/chrome", "--app=http://127.0.0.1:8765"])
        wb.assert_not_called()

    def test_falls_back_to_tab(self):
        with mock.patch.object(appwindow, "find_browser", return_value=None), mock.patch("webbrowser.open") as wb:
            self.assertEqual(appwindow.open_app("http://127.0.0.1:8765"), "tab")
        wb.assert_called_once_with("http://127.0.0.1:8765")
        with mock.patch.object(appwindow, "find_browser", return_value="/x/chrome"), \
                mock.patch("subprocess.Popen", side_effect=OSError("gone")), \
                mock.patch("webbrowser.open") as wb, redirect_stdout(io.StringIO()):
            self.assertEqual(appwindow.open_app("http://127.0.0.1:8765"), "tab")
        wb.assert_called_once()
        with mock.patch.object(appwindow, "find_browser") as find, mock.patch("webbrowser.open"):
            self.assertEqual(appwindow.open_app("http://127.0.0.1:8765", tab=True), "tab")
        find.assert_not_called()


class TestCloseWithWindow(Base):
    def test_pages(self):
        pages = server.Pages()
        self.assertFalse(pages.all_closed(100.0, stale=60, grace=5))   # no page has opened yet
        pages.beat("a", now=100.0)
        pages.beat("b", now=100.0)
        pages.gone("a", now=101.0)
        self.assertFalse(pages.all_closed(110.0, stale=60, grace=5))   # b is still open
        pages.gone("b", now=111.0)
        self.assertFalse(pages.all_closed(113.0, stale=60, grace=5))   # maybe a reload
        pages.beat("c", now=114.0)                                     # it was
        self.assertFalse(pages.all_closed(120.0, stale=60, grace=5))
        self.assertTrue(pages.all_closed(200.0, stale=60, grace=5))    # c stopped reporting

    def test_page_endpoint(self):
        srv = server.make_server(0, None)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            status, body = request(srv.url + "/api/page?id=p1", "POST", {})
            self.assertEqual((status, json.loads(body)["closeWithWindow"]), (200, False))
            self.assertEqual(srv.pages.count(), 1)
            srv.close_with_window = True
            status, body = request(srv.url + "/api/page?id=p1&closing=1", "POST", {})
            self.assertTrue(json.loads(body)["closeWithWindow"])
            self.assertEqual(srv.pages.count(), 0)
            status, _ = request(srv.url + "/api/page", "POST", {})
            self.assertEqual(status, 400)
            status, _ = request(srv.url + "/api/page?id=p2", "POST", {}, {"Origin": "http://evil.example"})
            self.assertEqual(status, 403)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_closing_window_stops_models_then_axis(self):
        srv = server.make_server(0, None)
        serving = threading.Thread(target=srv.serve_forever, daemon=True)
        serving.start()
        order = []
        real_shutdown = srv.shutdown
        try:
            with mock.patch.object(cli.manager, "shutdown", side_effect=lambda *a, **k: order.append("models")), \
                    mock.patch.object(srv, "shutdown", side_effect=lambda: (order.append("axis"), real_shutdown())), \
                    redirect_stdout(io.StringIO()) as out:
                request(srv.url + "/api/page?id=w", "POST", {})
                watcher = cli._watch_window(srv, grace=0.2, stale=60, every=0.05)
                time.sleep(0.4)
                self.assertTrue(watcher.is_alive())        # the window is still open
                request(srv.url + "/api/page?id=w&closing=1", "POST", {})
                watcher.join(5)
            self.assertFalse(watcher.is_alive())
            serving.join(5)
            self.assertFalse(serving.is_alive())
            self.assertEqual(order, ["models", "axis"])
            self.assertIn("window was closed", out.getvalue())
        finally:
            srv.server_close()

    def test_window_mode_turns_on_watching(self):
        srv = mock.Mock(url="http://127.0.0.1:1")
        with mock.patch.object(appwindow, "open_app", return_value="window"), \
                mock.patch.object(cli, "_watch_window") as watch, redirect_stdout(io.StringIO()):
            cli._open_window(srv, False)
        self.assertTrue(srv.close_with_window)
        watch.assert_called_once_with(srv)
        srv = mock.Mock(url="http://127.0.0.1:1", close_with_window=False)
        with mock.patch.object(appwindow, "open_app", return_value="tab"), \
                mock.patch.object(cli, "_watch_window") as watch:
            cli._open_window(srv, True)
        self.assertFalse(srv.close_with_window)
        watch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
