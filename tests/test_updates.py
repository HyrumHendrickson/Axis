"""Tests for the update check and the in-page updater (no network)."""

from __future__ import annotations

import subprocess
import time
import unittest
from pathlib import Path
from unittest import mock

import axis
from axis import updates


def wait_for(predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(updates._state)
        updates._state.update(status="idle", latest=None, error="", installed=None, log="", checked_at=0.0)
        self.env = mock.patch.dict("os.environ", {"AXIS_NO_UPDATE_CHECK": ""})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        updates._state.clear()
        updates._state.update(self._saved)

    def test_version_file_is_the_only_source(self):
        text = (Path(axis.__file__).parent / "VERSION").read_text(encoding="utf-8")
        self.assertEqual(text.strip(), axis.__version__)
        self.assertEqual(len(text.strip().splitlines()), 1)
        self.assertNotIn("version =", (Path(axis.__file__).parent.parent / "pyproject.toml").read_text().split("[tool.setuptools.dynamic]")[0])

    def test_compare(self):
        self.assertTrue(updates.is_newer("3.10.0", "3.9.9"))
        self.assertFalse(updates.is_newer("3.8", "3.8.0"))
        self.assertFalse(updates.is_newer("3.7.0", "3.8.0"))
        self.assertFalse(updates.is_newer("garbage", "3.8.0"))

    def _check_with(self, **kw):
        with mock.patch.object(updates, "fetch_latest", **kw):
            self.assertIn(updates.state()["status"], ("checking", "current", "outdated", "unknown"))
            self.assertTrue(wait_for(lambda: updates._state["status"] != "checking"))
        return updates.state()

    def test_outdated(self):
        st = self._check_with(return_value="99.0.0")
        self.assertEqual((st["status"], st["latest"]), ("outdated", "99.0.0"))

    def test_up_to_date(self):
        self.assertEqual(self._check_with(return_value=axis.__version__)["status"], "current")

    def test_offline_is_silent(self):
        self.assertEqual(self._check_with(side_effect=OSError("offline"))["status"], "unknown")

    def test_disabled(self):
        with mock.patch.dict("os.environ", {"AXIS_NO_UPDATE_CHECK": "1"}), \
                mock.patch.object(updates, "fetch_latest") as fetch:
            self.assertEqual(updates.state()["status"], "idle")
        fetch.assert_not_called()

    def test_update_success_and_failure(self):
        def ok(cmd, **kw):
            if "pip" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="done", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="99.0.0\n", stderr="")
        with mock.patch("subprocess.run", side_effect=ok) as run:
            self.assertIn(updates.start_update()["status"], ("updating", "updated"))
            self.assertTrue(wait_for(lambda: updates._state["status"] == "updated"))
        self.assertEqual(run.call_args_list[0].args[0][-1], updates.REPO_ZIP)
        self.assertEqual(updates.state()["installed"], "99.0.0")

        updates._state["status"] = "outdated"
        bad = subprocess.CompletedProcess([], 1, stdout="", stderr="ERROR: no network")
        with mock.patch("subprocess.run", return_value=bad):
            updates.start_update()
            self.assertTrue(wait_for(lambda: updates._state["status"] == "failed"))
        self.assertIn("no network", updates.state()["log"])


if __name__ == "__main__":
    unittest.main()
