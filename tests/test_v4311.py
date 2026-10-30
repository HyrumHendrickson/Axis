"""Checks of the page for the 4.3.11 changes. There are no browser tests, so these read the page's source."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

PAGE = (Path(__file__).resolve().parent.parent / "axis" / "web" / "index.html").read_text(encoding="utf-8")


class TestVersion(unittest.TestCase):
    def test_this_release(self):
        import axis
        self.assertGreaterEqual(tuple(int(x) for x in axis.__version__.split(".")), (4, 3, 11))
        changes = (Path(__file__).resolve().parent.parent / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## [4.3.11]", changes)


class TestPage(unittest.TestCase):
    def test_agent_rows_have_no_workspace_link(self):
        rows = PAGE[PAGE.index("function renderAgents()"):PAGE.index("/* ---", PAGE.index("function renderAgents()"))]
        self.assertNotIn('"Workspace"', rows)

    def test_full_chat_passes_the_folder(self):
        self.assertIn("openFullChat(target, cfg.folder ? cfg.folder() : \"\")", PAGE)
        for cfg in ("const TERM_CHAT", "const IDE_CHAT"):
            block = PAGE[PAGE.index(cfg):PAGE.index("};", PAGE.index(cfg))]
            self.assertIn("folder: () =>", block, cfg)
            self.assertIn("Hide this panel", block, cfg)

    def test_terminal_has_the_same_agent_button(self):
        self.assertIn('id: "term-side-btn"', PAGE)
        self.assertNotIn("Show agent", PAGE)

    def test_ocr_settings_are_on_the_ocr_page_only(self):
        self.assertEqual(len(re.findall(r"ocrSettingsSection\(", PAGE)), 3)   # defined, set-up screen, form
        models = PAGE[PAGE.index("function renderModels()"):PAGE.index("function engineOverview")]
        self.assertNotIn("ocrSettingsCard", models)


if __name__ == "__main__":
    unittest.main()
