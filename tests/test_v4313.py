"""Tests for Axis 4.3.13: the Settings page, Generate default agents, and Update all on the Art Craft page.

The installs are replaced by a fake, so nothing is downloaded. The page has no browser tests; its new parts are
checked as text, and its script is parsed by tests in the other files.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import axis
from axis import agents, artcraft, defaults, jobs, skills
from test_axis import Base, OllamaHandler, request

ROOT = Path(__file__).resolve().parent.parent
PAGE = (ROOT / "axis" / "web" / "index.html").read_text(encoding="utf-8")
NAMES = ["MainAgent", "WebSearchSubAgent", "CodeWriterSubAgent", "DocumentEditorSubAgent"]


def status(rust=False, wordcraft=False):
    return {"apps": [{"id": "wordcraft", "installed": wordcraft}], "rust": {"installed": rust}}


class TestVersion(unittest.TestCase):
    def test_version(self):
        self.assertEqual(axis.__version__, "4.3.13")
        self.assertEqual((ROOT / "axis" / "VERSION").read_text(encoding="utf-8").strip(), "4.3.13")
        self.assertIn("## [4.3.13]", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
        self.assertIn("/api/agents/defaults", (ROOT / "docs" / "api.md").read_text(encoding="utf-8"))
        self.assertIn("/api/agents/defaults", (ROOT / "axis" / "server.py").read_text(encoding="utf-8"))


class TestDefaultAgents(Base):
    def generate(self, st=None, install=None):
        install = install or mock.Mock(return_value={"job": {}})
        with mock.patch.object(artcraft, "status", return_value=st or status()), mock.patch.object(artcraft, "install", install):
            return defaults.generate(), install

    def test_makes_the_four_agents(self):
        result, _ = self.generate()
        self.assertEqual(result["created"], NAMES)
        self.assertEqual(result["skipped"], [])
        self.assertEqual(sorted(a.name for a in agents.list_all()), sorted(NAMES))

    def test_skills_and_called_agents(self):
        self.generate()
        main = agents.load("MainAgent")
        self.assertEqual(sorted(main.skills), ["call-agent", "share-file"])
        self.assertEqual([c["name"] for c in main.can_call], NAMES[1:])
        self.assertIn("web", main.can_call[0]["description"])
        self.assertIn("executive assistant", main.system_prompt)
        self.assertEqual(sorted(agents.load("WebSearchSubAgent").skills), ["run-terminal", "web-search", "wikipedia"])
        self.assertEqual(sorted(agents.load("CodeWriterSubAgent").skills), ["run-python", "run-rust", "run-terminal"])
        self.assertEqual(sorted(agents.load("DocumentEditorSubAgent").skills), ["run-terminal", "wordcraft"])

    def test_every_skill_exists(self):
        for spec in defaults.DEFAULT_AGENTS:
            for skill_id in spec["skills"]:
                self.assertTrue(skills.exists(skill_id), skill_id)

    def test_installs_rust_and_wordcraft(self):
        result, install = self.generate()
        self.assertEqual([c.args[0] for c in install.call_args_list], [{"app": "rust"}, {"app": "wordcraft"}])
        self.assertEqual([i["state"] for i in result["installs"]], ["started", "started"])

    def test_installed_apps_are_not_installed_again(self):
        result, install = self.generate(status(rust=True, wordcraft=True))
        install.assert_not_called()
        self.assertEqual([i["state"] for i in result["installs"]], ["installed", "installed"])

    def test_only_the_missing_app_is_installed(self):
        result, install = self.generate(status(rust=True))
        self.assertEqual([c.args[0] for c in install.call_args_list], [{"app": "wordcraft"}])

    def test_an_install_already_running_is_not_an_error(self):
        result, _ = self.generate(install=mock.Mock(side_effect=jobs.JobError("Installing Rust is still in progress.")))
        self.assertEqual([i["state"] for i in result["installs"]], ["started", "started"])

    def test_an_install_that_cannot_start_is_reported_and_the_agents_are_still_made(self):
        result, _ = self.generate(install=mock.Mock(side_effect=artcraft.ArtCraftError("No build for this computer.")))
        self.assertEqual(result["created"], NAMES)
        self.assertEqual([i["state"] for i in result["installs"]], ["unavailable", "unavailable"])
        self.assertIn("No build", result["installs"][0]["message"])

    def test_existing_agents_are_never_replaced(self):
        agents.create({"name": "MainAgent", "system_prompt": "Mine."})
        result, _ = self.generate()
        self.assertEqual(result["skipped"], ["MainAgent"])
        self.assertEqual(result["created"], NAMES[1:])
        self.assertEqual(agents.load("MainAgent").system_prompt, "Mine.")

    def test_a_second_run_makes_nothing(self):
        self.generate()
        result, _ = self.generate()
        self.assertEqual(result["created"], [])
        self.assertEqual(result["skipped"], NAMES)

    def test_route(self):
        base = self.axis_server(self.model_server(OllamaHandler, ["m"]))
        with mock.patch.object(artcraft, "status", return_value=status()), \
                mock.patch.object(artcraft, "install", return_value={"job": {}}):
            code, text = request(base + "/api/agents/defaults", "POST", {})
        self.assertEqual(code, 201)
        self.assertEqual(json.loads(text)["created"], NAMES)
        code, text = request(base + "/api/agents")
        self.assertEqual(sorted(a["name"] for a in json.loads(text)), sorted(NAMES))
        self.assertEqual(request(base + "/api/agents/MainAgent")[0], 200)


class TestPage(unittest.TestCase):
    def test_settings_is_a_page_with_two_tabs(self):
        self.assertIn("function renderSettings()", PAGE)
        self.assertIn('[["appearance", "Appearance"], ["agents", "Agents"]]', PAGE)
        self.assertIn('go({ kind: "settings" })', PAGE)
        self.assertIn('if (S.view.kind === "settings") return renderSettings();', PAGE)
        self.assertNotIn("settings-pop", PAGE)

    def test_generate_default_agents_button(self):
        self.assertIn('"Generate default agents"', PAGE)
        self.assertIn('"/api/agents/defaults"', PAGE)

    def test_example_agents_button_only_when_there_are_no_agents(self):
        agents_page = PAGE[PAGE.index("function renderAgents()"):PAGE.index("const rows = S.agents.map")]
        self.assertIn('"Generate example agents"', agents_page)
        self.assertEqual(PAGE.count('"Generate example agents"'), 1)

    def test_update_all(self):
        self.assertIn("function artPending(a)", PAGE)
        self.assertIn("pending.length > 1", PAGE)
        self.assertIn("`Update all (${pending.length})`", PAGE)
        self.assertIn('"/api/artcraft/update"', PAGE)


if __name__ == "__main__":
    unittest.main()
