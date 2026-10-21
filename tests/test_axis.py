"""End-to-end tests against mock Ollama and llama.cpp servers.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from axis import agents, backend, cli, runner, server, skills, tools

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
        self.env = mock.patch.dict(os.environ, {"AXIS_HOME": self.home})
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
        self.assertEqual(ids, ["run-python", "list-files", "read-files", "write-files", "delete-files"])
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
        self.assertEqual(len(json.loads(request(self.url + "/api/skills")[1])), 5)
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


if __name__ == "__main__":
    unittest.main()
