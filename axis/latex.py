"""Optional local LaTeX rendering support for the web chat.

MathJax is downloaded only when the user asks for it.  Keeping the asset in
Axis' home directory means chat rendering still works without a CDN once it
has been installed, and the user can remove it from the Tools page.

The file is a fixed version (MathJax 3.2.2) and is checked against its SHA-256 before it is
kept, because Axis serves it to its own page, where it runs with the page's full access.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from . import __version__
from .paths import axis_home

ASSET_NAME = "tex-svg.js"
ASSET_URL = "https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-svg.js"
# The same file from a second host, tried when the first cannot be reached (some networks block one of them).
MIRROR_URL = "https://unpkg.com/mathjax@3.2.2/es5/tex-svg.js"
# SHA-256 of es5/tex-svg.js in the mathjax 3.2.2 package on npm (read from the package that npm's own
# sha512 integrity value vouches for).
ASSET_SHA256 = "d4295dc33744836935c1399feece5159577b34c5c8ffb9f1c6324cd82e03a882"
MIN_SIZE = 10000


class LatexError(Exception):
    pass


def _folder() -> Path:
    return axis_home() / "latex"


def _settings_path() -> Path:
    return axis_home() / "latex-settings.json"


def _settings() -> Dict[str, Any]:
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: Dict[str, Any]) -> None:
    axis_home().mkdir(parents=True, exist_ok=True)
    tmp = _settings_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, _settings_path())


def status() -> Dict[str, Any]:
    installed = (_folder() / ASSET_NAME).is_file()
    return {"installed": installed, "enabled": installed and bool(_settings().get("enabled")),
            "size": (_folder() / ASSET_NAME).stat().st_size if installed else 0,
            "url": ASSET_URL}


def _fetch(url: str, temp: Path) -> None:
    """Download url into temp. Raises LatexError if the answer is not the renderer."""
    request = urllib.request.Request(url, headers={"User-Agent": f"Axis/{__version__}"})
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=60) as response, temp.open("wb") as out:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            digest.update(chunk)
    if temp.stat().st_size < MIN_SIZE:
        raise LatexError("The LaTeX renderer download was incomplete.")
    if digest.hexdigest() != ASSET_SHA256:
        raise LatexError("The LaTeX renderer download failed its checksum, so it was not used.")


def _move_into_place(temp: Path, target: Path) -> None:
    """Rename temp over target. On Windows the rename fails while an antivirus program or the search
    indexer still has the new file open, so it is tried again for a few seconds."""
    for attempt in range(20):
        try:
            os.replace(temp, target)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.25)


def install() -> Dict[str, Any]:
    folder = _folder()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / ASSET_NAME
    # mkstemp returns a file that is already open. Close it at once: Windows will not rename or delete a
    # file that anything still has open, which made every download on Windows end in "Access is denied".
    fd, name = tempfile.mkstemp(prefix="mathjax-", suffix=".js", dir=str(folder))
    os.close(fd)
    temp = Path(name)
    problem: Optional[str] = None
    try:
        for url in (ASSET_URL, MIRROR_URL):
            try:
                _fetch(url, temp)
                problem = None
                break
            except (urllib.error.URLError, OSError, LatexError) as exc:
                reason = getattr(exc, "reason", None) or exc
                problem = str(reason) if not isinstance(exc, LatexError) else str(exc)
        if problem is not None:
            hint = "" if "checksum" in problem else " Check your internet connection."
            raise LatexError(f"Could not download the LaTeX renderer: {problem.rstrip('.')}.{hint}")
        try:
            _move_into_place(temp, target)
        except OSError as exc:
            raise LatexError(f"The LaTeX renderer was downloaded but could not be saved ({exc.strerror or exc}). "
                             "Close any program that is scanning the Axis folder and try again.") from exc
    finally:
        try:
            temp.unlink()
        except OSError:
            pass
    _save({**_settings(), "enabled": True})
    return status()


def set_enabled(enabled: Any) -> Dict[str, Any]:
    if bool(enabled) and not (_folder() / ASSET_NAME).is_file():
        raise LatexError("Download the LaTeX renderer before turning it on.")
    _save({**_settings(), "enabled": bool(enabled)})
    return status()


def remove() -> Dict[str, Any]:
    try:
        (_folder() / ASSET_NAME).unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise LatexError(f"Could not delete the LaTeX renderer ({exc.strerror or exc}). Try again in a moment.") from exc
    try:
        _folder().rmdir()
    except OSError:
        pass
    _save({"enabled": False})
    return status()


def script_path() -> Optional[Path]:
    info = status()
    return _folder() / ASSET_NAME if info["installed"] else None
