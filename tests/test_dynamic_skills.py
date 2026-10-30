"""The text and actions an agent is given follow what is set up on this computer."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

from axis import agents, craft_skills, skills, tools

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_manage import Base


class TestDynamicSkills(Base):
    def prompt(self, skill):
        return agents.skills_prompt([skill], agents.tool_names(agents.Agent(name="a", skills=[skill])))

    def test_terminal_names_the_default_shell(self):
        with mock.patch.object(tools, "_shells_here", return_value=["cmd", "powershell"]):
            text = self.prompt("run-terminal")
            self.assertIn("default shell here is cmd", text)
            self.assertIn("cmd syntax", text)
            self.assertIn("one of cmd, powershell", tools.tool_spec("run_terminal")["parameters"]["properties"]["shell"]["description"])
        with mock.patch.object(tools, "_shells_here", return_value=["bash", "sh"]):
            self.assertIn("POSIX", self.prompt("run-terminal"))

    def test_rust_says_when_it_is_missing(self):
        with mock.patch("axis.artcraft._rust_info", return_value=None), mock.patch.object(skills.shutil, "which", return_value=None):
            self.assertIn("Rust is not installed yet", self.prompt("run-rust"))

    def test_craft_skill_follows_install_state_and_shell(self):
        with mock.patch("axis.artcraft.cli_program", return_value=None), mock.patch("axis.artcraft._app_info", return_value=None), \
                mock.patch.object(tools, "_shells_here", return_value=["cmd"]):
            text = self.prompt("photocraft")
            self.assertIn("photocraft-cli is not installed", text)
            self.assertIn("no shell, so nothing needs quoting", text)
        with mock.patch("axis.artcraft.cli_program", return_value=Path("/x/photocraft-cli")), \
                mock.patch("axis.artcraft._app_info", return_value={}):
            self.assertIn("photocraft-cli is installed", self.prompt("photocraft"))

    def test_every_craft_skill_adapts(self):
        for app_id in craft_skills.IDS:
            self.assertIn(app_id + "-cli", skills.adapt(app_id, skills.get(app_id)["instructions"]))

    def test_images_and_ocr_report_nothing_installed(self):
        self.assertIn("No image model is set up", self.prompt("images"))
        with mock.patch("axis.ocr.list_models", return_value=[]):
            self.assertIn("No OCR model is installed right now", self.prompt("ocr"))
        self.assertNotIn("Image models you can use", tools.tool_spec("generate_image")["description"])


if __name__ == "__main__":
    unittest.main()
