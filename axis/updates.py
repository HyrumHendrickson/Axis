"""Checking GitHub for a newer Axis, and installing it from the page.

Two things are compared with the main branch on GitHub:

* the version in axis/VERSION, and
* the files themselves: GitHub lists the git hash of every file under axis/, and each one is
  compared with the hash of the installed copy. So a change pushed without raising the version
  number is still found.

When GitHub's API does not answer (it allows 60 requests an hour per address), only the version
is compared. The check runs in the background when the page asks and again every half hour; it
never blocks anything, and a failed check is shown on the page with its reason.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import __version__

REPO_ZIP = "https://github.com/HyrumHendrickson/Axis/archive/refs/heads/main.zip"
VERSION_URL = "https://raw.githubusercontent.com/HyrumHendrickson/Axis/main/axis/VERSION"
TREE_URL = "https://api.github.com/repos/HyrumHendrickson/Axis/git/trees/main?recursive=1"
COMMIT_ZIP = "https://github.com/HyrumHendrickson/Axis/archive/{commit}.zip"
PACKAGE_DIR = Path(__file__).resolve().parent
CHECK_TIMEOUT = 10          # seconds for each request to GitHub
RECHECK_AFTER = 30 * 60     # a long-running Axis looks again after this many seconds
LOG_LINES = 30              # last lines of pip output kept for a failed update

_lock = threading.Lock()
_state: Dict[str, Any] = {
    "current": __version__,
    "latest": None,
    "status": "idle",        # idle | checking | current | outdated | unknown | updating | updated | failed
    "error": "",
    "installed": None,       # the version on disk after an update
    "commit": None,          # the newest commit on main, when GitHub's API answered
    "changed": 0,            # installed files that differ from that commit
    "compared": False,       # True when the files were compared, not just the version
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


def blob_hash(data: bytes) -> str:
    """The hash git gives a file's contents (what GitHub lists for each file)."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def fetch_tree(url: str = TREE_URL, timeout: float = CHECK_TIMEOUT) -> Tuple[str, Dict[str, str]]:
    """The newest commit on main and {path inside axis/: git hash} for its files."""
    req = urllib.request.Request(url, headers={"User-Agent": f"Axis/{__version__}",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    files = {e["path"][len("axis/"):]: e["sha"] for e in data.get("tree") or []
             if isinstance(e, dict) and e.get("type") == "blob" and str(e.get("path", "")).startswith("axis/")}
    if not files or not data.get("sha"):
        raise ValueError("GitHub sent an empty file list.")
    if not re.fullmatch(r"[0-9a-f]{40}", str(data["sha"])):
        raise ValueError("GitHub sent something that is not a commit.")   # it goes into a download address
    return str(data["sha"]), files


def changed_files(remote: Dict[str, str], root: Path = PACKAGE_DIR) -> List[str]:
    """Files whose installed copy differs from GitHub's.

    A file GitHub has but this install does not is skipped: not every file in the repository is
    packaged, and a new module only matters once a file that is installed starts using it.
    """
    out = []
    for rel, sha in sorted(remote.items()):
        try:
            data = (root / rel).read_bytes()
        except OSError:
            continue
        if blob_hash(data) != sha and blob_hash(data.replace(b"\r\n", b"\n")) != sha:
            out.append(rel)
    return out


def disabled() -> bool:
    return os.environ.get("AXIS_NO_UPDATE_CHECK", "").strip().lower() in ("1", "true", "yes")


def _check() -> None:
    try:
        latest = fetch_latest()
    except Exception as exc:  # offline, timeout, bad reply
        with _lock:
            _state.update(status="unknown", error=_reason(exc), checked_at=time.time())
        return
    commit, changed, compared = None, [], False
    try:
        commit, remote = fetch_tree()
        changed, compared = changed_files(remote), True
    except Exception:
        pass  # rate limit or API down: compare the version only
    # A copy newer than GitHub's (while working on Axis itself) is never reported as out of date.
    outdated = is_newer(latest, __version__) or (bool(changed) and not is_newer(__version__, latest))
    with _lock:
        _state.update(commit=commit, changed=len(changed), compared=compared)
        if _state["status"] in ("updating", "updated"):
            _state["latest"] = latest
            return
        _state.update(latest=latest, error="", checked_at=time.time(),
                      status="outdated" if outdated else "current")


def _reason(exc: Exception) -> str:
    text = str(getattr(exc, "reason", "") or exc)
    if "CERTIFICATE_VERIFY_FAILED" in text:
        return ("Python could not verify GitHub's certificate. On macOS, run 'Install Certificates.command' "
                "in your Python folder (Applications/Python 3.x).")
    return text or exc.__class__.__name__


def check(force: bool = False) -> None:
    """Start a background check unless one is running, recent, or an update is under way."""
    if disabled():
        return
    with _lock:
        busy = _state["status"] in ("checking", "updating", "updated")
        fresh = time.time() - _state["checked_at"] < RECHECK_AFTER
        if busy or (fresh and not force and _state["status"] != "idle"):
            return
        if _state["status"] in ("idle", "unknown"):
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


def start_update(source: Optional[str] = None) -> Dict[str, Any]:
    with _lock:
        if _state["status"] == "updating":
            return {k: v for k, v in _state.items() if k != "checked_at"}
        if source is None:
            # Install exactly the commit that was checked, so a cached main.zip cannot hand back the old copy.
            source = COMMIT_ZIP.format(commit=_state["commit"]) if _state.get("commit") else REPO_ZIP
        _state.update(status="updating", error="", log="")
    threading.Thread(target=_update, args=(source,), name="axis-update", daemon=True).start()
    return state()
