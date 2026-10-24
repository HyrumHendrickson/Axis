"""Where Axis keeps its data."""

from __future__ import annotations

import os
from pathlib import Path


def axis_home() -> Path:
    override = os.environ.get("AXIS_HOME")
    return Path(override).expanduser() if override else Path.home() / "Axis"


def shared_root() -> Path:
    """The folder shared by every agent that has the Shared folder skill."""
    return axis_home() / "shared"
