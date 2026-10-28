"""Axis: a small CLI for chatting with and running agents on a local model."""

from pathlib import Path as _Path

# The version lives in axis/VERSION and nowhere else. pyproject.toml reads it at build time,
# and the update check compares it with the same file on GitHub.
__version__ = (_Path(__file__).resolve().parent / "VERSION").read_text(encoding="utf-8").strip()
