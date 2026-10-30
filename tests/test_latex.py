"""LaTeX renderer download: the temporary file is closed before it is renamed (Windows refuses otherwise)."""

from __future__ import annotations

import io
import os
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from axis import latex

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_manage import Base


class Fake(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestLatex(Base):
    def test_install_closes_its_temp_file_and_enables(self):
        opened = []
        real = os.close
        with mock.patch.object(latex.os, "close", side_effect=lambda fd: (opened.append(fd), real(fd))[1]), \
                mock.patch.object(latex.urllib.request, "urlopen", return_value=Fake(b"x" * 20000)):
            status = latex.install()
        self.assertEqual(len(opened), 1)
        self.assertTrue(status["installed"] and status["enabled"])
        self.assertEqual(list(latex._folder().glob("mathjax-*")), [])

    def test_second_host_is_tried(self):
        calls = []

        def fake(request, timeout=0):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise urllib.error.URLError("blocked")
            return Fake(b"x" * 20000)
        with mock.patch.object(latex.urllib.request, "urlopen", side_effect=fake):
            self.assertTrue(latex.install()["installed"])
        self.assertEqual(calls, [latex.ASSET_URL, latex.MIRROR_URL])

    def test_failure_is_a_plain_message(self):
        with mock.patch.object(latex.urllib.request, "urlopen", side_effect=urllib.error.URLError("offline")):
            with self.assertRaisesRegex(latex.LatexError, "Could not download"):
                latex.install()
        self.assertFalse(latex.status()["installed"])


if __name__ == "__main__":
    unittest.main()
