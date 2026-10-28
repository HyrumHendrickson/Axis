"""Checking GitHub for a newer Axis, and installing it from the page.

The newest version is the one-line file axis/VERSION on the main branch. The check runs once,
in the background, the first time the page asks; it never blocks or interrupts anything, and a
failed check (offline, GitHub down) just leaves the state "unknown".
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from . import __version__

REPO_ZIP = "https://github.com/HyrumHendrickson/Axis/archive/refs/heads/main.zip"
VERSION_URL = "https://raw.githubusercontent.com/HyrumHendrickson/Axis/main/axis/VERSION"
CHECK_TIMEOUT = 6           # seconds for the version request
RECHECK_AFTER = 6 * 3600    # a long-running Axis looks again after this many seconds
LOG_LINES = 30              # last lines of pip output kept for a failed update

_lock = threading.Lock()
_state: Dict[str, Any] = {
    "current": __version__,
    "latest": None,
    "status": "idle",        # idle | checking | current | outdated | unknown | updating | updated | failed
    "error": "",
    "installed": None,       # the version on disk after an update
    "log": "",
    "checked_at": 0.0,
}


def parse_version(text: str) -> Tuple[int, ...]:
    """'3.10.2' -> (3, 10, 2). Anything after the numbers (rc1, -dev) is ignored."""
    nums = re.findall(r"\d+", (text or "").strip().split("-")[0].split("+")[0])
    if not nums:
        raise ValueError(f"not a version: {text!r}")
    parts = [int(n) for n in nums]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()        # so 3.8 == 3.8.0
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    try:
        return parse_version(latest) > parse_version(current)
    except ValueError:
        return False


def pip_command(source: str = REPO_ZIP) -> List[str]:
    return [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall",
            "--no-cache-dir", "--disable-pip-version-check", source]


def fetch_latest(url: str = VERSION_URL, timeout: float = CHECK_TIMEOUT) -> str:
    req = urllib.request.Request(f"{url}?t={int(time.time())}",
                                 headers={"User-Agent": f"Axis/{__version__}", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read(64).decode("utf-8", "replace").strip()
    parse_version(text)  # raises if GitHub sent something that is not a version
    return text


def disabled() -> bool:
    return os.environ.get("AXIS_NO_UPDATE_CHECK", "").strip().lower() in ("1", "true", "yes")


def _check() -> None:
    try:
        latest = fetch_latest()
    except Exception as exc:  # offline, timeout, bad reply: stay silent
        with _lock:
            _state.update(status="unknown", error=str(exc), checked_at=time.time())
        return
    with _lock:
        if _state["status"] in ("updating", "updated"):
            _state["latest"] = latest
            return
        _state.update(latest=latest, error="", checked_at=time.time(),
                      status="outdated" if is_newer(latest, __version__) else "current")


def check(force: bool = False) -> None:
    """Start a background check unless one is running, recent, or an update is under way."""
    if disabled():
        return
    with _lock:
        busy = _state["status"] in ("checking", "updating", "updated")
        fresh = time.time() - _state["checked_at"] < RECHECK_AFTER
        if busy or (fresh and not force and _state["status"] != "idle"):
            return
        if _state["status"] == "idle":
            _state["status"] = "checking"
    threading.Thread(target=_check, name="axis-update-check", daemon=True).start()


def state() -> Dict[str, Any]:
    """The update state for the page. Asking for it starts the first check."""
    check()
    with _lock:
        out = {k: v for k, v in _state.items() if k != "checked_at"}
    out["disabled"] = disabled()
    out["command"] = f"pipx install --force {REPO_ZIP}"
    return out


def _installed_version() -> Optional[str]:
    try:
        res = subprocess.run([sys.executable, "-c", "import axis; print(axis.__version__)"],
                             capture_output=True, text=True, timeout=60)
        return res.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _update(source: str) -> None:
    try:
        res = subprocess.run(pip_command(source), capture_output=True, text=True, timeout=900,
                             stdin=subprocess.DEVNULL)
        ok, output = res.returncode == 0, (res.stdout or "") + (res.stderr or "")
    except (OSError, subprocess.SubprocessError) as exc:
        ok, output = False, str(exc)
    tail = "\n".join(output.strip().splitlines()[-LOG_LINES:])
    if not ok:
        with _lock:
            _state.update(status="failed", error="pip could not install the update.", log=tail)
        return
    new = _installed_version()
    with _lock:
        _state.update(status="updated", installed=new, error="", log="")
        if new:
            _state["latest"] = new


def start_update(source: str = REPO_ZIP) -> Dict[str, Any]:
    with _lock:
        if _state["status"] == "updating":
            return {k: v for k, v in _state.items() if k != "checked_at"}
        _state.update(status="updating", error="", log="")
    threading.Thread(target=_update, args=(source,), name="axis-update", daemon=True).start()
    return state()
