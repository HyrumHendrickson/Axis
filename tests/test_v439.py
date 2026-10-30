"""Tests for Axis 4.3.9: the Share files skill (an agent offers a file to download), the number of actions
a reply may use, and choosing which agents an agent may call. The last two are in test_v437.py beside the
other Call agents tests. Everything runs against local fakes.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import urllib.parse
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import axis  # noqa: E402
from axis import agents, paths, runner, server, skills, tools  # noqa: E402
from test_axis import Base, OllamaHandler, request  # noqa: E402


class TestShareFileAction(Base):
    def setUp(self):
        super().setUp()
        self.ws = Path(self.home) / "shared"
        self.ws.mkdir()
        (self.ws / "report.txt").write_text("hello")
        (self.ws / "sub").mkdir()
        (self.ws / "sub" / "data.csv").write_bytes(b"a,b\n1,2\n")   # bytes: text mode writes \r\n on Windows

    def run_action(self, path, shared=None):
        return tools.execute(self.ws, "share_file", {"path": path}, ["share_file"], shared=shared)

    def test_a_file_is_shared(self):
        ok, out = self.run_action("sub/data.csv")
        self.assertTrue(ok, out)
        self.assertIn("Shared sub/data.csv (8 bytes)", out)
        self.assertIn("download it or open the folder", out)

    def test_only_a_file_in_the_agents_folder_can_be_shared(self):
        for path, text in (("missing.txt", "does not exist"), ("sub", "is a folder"), ("../outside.txt", "outside your folder"),
                           ("/etc/passwd", "not an absolute path"), (".axis-tmp", "does not exist")):
            ok, out = self.run_action(path)
            self.assertFalse(ok, path)
            self.assertIn(text, out)
        hidden = self.ws / ".axis-tmp"
        hidden.mkdir()
        (hidden / "x.txt").write_text("x")
        ok, out = self.run_action(".axis-tmp/x.txt")
        self.assertFalse(ok)
        self.assertIn("cannot be shared", out)

    def test_the_shared_prefix_works_and_other_names_for_the_action_are_understood(self):
        other = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, other, True)
        (other / "own.txt").write_text("mine")
        ok, out = tools.execute(other, "send_file", {"file": "shared/report.txt"}, ["share_file"], shared=self.ws)
        self.assertTrue(ok, out)
        self.assertIn("Shared shared/report.txt", out)

    def test_the_action_asks_for_one_path(self):
        spec = tools.tool_spec("share_file")
        self.assertEqual(spec["parameters"]["required"], ["path"])
        self.assertEqual(list(spec["parameters"]["properties"]), ["path"])


class TestShareFileEvent(Base):
    def run_session(self, workspace, shared, path):
        events = []
        session = runner.Session(workspace, ["share_file"], events.append, shared=shared)
        session.run("share_file", {"path": path}, "1")
        return events[-1]["tool_result"]

    def test_a_file_in_the_workspace_is_offered_from_the_workspace(self):
        ws = paths.workspace_root()
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "a").mkdir()
        (ws / "a" / "r.pdf").write_bytes(b"%PDF")
        for path in ("a/r.pdf", "shared/a/r.pdf"):
            res = self.run_session(ws, ws, path)
            self.assertTrue(res["ok"], res)
            self.assertEqual(res["file"], {"name": "r.pdf", "size": 4, "place": "_shared", "path": "a/r.pdf"})

    def test_a_file_in_a_chats_own_folder_is_offered_from_that_folder(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        (folder / "out.txt").write_text("x")
        res = self.run_session(folder, None, "out.txt")
        self.assertEqual(res["file"], {"name": "out.txt", "size": 1, "place": "_folder", "folder": str(folder.resolve()),
                                       "path": "out.txt"})

    def test_a_refused_share_offers_nothing(self):
        res = self.run_session(paths.workspace_root(), None, "nope.txt")
        self.assertFalse(res["ok"])
        self.assertNotIn("file", res)

    def test_other_actions_carry_no_file(self):
        events = []
        ws = paths.workspace_root()
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "a.txt").write_text("x")
        runner.Session(ws, ["run_python"], events.append).run("run_python", {"code": "print(1)"}, "1")
        self.assertNotIn("file", events[-1]["tool_result"])


class TestShareFileDownload(Base):
    def setUp(self):
        super().setUp()
        self.url = self.axis_server(self.model_server(OllamaHandler, ["m"]))
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        (self.folder / "note.txt").write_text("from the chat folder")
        (self.folder / "dir").mkdir()
        ws = paths.workspace_root()
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "w.txt").write_text("from the workspace")

    def raw(self, folder, path="note.txt", extra=""):
        return request(f"{self.url}/api/files/_folder/raw?path={urllib.parse.quote(path)}&folder={urllib.parse.quote(str(folder))}{extra}")

    def test_the_file_can_be_downloaded_from_the_workspace_or_from_a_chat_folder(self):
        self.assertEqual(request(self.url + "/api/files/_shared/raw?path=w.txt&download=1"), (200, "from the workspace"))
        self.assertEqual(self.raw(self.folder, extra="&download=1"), (200, "from the chat folder"))

    def test_the_download_is_sent_as_an_attachment(self):
        import urllib.request
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        resp = opener.open(f"{self.url}/api/files/_folder/raw?path=note.txt&download=1&folder={urllib.parse.quote(str(self.folder))}")
        self.assertIn("attachment", resp.headers["Content-Disposition"])
        self.assertIn("note.txt", resp.headers["Content-Disposition"])

    def test_nothing_but_download_and_show_in_folder_works_on_a_chat_folder(self):
        folder = urllib.parse.quote(str(self.folder))
        for method, route in (("GET", "?path="), ("GET", "/view?path=note.txt"), ("DELETE", "?path=note.txt"),
                              ("POST", "/mkdir?path=x"), ("POST", "/create?path=x.txt"), ("POST", "/run?path=note.txt"),
                              ("PUT", "/save?path=note.txt"), ("POST", "/upload?path=&name=x.txt")):
            code, body = request(f"{self.url}/api/files/_folder{route}&folder={folder}" if "?" in route
                                 else f"{self.url}/api/files/_folder{route}?folder={folder}", method)
            self.assertEqual(code, 400, (method, route, body))
            self.assertIn("only be used to download", body)
        self.assertTrue((self.folder / "note.txt").exists())
        self.assertFalse((self.folder / "x").exists())

    def test_the_folder_must_be_one_an_agent_may_use(self):
        (paths.axis_home() / "agents").mkdir(exist_ok=True)   # Axis's own data: never a place to download from
        for folder in ("", "relative/dir", paths.axis_home() / "agents"):
            code, _ = self.raw(folder)
            self.assertEqual(code, 400, folder)
        self.assertEqual(self.raw(self.folder / "missing")[0], 404)   # a folder that is gone
        code, _ = self.raw(self.folder, path="../" + self.folder.name + "/../../etc/passwd")
        self.assertEqual(code, 400)

    def test_show_in_folder_selects_the_file(self):
        with mock.patch.object(server, "reveal") as reveal:
            code, _ = request(f"{self.url}/api/files/_folder/reveal?path=note.txt&folder={urllib.parse.quote(str(self.folder))}", "POST")
        self.assertEqual(code, 200)
        reveal.assert_called_once_with((self.folder / "note.txt").resolve())


class TestShareFileSkill(Base):
    def test_the_skill_gives_the_action_and_its_text_is_sent(self):
        self.assertIn("share-file", skills.BUILTIN_IDS)
        agent = agents.create({"name": "a", "skills": ["share-file"]})
        self.assertEqual(agents.tool_names(agent), ["share_file"])
        system = agents.build_messages(agent, [{"role": "user", "content": "x"}])[0]["content"]
        self.assertIn("Share files", system)
        self.assertIn("your own folder", system)     # it works in the agent's folder, so the agent is told about it

    def test_an_agent_that_was_called_cannot_share_files(self):
        agent = agents.create({"name": "b", "skills": ["share-file", "run-python"]})
        self.assertNotIn("share_file", agents.tool_names(agent, nested=True))
        system = agents.build_messages(agent, [{"role": "user", "content": "x"}], called_by="boss")[0]["content"]
        self.assertNotIn("Share files", system)
        self.assertIn("Run Python", system)

    def test_the_skill_can_be_edited_and_reset_like_the_others(self):
        skills.update("share-file", {"instructions": "Share only PDFs."})
        self.assertIn("Share only PDFs.", agents.skill_preview("share-file")["system"])
        self.assertEqual(skills.reset("share-file")["modified"], False)


class TestVersion(Base):
    def test_this_release(self):
        self.assertGreaterEqual(tuple(int(x) for x in axis.__version__.split(".")), (4, 3, 9))
        changes = (Path(__file__).resolve().parent.parent / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## [4.3.9]", changes)
