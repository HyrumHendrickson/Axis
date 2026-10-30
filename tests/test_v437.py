"""Tests for Axis 4.3.7: quitting everything, calling agents, a folder for each picture, Ollama as an engine,
uninstalling what Axis installed, and refusing downloads that have no checksum.

Everything runs against local fakes: no network and no model are needed.
"""

from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from axis import agents, backend, cas, engines, images, instances, jobs, manager, ocr, procs, runner, skills, subagents, tools  # noqa: E402
from test_axis import Base  # noqa: E402

POSIX = os.name != "nt"


# ------------------------------------------------------------------ quitting


class TestQuitStopsEverything(Base):
    @unittest.skipUnless(POSIX, "uses process groups")
    def test_tracked_programs_and_what_they_started_are_ended(self):
        marker = Path(self.home) / "child.pid"
        code = f"import os,subprocess,time;p=subprocess.Popen(['sleep','60']);open(r'{marker}','w').write(str(p.pid));time.sleep(60)"
        proc = procs.track(subprocess.Popen([sys.executable, "-c", code], start_new_session=True))
        for _ in range(100):
            if marker.exists() and marker.read_text():
                break
            time.sleep(0.05)
        grandchild = int(marker.read_text())
        self.assertEqual(procs.stop_children(), 1)
        self.assertIsNotNone(proc.poll())
        for _ in range(40):
            if not procs._pid_alive(grandchild):
                break
            time.sleep(0.05)
        self.assertFalse(procs._pid_alive(grandchild))
        self.assertEqual(procs.tracked(), [])

    def test_kill_tree_leaves_axis_alone_when_the_program_shares_its_group(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"])
        self.addCleanup(proc.kill)
        procs.kill_tree(proc)           # a program without a group of its own is killed alone
        self.assertIsNotNone(proc.poll())

    def test_shutdown_cancels_jobs_closes_terminals_unloads_and_stops_programs(self):
        order = []
        with mock.patch.object(jobs, "cancel_all", side_effect=lambda *a: order.append("jobs")), \
                mock.patch.object(manager.terminal, "close_all", side_effect=lambda: order.append("terminals")), \
                mock.patch.object(manager, "unload_all_ollama", side_effect=lambda urls: (order.append("unload"), ["m:1b"])[1]), \
                mock.patch.object(manager.procs, "stop_all", side_effect=lambda: order.append("stop")):
            self.assertEqual(manager.shutdown("http://127.0.0.1:9"), ["m:1b"])
        self.assertEqual(order, ["jobs", "terminals", "unload", "stop"])   # models are unloaded before Ollama stops

    def test_jobs_are_cancelled_on_quit(self):
        seen = []

        def work(job):
            while True:
                job.check()
                time.sleep(0.02)
                seen.append(1)
        job = jobs.submit("install", "engine", "x", "Installing x", work)
        time.sleep(0.1)
        self.assertEqual(jobs.cancel_all(), 1)
        jobs.wait(job, 5)
        self.assertEqual(job.state, "cancelled")

    def test_asking_a_copy_to_quit_never_makes_it_ask_the_others(self):
        sent = {}

        class Resp:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        with mock.patch.object(instances._LOCAL, "open", side_effect=lambda req, timeout=0: (sent.update(body=req.data), Resp())[1]):
            self.assertTrue(instances.ask_to_quit(8765))
        self.assertEqual(json.loads(sent["body"]), {"others": False})

    def test_quit_all_skips_the_copy_that_is_asking(self):
        with mock.patch.object(instances, "find", return_value=[{"port": 1, "pid": 0, "file": None},
                                                                  {"port": 2, "pid": 0, "file": None}]), \
                mock.patch.object(instances, "ask_to_quit", return_value=True) as ask, \
                mock.patch.object(instances, "ping", return_value=None):
            self.assertEqual(instances.quit_all([], timeout=0.2, say=lambda *_: None, skip_port=1), 1)
        ask.assert_called_once_with(2)

    def test_axis_quit_reports_a_program_that_would_not_stop(self):
        from axis import cli
        stuck = mock.Mock(key="llama:x")
        with mock.patch.object(cli.instances, "quit_all", return_value=0), \
                mock.patch.object(cli.manager, "shutdown", return_value=[]), \
                mock.patch.object(procs, "running", return_value=[stuck]), \
                redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["quit"]), 1)


# ------------------------------------------------------------ calling agents


class TestCallAgents(Base):
    def make(self):
        helper = agents.create({"name": "helper", "system_prompt": "You check sums.", "skills": ["call-agent", "run-python"]})
        boss = agents.create({"name": "boss", "skills": ["call-agent", "run-python"],
                              "can_call": [{"name": "helper", "description": "checks sums and units"}]})
        return boss, helper

    def test_the_skill_gives_the_action(self):
        boss, _ = self.make()
        self.assertIn("call_agent", agents.tool_names(boss))
        spec = tools.tool_spec("call_agent", agents.callable_agents(boss))
        self.assertIn("helper", spec["parameters"]["properties"]["agent"]["description"])

    def test_a_called_agent_keeps_its_skills_but_not_the_call_skill(self):
        _, helper = self.make()
        names = agents.tool_names(helper, nested=True)
        self.assertNotIn("call_agent", names)
        self.assertIn("run_python", names)
        system = agents.build_messages(helper, [{"role": "user", "content": "x"}], called_by="boss")[0]["content"]
        self.assertNotIn("Call agents", system)
        self.assertNotIn("call_agent", system)
        self.assertIn("Run Python", system)
        self.assertIn("called you", system)
        agents.update("helper", {"can_call": [{"name": "boss", "description": "plans"}]})   # allowed to call, but was called
        plain = agents.build_messages(agents.load("helper"), [{"role": "user", "content": "x"}])[0]["content"]
        self.assertIn("Call agents", plain)

    def test_the_runner_runs_the_call_and_returns_the_answer(self):
        events, asked = [], []

        def caller(agent, message, on_event):
            asked.append((agent, message))
            return "Answer from helper:\n42"
        session = runner.Session(Path(self.home), ["call_agent"], events.append, agent_caller=caller)
        out = session.run("call_agent", {"agent": "helper", "message": "add"}, "1")
        self.assertEqual(out, "Answer from helper:\n42")
        self.assertEqual(asked, [("helper", "add")])
        self.assertTrue(events[-1]["tool_result"]["ok"])

    def test_without_a_caller_the_action_is_refused(self):
        events = []
        session = runner.Session(Path(self.home), ["call_agent"], events.append, agent_caller=None)
        out = session.run("call_agent", {"agent": "helper", "message": "add"}, "1")
        self.assertIn("cannot call other agents", out)
        self.assertFalse(events[-1]["tool_result"]["ok"])
        # An agent whose action list lacks it is refused too, even with a caller present.
        session = runner.Session(Path(self.home), ["run_python"], events.append, agent_caller=lambda *a: "x")
        self.assertIn("cannot call other agents", session.run("call_agent", {"agent": "a", "message": "b"}, "2"))

    def test_the_number_of_calls_in_one_reply_is_limited(self):
        session = runner.Session(Path(self.home), ["call_agent"], lambda e: None, agent_caller=lambda a, m, on: "ok " + m)
        outs = [session.run("call_agent", {"agent": "helper", "message": str(i)}, str(i)) for i in range(runner.MAX_AGENT_CALLS + 1)]
        self.assertTrue(all(o.startswith("ok") for o in outs[:-1]))
        self.assertIn("at most", outs[-1])

    def test_a_called_agent_is_run_with_no_way_to_call_agents(self):
        _, helper = self.make()
        seen = {}

        def fake_reply(conn, model, messages, emit, tool_names=None, workspace=None, shared=None, **kw):
            seen.update(tool_names=tool_names, kw=kw, system=messages[0]["content"])
            emit({"round": 0})
            emit({"t": "all "})
            emit({"t": "done"})
        conn = backend.Backend("ollama", "http://127.0.0.1:9", ["m"])
        call = subagents.make_caller(None, conn, "m", "boss", Path(self.home), None, ["helper"])
        with mock.patch.object(runner, "reply", side_effect=fake_reply):
            out = call("helper", "add 1 and 1", lambda note: None)
        self.assertEqual(out, "Answer from helper:\nall done")
        self.assertNotIn("call_agent", seen["tool_names"])
        self.assertIn("run_python", seen["tool_names"])
        self.assertIsNone(seen["kw"].get("agent_caller"))   # no caller: the runner refuses the action as well
        self.assertNotIn("Call agents", seen["system"])

    def test_bad_calls_are_explained(self):
        self.make()
        conn = backend.Backend("ollama", "http://127.0.0.1:9", ["m"])
        call = subagents.make_caller(None, conn, "m", "boss", Path(self.home), None, ["helper", "gone"])
        for agent, message, text in (("boss", "x", "cannot call itself"), ("nobody", "x", "not allowed"),
                                     ("gone", "x", "does not exist"),
                                     ("helper", "  ", "Give the agent a task"), (None, "x", "Say which agent"),
                                     ("helper", "x" * (subagents.MAX_TASK + 1), "too long")):
            with self.assertRaisesRegex(tools.ToolError, text):
                call(agent, message, lambda n: None)

    def test_an_agent_cannot_call_one_it_was_not_allowed_to(self):
        self.make()
        agents.create({"name": "secret"})
        conn = backend.Backend("ollama", "http://127.0.0.1:9", ["m"])
        call = subagents.make_caller(None, conn, "m", "boss", Path(self.home), None, ["helper"])
        with self.assertRaisesRegex(tools.ToolError, "not allowed to call 'secret'.*helper"):
            call("secret", "x", lambda n: None)
        none = subagents.make_caller(None, conn, "m", "boss", Path(self.home), None)   # no list: no calls at all
        with self.assertRaisesRegex(tools.ToolError, "may not call any agent"):
            none("helper", "x", lambda n: None)

    def test_the_action_cannot_be_run_outside_a_reply(self):
        ok, out = tools.execute(Path(self.home), "call_agent", {"agent": "a", "message": "b"}, ["call_agent"])
        self.assertFalse(ok)

    def test_the_agents_that_can_be_called_are_listed_in_the_action_only(self):
        boss, _ = self.make()
        agents.create({"name": "secret", "system_prompt": "Nobody may know this."})
        self.assertIn("call-agent", skills.BUILTIN_IDS)
        self.assertEqual(skills.adapt("call-agent", "Use it."), "Use it.")   # not repeated in the skill
        spec = tools.tool_spec("call_agent", agents.callable_agents(boss))
        agent = spec["parameters"]["properties"]["agent"]
        self.assertEqual(agent["enum"], ["helper"])
        self.assertIn("helper (checks sums and units)", agent["description"])
        for text in (str(spec), agents.build_messages(boss, [{"role": "user", "content": "x"}])[0]["content"]):
            self.assertNotIn("secret", text)             # an agent that was not allowed is never mentioned
            self.assertNotIn("You check sums", text)      # and no agent's system prompt is ever shown

    def test_the_chat_is_sent_only_the_allowed_agents_and_the_users_notes(self):
        boss, _ = self.make()
        agents.create({"name": "secret", "system_prompt": "Nobody may know this."})
        sent = {}

        def fake_complete(conn, model, messages, on_text, tools=None, temperature=None):
            sent.update(tools=tools, system=messages[0]["content"])
            return backend.Turn(text="done")
        conn = backend.Backend("ollama", "http://127.0.0.1:9", ["m"])
        with mock.patch.object(backend, "complete", side_effect=fake_complete):
            runner.reply(conn, "m", agents.build_messages(boss, [{"role": "user", "content": "x"}]), lambda e: None,
                         tool_names=agents.tool_names(boss), workspace=Path(self.home), agent_caller=lambda *a: "x",
                         callable_agents=agents.callable_agents(boss))
        call = next(t for t in sent["tools"] if t["function"]["name"] == "call_agent")
        text = json.dumps(call)
        self.assertIn("helper (checks sums and units)", text)
        self.assertNotIn("secret", text)
        self.assertNotIn("You check sums", text)

    def test_without_an_allowed_agent_the_action_and_skill_are_left_out(self):
        lone = agents.create({"name": "lone", "skills": ["call-agent", "run-python"]})
        self.assertEqual(agents.tool_names(lone), ["run_python", "run_python_file"])
        system = agents.build_messages(lone, [{"role": "user", "content": "x"}])[0]["content"]
        self.assertNotIn("call_agent", system)
        self.assertNotIn("Call agents", system)

    def test_the_allowed_agents_follow_a_rename_and_a_delete(self):
        self.make()
        agents.create({"name": "other"})
        agents.update("boss", {"can_call": [{"name": "helper", "description": "sums"}, {"name": "other", "description": ""}]})
        agents.update("helper", {"name": "checker"})
        self.assertEqual([c["name"] for c in agents.load("boss").can_call], ["checker", "other"])
        self.assertEqual(agents.load("boss").can_call[0]["description"], "sums")
        agents.delete("other")
        self.assertEqual([c["name"] for c in agents.load("boss").can_call], ["checker"])
        self.assertEqual([c["name"] for c in agents.callable_agents(agents.load("boss"))], ["checker"])

    def test_the_allowed_list_is_checked_and_cleaned(self):
        self.make()
        got = agents.update("boss", {"can_call": [{"name": "helper", "description": "  sums\n and   units "},
                                                  {"name": "helper", "description": "twice"}, {"name": "boss", "description": "me"}]})
        self.assertEqual(got.can_call, [{"name": "helper", "description": "sums and units"}])   # no repeat, never itself
        for bad in ("helper", [["helper"]], [{"name": "bad name!"}], [{"name": "helper", "description": "x" * 301}]):
            with self.assertRaises(agents.AgentError):
                agents.update("boss", {"can_call": bad})
        self.assertEqual(agents.load("boss").can_call, got.can_call)

    def test_the_agents_page_data_has_both_new_settings(self):
        boss, _ = self.make()
        data = agents.load("boss").to_dict()
        self.assertEqual((data["max_actions"], data["can_call"]), (10, [{"name": "helper", "description": "checks sums and units"}]))


# ------------------------------------------------------- actions in one reply


class TestActionsPerReply(Base):
    def test_the_setting_has_a_default_a_range_and_survives_a_save(self):
        self.assertEqual(agents.create({"name": "a"}).max_actions, 10)
        self.assertEqual(agents.update("a", {"max_actions": 3}).max_actions, 3)
        self.assertEqual(agents.load("a").max_actions, 3)
        for bad in (0, -1, 101, 2.5, "5", True, None):
            with self.assertRaisesRegex(agents.AgentError, "whole number from 1 to 100"):
                agents.update("a", {"max_actions": bad})
        self.assertEqual(agents.load("a").max_actions, 3)
        self.assertEqual(agents.update("a", {"max_actions": 100}).max_actions, 100)

    def test_an_agent_file_without_or_with_a_damaged_value_still_loads(self):
        agents.create({"name": "a"})
        path = Path(self.home) / "agents" / "a" / "agent.json"
        data = json.loads(path.read_text())
        data.pop("max_actions"); data.pop("can_call")
        path.write_text(json.dumps(data))
        self.assertEqual((agents.load("a").max_actions, agents.load("a").can_call), (10, []))
        path.write_text(json.dumps({**data, "max_actions": "lots", "can_call": "everyone"}))
        self.assertEqual((agents.load("a").max_actions, agents.load("a").can_call), (10, []))

    def _loop(self, limit, per_round=1):
        """A model that asks for a tool again and again; returns the tool events and the rounds it was asked."""
        events, rounds = [], []

        def fake_complete(conn, model, messages, on_text, tools=None, temperature=None):
            rounds.append(tools is not None)
            if tools is None:
                on_text("final answer")
                return backend.Turn(text="final answer")
            n = len(rounds)
            return backend.Turn(text="", tool_calls=[backend.ToolCall(id=f"c{n}-{i}", name="list_files", arguments={"path": f"d{n}-{i}"})
                                                     for i in range(per_round)])
        conn = backend.Backend("ollama", "http://127.0.0.1:9", ["m"])
        with mock.patch.object(backend, "complete", side_effect=fake_complete):
            runner.reply(conn, "m", [{"role": "system", "content": "s"}, {"role": "user", "content": "go"}], events.append,
                         tool_names=["list_files"], workspace=Path(self.home), max_actions=limit)
        return [e for e in events if "tool" in e], rounds

    def test_a_reply_stops_at_the_agents_number(self):
        for limit in (1, 3, 7):
            ran, rounds = self._loop(limit)
            self.assertEqual(len(ran), limit)
            self.assertFalse(rounds[-1])      # the last request asks for the final answer, with no tools

    def test_actions_asked_for_together_are_counted_one_by_one(self):
        with mock.patch.object(runner.tools, "execute", return_value=(True, "ok")):
            events = []
            session = runner.Session(Path(self.home), ["list_files"], events.append, max_actions=2)
            outs = [session.run("list_files", {"path": f"d{i}"}, str(i)) for i in range(4)]
        self.assertEqual(outs[:2], ["ok", "ok"])
        self.assertTrue(all("at most 2 actions" in o for o in outs[2:]))
        self.assertEqual([e["tool_result"]["ok"] for e in events if "tool_result" in e], [True, True, False, False])

    def test_without_a_setting_the_old_limit_of_ten_is_kept(self):
        ran, _ = self._loop(None)
        self.assertEqual(len(ran), runner.MAX_STEPS)

    def test_the_chat_route_uses_the_agents_number(self):
        agents.create({"name": "a", "skills": ["run-python"], "max_actions": 4})
        seen = {}

        def fake_reply(conn, model, messages, emit, **kw):
            seen.update(kw)
        from test_axis import OllamaHandler, chat_events, request
        url = self.axis_server(self.model_server(OllamaHandler, ["m"]))
        with mock.patch.object(runner, "reply", side_effect=fake_reply):
            chat_events(request(url + "/api/chat", "POST", {"agent": "a", "messages": [{"role": "user", "content": "hi"}]})[1])
        self.assertEqual(seen["max_actions"], 4)

    def test_a_called_agent_uses_its_own_number(self):
        agents.create({"name": "boss"})
        agents.create({"name": "helper", "max_actions": 2})
        seen = {}

        def fake_reply(conn, model, messages, emit, **kw):
            seen.update(kw)
            emit({"t": "ok"})
        conn = backend.Backend("ollama", "http://127.0.0.1:9", ["m"])
        call = subagents.make_caller(None, conn, "m", "boss", Path(self.home), None, ["helper"])
        with mock.patch.object(runner, "reply", side_effect=fake_reply):
            call("helper", "task", lambda n: None)
        self.assertEqual(seen["max_actions"], 2)


# ------------------------------------------------------- a folder per picture


class TestImageFolders(Base):
    def test_gallery_reads_settings_from_each_folder_and_old_files(self):
        root = Path(self.home) / "images"
        folder = root / "20260101-000000-fox"
        folder.mkdir(parents=True)
        (folder / "20260101-000000-fox.png").write_bytes(b"\x89PNG")
        (folder / "20260101-000000-fox.json").write_text(json.dumps(
            {"prompt": "a fox", "seed": 7, "steps": 4, "model": "m", "created": "2026-01-01T00:00:00"}))
        (root / "old.png").write_bytes(b"\x89PNG")
        (root / "empty-folder").mkdir()          # a folder with no picture is ignored
        listed = {g["path"]: g for g in images.gallery(root)}
        self.assertEqual(set(listed), {"20260101-000000-fox/20260101-000000-fox.png", "old.png"})
        fox = listed["20260101-000000-fox/20260101-000000-fox.png"]
        self.assertEqual((fox["prompt"], fox["seed"], fox["folder"]), ("a fox", 7, "20260101-000000-fox"))
        self.assertNotIn("folder", listed["old.png"])

    def test_a_damaged_settings_file_does_not_hide_the_picture(self):
        root = Path(self.home) / "images"
        (root / "a").mkdir(parents=True)
        (root / "a" / "a.png").write_bytes(b"\x89PNG")
        (root / "a" / "a.json").write_text("{not json")
        self.assertEqual([g["path"] for g in images.gallery(root)], ["a/a.png"])


# ----------------------------------------------------------------- engines


def _asset(name, sha="a" * 64):
    return {"name": name, "url": "u/" + name, "size": 1, "sha256": sha}


class TestOllamaEngine(Base):
    def test_every_platform_has_a_build(self):
        for plat, name in ((("windows", "x64"), "ollama-windows-amd64.zip"), (("macos", "arm64"), "ollama-darwin.tgz"),
                           (("linux", "x64"), "ollama-linux-amd64.tar.zst"), (("linux", "arm64"), "ollama-linux-arm64.tar.zst")):
            rels = [{"tag": "v1", "assets": [_asset("ollama-linux-amd64-rocm.tar.zst"), _asset("sha256sum.txt"), _asset(name)]}]
            _, got = engines.choose("ollama", "standard", rels, plat)
            self.assertEqual(got[0]["name"], name)
        self.assertEqual(engines.preferred_builds("ollama", ("linux", "x64")), ["standard"])
        self.assertEqual(engines.default_build("ollama", ("linux", "x64")), "standard")

    def test_ollama_is_not_installed_on_the_first_start_or_by_setup(self):
        self.assertNotIn("ollama", manager.ENGINE_FINDERS)

    def test_a_download_without_a_checksum_is_refused_and_an_older_release_is_used(self):
        rels = [{"tag": "v2", "assets": [_asset("ollama-windows-amd64.zip", None)]},
                {"tag": "v1", "assets": [_asset("ollama-windows-amd64.zip")]}]
        rel, _ = engines.choose("ollama", "standard", rels, ("windows", "x64"))
        self.assertEqual(rel["tag"], "v1")
        with self.assertRaisesRegex(engines.EngineError, "no published checksum"):
            engines.choose("ollama", "standard", rels[:1], ("windows", "x64"))
        with mock.patch.dict(os.environ, {"AXIS_ALLOW_UNVERIFIED": "1"}):
            self.assertEqual(engines.choose("ollama", "standard", rels[:1], ("windows", "x64"))[0]["tag"], "v2")

    def test_download_refuses_a_file_with_no_checksum(self):
        class Resp(io.BytesIO):
            headers = {"Content-Length": "3"}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        dest = Path(self.home) / "x.zip"
        job = jobs.Job(id="1", kind="install", engine="engine", target="x", label="x")
        with mock.patch.object(engines, "_open", return_value=Resp(b"abc")):
            with self.assertRaisesRegex(engines.EngineError, "no published checksum"):
                engines._download({"name": "x.zip", "url": "u", "size": 3, "sha256": None}, dest, job, 0, None)
        self.assertFalse(dest.exists())

    @unittest.skipUnless(POSIX, "uses a stand-in program")
    def test_tar_zst_is_unpacked_through_zstd_and_unsafe_entries_are_refused(self):
        bin_dir = Path(self.home) / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "zstd"      # stands in for zstd: the "compressed" file is a plain tar
        fake.write_text("#!/bin/sh\nexec cat \"$2\"\n")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)

        def make(name, entries):
            path = Path(self.home) / name
            with tarfile.open(path, "w") as t:
                for entry, data in entries:
                    info = tarfile.TarInfo(entry)
                    info.size = len(data)
                    t.addfile(info, io.BytesIO(data))
            return path
        good = make("ollama-linux-amd64.tar.zst", [("bin/ollama", b"#!/bin/sh\n"), ("lib/ollama/x.so", b"x")])
        out = Path(self.home) / "out"
        with mock.patch.dict(os.environ, {"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]}):
            engines._unpack(good, out)
            self.assertEqual((out / "bin" / "ollama").read_bytes(), b"#!/bin/sh\n")
            self.assertEqual(engines._find(out, "ollama"), (out / "bin" / "ollama").resolve())
            evil = make("evil.tar.zst", [("../escape.txt", b"no")])
            with self.assertRaisesRegex(engines.EngineError, "unsafe"):
                engines._unpack(evil, Path(self.home) / "out2")
        self.assertFalse((Path(self.home) / "escape.txt").exists())

    def test_missing_zstd_is_explained(self):
        archive = Path(self.home) / "a.tar.zst"
        archive.write_bytes(b"x")
        with mock.patch.object(engines, "_zstd_program", return_value=None):
            with self.assertRaisesRegex(engines.EngineError, "zstd"):
                engines._unpack(archive, Path(self.home) / "o")

    def test_the_copy_axis_installed_is_found_and_removed(self):
        folder = engines.root() / "ollama" / "v1-standard"
        (folder / "bin").mkdir(parents=True)
        program = folder / "bin" / ("ollama.exe" if os.name == "nt" else "ollama")
        program.write_text("x")
        engines._save({"ollama": {"tag": "v1", "build": "standard", "dir": "ollama/v1-standard",
                                  "programs": {"ollama": program.relative_to(engines.root()).as_posix()}}})
        self.assertEqual(manager.ollama_binary(), program.resolve())
        st = engines.status({"ollama": manager.ollama_binary})["ollama"]
        self.assertTrue(st["axis"])
        self.assertEqual(manager.remove_engine({"engine": "ollama"}), {"removed": "ollama"})
        self.assertFalse(folder.exists())
        self.assertIsNone(engines.installed("ollama"))

    def test_ollama_is_not_removed_while_axis_runs_it(self):
        with mock.patch.object(manager.procs, "running", return_value=[mock.Mock()]):
            with self.assertRaisesRegex(manager.ManageError, "Stop Ollama"):
                manager.remove_engine({"engine": "ollama"})


# --------------------------------------------------- uninstalling what Axis installed


class TestUninstall(Base):
    def test_a_tesseract_axis_did_not_install_is_never_removed(self):
        with self.assertRaisesRegex(ocr.OcrError, "did not install"):
            manager.remove_engine({"engine": "tesseract"})

    def test_a_maxima_axis_did_not_install_is_never_removed(self):
        with self.assertRaisesRegex(cas.CasError, "did not install"):
            cas.remove({"system": "maxima"})

    def test_removing_the_maxima_axis_installed_uses_the_package_manager_that_installed_it(self):
        cas._remember("maxima", {"method": "brew", "binary": "/x/maxima"})
        ran = []
        with mock.patch.object(cas, "_run_install", side_effect=lambda cmd, job, env, what: ran.append(cmd)), \
                mock.patch.object(cas, "_brew", return_value="/opt/brew"):
            job = jobs.Job(id="1", kind="delete", engine="cas", target="maxima", label="x")
            cas.remove_maxima(job)
        self.assertEqual(ran, [["/opt/brew", "uninstall", "maxima"]])
        self.assertIsNone(cas.maxima_record())

    def test_removing_the_tesseract_axis_installed_uses_the_package_manager_that_installed_it(self):
        ocr._write_json(ocr._engines_record(), {"tesseract": {"method": "brew", "binary": "/x/tesseract"}})
        ran = []

        class Lines:
            def tail(self, n=12):
                return ""
        with mock.patch.object(ocr, "_run", side_effect=lambda cmd, job, **kw: (ran.append(cmd), (0, Lines()))[1]), \
                mock.patch.object(ocr, "_brew", return_value="/opt/brew"):
            job = jobs.Job(id="1", kind="delete", engine="engine", target="tesseract", label="x")
            ocr.remove_tesseract(job)
        self.assertEqual(ran, [["/opt/brew", "uninstall", "tesseract"]])
        self.assertIsNone(ocr.tesseract_axis_record())


if __name__ == "__main__":
    unittest.main()
