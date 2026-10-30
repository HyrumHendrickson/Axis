"""Tests for the Art Craft skills (craft_skills.py) and how an agent finds an app's command-line program.

Everything runs against a temporary Axis home and a stand-in <app>-cli script; no app is downloaded.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from axis import agents, artcraft, craft_skills, skills, tool_skills, tools

POSIX_ONLY = unittest.skipIf(os.name == "nt", "uses a shell script as the stand-in program")


class TestCraftSkillDefinitions(unittest.TestCase):
    def test_there_is_one_skill_for_each_craft_app(self):
        built = craft_skills.build()
        self.assertEqual([s["id"] for s in built], artcraft.APP_IDS)
        names = {a[0]: a[1] for a in artcraft.APPS}
        for s in built:
            self.assertEqual(s["name"], names[s["id"]])

    def test_every_skill_is_a_usable_built_in(self):
        for s in craft_skills.build():
            with self.subTest(app=s["id"]):
                self.assertIn(s["id"], skills.BUILTIN_IDS)
                self.assertEqual(s["tools"], ["run_terminal", "run_terminal_file"])
                self.assertEqual(s["group"], craft_skills.GROUP)
                self.assertLessEqual(len(s["description"]), 300)
                self.assertLess(len(s["instructions"]), 3000)   # small models have small contexts
                for t in s["tools"]:
                    self.assertIn(t, tools.TOOL_SPECS)

    def test_each_skill_names_its_own_program_and_the_shared_rules(self):
        for s in craft_skills.build():
            with self.subTest(app=s["id"]):
                text = s["instructions"]
                self.assertIn(s["id"] + "-cli", text)
                self.assertNotIn("{cli}", text)
                self.assertIn("Never overwrite an original", text)
                self.assertIn("Art Craft page", text)
                # no other app's program is mentioned by mistake
                others = [a for a in artcraft.APP_IDS if a != s["id"] and a + "-cli" in text]
                self.assertEqual(others, [])

    def test_the_library_lists_them_after_the_older_built_ins(self):
        ids = [s["id"] for s in skills.list_all() if s["builtin"]]
        self.assertEqual(ids[:5], ["run-python", "run-rust", "run-terminal", "web-search", "wikipedia"])
        self.assertEqual(ids[5:8], ["cas", "images", "ocr"])   # the Tools skills
        self.assertEqual(ids[8:], artcraft.APP_IDS)

    def test_only_craft_and_tools_skills_have_a_group(self):
        tool_ids = {s["id"] for s in tool_skills.build()}
        for s in skills.list_all():
            want = craft_skills.GROUP if s["id"] in artcraft.APP_IDS else tool_skills.GROUP if s["id"] in tool_ids else ""
            self.assertEqual(s["group"], want)


class Home(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        patch = mock.patch.dict(os.environ, {"AXIS_HOME": self.home})
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(shutil.rmtree, self.home, True)

    def install(self, app_id, with_cli=True):
        """Record a stand-in install the way artcraft does, with the program in bin/ like the Linux tarball."""
        folder = artcraft.root() / app_id / "v1" / f"{app_id}-1-linux-x86_64" / "bin"
        folder.mkdir(parents=True)
        program = folder / app_id
        program.write_text("#!/bin/sh\n")
        program.chmod(0o755)
        if with_cli:
            cli = folder / (app_id + "-cli")
            cli.write_text(f'#!/bin/sh\necho "{app_id}-cli got: $*"\n')
            cli.chmod(0o755)
        rel = program.relative_to(artcraft.engines.root()).as_posix()
        artcraft._remember(app_id, {"tag": "v1", "dir": rel.rsplit("/", 2)[0], "program": rel})
        return folder


@POSIX_ONLY
class TestFindingTheCli(Home):
    def test_nothing_installed_means_no_folders(self):
        self.assertEqual(artcraft.cli_dirs(), [])
        self.assertIsNone(artcraft.cli_program("photocraft"))

    def test_an_installed_apps_cli_is_found_beside_the_app(self):
        folder = self.install("photocraft")
        self.assertEqual(artcraft.cli_program("photocraft"), folder / "photocraft-cli")
        self.assertEqual(artcraft.cli_dirs(), [str(folder)])

    def test_an_app_without_a_cli_has_none_and_is_not_on_the_path(self):
        self.install("soundcraft", with_cli=False)   # like the macOS download, which holds only the app
        self.assertIsNone(artcraft.cli_program("soundcraft"))
        self.assertEqual(artcraft.cli_dirs(), [])

    def test_a_name_that_is_not_a_craft_app_is_refused(self):
        self.assertIsNone(artcraft.cli_program("../photocraft"))
        self.assertIsNone(artcraft.cli_program("rust"))

    def test_the_run_environment_puts_each_cli_folder_first(self):
        a, b = self.install("photocraft"), self.install("pdfcraft")
        path = tools._run_env(tools.Places(Path(self.home) / "ws")).get("PATH", "").split(os.pathsep)
        self.assertEqual(sorted(path[:2]), sorted([str(a), str(b)]))
        self.assertIn(os.environ.get("PATH", "").split(os.pathsep)[0], path)   # the old PATH is kept

    def test_the_environment_is_unchanged_when_no_app_is_installed(self):
        env = tools._run_env(tools.Places(Path(self.home) / "ws"))
        self.assertEqual(env.get("PATH"), os.environ.get("PATH"))

    def test_an_agent_runs_the_cli_by_name_in_its_folder(self):
        self.install("photocraft")
        ws = Path(self.home) / "ws"
        ok, text = tools.execute(ws, "run_terminal", {"code": "photocraft-cli info a.png\npwd", "shell": "sh"},
                                 ["run_terminal"])
        self.assertTrue(ok, text)
        self.assertIn("photocraft-cli got: info a.png", text)
        self.assertIn(str(ws.resolve()), text)

    def test_without_the_app_the_shell_reports_a_missing_program(self):
        ws = Path(self.home) / "ws"
        ok, text = tools.execute(ws, "run_terminal", {"code": "photocraft-cli --version", "shell": "sh"},
                                 ["run_terminal"])
        self.assertIn("not found", text.lower())


class TestSkillsReachTheAgent(Home):
    def test_a_craft_skill_gives_the_agent_terminal_actions_and_its_text(self):
        agent = agents.create({"name": "editor", "skills": ["photocraft"]})
        names = agents.tool_names(agent)
        self.assertEqual(names, ["run_terminal", "run_terminal_file"])
        prompt = agents.skills_prompt(agent.skills, names)
        self.assertIn("## PhotoCraft", prompt)
        self.assertIn("photocraft-cli commands --filter", prompt)

    def test_two_craft_skills_add_the_terminal_actions_once(self):
        agent = agents.create({"name": "both", "skills": ["photocraft", "pdfcraft", "run-terminal"]})
        self.assertEqual(agents.tool_names(agent), ["run_terminal", "run_terminal_file"])

    def test_the_skills_page_preview_shows_what_is_sent(self):
        view = agents.skill_preview("gridcraft")
        self.assertIn("gridcraft-cli", view["system"])
        self.assertEqual([t["function"]["name"] for t in view["tools"]], ["run_terminal", "run_terminal_file"])

    def test_the_text_can_be_edited_and_reset_like_any_built_in(self):
        before = skills.get("cadcraft")["instructions"]
        skills.update("cadcraft", {"instructions": before + "\nAlways use millimetres."})
        edited = skills.get("cadcraft")
        self.assertTrue(edited["modified"])
        self.assertTrue(edited["instructions"].endswith("Always use millimetres."))
        self.assertEqual(edited["group"], craft_skills.GROUP)
        self.assertEqual(skills.reset("cadcraft")["instructions"], before)


if __name__ == "__main__":
    unittest.main()
